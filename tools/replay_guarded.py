"""Evaluator-only replay of an exactly recompiled workflow; no network or model."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from builder.call_variables import declared_call_variables
from builder.safety import canonical_json, digest, read_json, sanitize
from builder.validation import validate_workflow


class ReplayError(ValueError):
    pass


def _mapped(value):
    # The kit documents string scalar result bindings. JSON containers remain
    # serialized inputs for the sealed kernel, which parses its account result.
    if value is None:
        return ""
    if isinstance(value, (dict, list, bool)):
        return canonical_json(value)
    return str(value)


def _path(value, path):
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _condition(condition, variables, *, intent=False):
    if "conditions" in condition:
        values = [_condition(item, variables, intent=intent) for item in condition["conditions"]]
        return any(values) if condition["logic"] == "or" else all(values)
    operator = condition["operator"]
    if operator == "always":
        return True
    if operator == "matches_intent":
        return intent
    value, operand = variables.get(condition.get("variable")), condition.get("operand")
    if operator == "is_set":
        return value is not None and value != ""
    if operator == "is_not_set":
        return value is None or value == ""
    if operator == "equals":
        return str(value) == str(operand)
    if operator == "not_equals":
        return str(value) != str(operand)
    try:
        if operator == "contains":
            return str(operand).casefold() in str(value).casefold()
        if operator == "greater_than":
            return float(value) > float(operand)
        if operator == "less_than":
            return float(value) < float(operand)
    except (TypeError, ValueError):
        return False
    raise ReplayError("unsupported replay condition")


def _has_intent(condition):
    return condition.get("operator") == "matches_intent" or any(
        _has_intent(item) for item in condition.get("conditions", []))


def _execute(code, namespace, remaining):
    if remaining <= 0:
        raise ReplayError("replay execution deadline exhausted")
    previous = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    timed_out = False
    def expired(*_):
        nonlocal timed_out
        timed_out = True
        raise ReplayError("sealed code exceeded replay execution allocation")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(10, remaining))
    try:
        exec(code, namespace)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if old_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, old_timer[0] - (time.monotonic() - started)), old_timer[1])
    if timed_out:
        # The sealed wrapper catches ordinary exceptions to quarantine state.
        # An allocation overrun remains a failed replay even if it caught ours.
        raise ReplayError("sealed code exceeded replay execution allocation")


def replay(workflow, context, openapi, script, *, max_steps=256, seconds=30):
    """Use explicit ideal extraction/intent frames, never interpret caller speech."""
    if type(max_steps) is not int or not 1 <= max_steps <= 10000 or not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 < seconds <= 1800:
        raise ReplayError("invalid replay resource limits")
    started = time.monotonic()
    deadline = started + seconds
    workflow, context, openapi, script = deepcopy((workflow, context, openapi, script))
    if len(canonical_json([workflow, context, openapi, script]).encode()) > 32 * 1024 * 1024:
        raise ReplayError("replay inputs exceed the size bound")
    if not isinstance(context, dict) or set(context) - {"plan", "contract", "execution_policy", "bindings"}:
        raise ReplayError("replay requires the original controller guard context")
    errors = validate_workflow(workflow, openapi, guard_context=context)
    if errors:
        raise ReplayError("exact trusted workflow validation failed: " + "; ".join(errors[:8]))
    if (not isinstance(script, dict) or set(script) != {"version", "initial_inputs", "frames", "responses"}
            or type(script["version"]) is not int or script["version"] != 1
            or not isinstance(script["initial_inputs"], dict)
            or not isinstance(script["frames"], list) or not isinstance(script["responses"], list)):
        raise ReplayError("invalid evaluator replay script")
    readonly = set(declared_call_variables(context["contract"])) | {"customer_id", "now"}
    if set(script["initial_inputs"]) - readonly or not {"customer_id", "now"} <= set(script["initial_inputs"]):
        raise ReplayError("initial inputs must be declared call inputs, including customer_id and now")
    variables = deepcopy(script["initial_inputs"])
    nodes, current = workflow["nodes"], workflow["initial_node"]
    steps, calls, prompts, seen, turns = [], [], [], set(), {}
    frame_index = response_index = 0
    status = "step_limit"
    for _ in range(max_steps):
        if time.monotonic() >= deadline:
            status = "deadline"
            break
        node = nodes[current]
        signature = digest({"node": current, "variables": variables})
        frame = None
        if node["node_type"] == "conversation" and frame_index < len(script["frames"]):
            candidate = script["frames"][frame_index]
            if isinstance(candidate, dict) and candidate.get("node") == current:
                frame = candidate
        if frame is None and signature in seen:
            status = "absorbing_loop"
            break
        seen.add(signature)
        steps.append({"node": current, "type": node["node_type"]})
        if node["node_type"] == "end_call":
            status = "terminated"
            break
        intent_index = None
        if node["node_type"] == "code":
            code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
            _execute(code, {"vars": variables}, deadline - time.monotonic())
        elif node["node_type"] == "api_request":
            tool = node["pre_actions"][0]
            operation = tool["function"]["name"]
            if response_index >= len(script["responses"]):
                status = "missing_mock_response"
                break
            response = script["responses"][response_index]
            if (not isinstance(response, dict) or set(response) - {"operation", "node", "envelope"}
                    or response.get("operation") != operation or "envelope" not in response
                    or response.get("node", current) != current):
                raise ReplayError("mock response must match the actual next API operation and node")
            config = {"body": ""}
            _execute(tool["pre_process_code"], {"config": config, "vars": variables}, deadline - time.monotonic())
            arguments = json.loads(config["body"])
            if not isinstance(arguments, dict):
                raise ReplayError("sealed request body must be an object")
            result = deepcopy(response["envelope"])
            calls.append({"tool": operation, "node": current, "arguments": arguments, "envelope": deepcopy(result)})
            _execute(tool["post_process_code"], {"result": result}, deadline - time.monotonic())
            for mapping in node["response_mapping"]:
                variables[mapping["variable"]] = _mapped(_path(result, mapping["path"]))
            response_index += 1
        elif node["node_type"] == "conversation":
            prompts.append({"node": current, "task_message": node.get("task_message", ""), "on_enter": node.get("on_enter", {})})
            if frame is not None:
                if set(frame) - {"node", "values", "intent_transition"} or not isinstance(frame.get("values"), dict):
                    raise ReplayError("invalid conversation extraction frame")
                declared = {item["name"] for item in node.get("extraction_schema", [])}
                if set(frame["values"]) - declared or set(frame["values"]) & readonly or any(name.startswith("bg_") and name != "bg_consent" for name in frame["values"]):
                    raise ReplayError("conversation frame may write only this node's extraction outputs")
                intent_index = frame.get("intent_transition")
                if intent_index is not None and (type(intent_index) is not int or not 0 <= intent_index < len(node.get("transitions", []))):
                    raise ReplayError("intent selector must name this node's actual transition index")
                if intent_index is not None and not _has_intent(node["transitions"][intent_index]["condition"]):
                    raise ReplayError("intent selector cannot force a deterministic condition")
                variables.update(deepcopy(frame["values"]))
                frame_index += 1
                turns[current] = turns.get(current, 0) + 1
                seen.clear()  # New evaluator input may recover a prior blocked action.
        else:
            raise ReplayError("unhandled node in exact compiled workflow")
        target = next((route["target_node"] for index, route in enumerate(node.get("transitions", []))
                       if _condition(route["condition"], variables, intent=index == intent_index)), None)
        if node["node_type"] == "conversation" and frame is not None and node.get("max_node_turns") is not None and turns[current] >= node["max_node_turns"]:
            target = node.get("max_turn_transition", target)
        target = target or node.get("passthrough_default_transition")
        if target is None:
            # Kit routing rule 4 keeps a conversation in place when a caller
            # turn matches no route. Missing future input remains incomplete.
            if node["node_type"] == "conversation" and frame is not None:
                continue
            status = "missing_conversation_frame" if node["node_type"] == "conversation" and frame is None else "routing_deadlock"
            break
        if target != current:
            turns[current] = 0
        current = target
    state = json.loads(variables.get("bg_state", "{}"))
    return {"version": 1, "status": status, "terminal_node": current if status == "terminated" else None,
            "workflow_sha256": digest(workflow), "contract_sha256": context["contract"]["sha256"],
            "script_sha256": digest(script), "steps": steps, "calls": calls, "prompts": prompts,
            "frames_consumed": frame_index, "responses_consumed": response_index,
            "unused_frames": len(script["frames"]) - frame_index, "unused_responses": len(script["responses"]) - response_index,
            "final_state": state, "elapsed_seconds": time.monotonic() - started,
            "mock": True, "network_executed": False, "model_executed": False,
            "conversation_compliance_proven": False, "builder_budget_extended": False}


def _input(path):
    candidate = path if path.is_absolute() else ROOT / path
    if any(item.is_symlink() for item in (candidate, *candidate.parents)) or candidate.suffix != ".json":
        raise ReplayError("replay inputs require regular repository JSON files")
    resolved = candidate.resolve(strict=True)
    def common(directory):
        result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=directory, text=True, capture_output=True, check=True)
        return (directory / result.stdout.strip()).resolve()
    if (not resolved.is_file() or resolved.stat().st_size > 32 * 1024 * 1024
            or not resolved.is_relative_to(ROOT) and common(resolved.parent) != common(ROOT)):
        raise ReplayError("replay inputs must belong to this repository and fit the size bound")
    return read_json(resolved)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workflow", "guard-context", "tool-api", "script"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=256)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        workflow, context, api, script = [_input(path) for path in (args.workflow, args.guard_context, args.tool_api, args.script)]
        report = replay(workflow, context, api, script, max_steps=args.max_steps, seconds=args.seconds)
        rendered = canonical_json(sanitize(report)) + "\n"
        if args.output:
            output = args.output if args.output.is_absolute() else ROOT / args.output
            if not output.resolve().is_relative_to(ROOT / "artifacts") or any(item.is_symlink() for item in (output, *output.parents)):
                raise ReplayError("replay reports must stay under this checkout's artifacts directory")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
        else:
            print(rendered, end="")
        return 0 if report["status"] == "terminated" else 1
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(canonical_json({"status": "replay_rejected", "error": sanitize(str(exc))}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Evaluator-only, source-bound expectations for an original compiled replay.

An expectation is an independent evaluator input, never a generator input.
This checks one supplied ideal-extraction case; it does not establish source
interpretation, generation provenance, caller speech, hosting or all-path safety.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from builder.safety import canonical_json, digest, sanitize
from tools.replay_guarded import _input, replay, ReplayError


class AcceptanceError(ValueError):
    pass


def input_bindings(workflow, context, openapi, script):
    """Canonical digests of whole inputs, including the full frozen contract.

    contract_sha256 here includes the contract's own embedded sha256 field;
    it is intentionally distinct from that field's compiler-defined digest.
    """
    return {name + "_sha256": digest(value) for name, value in (
        ("workflow", workflow), ("guard_context", context), ("openapi", openapi),
        ("contract", context["contract"]), ("script", script))}


def _operations(openapi):
    names = []
    for path, methods in openapi.get("paths", {}).items():
        for method, operation in methods.items():
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}:
                # Same naming as builder/toolbindings.py: the kit OpenAPI has no operationId.
                name = operation.get("operationId") or path.rstrip("/").rsplit("/", 1)[-1]
                if not isinstance(name, str) or not name:
                    raise AcceptanceError("API operation requires an operationId or a named path")
                names.append(name)
    if len(names) != len(set(names)):
        raise AcceptanceError("API operation IDs must be unique")
    return set(names)


def _expectation(expectation, workflow, context, openapi, script):
    keys = {"version", "bindings", "allowed_operations", "calls", "terminal_node", "final_state", "closing"}
    if (not isinstance(expectation, dict) or set(expectation) != keys
            or type(expectation["version"]) is not int or expectation["version"] != 1):
        raise AcceptanceError("expectation requires the exact version1 schema")
    actual = input_bindings(workflow, context, openapi, script)
    bindings = expectation["bindings"]
    if (not isinstance(bindings, dict) or set(bindings) != set(actual)
            or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                   for value in bindings.values())):
        raise AcceptanceError("expectation requires all canonical input digests")
    if bindings != actual:
        raise AcceptanceError("expectation does not bind the original replay inputs")
    allowed = expectation["allowed_operations"]
    operations = _operations(openapi)
    if (not isinstance(allowed, list) or any(not isinstance(name, str) or not name for name in allowed)
            or len(allowed) != len(set(allowed)) or not set(allowed) <= operations):
        raise AcceptanceError("allowed operations must be unique supplied API operation IDs")
    calls = expectation["calls"]
    if not isinstance(calls, list) or any(
            not isinstance(call, dict) or set(call) != {"operation", "arguments"}
            or call["operation"] not in allowed or not isinstance(call["arguments"], dict) for call in calls):
        raise AcceptanceError("expected calls require allowed operations and exact argument objects")
    node = expectation["terminal_node"]
    if (not isinstance(node, str) or workflow.get("nodes", {}).get(node, {}).get("node_type") != "end_call"):
        raise AcceptanceError("expected terminal must be an original end_call node")
    state = expectation["final_state"]
    if (not isinstance(state, dict) or not state
            or any(not isinstance(name, str) or not name for name in state)
            or any(type(state.get(name)) is not bool for name in ("closed", "close_attempted", "quarantined"))):
        raise AcceptanceError("final state requires exact closed, close_attempted and quarantined booleans")
    closing = expectation["closing"]
    close_operation = context["execution_policy"]["profile"].get("tool_aliases", {}).get("close_call", "close_call")
    if (not isinstance(closing, dict) or set(closing) != {"mode", "operation"}
            or closing["mode"] not in {"recorded", "not_attempted", "unrecorded_attempt"}
            or closing["operation"] != close_operation or close_operation not in operations):
        raise AcceptanceError("closing requires a supported mode and the frozen close operation")
    mode = closing["mode"]
    if state["closed"] != (mode == "recorded") or state["close_attempted"] != (mode != "not_attempted"):
        raise AcceptanceError("expected closing mode contradicts expected protected state")
    if mode == "recorded" and state["quarantined"] or mode == "unrecorded_attempt" and not state["quarantined"]:
        raise AcceptanceError("expected closing certainty contradicts quarantine state")
    # Also reject non-JSON/nonfinite values before replay executes anything.
    canonical_json(expectation)
    return actual


def evaluate(workflow, context, openapi, script, expectation, *, max_steps=256, seconds=30):
    """Compare exact compiled behavior to a separately authored expectation.

    The sealed result kernel, verified by exact trusted recompilation, records
    its success interpretation in closed. Checking that protected state and
    the exact final close call reuses that implementation. Independent source
    permission and raw API receipt/schema interpretation remain review inputs.
    unrecorded_attempt does not distinguish definite failure from unknown
    persistence. The immediate replay transitions also do not prove platform
    scheduling of caller events or automatic entry actions.
    """
    workflow, context, openapi, script, expectation = deepcopy((workflow, context, openapi, script, expectation))
    bindings = _expectation(expectation, workflow, context, openapi, script)
    report = replay(workflow, context, openapi, script, max_steps=max_steps, seconds=seconds)
    failures = []
    if report["status"] != "terminated":
        failures.append("replay did not terminate")
    if report["terminal_node"] != expectation["terminal_node"]:
        failures.append("terminal differs from the expected original terminal")
    if (report["unused_frames"] or report["unused_responses"]
            or report["frames_consumed"] != len(script["frames"])
            or report["responses_consumed"] != len(script["responses"])):
        failures.append("replay did not consume every authored frame and mock response")
    calls = [{"operation": call["tool"], "arguments": call["arguments"]} for call in report["calls"]]
    if any(call["operation"] not in expectation["allowed_operations"] for call in calls):
        failures.append("replay called an operation outside the independent allowed set")
    if canonical_json(calls) != canonical_json(expectation["calls"]):
        failures.append("ordered API calls or exact typed arguments differ")
    state = report["final_state"]
    if any(name not in state or canonical_json(state[name]) != canonical_json(value)
           for name, value in expectation["final_state"].items()):
        failures.append("protected final state differs from the expected subset")
    closing = expectation["closing"]
    close_calls = [call for call in calls if call["operation"] == closing["operation"]]
    expected_count = 0 if closing["mode"] == "not_attempted" else 1
    if len(close_calls) != expected_count or close_calls and calls[-1] != close_calls[-1]:
        failures.append("closing call count or final ordering differs")
    return {"version": 1, "pass": not failures, "failures": failures,
            "bindings": bindings, "expectation_sha256": digest(expectation),
            "replay": report, "mock": True, "network_executed": False, "model_executed": False,
            "source_interpretation_proven": False, "generation_provenance_proven": False,
            "speech_behavior_proven": False, "hosted_behavior_proven": False,
            "platform_entry_event_behavior_proven": False,
            "all_path_safety_proven": False, "t07_acceptance_proven": False,
            "builder_budget_extended": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workflow", "guard-context", "tool-api", "script", "expectation"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=256)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = [_input(path) for path in (args.workflow, args.guard_context, args.tool_api, args.script, args.expectation)]
        result = evaluate(*inputs, max_steps=args.max_steps, seconds=args.seconds)
        rendered = canonical_json(sanitize(result)) + "\n"
        if args.output:
            output = args.output if args.output.is_absolute() else ROOT / args.output
            if (not output.resolve().is_relative_to(ROOT / "artifacts")
                    or any(item.is_symlink() for item in (output, *output.parents))):
                raise AcceptanceError("acceptance reports must stay under this checkout's artifacts directory")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
        else:
            print(rendered, end="")
        return 0 if result["pass"] else 1
    except (ValueError, TypeError, KeyError, AttributeError, OSError, subprocess.SubprocessError) as exc:
        print(canonical_json({"status": "acceptance_rejected", "error": sanitize(str(exc))}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

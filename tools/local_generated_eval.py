"""Join ideal-extraction trusted replay to an independent development ledger oracle."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.offline import controller_sources
from benchmarks.scoring import score_ledger
from builder.call_variables import declared_call_variables
from builder.safety import canonical_json, digest, sanitize
from tools.replay_guarded import _input, replay, ReplayError


class LocalEvaluationError(ValueError):
    pass


def _valid(value, schema):
    """Check the instance subset actually used by the approved development API."""
    kind = schema.get("type")
    kinds = {"string": (str,), "integer": (int,), "number": (int, float),
             "boolean": (bool,), "object": (dict,), "array": (list,)}
    if kind not in kinds or type(value) not in kinds[kind]:
        return False
    if any(key in schema for key in ("$ref", "allOf", "oneOf", "anyOf", "not")):
        return False  # Unsupported instance evidence is never accepted silently.
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        return False
    if kind in ("integer", "number"):
        return value >= schema.get("minimum", -float("inf")) and value <= schema.get("maximum", float("inf"))
    if kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 100000):
            return False
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return False
        if schema.get("format") == "date":
            try:
                return date.fromisoformat(value).isoformat() == value
            except ValueError:
                return False
    if kind == "object":
        fields = schema.get("properties", {})
        return (not set(value) - set(fields) and set(schema.get("required", [])) <= set(value)
                and all(_valid(child, fields[name]) for name, child in value.items()))
    if kind == "array":
        return all(_valid(child, schema["items"]) for child in value)
    return True


def normalize_calls(report, case, profile):
    """Decode declared profile wire fields, never candidate-asserted bindings.

    This is a projection of explicit mock receipts, not a bank simulator or a
    claim that real persistence occurred. Original envelopes stay in the replay.
    """
    ledger = {"promises": [], "notes": [], "callbacks": [], "transfers": [],
              "calls": [], "outcome": None, "reason": None}
    errors = []
    # Development-v2 retains a legacy flat alias map as well as its explicit
    # per-operation source-backed action_fields. The latter supplies roles.
    request_maps = profile["effective_values_expected"].get("action_fields", profile["request_field_mapping"])
    result_maps = profile["result_field_mapping"]
    if not request_maps or any(not isinstance(mapping, dict) for mapping in request_maps.values()):
        raise LocalEvaluationError("independent per-operation request bindings are required")
    aliases = profile["effective_values_expected"].get("tool_aliases", {})
    roles = {aliases.get(role, role): role for role in request_maps}
    if len(roles) != len(request_maps):
        raise LocalEvaluationError("independent operation bindings are ambiguous")
    operations = {entry["post"]["operationId"]: entry["post"] for entry in profile["openapi"]["paths"].values()}
    collections = {"create_promise": ("promises", ("product_id", "amount", "date")),
                   "add_note": ("notes", ("product_id", "reason")),
                   "schedule_callback": ("callbacks", ("date", "time")),
                   "transfer_to_agent": ("transfers", ("queue",))}
    for index, call in enumerate(report["calls"]):
        operation = call["tool"]
        if operation not in roles or operation not in operations:
            raise LocalEvaluationError("replay used an operation outside the independent profile")
        role, specification = roles[operation], operations[operation]
        raw_args, envelope = call["arguments"], call["envelope"]
        fields, results = request_maps[role], result_maps[role]
        args = {semantic: deepcopy(raw_args[wire]) for semantic, wire in fields.items() if wire in raw_args}
        body = envelope.get("data") if isinstance(envelope, dict) else None
        result = {semantic: deepcopy(body[wire]) for semantic, wire in results.items() if isinstance(body, dict) and wire in body}
        status = envelope.get("status_code") if isinstance(envelope, dict) else None
        valid = True
        def invalid(reason):
            nonlocal valid
            valid = False
            errors.append(f"call {index}: " + reason)
        request_schema = specification["requestBody"]["content"]["application/json"]["schema"]
        if not _valid(raw_args, request_schema) or set(raw_args) - set(fields.values()):
            invalid("request does not match independent API fields/schema")
        if args.get("customer_id") != case["variables"]["customer_id"]:
            invalid("request customer differs from the bound fixture")
        response = specification["responses"].get(str(status)) if type(status) is int else None
        schema = (response or {}).get("content", {}).get("application/json", {}).get("schema")
        if schema is None or not _valid(body, schema):
            invalid("receipt does not match its declared response status/schema")
        # Raw development APIs explicitly declare lower-case string booleans.
        # Schema validation above must succeed before those values mean anything.
        for flag in ("ok", "verified"):
            if flag in result and schema is not None:
                leaf = schema.get("properties", {}).get(results[flag], {})
                if leaf.get("type") == "string" and type(result[flag]) is str and result[flag] in ("true", "false"):
                    result[flag] = result[flag] == "true"
        if type(result.get("ok")) is not bool:
            invalid("explicit typed action receipt is absent")
        positive = result.get("ok") is True
        if positive and (type(status) is not int or not 200 <= status < 300):
            invalid("positive receipt contradicts its HTTP status")
        if role == "verify_identity" and positive:
            if type(result.get("verified")) is not bool or type(result.get("wrong_answers_left")) is not int:
                invalid("successful identity receipt lacks factor/counter evidence")
            if result.get("verified") is True and case["identity"].get(args.get("field")) != args.get("answer"):
                invalid("positive identity receipt contradicts the bound synthetic answer")
        if role == "get_account" and positive and canonical_json(result.get("products")) != canonical_json(case["products"]):
            invalid("account receipt differs from the bound fixture facts/order")
        if role == "create_promise":
            receipt = result.get("promise_id")
            if positive and (not isinstance(receipt, str) or not receipt.strip()):
                invalid("promise success lacks an explicit persisted-record identity")
            if not positive and receipt is not None:
                invalid("negative promise receipt contains a conflicting record identity")
        ledger["calls"].append({"tool": role, "args": args, "result": result,
                                "wire_operation": operation, "http_status": status})
        if not valid or not positive:
            continue
        if role in collections:
            collection, keys = collections[role]
            if any(key not in args for key in keys):
                invalid("successful write lacks complete independent terms")
                continue
            record = {key: deepcopy(args[key]) for key in keys}
            if role == "create_promise":
                record["promise_id"] = result["promise_id"]
            ledger[collection].append(record)
        elif role == "close_call":
            ledger["outcome"], ledger["reason"] = args.get("outcome"), args.get("reason")
    for fault in case["faults"]:
        attempts = [call for call in ledger["calls"] if call["tool"] == fault["tool"]]
        count = fault["fail_first_n"]
        if len(attempts) < count or any(call["result"].get("ok") is not False for call in attempts[:count]):
            errors.append("declared fixture fault was not represented by failed receipts")
    return ledger, errors


def evaluate(manifest, fixture_id, workflow, context, openapi, script, *, split="train", max_steps=256, seconds=30):
    if manifest.get("kind") != "offline":
        raise LocalEvaluationError("only independently authored offline development fixtures are allowed")
    case = next((row for row in manifest["cases"] if row["fixture_id"] == fixture_id), None)
    if split not in ("train", "validation") or case is None or case["split"] != split or case["status"] != "materialized":
        raise LocalEvaluationError("fixture must be materialized in the explicitly selected development split; no holdout execution")
    profile = next(row for row in manifest["profiles"] if row["profile_id"] == case["profile_id"])
    sources = [{key: value for key, value in row.items() if key != "text"} for row in controller_sources(profile)]
    if digest(openapi) != digest(profile["openapi"]) or context["contract"]["sources"] != sources:
        raise LocalEvaluationError("original generated sources/API do not match the independently bound profile")
    names = set(declared_call_variables(context["contract"])) | {"customer_id", "now"}
    if names - set(case["variables"]):
        raise LocalEvaluationError("fixture lacks a declared input; defaults are forbidden")
    initial = {name: case["variables"][name] for name in names}
    if canonical_json(script.get("initial_inputs")) != canonical_json(initial) or not isinstance(script.get("frames"), list) or not script["frames"]:
        raise LocalEvaluationError("supply bound synthetic inputs and independently authored current-plan frames")
    original = replay(workflow, context, openapi, script, max_steps=max_steps, seconds=seconds)
    ledger, errors = normalize_calls(original, case, profile)
    rules = profile["effective_values_expected"]
    verdict = score_ledger(case["expected"], ledger, identity_fields=rules["identity_fields"],
                           wrong_limit=rules["identity_wrong_limit"], brief="B", wire_codes=case["wire_codes"])
    if original["status"] != "terminated" or original["unused_frames"] or original["unused_responses"]:
        errors.append("replay did not terminate with the complete authored script consumed")
    if errors:
        verdict["pass"] = False
        verdict["failures"].extend({"category": "evidence", "message": error} for error in errors)
    return {"version": 1, "fixture_id": fixture_id, "profile_id": case["profile_id"], "split": split,
            "fixture_sha256": digest(case), "profile_sha256": digest(profile), "openapi_sha256": digest(openapi),
            "guard_context_sha256": digest(context), "replay": original, "normalized_ledger": ledger,
            "ledger_verdict": verdict, "source_api_binding_verified": True,
            "claim": "Ideal-extraction compiled execution and simulated-receipt ledger checks only.",
            "speech_behavior_proven": False, "hosted_behavior_proven": False,
            "actual_generation_provenance_checked": False, "builder_budget_extended": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "benchmarks/fixtures/offline.json")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--split", choices=("train", "validation"), default="train",
                        help="Validation is evaluator-only and requires this explicit opt-in.")
    for name in ("workflow", "guard-context", "tool-api", "script", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=256)
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        values = [_input(path) for path in (args.cases, args.workflow, args.guard_context, args.tool_api, args.script)]
        result = evaluate(values[0], args.fixture, *values[1:], split=args.split, max_steps=args.max_steps, seconds=args.seconds)
        from benchmarks.paths import output_path
        output = output_path(args.output)
        if not output.is_relative_to(ROOT / "artifacts"):
            raise LocalEvaluationError("local evaluation reports must stay under this checkout's artifacts")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(sanitize(result)) + "\n")
        print(canonical_json({"fixture_id": args.fixture, "pass": result["ledger_verdict"]["pass"],
                              "evaluation_kind": "ideal_extraction_local_replay", "output": str(output)}))
        return 0 if result["ledger_verdict"]["pass"] else 1
    except (ValueError, TypeError, KeyError, StopIteration, OSError, subprocess.SubprocessError) as exc:
        print(canonical_json({"status": "local_evaluation_rejected", "error": sanitize(str(exc))}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

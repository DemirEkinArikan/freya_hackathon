"""Evaluate original generated artifacts with source-reviewed local replay cases.

JSON format (version 1): candidate_bindings is the exact ``bindings`` returned by
load_candidate; source_inputs is [{id, path, sha256}] for ORIGINAL brief files;
cases is [{id, requirements, status, source_review, script, expected_calls,
assertions}]. Requirements are the three keys in REQUIREMENTS. Ready rows need
source_review={rationale, source_refs:[{id, quote}]} (reviewer is optional), an
explicit tools.replay_guarded script, complete ordered expected_calls (including an explicit [] for no dispatch) containing
{tool, arguments}, and assertions containing {path: JSON pointer, equals: value}.
Assertion paths must address final_state. Pending/unsupported rows instead need
a nonempty reason. No defaults, extracted caller speech or source values are
invented. PASS means these local assertions passed; independent source semantics,
case adequacy, T07 acceptance, hosted behavior and issue closure remain unproven.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from builder.action_bindings import compile_action_bindings
from builder.call_variables import declared_call_variables
from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements, validate_plan_execution
from builder.guards import compile_guarded_workflow, required_execution_kinds
from builder.policy import compile_execution_policy
from builder.safety import canonical_json, digest, read_json, sanitize, _pairs, _nonfinite
from builder.source_provenance import bind_run_provenance, verify_source_unchanged
from tools.replay_guarded import replay

REQUIREMENTS = {
    "confirmed_final_terms": "Write only confirmed final terms; never duplicate a recorded promise.",
    "deferred_notes_transfer": "Stage notes until non-transfer close; discard pending notes and unwritten commitments on transfer.",
    "partial_success_close_once": "Keep successful promises after partial failure; close exactly once.",
}
MAX_BYTES = 32 * 1024 * 1024
ARTIFACTS = {
    "manifest": "manifest.json", "policy": "policy.json", "plan": "plan.json",
    "contract": "policy.contract.json", "execution_policy": "execution-policy.json",
    "action_bindings": "action-bindings.json", "guard_manifest": "guard-manifest.json",
    "workflow": "workflow.json", "execution_requirements": "execution-requirements.json",
    "builder_source": "builder-source.json", "build_provenance": "build-provenance.json",
}


class MatrixError(ValueError):
    pass


def _check(condition, message):
    if not condition:
        raise MatrixError(message)


def _path(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    _check(not any(item.is_symlink() for item in (path, *path.parents)), "matrix inputs cannot be symlinks")
    resolved = path.resolve(strict=True)
    _check(resolved.is_dir() if directory else resolved.is_file(), "matrix input is not a regular file/directory")
    if not resolved.is_relative_to(ROOT):
        def common(where):
            result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=where,
                                    text=True, capture_output=True, check=True)
            return (where / result.stdout.strip()).resolve()
        _check(common(resolved if directory else resolved.parent) == common(ROOT),
               "matrix inputs must belong to this repository")
    if not directory:
        _check(resolved.stat().st_size <= MAX_BYTES, "matrix input exceeds size limit")
    return resolved


def _json(path):
    path = _path(path)
    _check(path.suffix == ".json", "matrix JSON input must have .json extension")
    return read_json(path)


def _raw_hash(path):
    return hashlib.sha256(_path(path).read_bytes()).hexdigest()


def load_candidate(candidate_dir, openapi_path, source_inputs):
    """Read, bind and recompile; never repair or replace original evidence."""
    directory = _path(candidate_dir, directory=True)
    values = {key: _json(directory / name) for key, name in ARTIFACTS.items()}
    manifest = values["manifest"]
    _check(manifest.get("status") in {"generated_validated", "exported"}
           and manifest.get("action_guard_compilation_verified") is True,
           "candidate must be an originally completed guarded generation")
    selected = manifest.get("selected_attempt")
    _check(type(selected) is int and selected > 0, "missing original selected attempt")
    attempt_name = f"attempts/plan-{selected:03d}"
    _check(manifest.get("selected_attempt_directory") == attempt_name, "selected attempt directory changed")
    attempt = directory / attempt_name
    values["guard_context"] = _json(attempt / "guard-context.json")
    values["attempt_record"] = _json(attempt / "attempt.json")
    context = values["guard_context"]
    _check(isinstance(context, dict) and set(context) == {"plan", "contract", "execution_policy", "bindings"},
           "missing original guard context or bindings")
    for key, context_key in (("plan", "plan"), ("contract", "contract"),
                             ("execution_policy", "execution_policy"), ("action_bindings", "bindings")):
        _check(values[key] == context[context_key], "original context differs from candidate " + key)
    for key in ("plan", "execution_policy", "action_bindings", "guard_manifest", "workflow", "execution_requirements"):
        _check(_json(attempt / ARTIFACTS[key]) == values[key], "selected original attempt differs: " + key)
    api = _json(openapi_path)
    _check(digest(api) == manifest.get("tool_api_sha256"), "original OpenAPI binding differs")
    _check(isinstance(source_inputs, list) and source_inputs, "original source input bindings are required")
    sources, source_ids = [], set()
    for item in source_inputs:
        _check(isinstance(item, dict) and set(item) == {"id", "path", "sha256"}, "invalid original source input binding")
        _check(isinstance(item["id"], str) and item["id"] and item["id"] not in source_ids,
               "source input identifiers must be unique")
        source_ids.add(item["id"])
        path = _path(item["path"])
        text = path.read_text(encoding="utf-8")
        _check(text.strip() and digest(text) == item["sha256"], "original source text hash differs")
        sources.append({"id": item["id"], "file": path.name, "sha256": digest(text), "text": text})
    inventory = [{key: item[key] for key in ("id", "file", "sha256")} for item in sources]
    _check(inventory == manifest.get("sources"), "original source inventory differs from generated inputs")
    _check(compile_policy_contract(values["policy"], sources, api) == values["contract"],
           "original policy contract does not recompile exactly")
    _check(compile_action_bindings(values["policy"], api) == values["action_bindings"], "action bindings changed")
    _check(compile_execution_policy(values["policy"], values["action_bindings"],
                                   required_kinds=required_execution_kinds(values["plan"])) == values["execution_policy"],
           "execution policy changed")
    requirements = compile_execution_requirements(values["policy"], sources, api)
    _check(requirements == values["execution_requirements"], "execution requirements changed")
    _check(not validate_plan_execution(values["plan"], requirements, values["policy"], sources, api),
           "original plan execution coverage is invalid")
    bundle = compile_guarded_workflow(openapi=api, **context)
    _check(bundle["workflow"] == values["workflow"] and bundle["guard_manifest"] == values["guard_manifest"],
           "workflow/guard manifest do not recompile exactly")
    checks = {"plan_sha256": digest(values["plan"]), "policy_contract_sha256": values["contract"]["sha256"],
              "guard_manifest_sha256": digest(values["guard_manifest"]),
              "execution_requirements_sha256": requirements["sha256"]}
    checks["authored_workflow_sha256" if manifest["status"] == "exported" else "workflow_sha256"] = digest(values["workflow"])
    _check(all(manifest.get(key) == value for key, value in checks.items()), "original manifest artifact binding differs")
    record = values["attempt_record"]
    _check(record.get("number") == selected and record.get("directory") == attempt_name
           and record.get("status") == manifest["status"] and record.get("phase") == "complete"
           and record.get("policy_sha256") == digest(values["policy"])
           and record.get("workflow_sha256") == digest(values["workflow"])
           and record.get("plan_sha256") == digest(values["plan"])
           and record.get("tool_api_sha256") == digest(api)
           and record.get("policy_contract_sha256") == values["contract"]["sha256"],
           "original selected attempt record differs")
    trace_path = _path(directory / "trace.jsonl")
    events = [json.loads(line, object_pairs_hook=_pairs, parse_constant=_nonfinite)
              for line in trace_path.read_text(encoding="utf-8").splitlines()]
    _check(events and all(isinstance(item, dict) for item in events), "original trace is invalid")
    _check(any(item.get("event") == "inputs" and item.get("sources") == inventory
               and item.get("tool_api_sha256") == digest(api) for item in events), "original trace input binding differs")
    _check(any(item.get("event") == "policy_frozen" and item.get("policy_sha256") == digest(values["policy"])
               and item.get("contract_sha256") == values["contract"]["sha256"] for item in events),
           "original trace frozen-policy binding differs")
    _check(events[-1].get("event") == "end" and events[-1].get("status") == manifest["status"],
           "original trace did not complete the selected candidate")
    verify_source_unchanged(ROOT, values["builder_source"])
    _check(bind_run_provenance(values["builder_source"], manifest, events) == values["build_provenance"],
           "original source/manifest/trace provenance changed")
    values["trace"] = events
    bindings = {key + "_sha256": digest(value) for key, value in values.items()}
    bindings.update(openapi_sha256=digest(api), input_bindings_sha256=digest(inventory),
                    builder_source_sha256=values["builder_source"]["builder_source_sha256"],
                    builder_source_record_sha256=digest(values["builder_source"]),
                    source_inputs_sha256=digest(source_inputs))
    raw = {name: _raw_hash(directory / name) for name in ARTIFACTS.values()}
    raw["trace.jsonl"] = _raw_hash(trace_path)
    for name in ("guard-context.json", "attempt.json", "plan.json", "execution-policy.json", "action-bindings.json",
                 "guard-manifest.json", "workflow.json", "execution-requirements.json"):
        raw[attempt_name + "/" + name] = _raw_hash(attempt / name)
    raw["openapi"] = _raw_hash(openapi_path)
    raw["source_inputs"] = {item["id"]: _raw_hash(item["path"]) for item in source_inputs}
    return {"values": values, "openapi": api, "sources": sources, "bindings": bindings, "raw_file_sha256": raw}


def _pointer(value, path):
    _check(isinstance(path, str) and path.startswith("/final_state/"), "assertions must address final_state")
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            _check(key.isdigit() and int(key) < len(value), "assertion path is absent")
            value = value[int(key)]
        else:
            _check(isinstance(value, dict) and key in value, "assertion path is absent")
            value = value[key]
    return value


def _review(row, sources):
    review = row.get("source_review")
    _check(isinstance(review, dict) and not set(review) - {"reviewer", "rationale", "source_refs"}
           and isinstance(review.get("rationale"), str) and review["rationale"].strip(),
           "ready cases require a source-review rationale")
    refs = review.get("source_refs")
    _check(isinstance(refs, list) and refs, "ready cases require original source quotation references")
    texts = {item["id"]: item["text"] for item in sources}
    for ref in refs:
        _check(isinstance(ref, dict) and set(ref) == {"id", "quote"}
               and isinstance(ref["quote"], str) and ref["quote"].strip()
               and isinstance(ref["id"], str) and ref["id"] in texts and ref["quote"] in texts[ref["id"]], "case source quotation is not in original input")


def _relevant(requirement, report, assertions, context):
    """Reject unrelated witnesses; semantic sufficiency remains a human review."""
    paths = {item["path"].split("/")[2] for item in assertions}
    aliases = context["execution_policy"]["profile"].get("tool_aliases", {})
    calls = [item["tool"] for item in report["calls"]]
    steps = {item["node"] for item in report["steps"]}
    staged_note = any(name in steps and node.get("action") == "stage_note"
                      for name, node in context["plan"]["nodes"].items())
    if requirement == "confirmed_final_terms":
        actions = {node.get("action") for name, node in context["plan"]["nodes"].items() if name in steps}
        promise = "recorded_promises" in paths and (
            aliases.get("create_promise", "create_promise") in calls or {"stage_promise", "commit_promises"} & actions)
        callback = "callbacks" in paths and (
            aliases.get("schedule_callback", "schedule_callback") in calls or "schedule_callback" in actions)
        return bool(promise or callback)
    if requirement == "deferred_notes_transfer":
        return staged_note and {"pending_notes", "pending_promises"} <= paths and any(
            aliases.get(role, role) in calls for role in ("add_note", "transfer_to_agent"))
    return (aliases.get("create_promise", "create_promise") in calls
            and aliases.get("close_call", "close_call") in calls and {"recorded_promises", "closed"} <= paths)


def evaluate(candidate, cases, *, max_steps=256, seconds=30):
    _check(isinstance(cases, dict) and set(cases) == {"version", "candidate_bindings", "source_inputs", "cases"}
           and type(cases["version"]) is int and cases["version"] == 1, "invalid matrix case file or unsupported claims")
    _check(cases["candidate_bindings"] == candidate["bindings"], "case file is not bound to the exact original candidate")
    _check(isinstance(cases["cases"], list) and len(cases["cases"]) <= 256, "invalid matrix case rows")
    rows, ids = [], set()
    context = candidate["values"]["guard_context"]
    required_inputs = set(declared_call_variables(context["contract"])) | {"customer_id", "now"}
    for row in cases["cases"]:
        _check(isinstance(row, dict) and not set(row) - {"id", "requirements", "status", "reason", "source_review",
                                                       "script", "expected_calls", "assertions"}, "invalid case row or unsupported result claim")
        cid, required = row.get("id"), row.get("requirements")
        _check(isinstance(cid, str) and cid.strip() and cid not in ids, "case IDs must be nonempty and unique")
        ids.add(cid)
        _check(isinstance(required, list) and required and all(isinstance(item, str) for item in required) and len(set(required)) == len(required)
               and all(item in REQUIREMENTS for item in required), "case must bind declared T08 requirements")
        _check(row.get("status") in {"ready", "pending", "unsupported"}, "case status cannot assert a result")
        entry = {"id": cid, "requirements": required, "case_sha256": digest(row)}
        if row["status"] != "ready":
            _check(isinstance(row.get("reason"), str) and row["reason"].strip(), "pending/unsupported case needs a reason")
            rows.append({**entry, "status": "PENDING", "executed": False, "reason": row["reason"],
                         "unsupported": row["status"] == "unsupported"})
            continue
        _review(row, candidate["sources"])
        script, expected, assertions = row.get("script"), row.get("expected_calls"), row.get("assertions")
        _check(isinstance(script, dict) and isinstance(script.get("initial_inputs"), dict)
               and set(script["initial_inputs"]) == required_inputs, "every original call input must be explicitly bound")
        _check(isinstance(expected, list) and all(isinstance(item, dict)
               and set(item) == {"tool", "arguments"} and isinstance(item["tool"], str)
               and isinstance(item["arguments"], dict) for item in expected), "ready cases need complete ordered API expectations")
        _check(isinstance(assertions, list) and assertions and all(isinstance(item, dict)
               and set(item) == {"path", "equals"} and isinstance(item["path"], str)
               and item["path"].startswith("/final_state/") and item["path"] != "/final_state/" for item in assertions), "ready cases need explicit final-state assertions")
        original = replay(candidate["values"]["workflow"], context, candidate["openapi"], script,
                          max_steps=max_steps, seconds=seconds)
        _check(all(_relevant(item, original, assertions, context) for item in required),
               "case requirement lacks relevant executed API/state witnesses")
        results = [{"path": item["path"], "expected": item["equals"], "actual": _pointer(original, item["path"]),
                    "pass": canonical_json(_pointer(original, item["path"])) == canonical_json(item["equals"])} for item in assertions]
        actual_calls = [{key: call[key] for key in ("tool", "arguments")} for call in original["calls"]]
        complete = original["status"] == "terminated" and not original["unused_frames"] and not original["unused_responses"]
        passed = complete and canonical_json(actual_calls) == canonical_json(expected) and all(item["pass"] for item in results)
        rows.append({**entry, "status": "PASS" if passed else "FAIL", "executed": True,
                     "script_sha256": digest(script), "initial_inputs_sha256": digest(script["initial_inputs"]),
                     "complete_script_consumed": complete, "ordered_calls_match": canonical_json(actual_calls) == canonical_json(expected),
                     "assertions": results, "source_review": row["source_review"], "replay": original})
    requirements = {}
    for key, text in REQUIREMENTS.items():
        related = [row for row in rows if key in row["requirements"]]
        status = "FAIL" if any(row["status"] == "FAIL" for row in related) else (
            "PASS" if related and all(row["status"] == "PASS" for row in related) else "PENDING")
        requirements[key] = {"text": text, "local_assertion_status": status, "case_ids": [row["id"] for row in related],
                             "independent_adequacy_verified": False}
    statuses = [row["local_assertion_status"] for row in requirements.values()]
    return {"version": 1, "kind": "t08_original_candidate_local_release_matrix",
            "status": "FAIL" if "FAIL" in statuses else "PASS" if all(item == "PASS" for item in statuses) else "PENDING",
            "candidate_bindings": candidate["bindings"], "raw_file_sha256": candidate["raw_file_sha256"],
            "cases_sha256": digest(cases), "requirements": requirements, "cases": rows,
            "claim": "Source-referenced ideal-extraction local assertions only; PASS does not establish case adequacy or task acceptance.",
            "original_artifact_recompilation_verified": True, "source_semantics_verified": False,
            "t07_acceptance_verified": False, "task_acceptance_verified": False, "issue_closure_authorized": False,
            "hosted_behavior_verified": False, "conversation_compliance_proven": False,
            "network_executed": False, "model_executed": False, "builder_budget_extended": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate-dir", "openapi", "cases", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=256)
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        cases = _json(args.cases)
        _check(isinstance(cases, dict), "matrix case file must be an object")
        candidate = load_candidate(args.candidate_dir, args.openapi, cases.get("source_inputs"))
        result = evaluate(candidate, cases, max_steps=args.max_steps, seconds=args.seconds)
        result["cases_file_sha256"] = _raw_hash(args.cases)
        output = args.output if args.output.is_absolute() else ROOT / args.output
        _check(output.suffix == ".json" and output.resolve().is_relative_to(ROOT / "artifacts")
               and not any(item.is_symlink() for item in (output, *output.parents)),
               "matrix output must be new JSON under this checkout's artifacts")
        original_paths = [Path(args.cases), Path(args.openapi)] + [Path(item["path"]) for item in cases["source_inputs"]]
        _check(not output.resolve().is_relative_to(_path(args.candidate_dir, directory=True))
               and all(output.resolve() != _path(path) for path in original_paths), "output cannot modify original inputs")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(sanitize(result)) + "\n")
        print(canonical_json({"status": result["status"], "output": str(output), "task_acceptance_verified": False}))
        return 0 if result["status"] == "PASS" else 1
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(canonical_json({"status": "matrix_rejected", "error": sanitize(str(exc))}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

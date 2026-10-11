"""Compare recorded development trials locally; never execute a builder or API."""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, DecimalException
import json
import math
from pathlib import Path
import re

from builder.platform import AGENT_LLM, SIM_LLM, STAGING
from builder.safety import assert_no_secrets, digest, read_json, sanitize
from .paths import PROJECT, output_path
from .scoring import cents, score_ledger
from .hosted import attribute_version

CRITICAL_CATEGORIES = ("security", "policy", "honesty")


class ComparisonError(ValueError):
    """Fixed, data-free evidence rejection code."""


def _require(condition, code):
    if not condition:
        raise ComparisonError(code)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


def _bounded_cents(value):
    number = Decimal(str(value))
    # A normalization resource limit, not a bank eligibility/amount rule.
    _require(number.is_finite() and (number.is_zero() or abs(number.adjusted()) <= 1000),
             "promise_amount_evidence_invalid")
    return 0 if number.is_zero() else cents(value)


def _period(value):
    _require(isinstance(value, dict), "timing_absent")
    try:
        start = datetime.fromisoformat(value["started_at"])
        end = datetime.fromisoformat(value["ended_at"])
        _require(start.utcoffset() is not None and end.utcoffset() is not None, "timing_timezone_absent")
        _require(end >= start, "timing_reversed")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ComparisonError):
            raise
        raise ComparisonError("timing_invalid") from None
    return start, end, (end - start).total_seconds()


def _pin(report, conditions):
    pin = report.get("deployment_pin")
    _require(isinstance(pin, dict) and pin.get("verified") is True and pin.get("per_call_version_verified") is True,
             "deployment_pin_unverified")
    before, after = pin.get("before"), pin.get("after")
    _require(isinstance(before, dict) and isinstance(after, dict) and digest(before) == digest(after), "deployment_pin_changed")
    _require(all(_identifier(before.get(key)) for key in ("workflow_id", "agent_id", "version_id"))
             and type(before.get("version_number")) is int and before["version_number"] > 0
             and _sha(before.get("graph_sha256")) and before.get("published") is True,
             "deployment_pin_incomplete")
    _require(before.get("model") == conditions["builder_model"]
             and before.get("reasoning_effort") == conditions["reasoning_effort"], "deployment_config_mismatch")
    return before


def _trial(value, conditions, brief):
    _require(isinstance(value, dict) and _sha(value.get("case_manifest_sha256")), "case_source_absent")
    start, end, elapsed = _period(value)
    _require(elapsed <= conditions["budget_seconds"], "trial_budget_exceeded")
    report = value.get("report")
    _require(isinstance(report, dict) and report.get("status") == "completed", "trial_infrastructure_incomplete")
    pin = _pin(report, conditions)
    reps = report.get("reps")
    _require(type(reps) is int and reps == conditions["reps"], "repetition_conditions_mismatch")
    rows, final = report.get("rows"), report.get("final")
    _require(isinstance(rows, list) and isinstance(final, list) and bool(final), "trial_rows_absent")
    _require(type(report.get("total")) is int and report["total"] == len(final), "trial_total_inconsistent")
    _require(len(rows) == len(final) * reps, "trial_row_missing_or_duplicated")
    fixture_ids = [row.get("fixture_id") for row in final if isinstance(row, dict)]
    _require(len(fixture_ids) == len(final) and all(_identifier(identifier) for identifier in fixture_ids)
             and len(set(fixture_ids)) == len(fixture_ids), "fixture_inventory_invalid")
    expected_pairs = {(identifier, repetition) for identifier in fixture_ids for repetition in range(1, reps + 1)}
    pairs, by_fixture, calls, sessions = set(), {identifier: [] for identifier in fixture_ids}, set(), set()
    failures, failure_runs, amount = [], 0, 0
    recorded_amounts = {}
    critical_counts = {category: 0 for category in CRITICAL_CATEGORIES}
    unclassified_runs = 0
    for row in rows:
        _require(isinstance(row, dict) and _identifier(row.get("fixture_id"))
                 and type(row.get("repetition")) is int, "trial_row_invalid")
        pair = row["fixture_id"], row["repetition"]
        _require(pair in expected_pairs and pair not in pairs, "trial_row_missing_or_duplicated")
        pairs.add(pair)
        _require(type(row.get("pass")) is bool and isinstance(row.get("failures"), list), "row_verdict_invalid")
        _require(all(isinstance(failure, dict) and isinstance(failure.get("category"), str) and failure["category"] in
                     {"behavior", "conversation", "honesty", "policy", "security"} for failure in row["failures"]),
                 "row_infrastructure_or_unknown_failure")
        _require(("missing_evidence" not in row or isinstance(row["missing_evidence"], list)
                  and not row["missing_evidence"]) and row["pass"] == (not row["failures"]), "row_verdict_inconsistent")
        call_id, session = row.get("call_id"), row.get("session_fingerprint")
        _require(_identifier(call_id) and call_id not in calls and isinstance(session, str)
                 and re.fullmatch(r"[0-9a-f]{16,64}", session) is not None and session not in sessions,
                 "call_or_session_evidence_reused")
        calls.add(call_id)
        sessions.add(session)
        ledger, judge, transcript = row.get("ledger"), row.get("judge"), row.get("transcript")
        _require(isinstance(ledger, dict) and all(isinstance(ledger.get(key), list) for key in
                 ("promises", "notes", "callbacks", "transfers", "calls")), "ledger_evidence_absent")
        unclosed = "outcome" in ledger and "reason" in ledger and ledger["outcome"] is None and ledger["reason"] is None
        _require(unclosed or bool(ledger["calls"]), "ledger_evidence_absent")
        _require("outcome" in ledger and "reason" in ledger
                 and (unclosed or _identifier(ledger["outcome"]))
                 and (ledger["reason"] is None or _identifier(ledger["reason"]))
                 and all(isinstance(call, dict) and _identifier(call.get("tool")) and isinstance(call.get("result"), dict)
                         for call in ledger["calls"]), "ledger_evidence_malformed")
        for collection, fields in (("promises", ("product_id", "date")), ("notes", ("reason",)),
                                   ("callbacks", ("date", "time")), ("transfers", ("queue",))):
            _require(all(isinstance(record, dict) and all(_identifier(record.get(field)) for field in fields)
                         for record in ledger[collection]), "ledger_evidence_malformed")
        try:
            values = [_bounded_cents(record["amount"]) for record in ledger["promises"]]
            _require(all(value >= 0 for value in values), "promise_amount_evidence_invalid")
            recorded_amounts[pair] = sum(values)
        except (KeyError, TypeError, ValueError, DecimalException):
            raise ComparisonError("promise_amount_evidence_invalid") from None
        # This rechecks tool evidence and invariant behavior, not the independent
        # fixture oracle or conversation. A failure with complete evidence may
        # be compared; contradictory/missing evidence cannot be ranked.
        # A present null/null closing receipt is complete evidence of an
        # unclosed call. Supply a synthetic expected close solely for invariant
        # validation; never turn it into a passing oracle or alter the receipt.
        internal_expected = {**ledger, "outcome": "no_promise"} if unclosed else ledger
        internal = score_ledger(internal_expected, ledger, brief=brief)
        _require(not internal["missing_evidence"] and not any(failure["category"] in
                 ("infrastructure", "evidence") for failure in internal["failures"]), "ledger_evidence_incomplete")
        _require(not row["pass"] or internal["pass"], "row_ledger_verdict_inconsistent")
        _require(isinstance(judge, dict) and judge.get("callId") == call_id and judge.get("outcome") in ("pass", "fail"),
                 "judge_evidence_absent")
        _require(isinstance(transcript, dict) and isinstance(transcript.get("messages"), list)
                 and bool(transcript["messages"]) and all(isinstance(message, dict)
                     and message.get("role") in ("user", "assistant", "system")
                     and _identifier(message.get("message", message.get("content"))) for message in transcript["messages"]),
                 "transcript_evidence_absent")
        _require(any(field in transcript for field in ("id", "callId"))
                 and all(transcript[field] == call_id for field in ("id", "callId") if field in transcript),
                 "transcript_call_mismatch")
        _require(all(evidence[field] == pin[pin_field] for evidence in (judge, transcript)
                     for field, pin_field in (("agentId", "agent_id"), ("workflowId", "workflow_id"))
                     if field in evidence), "per_call_deployment_mismatch")
        inferred, attribution_failures = attribute_version(judge, transcript, pin["version_number"])
        attribution = row.get("version_attribution")
        _require(not attribution_failures and inferred["verified"] and isinstance(attribution, dict)
                 and digest(attribution) == digest(inferred), "per_call_version_unattributed")
        _require(not row["pass"] or judge["outcome"] == "pass", "judge_verdict_inconsistent")
        _require(judge["outcome"] != "fail" or any(failure["category"] == "conversation"
                 for failure in row["failures"]), "judge_verdict_inconsistent")
        by_fixture[pair[0]].append(row)
        failures.extend(row["failures"])
        failure_runs += bool(row["failures"])
        for failure in row["failures"]:
            if failure["category"] in critical_counts:
                critical_counts[failure["category"]] += 1
        unclassified_runs += any(failure["category"] in ("behavior", "conversation") for failure in row["failures"])
    _require(pairs == expected_pairs, "trial_row_missing_or_duplicated")
    _require(report.get("failures") == failures, "failure_aggregate_inconsistent")
    passed = 0
    for summary in final:
        matches = by_fixture[summary["fixture_id"]]
        passes = sum(row["pass"] for row in matches)
        strict = passes == reps
        _require(type(summary.get("passes")) is int and summary["passes"] == passes
                 and type(summary.get("runs")) is int and summary["runs"] == reps
                 and type(summary.get("required_runs")) is int and summary["required_runs"] == reps
                 and type(summary.get("pass")) is bool and summary["pass"] == strict, "strict_summary_inconsistent")
        passed += strict
        if strict:
            # Count the conservative minimum for each stable fixture once; never
            # multiply the same customer's amount by the number of repetitions.
            amounts = [recorded_amounts[(row["fixture_id"], row["repetition"])] for row in matches]
            amount += min(amounts)
    _require(type(report.get("passed")) is int and report["passed"] == passed, "strict_summary_inconsistent")
    simulations = report.get("simulations")
    _require(isinstance(simulations, list) and len(simulations) == reps, "simulation_evidence_incomplete")
    simulation_ids, iteration_ids, repetition_ids = set(), set(), set()
    simulation_by_repetition = {}
    for simulation in simulations:
        _require(isinstance(simulation, dict) and type(simulation.get("repetition")) is int
                 and 1 <= simulation["repetition"] <= reps and simulation["repetition"] not in repetition_ids
                 and simulation.get("status") == "completed", "simulation_evidence_incomplete")
        repetition_ids.add(simulation["repetition"])
        simulation_by_repetition[simulation["repetition"]] = simulation
        for key, seen in (("simulation_id", simulation_ids), ("iteration_id", iteration_ids)):
            _require(_identifier(simulation.get(key)) and simulation[key] not in seen, "simulation_evidence_reused")
            seen.add(simulation[key])
    for row in rows:
        simulation = simulation_by_repetition[row["repetition"]]
        _require(all(row.get(key) == simulation[key] for key in ("simulation_id", "iteration_id")), "row_simulation_attribution_absent")
    return {"start": start, "end": end, "case_sha": value["case_manifest_sha256"], "fixtures": set(fixture_ids),
            "pin": pin, "calls": calls, "sessions": sessions, "simulations": simulation_ids, "iterations": iteration_ids,
            "metrics": {"strict_passed": passed, "total": len(final), "required_repetitions": reps,
                        "behavior_failure_runs": failure_runs, "critical_failures": critical_counts,
                        "unclassified_behavior_runs": unclassified_runs, "strict_promised_cents": amount},
            "report_sha256": digest(report)}


def _build_provenance(value, manifest, conditions, build_start, build_end):
    """Check the original invocation record without consulting current source files."""
    binding = value.get("build_provenance")
    _require(isinstance(binding, dict) and type(binding.get("version")) is int
             and binding["version"] == 1, "build_source_provenance_absent")
    snapshot = binding.get("source_snapshot")
    _require(isinstance(snapshot, dict) and type(snapshot.get("version")) is int
             and snapshot["version"] == 1, "build_source_snapshot_invalid")
    files, config = snapshot.get("files"), snapshot.get("resolved_config")
    _require(isinstance(files, dict) and bool(files)
             and all(isinstance(path, str) and "\\" not in path and ":" not in path
                     and all(ord(character) >= 32 and not 127 <= ord(character) <= 159 for character in path)
                     and all(part not in ("", ".", "..") for part in path.split("/"))
                     and _sha(fingerprint) for path, fingerprint in files.items())
             and isinstance(config, dict), "build_source_snapshot_invalid")
    fingerprint = digest({key: snapshot[key] for key in ("version", "files", "resolved_config")})
    _require(all(record.get("builder_source_sha256") == fingerprint
                 for record in (value, manifest, binding, snapshot))
             and binding.get("builder_source_record_sha256") == digest(snapshot)
             and manifest.get("builder_source_record_sha256") == digest(snapshot)
             and binding.get("build_manifest_sha256") == digest(manifest)
             and _sha(binding.get("trace_sha256")), "build_source_provenance_mismatch")
    deadline, reserve = config.get("deadline_seconds"), config.get("export_reserve_seconds")
    _require(config.get("mode") == "live" and config.get("model") == conditions["builder_model"]
             and config.get("reasoning_effort") == conditions["reasoning_effort"]
             and config.get("controller_dependency_injection") is False
             and type(deadline) in (int, float) and math.isfinite(deadline) and 0 < deadline <= 1800
             and type(reserve) in (int, float) and math.isfinite(reserve) and 300 <= reserve <= deadline,
             "build_source_config_mismatch")
    _require((build_end - build_start).total_seconds() <= deadline, "build_recorded_deadline_exceeded")
    elapsed = manifest.get("elapsed_seconds")
    _require(type(elapsed) in (int, float) and 0 <= elapsed <= deadline and math.isfinite(elapsed),
             "build_elapsed_evidence_invalid")
    try:
        captured = datetime.fromisoformat(snapshot.get("captured_at"))
        _require(captured.utcoffset() is not None and build_start <= captured <= build_end,
                 "build_source_capture_mismatch")
    except (TypeError, ValueError):
        raise ComparisonError("build_source_capture_mismatch") from None
    return max((build_end - build_start).total_seconds(), elapsed)


def _bundle(value):
    _require(isinstance(value, dict) and value.get("version") == 1 and type(value.get("version")) is int,
             "comparison_bundle_invalid")
    _require(value.get("brief") in ("A", "B") and _sha(value.get("source_sha256")), "brief_or_source_unrecorded")
    _require(_sha(value.get("builder_source_sha256")), "builder_source_unrecorded")
    conditions = value.get("conditions")
    _require(isinstance(conditions, dict), "conditions_unrecorded")
    _require(all(_identifier(conditions.get(key)) for key in ("builder_model", "reasoning_effort"))
             and type(conditions.get("reps")) is int and conditions["reps"] >= 3
             and type(conditions.get("concurrency")) is int and 1 <= conditions["concurrency"] <= 5
             and type(conditions.get("budget_seconds")) is int and 0 < conditions["budget_seconds"] <= 86400,
             "conditions_invalid")
    _require(conditions.get("agent_llm") == AGENT_LLM and conditions.get("persona_llm") == SIM_LLM
             and conditions.get("analysis_llm") == SIM_LLM, "fixed_models_unrecorded")
    _require(conditions.get("platform_base_url") == STAGING
             and conditions.get("bank_base_url") == "https://freya-bank-mock.freyavoice.workers.dev"
             and _sha(conditions.get("calendar_sha256")), "environment_or_calendar_unrecorded")
    manifest, exported = value.get("build_manifest"), value.get("workflow_export")
    _require(isinstance(manifest, dict) and manifest.get("mode") == "live" and manifest.get("status") == "exported"
             and manifest.get("model") == conditions["builder_model"]
             and manifest.get("reasoning_effort") == conditions["reasoning_effort"], "build_provenance_absent")
    sources = manifest.get("sources")
    _require(isinstance(sources, list) and bool(sources)
             and all(isinstance(source, dict) and _sha(source.get("sha256")) for source in sources)
             and _sha(manifest.get("tool_api_sha256")), "build_source_provenance_absent")
    source_sha = digest({"brief_sha256": [source["sha256"] for source in sources],
                         "tool_api_sha256": manifest["tool_api_sha256"]})
    _require(source_sha == value["source_sha256"], "build_source_hash_mismatch")
    _require(isinstance(exported, dict) and isinstance(exported.get("workflow"), dict)
             and manifest.get("export_sha256") == digest(exported), "build_export_hash_mismatch")
    deployment = manifest.get("deployment")
    _require(isinstance(deployment, dict) and deployment.get("published") is True
             and type(deployment.get("version_number")) is int and deployment["version_number"] > 0,
             "build_deployment_unverified")
    build_start, build_end, build_seconds = _period(value.get("build"))
    _require(build_seconds <= 1800, "build_budget_exceeded")
    trials = value.get("trials")
    _require(isinstance(trials, dict) and set(trials) == {"train", "validation"}, "development_splits_incomplete")
    checked = {split: _trial(trials[split], conditions, value["brief"]) for split in ("train", "validation")}
    _require(build_end <= checked["train"]["start"], "trial_preceded_selected_build")
    for trial in checked.values():
        _require(all(deployment.get(key) == trial["pin"][key] for key in
                     ("workflow_id", "agent_id", "version_id", "version_number"))
                 and all(exported.get(key) == trial["pin"][key] for key in ("workflow_id", "agent_id"))
                 and digest(exported["workflow"]) == trial["pin"]["graph_sha256"], "build_trial_attribution_mismatch")
    _require(checked["train"]["end"] <= checked["validation"]["start"], "validation_preceded_training")
    _require(digest(checked["train"]["pin"]) == digest(checked["validation"]["pin"]), "candidate_changed_between_splits")
    for field in ("fixtures", "calls", "sessions", "simulations", "iterations"):
        _require(not checked["train"][field].intersection(checked["validation"][field]), "split_evidence_reused")
    build_seconds = _build_provenance(value, manifest, conditions, build_start, build_end)
    build_config = value["build_provenance"]["source_snapshot"]["resolved_config"]
    return {"brief": value["brief"], "source": value["source_sha256"], "conditions": conditions,
            "builder_source": value["builder_source_sha256"], "build_seconds": build_seconds,
            "build_budget": {key: build_config[key] for key in ("deadline_seconds", "export_reserve_seconds")},
            "trials": checked}


def compare(baseline, structured):
    """Reject incomplete parity/evidence; output counts and hashes, never raw rows."""
    try:
        for bundle in (baseline, structured):
            assert_no_secrets(bundle)
            digest(bundle)  # Refuse non-JSON/recursive containers even in unused fields.
        left, right = _bundle(baseline), _bundle(structured)
        _require(left["brief"] == right["brief"] and left["source"] == right["source"], "source_parity_absent")
        _require(digest(left["conditions"]) == digest(right["conditions"]), "condition_parity_absent")
        _require(digest(left["build_budget"]) == digest(right["build_budget"]), "build_budget_parity_absent")
        for field in ("calls", "sessions", "simulations", "iterations"):
            a_ids = set().union(*(trial[field] for trial in left["trials"].values()))
            b_ids = set().union(*(trial[field] for trial in right["trials"].values()))
            _require(not a_ids.intersection(b_ids), "candidate_evidence_reused")
        _require(max(left["trials"]["train"]["end"], right["trials"]["train"]["end"]) <=
                 min(left["trials"]["validation"]["start"], right["trials"]["validation"]["start"]),
                 "validation_preceded_paired_training")
        summaries = []
        for split in ("train", "validation"):
            a, b = left["trials"][split], right["trials"][split]
            _require(a["case_sha"] == b["case_sha"] and a["fixtures"] == b["fixtures"], "case_parity_absent")
            am, bm = a["metrics"], b["metrics"]
            delta = bm["strict_passed"] - am["strict_passed"]
            failure_delta = bm["behavior_failure_runs"] - am["behavior_failure_runs"]
            critical_delta = {category: bm["critical_failures"][category] - am["critical_failures"][category]
                              for category in CRITICAL_CATEGORIES}
            fully_passing = am["strict_passed"] == bm["strict_passed"] == am["total"]
            if any(count_change > 0 for count_change in critical_delta.values()):
                decision = "critical_failure_regression"
            elif failure_delta > 0:
                decision = "behavior_regression"
            elif delta != 0:
                decision = "structured_progress" if delta > 0 else "baseline_preferred"
            elif fully_passing:
                rank_a = am["strict_promised_cents"], -left["build_seconds"]
                rank_b = bm["strict_promised_cents"], -right["build_seconds"]
                decision = "structured_progress" if rank_b > rank_a else "baseline_preferred" if rank_a > rank_b else "equivalent"
            else:
                decision = "equivalent"
            summaries.append({"split": split, "decision": decision, "strict_pass_delta": delta,
                              "critical_failure_delta": critical_delta,
                              "baseline": am, "structured": bm, "secondary_ranking_used": fully_passing,
                              "report_sha256": {"baseline": a["report_sha256"], "structured": b["report_sha256"]}})
        return {"comparable": True, "brief": left["brief"], "source_sha256": left["source"],
                "builder_source_sha256": {"baseline": left["builder_source"], "structured": right["builder_source"]},
                "conditions_sha256": digest(left["conditions"]), "splits": summaries,
                "build_seconds": {"baseline": left["build_seconds"], "structured": right["build_seconds"]},
                "critical_classification": "recorded explicit categories only; generic behavior/conversation failures remain unclassified",
                "eligibility": "not_assessed",
                "claim": "Recorded known-policy hosted evidence only; no new trial or unseen-policy claim."}
    except (ComparisonError, ValueError, TypeError, KeyError, OverflowError, RecursionError, DecimalException) as exc:
        return {"comparable": False, "reason": str(exc) if isinstance(exc, ComparisonError) else "input_secrets_or_invalid_json",
                "claim": "Incomplete evidence or parity cannot establish improvement."}


def load_bundle(path, workspace_root=PROJECT):
    path = output_path(path, workspace_root=workspace_root)
    _require(path.suffix == ".json" and not any(part.startswith(".") for part in path.relative_to(Path(workspace_root).resolve()).parts),
             "bundle_path_invalid")
    _require(path.stat().st_size <= 32 * 1024 * 1024, "bundle_size_limit_exceeded")
    return read_json(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--structured", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        result = compare(load_bundle(args.baseline), load_bundle(args.structured))
    except (ValueError, OSError, TypeError, RecursionError, OverflowError, DecimalException):
        result = {"comparable": False, "reason": "bundle_file_unavailable_or_invalid"}
    result = sanitize(result)
    if args.out:
        path = output_path(args.out)
        _require(path.suffix == ".json", "comparison_output_invalid")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve original bundles and earlier comparison attempts.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps(result, indent=2))
    return 0 if result["comparable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

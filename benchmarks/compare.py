"""Compare recorded development trials locally; never execute a builder or API."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re

from builder.platform import AGENT_LLM, SIM_LLM, STAGING
from builder.safety import assert_no_secrets, digest, read_json, sanitize
from .paths import PROJECT, output_path
from .scoring import cents


class ComparisonError(ValueError):
    """Fixed, data-free evidence rejection code."""


def _require(condition, code):
    if not condition:
        raise ComparisonError(code)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


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


def _trial(value, conditions):
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
    fixture_ids = [row.get("fixture_id") for row in final if isinstance(row, dict)]
    _require(len(fixture_ids) == len(final) and all(_identifier(identifier) for identifier in fixture_ids)
             and len(set(fixture_ids)) == len(fixture_ids), "fixture_inventory_invalid")
    expected_pairs = {(identifier, repetition) for identifier in fixture_ids for repetition in range(1, reps + 1)}
    pairs, by_fixture, calls, sessions = set(), {identifier: [] for identifier in fixture_ids}, set(), set()
    failures, failure_runs, amount = [], 0, 0
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
        _require(not row.get("missing_evidence") and row["pass"] == (not row["failures"]), "row_verdict_inconsistent")
        call_id, session = row.get("call_id"), row.get("session_fingerprint")
        _require(_identifier(call_id) and call_id not in calls and isinstance(session, str)
                 and re.fullmatch(r"[0-9a-f]{16,64}", session) is not None and session not in sessions,
                 "call_or_session_evidence_reused")
        calls.add(call_id)
        sessions.add(session)
        ledger, judge, transcript = row.get("ledger"), row.get("judge"), row.get("transcript")
        _require(isinstance(ledger, dict) and all(isinstance(ledger.get(key), list) for key in
                 ("promises", "notes", "callbacks", "transfers", "calls")) and bool(ledger["calls"]), "ledger_evidence_absent")
        _require(_identifier(ledger.get("outcome")) and (ledger.get("reason") is None or _identifier(ledger["reason"]))
                 and all(isinstance(call, dict) and _identifier(call.get("tool")) and isinstance(call.get("result"), dict)
                         for call in ledger["calls"]), "ledger_evidence_malformed")
        for collection, fields in (("promises", ("product_id", "date")), ("notes", ("reason",)),
                                   ("callbacks", ("date", "time")), ("transfers", ("queue",))):
            _require(all(isinstance(record, dict) and all(_identifier(record.get(field)) for field in fields)
                         for record in ledger[collection]), "ledger_evidence_malformed")
        _require(isinstance(judge, dict) and judge.get("callId") == call_id and judge.get("outcome") in ("pass", "fail"),
                 "judge_evidence_absent")
        _require(isinstance(transcript, dict) and isinstance(transcript.get("messages"), list)
                 and bool(transcript["messages"]) and all(isinstance(message, dict)
                     and message.get("role") in ("user", "assistant", "system")
                     and _identifier(message.get("message", message.get("content"))) for message in transcript["messages"]),
                 "transcript_evidence_absent")
        _require(transcript.get("id", call_id) == call_id, "transcript_call_mismatch")
        observed = {source: data["agentVersionNumber"] for source, data in (("judge", judge), ("transcript", transcript))
                    if data.get("agentVersionNumber") is not None}
        attribution = row.get("version_attribution")
        _require(bool(observed) and all(type(number) is int and number == pin["version_number"] for number in observed.values())
                 and isinstance(attribution, dict) and attribution.get("verified") is True and attribution.get("status") == "verified"
                 and type(attribution.get("expected")) is int and attribution["expected"] == pin["version_number"]
                 and digest(attribution.get("observed")) == digest(observed), "per_call_version_unattributed")
        _require(not row["pass"] or judge["outcome"] == "pass", "judge_verdict_inconsistent")
        by_fixture[pair[0]].append(row)
        failures.extend(row["failures"])
        failure_runs += bool(row["failures"])
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
            amounts = []
            for row in matches:
                try:
                    values = [cents(record["amount"]) for record in row["ledger"]["promises"]]
                    _require(all(value >= 0 for value in values), "promise_amount_evidence_invalid")
                    amounts.append(sum(values))
                except (KeyError, TypeError, ValueError):
                    raise ComparisonError("promise_amount_evidence_invalid") from None
            _require(all(value >= 0 for value in amounts), "promise_amount_evidence_invalid")
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
                        "behavior_failure_runs": failure_runs, "strict_promised_cents": amount},
            "report_sha256": digest(report)}


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
             and type(conditions.get("budget_seconds")) is int and 0 < conditions["budget_seconds"] <= 1800,
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
    _, build_end, build_seconds = _period(value.get("build"))
    _require(build_seconds <= 1800, "build_budget_exceeded")
    trials = value.get("trials")
    _require(isinstance(trials, dict) and set(trials) == {"train", "validation"}, "development_splits_incomplete")
    checked = {split: _trial(trials[split], conditions) for split in ("train", "validation")}
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
    return {"brief": value["brief"], "source": value["source_sha256"], "conditions": conditions,
            "builder_source": value["builder_source_sha256"], "build_seconds": build_seconds, "trials": checked}


def compare(baseline, structured):
    """Reject incomplete parity/evidence; output counts and hashes, never raw rows."""
    try:
        for bundle in (baseline, structured):
            assert_no_secrets(bundle)
        left, right = _bundle(baseline), _bundle(structured)
        _require(left["brief"] == right["brief"] and left["source"] == right["source"], "source_parity_absent")
        _require(digest(left["conditions"]) == digest(right["conditions"]), "condition_parity_absent")
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
            decision = "structured_progress" if delta > 0 and failure_delta <= 0 else "baseline_preferred" if delta < 0 else "equivalent"
            if failure_delta > 0:
                decision = "behavior_regression"
            fully_passing = am["strict_passed"] == bm["strict_passed"] == am["total"]
            if fully_passing:
                rank_a = am["strict_promised_cents"], -left["build_seconds"]
                rank_b = bm["strict_promised_cents"], -right["build_seconds"]
                decision = "structured_progress" if rank_b > rank_a else "baseline_preferred" if rank_a > rank_b else "equivalent"
            summaries.append({"split": split, "decision": decision, "strict_pass_delta": delta,
                              "baseline": am, "structured": bm, "secondary_ranking_used": fully_passing,
                              "report_sha256": {"baseline": a["report_sha256"], "structured": b["report_sha256"]}})
        return {"comparable": True, "brief": left["brief"], "source_sha256": left["source"],
                "builder_source_sha256": {"baseline": left["builder_source"], "structured": right["builder_source"]},
                "conditions_sha256": digest(left["conditions"]), "splits": summaries,
                "build_seconds": {"baseline": left["build_seconds"], "structured": right["build_seconds"]},
                "claim": "Recorded known-policy hosted evidence only; no new trial or unseen-policy claim."}
    except (ComparisonError, ValueError) as exc:
        return {"comparable": False, "reason": str(exc) if isinstance(exc, ComparisonError) else "input_secrets_or_invalid_json",
                "claim": "Incomplete evidence or parity cannot establish improvement."}


def load_bundle(path, workspace_root=PROJECT):
    path = output_path(path, workspace_root=workspace_root)
    _require(path.suffix == ".json" and not any(part.startswith(".") for part in path.relative_to(Path(workspace_root).resolve()).parts),
             "bundle_path_invalid")
    return read_json(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--structured", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        result = compare(load_bundle(args.baseline), load_bundle(args.structured))
    except (ValueError, OSError):
        result = {"comparable": False, "reason": "bundle_file_unavailable_or_invalid"}
    result = sanitize(result)
    if args.out:
        path = output_path(args.out)
        _require(path.suffix == ".json", "comparison_output_invalid")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["comparable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

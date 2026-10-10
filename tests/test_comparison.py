"""Synthetic report validation only; these fixtures are not trial evidence."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.compare import compare, load_bundle, main
from builder.platform import AGENT_LLM, SIM_LLM, STAGING
from builder.safety import digest


def bundle(candidate):
    model, effort = "synthetic-builder", "high"
    sources = [{"id": "brief-1", "sha256": digest("synthetic approved brief")}]
    api_hash = digest("synthetic API")
    workflow = {"initial_node": "start", "nodes": {"start": {"node_type": "conversation", "task_message": candidate}}}
    exported = {"workflow_id": candidate + "-workflow", "agent_id": candidate + "-agent", "workflow": workflow}
    pin = {"workflow_id": exported["workflow_id"], "agent_id": exported["agent_id"], "version_id": candidate + "-version",
           "version_number": 4, "published": True, "graph_sha256": digest(workflow), "model": model, "reasoning_effort": effort}
    trials = {}
    for split, hour in (("train", "09"), ("validation", "10")):
        rows = []
        for fixture in range(2):
            identifier = split + "-fixture-" + str(fixture)
            for repetition in range(1, 4):
                call = candidate + "-" + identifier + "-" + str(repetition)
                promise = {"product_id": "p1", "amount": "57.25", "date": "2032-04-09"}
                ledger = {"outcome": "promise", "reason": None, "promises": [promise], "notes": [], "callbacks": [], "transfers": [],
                          "calls": [{"tool": "verify_identity", "args": {"field": "date_of_birth"}, "result": {"verified": True}},
                                    {"tool": "verify_identity", "args": {"field": "phone_last4"}, "result": {"verified": True}},
                                    {"tool": "get_account", "args": {}, "result": {"ok": True}},
                                    {"tool": "create_promise", "args": deepcopy(promise), "result": {"ok": True}},
                                    {"tool": "close_call", "args": {"outcome": "promise"}, "result": {"ok": True}}]}
                rows.append({"fixture_id": identifier, "repetition": repetition, "pass": True, "failures": [],
                             "simulation_id": candidate + "-" + split + "-sim-" + str(repetition),
                             "iteration_id": candidate + "-" + split + "-iteration-" + str(repetition),
                             "ledger": ledger, "call_id": call, "session_fingerprint": digest(call + "-session")[:16],
                             "version_attribution": {"expected": 4, "observed": {"judge": 4, "transcript": 4}, "verified": True, "status": "verified"},
                             "judge": {"callId": call, "outcome": "pass", "agentVersionNumber": 4},
                             "transcript": {"id": call, "agentVersionNumber": 4,
                                            "messages": [{"role": "assistant", "message": "RAW_TRANSCRIPT_SENTINEL"}]}})
        report = {"status": "completed", "reps": 3, "passed": 2, "total": 2, "rows": rows, "failures": [],
                  "final": [{"fixture_id": split + "-fixture-" + str(fixture), "passes": 3, "runs": 3,
                             "required_runs": 3, "pass": True} for fixture in range(2)],
                  "deployment_pin": {"verified": True, "per_call_version_verified": True, "before": deepcopy(pin), "after": deepcopy(pin)},
                  "simulations": [{"repetition": repetition, "status": "completed", "simulation_id": candidate + "-" + split + "-sim-" + str(repetition),
                                   "iteration_id": candidate + "-" + split + "-iteration-" + str(repetition)} for repetition in range(1, 4)]}
        trials[split] = {"report": report, "case_manifest_sha256": digest(split + "-case-source"),
                         "started_at": "2032-04-07T" + hour + ":00:00+03:00", "ended_at": "2032-04-07T" + hour + ":05:00+03:00"}
    return {"version": 1, "brief": "A", "source_sha256": digest({"brief_sha256": [sources[0]["sha256"]], "tool_api_sha256": api_hash}),
            "builder_source_sha256": digest(candidate + "-synthetic-builder-source"),
            "conditions": {"builder_model": model, "reasoning_effort": effort, "reps": 3, "concurrency": 5, "budget_seconds": 1800,
                           "agent_llm": AGENT_LLM, "persona_llm": SIM_LLM, "analysis_llm": SIM_LLM,
                           "platform_base_url": STAGING, "bank_base_url": "https://freya-bank-mock.freyavoice.workers.dev", "calendar_sha256": digest("same supplied call calendar")},
            "build": {"started_at": "2032-04-07T08:00:00+03:00", "ended_at": "2032-04-07T08:10:00+03:00"},
            "build_manifest": {"mode": "live", "status": "exported", "model": model, "reasoning_effort": effort,
                               "sources": sources, "tool_api_sha256": api_hash, "export_sha256": digest(exported), "deployment": pin},
            "workflow_export": exported, "trials": trials}


def behavior_failure(value, split="train", row_index=0):
    report = value["trials"][split]["report"]
    row = report["rows"][row_index]
    row.update({"pass": False, "failures": [{"category": "behavior", "message": "synthetic policy violation"}]})
    report["failures"] = [failure for item in report["rows"] for failure in item["failures"]]
    for summary in report["final"]:
        matches = [item for item in report["rows"] if item["fixture_id"] == summary["fixture_id"]]
        summary["passes"] = sum(item["pass"] for item in matches)
        summary["pass"] = summary["passes"] == 3
    report["passed"] = sum(summary["pass"] for summary in report["final"])


class ComparisonTests(unittest.TestCase):
    def test_complete_parity_reports_compare_in_training_then_validation_order(self):
        result = compare(bundle("baseline"), bundle("structured"))
        self.assertTrue(result["comparable"])
        self.assertEqual([row["split"] for row in result["splits"]], ["train", "validation"])
        self.assertEqual(result["splits"][0]["baseline"]["strict_promised_cents"], 11450)
        self.assertNotEqual(result["builder_source_sha256"]["baseline"], result["builder_source_sha256"]["structured"])
        serialized = json.dumps(result)
        self.assertNotIn("RAW_TRANSCRIPT_SENTINEL", serialized)
        self.assertNotIn("date_of_birth", serialized)
        self.assertNotIn("baseline-workflow", serialized)

    def test_missing_recorded_conditions_or_changed_sources_cannot_establish_parity(self):
        for field in ("conditions", "source_sha256", "builder_source_sha256", "build_manifest", "workflow_export"):
            other = bundle("structured")
            del other[field]
            with self.subTest(field=field):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])
        other = bundle("structured")
        other["conditions"]["concurrency"] = 4
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "condition_parity_absent")

    def test_missing_or_changed_pin_graph_or_version_is_not_comparable(self):
        for mutation in ("missing", "unverified", "graph", "version"):
            other = bundle("structured")
            pin = other["trials"]["train"]["report"]["deployment_pin"]
            if mutation == "missing":
                pin.pop("before")
            elif mutation == "unverified":
                pin["verified"] = False
            elif mutation == "graph":
                pin["before"]["graph_sha256"] = digest("different graph")
                pin["after"] = deepcopy(pin["before"])
            else:
                pin["after"]["version_number"] = 5
            with self.subTest(mutation=mutation):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])

    def test_missing_or_conflicting_recorded_version_evidence_is_rejected(self):
        for field in ("judge", "transcript"):
            for version in (None, 3, True):
                other = bundle("structured")
                record = other["trials"]["train"]["report"]["rows"][0][field]
                if version is None:
                    del record["agentVersionNumber"]
                else:
                    record["agentVersionNumber"] = version
                with self.subTest(field=field, version=version):
                    self.assertEqual(compare(bundle("baseline"), other)["reason"], "per_call_version_unattributed")

    def test_evaluator_verified_single_source_version_attribution_is_sufficient(self):
        other = bundle("structured")
        for trial in other["trials"].values():
            for row in trial["report"]["rows"]:
                del row["transcript"]["agentVersionNumber"]
                row["version_attribution"]["observed"] = {"judge": 4}
        self.assertTrue(compare(bundle("baseline"), other)["comparable"])
        row = other["trials"]["train"]["report"]["rows"][0]
        del row["judge"]["agentVersionNumber"]
        row["version_attribution"]["observed"] = {}
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "per_call_version_unattributed")

    def test_exact_fixture_and_repetition_inventory_is_checked(self):
        for mutation in ("duplicate", "missing", "unknown-rep", "mismatched-fixtures", "different-manifest"):
            other = bundle("structured")
            trial = other["trials"]["train"]
            rows = trial["report"]["rows"]
            if mutation == "duplicate": rows.append(deepcopy(rows[0]))
            elif mutation == "missing": rows.pop()
            elif mutation == "unknown-rep": rows[0]["repetition"] = 4
            elif mutation == "mismatched-fixtures": trial["report"]["final"][0]["fixture_id"] = "different"
            else: trial["case_manifest_sha256"] = digest("different manifest")
            with self.subTest(mutation=mutation):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])

    def test_forged_success_aggregates_do_not_override_failed_repetition(self):
        other = bundle("structured")
        behavior_failure(other)
        report = other["trials"]["train"]["report"]
        report["passed"] = 2
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "strict_summary_inconsistent")

    def test_missing_or_infrastructure_evidence_is_never_ranked_as_improvement(self):
        for mutation in ("ledger", "judge", "transcript", "infrastructure", "unknown-category"):
            other = bundle("structured")
            row = other["trials"]["train"]["report"]["rows"][0]
            if mutation in ("ledger", "judge", "transcript"):
                del row[mutation]
            else:
                behavior_failure(other)
                row["failures"][0]["category"] = "infrastructure" if mutation == "infrastructure" else []
            with self.subTest(mutation=mutation):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])

    def test_less_than_three_repetitions_and_over_budget_build_are_rejected(self):
        other = bundle("structured")
        other["conditions"]["reps"] = 1
        self.assertFalse(compare(bundle("baseline"), other)["comparable"])
        other = bundle("structured")
        other["build"]["ended_at"] = "2032-04-07T08:30:01+03:00"
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "build_budget_exceeded")

    def test_validation_must_follow_both_training_runs(self):
        other = bundle("structured")
        other["trials"]["validation"]["started_at"] = "2032-04-07T08:20:00+03:00"
        self.assertFalse(compare(bundle("baseline"), other)["comparable"])

    def test_reused_calls_sessions_simulations_or_iterations_are_rejected(self):
        for field in ("call_id", "session_fingerprint"):
            other = bundle("structured")
            rows = other["trials"]["train"]["report"]["rows"]
            rows[1][field] = rows[0][field]
            with self.subTest(field=field):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])
        for field in ("simulation_id", "iteration_id"):
            other = bundle("structured")
            sims = other["trials"]["train"]["report"]["simulations"]
            sims[1][field] = sims[0][field]
            with self.subTest(field=field):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])

    def test_strict_pass_and_behavior_failures_take_precedence_over_amount_and_speed(self):
        baseline, other = bundle("baseline"), bundle("structured")
        behavior_failure(other)
        other["build"]["ended_at"] = "2032-04-07T08:00:01+03:00"
        for row in other["trials"]["train"]["report"]["rows"]:
            row["ledger"]["promises"][0]["amount"] = "999999.00"
            row["ledger"]["calls"][3]["args"]["amount"] = "999999.00"
        result = compare(baseline, other)
        self.assertTrue(result["comparable"])
        train = result["splits"][0]
        self.assertEqual(train["structured"]["strict_passed"], 1)
        self.assertEqual(train["decision"], "behavior_regression")
        self.assertFalse(train["secondary_ranking_used"])

    def test_report_and_build_graph_must_share_attribution(self):
        other = bundle("structured")
        other["workflow_export"]["workflow"]["nodes"]["start"]["task_message"] = "mutated"
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "build_export_hash_mismatch")

    def test_reader_is_local_and_refuses_symlinks_and_non_json_files(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            path = root / "bundle.json"
            path.write_text(json.dumps(bundle("baseline")))
            self.assertEqual(load_bundle(path, workspace_root=root)["brief"], "A")
            alias = root / "alias.json"
            alias.symlink_to(path)
            with self.assertRaises(ValueError): load_bundle(alias, workspace_root=root)
            private = root / ".env"
            private.write_text("never read")
            with self.assertRaises(ValueError): load_bundle(private, workspace_root=root)

    def test_cli_only_reads_recorded_bundles_and_outputs_metadata(self):
        with patch("benchmarks.compare.load_bundle", side_effect=[bundle("baseline"), bundle("structured")]), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network must not be used")), \
             patch("subprocess.Popen", side_effect=AssertionError("generator must not be used")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--baseline", "one.json", "--structured", "two.json"]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["comparable"])
        self.assertNotIn("RAW_TRANSCRIPT_SENTINEL", output.getvalue())

    def test_trials_cannot_precede_build_or_claim_naive_timestamps(self):
        for mutation in ("pre-build", "naive", "over-budget"):
            other = bundle("structured")
            trial = other["trials"]["train"]
            if mutation == "pre-build":
                other["build"]["ended_at"] = "2032-04-07T09:01:00+03:00"
                other["build"]["started_at"] = "2032-04-07T08:59:00+03:00"
            elif mutation == "naive":
                trial["started_at"] = "2032-04-07T09:00:00"
            else:
                trial["ended_at"] = "2032-04-07T09:30:01+03:00"
            with self.subTest(mutation=mutation):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])

    def test_condition_and_pin_equality_preserve_json_types(self):
        baseline, other = bundle("baseline"), bundle("structured")
        baseline["conditions"]["sampling_setting"] = True
        other["conditions"]["sampling_setting"] = 1
        self.assertEqual(compare(baseline, other)["reason"], "condition_parity_absent")
        other = bundle("structured")
        other["trials"]["train"]["report"]["deployment_pin"]["after"]["version_number"] = 4.0
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "deployment_pin_changed")
        other = bundle("structured")
        other["trials"]["train"]["report"]["rows"][0]["version_attribution"]["observed"]["judge"] = 4.0
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "per_call_version_unattributed")

    def test_supplied_transcript_call_id_must_match_even_when_id_is_correct(self):
        other = bundle("structured")
        transcript = other["trials"]["train"]["report"]["rows"][0]["transcript"]
        transcript["callId"] = "different-call"
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "transcript_call_mismatch")
        transcript["callId"] = transcript["id"]
        self.assertTrue(compare(bundle("baseline"), other)["comparable"])

    def test_critical_regressions_cannot_be_offset_by_fewer_ordinary_failures(self):
        for category in ("security", "policy", "honesty"):
            for already_present in (False, True):
                baseline, other = bundle("baseline"), bundle("structured")
                for row_index in range(6):
                    behavior_failure(baseline, row_index=row_index)
                if already_present:
                    baseline["trials"]["train"]["report"]["rows"][0]["failures"][0]["category"] = category
                for row_index in range(2 if already_present else 1):
                    behavior_failure(other, row_index=row_index)
                    other["trials"]["train"]["report"]["rows"][row_index]["failures"][0]["category"] = category
                with self.subTest(category=category, already_present=already_present):
                    result = compare(baseline, other)
                    self.assertTrue(result["comparable"])
                    train = result["splits"][0]
                    self.assertGreater(train["strict_pass_delta"], 0)
                    self.assertLess(train["structured"]["behavior_failure_runs"], train["baseline"]["behavior_failure_runs"])
                    self.assertEqual(train["critical_failure_delta"][category], 1)
                    self.assertEqual(train["decision"], "critical_failure_regression")
                    self.assertEqual(result["eligibility"], "not_assessed")
                    self.assertEqual(train["baseline"]["unclassified_behavior_runs"], 5 if already_present else 6)

    def test_malformed_native_containers_return_fixed_incomparable_result(self):
        for mutation in ("unused-set", "cycle", "attribution-list", "huge-reps", "unhashable-fixture"):
            other = bundle("structured")
            if mutation == "unused-set":
                other["unused"] = {"not", "json"}
            elif mutation == "cycle":
                other["cycle"] = other
            elif mutation == "attribution-list":
                other["trials"]["train"]["report"]["rows"][0]["judge"]["agentVersionNumber"] = []
            elif mutation == "huge-reps":
                other["conditions"]["reps"] = 10**12
                other["trials"]["train"]["report"]["reps"] = 10**12
            else:
                other["trials"]["train"]["report"]["final"][0]["fixture_id"] = []
            with self.subTest(mutation=mutation):
                result = compare(bundle("baseline"), other)
                self.assertFalse(result["comparable"])
                self.assertNotIn("RAW_TRANSCRIPT_SENTINEL", json.dumps(result))

    def test_extreme_money_exponents_are_rejected_before_integer_expansion(self):
        for value in ("1E+999999999", "1E-999999999", "NaN", True):
            other = bundle("structured")
            other["trials"]["train"]["report"]["rows"][0]["ledger"]["promises"][0]["amount"] = value
            with self.subTest(value=value):
                result = compare(bundle("baseline"), other)
                self.assertFalse(result["comparable"])
                self.assertEqual(result["reason"], "promise_amount_evidence_invalid")
        other = bundle("structured")
        behavior_failure(other)
        other["trials"]["train"]["report"]["rows"][0]["ledger"]["promises"][0]["amount"] = []
        self.assertEqual(compare(bundle("baseline"), other)["reason"], "promise_amount_evidence_invalid")


class ComparisonAuditTests(unittest.TestCase):
    def test_false_passes_with_contradictory_tool_behavior_are_not_ranked(self):
        for mutation in ("duplicate-attempt", "close-failed", "premature-account", "counter", "missing-account"):
            other = bundle("structured")
            calls = other["trials"]["train"]["report"]["rows"][0]["ledger"]["calls"]
            if mutation == "duplicate-attempt":
                duplicate = deepcopy(calls[3])
                duplicate["result"]["ok"] = False
                calls.insert(-1, duplicate)
            elif mutation == "close-failed":
                calls[-1]["result"]["ok"] = False
            elif mutation == "premature-account":
                calls.insert(0, calls.pop(2))
            elif mutation == "counter":
                calls[0]["result"]["wrong_answers_left"] = 0
            else:
                calls.pop(2)
            with self.subTest(mutation=mutation):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])

    def test_incomplete_or_inconsistent_tool_evidence_is_never_a_behavior_result(self):
        for mutation in ("missing-args", "missing-success", "write-terms", "malformed-args", "missing-evidence-type"):
            for failed in (False, True):
                other = bundle("structured")
                if failed:
                    behavior_failure(other)
                row = other["trials"]["train"]["report"]["rows"][0]
                calls = row["ledger"]["calls"]
                if mutation == "missing-args":
                    calls[-1].pop("args")
                elif mutation == "missing-success":
                    calls[3]["result"] = {}
                elif mutation == "write-terms":
                    calls[3]["args"]["amount"] = "999.00"
                elif mutation == "malformed-args":
                    calls[2]["args"] = []
                else:
                    row["missing_evidence"] = False
                with self.subTest(mutation=mutation, failed=failed):
                    self.assertFalse(compare(bundle("baseline"), other)["comparable"])

    def test_behavioral_ledger_failure_remains_comparable_when_recorded_as_failure(self):
        other = bundle("structured")
        behavior_failure(other)
        other["trials"]["train"]["report"]["rows"][0]["ledger"]["calls"][-1]["result"]["ok"] = False
        self.assertTrue(compare(bundle("baseline"), other)["comparable"])

    def test_failed_judge_must_have_a_recorded_conversation_failure(self):
        other = bundle("structured")
        behavior_failure(other)
        row = other["trials"]["train"]["report"]["rows"][0]
        row["judge"]["outcome"] = "fail"
        self.assertFalse(compare(bundle("baseline"), other)["comparable"])
        row["failures"][0]["category"] = "conversation"
        self.assertTrue(compare(bundle("baseline"), other)["comparable"])

    def test_transcript_must_have_call_attribution_and_supplied_deployment_ids_must_match(self):
        for mutation in ("missing-id", "agent-id", "workflow-id"):
            other = bundle("structured")
            row = other["trials"]["train"]["report"]["rows"][0]
            if mutation == "missing-id":
                row["transcript"].pop("id")
            elif mutation == "agent-id":
                row["judge"]["agentId"] = "different-agent"
            else:
                row["transcript"]["workflowId"] = "different-workflow"
            with self.subTest(mutation=mutation):
                self.assertFalse(compare(bundle("baseline"), other)["comparable"])
        other = bundle("structured")
        report = other["trials"]["train"]["report"]
        row = report["rows"][0]
        row["transcript"]["callId"] = row["transcript"].pop("id")
        row["judge"]["agentId"] = report["deployment_pin"]["before"]["agent_id"]
        row["transcript"]["workflowId"] = report["deployment_pin"]["before"]["workflow_id"]
        self.assertTrue(compare(bundle("baseline"), other)["comparable"])


if __name__ == "__main__":
    unittest.main()

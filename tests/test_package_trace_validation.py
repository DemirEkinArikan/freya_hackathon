"""Synthetic trace consistency checks; these fixtures are not live rehearsal."""
from copy import deepcopy
import datetime as dt
import unittest

from builder.safety import digest
from tools.package_submission import validate_trace


def evidence(with_tests=False):
    workflow = {"workflow_id": "mock-workflow", "agent_id": "mock-agent", "workflow": {"nodes": {}}}
    events = [{"event": "start", "mode": "live"},
              {"event": "model_call", "model": "mock-model", "purpose": "Synthetic compilation fixture", "prompt_characters": 100},
              {"event": "deployment", "workflow_id": "mock-workflow", "agent_id": "mock-agent",
               "version_id": "mock-version", "version_number": 1, "published": True}]
    if with_tests:
        events.append({"event": "tests", "result": {
            "status": "completed", "total": 1, "passed": 1, "reps": 1,
            "failures": [], "final": [{"fixture_id": "mock-fixture", "passes": 1, "runs": 1, "required_runs": 1, "pass": True}],
            "deployment_pin": {"verified": True, "per_call_version_verified": True,
                "before": {"workflow_id": "mock-workflow", "agent_id": "mock-agent", "version_id": "mock-version",
                           "version_number": 1, "published": True, "graph_sha256": digest(workflow["workflow"])},
                "after": {"workflow_id": "mock-workflow", "agent_id": "mock-agent", "version_id": "mock-version",
                          "version_number": 1, "published": True, "graph_sha256": digest(workflow["workflow"])}},
            "simulations": [{"repetition": 1, "simulation_id": "mock-sim", "iteration_id": "mock-iteration", "status": "completed"}],
            "rows": [{"fixture_id": "mock-fixture", "repetition": 1, "pass": True, "call_id": "mock-call",
                      "simulation_id": "mock-sim", "iteration_id": "mock-iteration",
                      "failures": [], "bank_case_id": "mock-bank-seed",
                      "ledger": {"outcome": "not_reached", "reason": None, "promises": [], "notes": [], "callbacks": [], "transfers": [],
                                 "calls": [{"tool": "close_call", "args": {"outcome": "not_reached"}, "result": {"ok": True}}]},
                      "transcript": {"id": "mock-call", "messages": [{"role": "assistant", "message": "Synthetic evidence"}]},
                      "judge": {"callId": "mock-call", "outcome": "pass", "agentVersionNumber": 1}}]}})
    events.append({"event": "end", "status": "exported", "export_sha256": digest(workflow)})
    return workflow, timestamps(events)


def timestamps(events):
    start = dt.datetime(2026, 10, 10, 12, tzinfo=dt.timezone.utc)
    for index, event in enumerate(events):
        event["time"] = (start + dt.timedelta(seconds=index)).isoformat()
    return events


class PackageTraceValidationTests(unittest.TestCase):
    def test_complete_synthetic_trace_with_optional_tests_is_consistent(self):
        for with_tests in (False, True):
            workflow, events = evidence(with_tests)
            validate_trace(events, workflow)

    def test_original_three_untimed_events_cannot_establish_an_eligible_trace(self):
        workflow, events = evidence()
        events.pop(1)
        for event in events:
            event.pop("time")
        with self.assertRaises(ValueError):
            validate_trace(events, workflow)

    def test_every_event_needs_an_aware_timestamp(self):
        for value in (None, 123, "bad", "2026-10-10T12:00:00"):
            workflow, events = evidence()
            events[1]["time"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "timezone-aware"):
                validate_trace(events, workflow)

    def test_event_times_and_start_end_positions_must_be_ordered(self):
        workflow, events = evidence()
        events[2]["time"] = "2026-10-10T11:59:00Z"
        with self.assertRaisesRegex(ValueError, "ordered"):
            validate_trace(events, workflow)
        for mutation in (lambda rows: rows.insert(0, deepcopy(rows[1])), lambda rows: rows.append(deepcopy(rows[1]))):
            workflow, events = evidence()
            mutation(events)
            with self.assertRaisesRegex(ValueError, "bound all"):
                validate_trace(events, workflow)

    def test_wall_clock_duration_enforces_organizer_at_most_thirty_minutes(self):
        workflow, events = evidence()
        events[-1]["time"] = "2026-10-10T12:30:00Z"
        validate_trace(events, workflow)
        events[-1]["time"] = "2026-10-10T12:30:00.001Z"
        with self.assertRaisesRegex(ValueError, "30-minute"):
            validate_trace(events, workflow)

    def test_elapsed_time_compares_timezone_offsets_correctly(self):
        workflow, events = evidence()
        events[-1]["time"] = "2026-10-10T15:00:03+03:00"
        validate_trace(events, workflow)

    def test_model_call_records_need_model_and_one_line_purpose(self):
        for field, value in (("model", None), ("model", ""), ("purpose", ""), ("purpose", "two\nlines"), ("purpose", "two\rlines")):
            workflow, events = evidence()
            events[1][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "one-line purpose"):
                validate_trace(events, workflow)
        workflow, events = evidence()
        events.pop(1)
        with self.assertRaisesRegex(ValueError, "model-call"):
            validate_trace(events, workflow)

    def test_isolation_audit_alone_cannot_establish_substantive_generation(self):
        workflow, events = evidence()
        events[1]["purpose"] = "Audit generation tools/context with synthetic isolation canaries"
        events[1].pop("prompt_characters")
        with self.assertRaisesRegex(ValueError, "substantive generation"):
            validate_trace(events, workflow)

    def test_generation_must_precede_selected_deployment(self):
        workflow, events = evidence()
        events[1], events[2] = events[2], events[1]
        timestamps(events)
        with self.assertRaisesRegex(ValueError, "generation must precede"):
            validate_trace(events, workflow)

    def test_published_deployment_needs_exact_version_type(self):
        for field, value in (("published", False), ("version_id", ""), ("version_number", True), ("version_number", 1.0), ("version_number", 0)):
            workflow, events = evidence()
            events[2][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "published workflow"):
                validate_trace(events, workflow)

    def test_tests_need_deployment_and_actual_evidence_references(self):
        workflow, events = evidence(True)
        events[2], events[3] = events[3], events[2]
        timestamps(events)
        with self.assertRaisesRegex(ValueError, "preceding deployment"):
            validate_trace(events, workflow)
        for field, value in (("rows", []), ("simulations", []), ("reps", True)):
            workflow, events = evidence(True)
            events[3]["result"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_trace(events, workflow)

    def test_passing_test_call_judge_and_deployment_versions_must_agree(self):
        for field, value in (("callId", "other-call"), ("agentVersionNumber", 2), ("agentVersionNumber", True)):
            workflow, events = evidence(True)
            events[3]["result"]["rows"][0]["judge"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_trace(events, workflow)

    def test_test_summary_cannot_claim_a_failed_row_passed(self):
        workflow, events = evidence(True)
        events[3]["result"]["rows"][0]["pass"] = False
        with self.assertRaisesRegex(ValueError, "summary differs"):
            validate_trace(events, workflow)

    def test_duplicate_rows_and_wrong_simulation_references_fail(self):
        workflow, events = evidence(True)
        result = events[3]["result"]
        result["rows"].append(deepcopy(result["rows"][0]))
        with self.assertRaisesRegex(ValueError, "repeat a fixture"):
            validate_trace(events, workflow)
        workflow, events = evidence(True)
        events[3]["result"]["rows"][0]["simulation_id"] = "another-simulation"
        with self.assertRaisesRegex(ValueError, "simulation/iteration reference"):
            validate_trace(events, workflow)

    def test_selected_update_cannot_reuse_earlier_deployment_tests(self):
        workflow, events = evidence(True)
        events.insert(-1, {**events[2], "version_id": "new-version", "version_number": 2})
        timestamps(events)
        with self.assertRaisesRegex(ValueError, "after its final update"):
            validate_trace(events, workflow)

    def test_passing_rows_require_retained_ledger_transcript_and_exact_simulation_ids(self):
        for field in ("ledger", "transcript", "simulation_id", "iteration_id"):
            workflow, events = evidence(True)
            events[3]["result"]["rows"][0].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_trace(events, workflow)
        for messages in (None, []):
            workflow, events = evidence(True)
            events[3]["result"]["rows"][0]["transcript"]["messages"] = messages
            with self.subTest(messages=messages), self.assertRaisesRegex(ValueError, "transcript messages"):
                validate_trace(events, workflow)

    def test_supplied_transcript_ids_must_match_the_judged_call(self):
        for field in ("id", "callId"):
            workflow, events = evidence(True)
            events[3]["result"]["rows"][0]["transcript"][field] = "another-call"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "transcript differs"):
                validate_trace(events, workflow)

    def test_passing_rows_cannot_omit_or_conflict_on_version_proof(self):
        for judge_version, transcript_version in ((None, None), (True, None), (1, 2), (1.0, None)):
            workflow, events = evidence(True)
            row = events[3]["result"]["rows"][0]
            row["judge"]["agentVersionNumber"] = judge_version
            row["transcript"]["agentVersionNumber"] = transcript_version
            with self.subTest(judge=judge_version, transcript=transcript_version), self.assertRaisesRegex(ValueError, "per-call version"):
                validate_trace(events, workflow)
        workflow, events = evidence(True)
        row = events[3]["result"]["rows"][0]
        row["judge"].pop("agentVersionNumber")
        row["transcript"]["agentVersionNumber"] = 1
        validate_trace(events, workflow)

    def test_fully_passing_results_require_unchanged_verified_export_and_version_pins(self):
        for field, value in (("verified", False), ("per_call_version_verified", False), ("before", None), ("after", None)):
            workflow, events = evidence(True)
            events[3]["result"]["deployment_pin"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "before/after"):
                validate_trace(events, workflow)
        for field, value in (("workflow_id", "another-workflow"), ("agent_id", "another-agent"), ("version_id", "another-version"),
                             ("version_number", True), ("version_number", 1.0), ("graph_sha256", "wrong-graph"), ("published", False)):
            workflow, events = evidence(True)
            pin = events[3]["result"]["deployment_pin"]
            pin["before"][field] = value
            pin["after"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "pin differs"):
                validate_trace(events, workflow)
        workflow, events = evidence(True)
        events[3]["result"]["deployment_pin"]["after"]["graph_sha256"] = "changed-graph"
        with self.assertRaisesRegex(ValueError, "before/after"):
            validate_trace(events, workflow)

    def test_incomplete_failed_repair_evidence_can_precede_a_fully_proven_selected_run(self):
        workflow, events = evidence(True)
        incomplete = {"event": "tests", "result": {"status": "infrastructure_failure", "total": 1, "passed": 0, "reps": 1,
                      "simulations": [], "rows": [{"fixture_id": "mock-fixture", "repetition": 1, "pass": False,
                                                   "failures": [{"category": "infrastructure", "message": "Synthetic interrupted attempt"}]}]}}
        events.insert(3, incomplete)
        events.insert(4, deepcopy(events[1]))
        events.insert(5, deepcopy(events[2]))
        timestamps(events)
        validate_trace(events, workflow)
        incomplete["result"]["simulations"] = [{"repetition": 1, "simulation_id": "interrupted-simulation",
                                                "iteration_id": None, "status": "running", "interrupted": True}]
        validate_trace(events, workflow)

    def test_malformed_events_fail_with_value_errors(self):
        workflow, events = evidence()
        for malformed in (None, {}, [None], ["bad"]):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                validate_trace(malformed, workflow)


if __name__ == "__main__":
    unittest.main()

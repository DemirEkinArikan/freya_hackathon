"""Failure-path evidence preservation and attribution using offline backend mocks."""
from copy import deepcopy
import json
import unittest

from benchmarks.hosted import evaluate
from test_hosted_runner import RunnerBackend, fixture_rows


class HostedEvidenceTests(unittest.TestCase):
    def test_invalid_simulation_ids_never_reach_iteration_or_become_a_pass(self):
        for field in ("simulation_id", "iteration_id"):
            for identifier in (None, "", " ", True, 12, []):
                rows = fixture_rows()
                backend = RunnerBackend(rows)
                original = backend.simulate
                def invalid(*args):
                    simulation = original(*args)
                    simulation[field] = identifier
                    return simulation
                backend.simulate = invalid
                backend.iteration = lambda _: self.fail("invalid identifiers reached iteration retrieval")
                result = evaluate(backend, "agent", rows, reps=1)
                with self.subTest(field=field, identifier=identifier):
                    self.assertEqual((result["passed"], result["status"]), (0, "infrastructure_failure"))
                    self.assertEqual(result["final"][0]["runs"], 1)

    def test_transcript_outage_preserves_ledger_judge_and_repetition_ids(self):
        rows = fixture_rows()
        backend = RunnerBackend(rows)
        def unavailable(call_id):
            raise TimeoutError("mock transcript unavailable")
        backend.call = unavailable
        result = evaluate(backend, "agent", rows, reps=1)
        record = result["rows"][0]
        self.assertEqual((record["simulation_id"], record["iteration_id"], record["call_id"]),
                         ("simulation-1", "iteration-1", "call-1-0"))
        self.assertEqual(record["ledger"]["promises"], rows[0]["case"]["expected"]["promises"])
        self.assertEqual(record["judge"]["outcome"], "pass")
        self.assertIsNone(record["transcript"])
        self.assertFalse(record["pass"])
        self.assertTrue(any("transcript retrieval failed" in failure["message"] for failure in record["failures"]))

    def test_ledger_outage_still_collects_transcript_and_judge(self):
        rows = fixture_rows()
        backend = RunnerBackend(rows)
        calls = []
        def unavailable(customer):
            raise TimeoutError("mock ledger unavailable")
        original = backend.call
        def retrieve(call_id):
            calls.append(call_id)
            return original(call_id)
        backend.ledger, backend.call = unavailable, retrieve
        result = evaluate(backend, "agent", rows, reps=1)
        self.assertEqual(calls, ["call-1-0"])
        record = result["rows"][0]
        self.assertIsNone(record["ledger"])
        self.assertEqual(record["judge"]["callId"], record["transcript"]["id"])
        self.assertEqual(result["status"], "infrastructure_failure")

    def test_scoring_error_keeps_received_malformed_ledger_and_other_evidence(self):
        rows = fixture_rows()
        backend = RunnerBackend(rows)
        original = backend.ledger
        def malformed(customer):
            ledger = deepcopy(original(customer))
            ledger["calls"] = [None]
            return ledger
        backend.ledger = malformed
        result = evaluate(backend, "agent", rows, reps=1)
        record = result["rows"][0]
        self.assertEqual(record["ledger"]["calls"], [None])
        self.assertEqual(record["transcript"]["id"], record["judge"]["callId"])
        self.assertFalse(record["pass"])

    def test_conversation_failure_survives_transcript_error_and_secrets_are_redacted(self):
        rows = fixture_rows()
        backend = RunnerBackend(rows)
        backend.team_key = "mock-private-team-key"
        original = backend.iteration
        def failed(simulation):
            iteration = original(simulation)
            iteration["runs"][0].update({"outcome": "fail", "reason": "mock conversation failed for customer-0"})
            return iteration
        def unavailable(call_id):
            raise TimeoutError(backend.team_key + " customer-0 session-0")
        backend.iteration, backend.call = failed, unavailable
        result = evaluate(backend, "agent", rows, reps=1)
        record = result["rows"][0]
        self.assertEqual({failure["category"] for failure in record["failures"]}, {"conversation", "infrastructure"})
        self.assertEqual(record["judge"]["outcome"], "fail")
        text = json.dumps(result)
        for secret in (backend.team_key, "customer-0", "session-0"):
            self.assertNotIn(secret, text)

    def test_different_transcript_call_identity_is_rejected_without_discarding_it(self):
        rows = fixture_rows()
        for field in ("id", "callId"):
            backend = RunnerBackend(rows)
            original = backend.call
            backend.call = lambda call_id: {**original(call_id), field: "another-call"}
            result = evaluate(backend, "agent", rows, reps=1)
            with self.subTest(field=field):
                self.assertEqual(result["status"], "infrastructure_failure")
                self.assertEqual(result["rows"][0]["transcript"][field], "another-call")
                self.assertFalse(result["rows"][0]["pass"])

    def test_supplied_versions_require_exact_positive_integer_types(self):
        rows = fixture_rows()
        for source in ("judge", "transcript"):
            for value in (True, 1.0, "1", 0, -1, {}, []):
                backend = RunnerBackend(rows)
                if source == "judge":
                    original = backend.iteration
                    def version(simulation):
                        iteration = original(simulation)
                        iteration["runs"][0]["agentVersionNumber"] = value
                        return iteration
                    backend.iteration = version
                else:
                    original = backend.call
                    backend.call = lambda call_id: {**original(call_id), "agentVersionNumber": value}
                result = evaluate(backend, "agent", rows, reps=1, deployment={"agent_id": "agent", "version_number": 1})
                with self.subTest(source=source, value=value):
                    self.assertEqual(result["status"], "infrastructure_failure")
                    self.assertFalse(result["deployment_pin"]["per_call_version_verified"])
                    self.assertFalse(result["rows"][0]["pass"])

    def test_missing_version_attribution_fails_pinned_runs_but_is_explicit_for_unpinned_development(self):
        rows = fixture_rows()
        for deployment in (None, {"agent_id": "agent", "version_number": 3}):
            result = evaluate(RunnerBackend(rows), "agent", rows, reps=1, deployment=deployment)
            record = result["rows"][0]
            with self.subTest(deployment=deployment):
                self.assertEqual(record["version_attribution"]["status"], "unavailable")
                self.assertFalse(result["deployment_pin"]["per_call_version_verified"])
                self.assertEqual(record["pass"], deployment is None)
                self.assertEqual(result["status"], "infrastructure_failure" if deployment else "completed")

    def test_every_requested_repetition_needs_matching_attribution(self):
        rows = fixture_rows()
        for missing_repetition in (None, 2):
            backend = RunnerBackend(rows)
            original = backend.iteration
            def version(simulation):
                iteration = original(simulation)
                if backend.run_number != missing_repetition:
                    iteration["runs"][0]["agentVersionNumber"] = 3
                return iteration
            backend.iteration = version
            result = evaluate(backend, "agent", rows, deployment={"agent_id": "agent", "version_number": 3})
            with self.subTest(missing_repetition=missing_repetition):
                self.assertEqual(result["passed"], 1 if missing_repetition is None else 0)
                self.assertEqual(result["deployment_pin"]["per_call_version_verified"], missing_repetition is None)
                self.assertEqual(result["final"][0]["runs"], 3)
                self.assertEqual(result["final"][0]["passes"], 3 if missing_repetition is None else 2)

    def test_conflicting_judge_and_transcript_versions_fail_even_without_a_pin(self):
        rows = fixture_rows()
        backend = RunnerBackend(rows)
        original_iteration, original_call = backend.iteration, backend.call
        def version(simulation):
            iteration = original_iteration(simulation)
            iteration["runs"][0]["agentVersionNumber"] = 2
            return iteration
        backend.iteration = version
        backend.call = lambda call_id: {**original_call(call_id), "agentVersionNumber": 3}
        result = evaluate(backend, "agent", rows, reps=1)
        self.assertEqual(result["status"], "infrastructure_failure")
        self.assertEqual(result["rows"][0]["version_attribution"]["observed"], {"judge": 2, "transcript": 3})


if __name__ == "__main__":
    unittest.main()

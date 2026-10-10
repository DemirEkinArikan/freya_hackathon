"""Production execute uses the real repair boundary; all external services are doubles."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from benchmarks.hosted import run_builder_tests
from builder import build
from builder.repair_diagnostics import DIAGNOSTICS
import test_builder
from test_hosted_runner import RunnerBackend, fixture_rows


def read(path):
    return json.loads(Path(path).read_text())


class RepairProductionWiringTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def configured_generator(self):
        generator = self.generator()
        generator.generate.return_value = test_builder.fixture()
        return generator

    def run_directory(self):
        runs = list((self.directory / "out").iterdir())
        self.assertEqual(len(runs), 1)
        return runs[0]

    def reject_infrastructure(self, result):
        generator, client = self.configured_generator(), test_builder.FakeClient()
        runner = Mock(return_value=result)
        with self.assertRaises(build.HostedTestInfrastructureError):
            build.execute(self.args("--test-cases", str(self.directory / "fixture.json")),
                          generator=generator, client_factory=lambda **kwargs: client,
                          test_runner=runner)
        self.assertEqual((generator.generate.call_count, generator.generate_plan.call_count), (1, 1))
        self.assertEqual((client.saves, client.exports, runner.call_count), (1, 0, 1))
        run = self.run_directory()
        attempt = run / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "tests.json"), result)
        self.assertEqual(read(attempt / "plan.json"), test_builder.plan())
        self.assertEqual(read(attempt / "attempt.json")["failure_kind"], "test_infrastructure")
        self.assertFalse((run / "attempts" / "plan-002").exists())
        self.assertFalse(read(run / "manifest.json")["hosted_behavior_verified"])

    def test_nested_missing_evidence_preserves_candidate_without_regeneration(self):
        failures = [{"category": "behavior", "message": "final close_call failed"}]
        self.reject_infrastructure({"status": "completed", "passed": 0, "total": 1,
                                    "failures": failures,
                                    "rows": [{"pass": False, "failures": copy.deepcopy(failures),
                                              "score": {"missing_evidence": ["local injected gap"]}}]})

    def test_nonbehavioral_scorer_failure_cannot_be_reclassified_to_trigger_repair(self):
        self.reject_infrastructure({"status": "completed", "passed": 0, "total": 1,
                                    "failures": [{"category": "behavior", "message": "malformed tool arguments"}]})

    def test_contradictory_pass_status_does_not_trigger_regeneration(self):
        self.reject_infrastructure({"status": "passed", "passed": 0, "total": 1,
                                    "failures": [{"category": "behavior", "message": "final close_call failed"}]})

    def hosted_inputs(self):
        rows = fixture_rows(2)
        cases = self.directory / "hosted-evaluator.json"
        cases.write_text(json.dumps({"kind": "hosted", "cases": rows}))
        return cases, rows

    def backend(self, rows, *, failure_reasons=()):
        backend = RunnerBackend(rows)
        original_iteration = backend.iteration
        def iteration(simulation):
            result = original_iteration(simulation)
            for index, row in enumerate(result["runs"]):
                row["agentVersionNumber"] = 1
                if index < len(failure_reasons):
                    row.update(outcome="fail", reason=failure_reasons[index])
            return result
        backend.iteration = iteration
        return backend

    def test_actual_hosted_runner_complete_result_is_accepted_and_exported(self):
        cases, rows = self.hosted_inputs()
        generator, client = self.configured_generator(), test_builder.FakeClient()
        backend = self.backend(rows)
        with patch("benchmarks.hosted.HostedBackend", return_value=backend):
            result = build.execute(self.args("--test-cases", str(cases)), generator=generator,
                                   client_factory=lambda **kwargs: client, test_runner=run_builder_tests)
        self.assertEqual(result["status"], "exported")
        self.assertEqual((client.saves, client.exports, generator.generate_plan.call_count), (1, 1, 1))
        evidence = read(self.run_directory() / "attempts" / "plan-001" / "tests.json")
        self.assertEqual((evidence["status"], evidence["passed"], evidence["total"], evidence["reps"]),
                         ("completed", 2, 2, 1))
        self.assertEqual(len(evidence["rows"]), 2)
        self.assertTrue(evidence["deployment_pin"]["per_call_version_verified"])
        self.assertTrue(all(row["version_attribution"]["verified"] for row in evidence["rows"]))

    def test_actual_hosted_behavioral_repair_receives_fixed_guidance_without_judge_leakage(self):
        cases, rows = self.hosted_inputs()
        marker = "PRIVATE_JUDGE_CUSTOMER_SCRIPT_EXPECTED_938147"
        message = "promise lacks exact readback and subsequent confirmation before tool start"
        backends = [self.backend(rows, failure_reasons=(message, marker)), self.backend(rows)]
        generator, client = self.configured_generator(), test_builder.FakeClient()
        with patch("benchmarks.hosted.HostedBackend", side_effect=backends):
            result = build.execute(self.args("--test-cases", str(cases)), generator=generator,
                                   client_factory=lambda **kwargs: client, test_runner=run_builder_tests)
        self.assertEqual((result["status"], result["repairs"], result["policy_repairs"], result["workflow_repairs"]),
                         ("exported", 1, 0, 1))
        self.assertEqual((client.saves, client.exports, generator.generate.call_count,
                          generator.generate_plan.call_count), (2, 1, 1, 2))
        prompt = generator.generate_plan.call_args_list[1].args[0]
        payload = json.loads(prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1])
        summary = payload["repair"]["test_summary"]
        self.assertEqual((summary["status"], summary["passed"], summary["total"]),
                         ("behavioral_failure", 0, 2))
        self.assertEqual(summary["failures"][0]["diagnostic"], DIAGNOSTICS[message])
        self.assertNotIn(marker, prompt)
        for key in ("rows", "final", "judge", "ledger", "expected", "transcript", "simulations"):
            self.assertNotIn(key, summary)
        run = self.run_directory()
        first, second = [run / "attempts" / f"plan-{index:03d}" for index in (1, 2)]
        self.assertIn(marker, json.dumps(read(first / "tests.json")))
        self.assertEqual(read(first / "attempt.json")["status"], "behavioral_failure")
        self.assertEqual(read(second / "attempt.json")["status"], "exported")
        self.assertEqual(read(first / "guard-context.json")["contract"],
                         read(second / "guard-context.json")["contract"])
        self.assertEqual(read(first / "guard-context.json")["execution_policy"],
                         read(second / "guard-context.json")["execution_policy"])
        self.assertEqual(result["selected_attempt_directory"], "attempts/plan-002")

    def test_complete_hosted_pass_with_unverified_pin_is_preserved_and_rejected(self):
        cases, rows = self.hosted_inputs()
        backend = self.backend(rows)
        generator, client = self.configured_generator(), test_builder.FakeClient()
        def runner(*args):
            result = run_builder_tests(*args)
            result["deployment_pin"]["per_call_version_verified"] = False
            return result
        with patch("benchmarks.hosted.HostedBackend", return_value=backend), \
                self.assertRaises(build.HostedTestInfrastructureError):
            build.execute(self.args("--test-cases", str(cases)), generator=generator,
                          client_factory=lambda **kwargs: client, test_runner=runner)
        self.assertEqual((client.saves, client.exports, generator.generate_plan.call_count), (1, 0, 1))
        run = self.run_directory()
        evidence = read(run / "attempts" / "plan-001" / "tests.json")
        self.assertEqual((evidence["status"], evidence["passed"], evidence["total"]), ("completed", 2, 2))
        self.assertFalse(evidence["deployment_pin"]["per_call_version_verified"])
        self.assertFalse((run / "attempts" / "plan-002").exists())


if __name__ == "__main__":
    unittest.main()

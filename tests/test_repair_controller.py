"""Local injected controller failures; these are not live Freya acceptance.

The test-only patches model the two-line integration hook owned by the primary
builder thread. Platform publication/read-back remains a local contract double.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from builder import build
from builder.repair_diagnostics import repair_test_summary, test_infrastructure_failed
from builder.runtime import Deadline, DeadlineExceeded
from builder.safety import digest
from builder.stored_workflow import assert_equivalent_stored_workflow
import test_builder
from test_builder import FakeClient, fixture, plan


def read(path):
    return json.loads(Path(path).read_text())


def failure(message="final close_call failed", category="behavior"):
    return {"status": "completed", "passed": 0, "total": 1,
            "failures": [{"category": category, "message": message}]}


PASS = {"status": "completed", "passed": 1, "total": 1, "failures": []}


class RepairControllerTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args

    def run_directory(self):
        runs = list((self.directory / "out").iterdir())
        self.assertEqual(len(runs), 1)
        return runs[0]

    def generator(self, proposals, now, extraction=None):
        generator = Mock()
        extracted = iter(copy.deepcopy(extraction or [fixture()]))
        candidates = iter(copy.deepcopy(proposals))
        def extract(*args, **kwargs):
            now[0] += 120
            return next(extracted)
        def generate(prompt, *, contract_sha256):
            now[0] += 180
            return {"plan": next(candidates), "contract_sha256": contract_sha256}
        generator.generate.side_effect = extract
        generator.generate_plan.side_effect = generate
        return generator

    def execute(self, generator, client, runner, deadline):
        with patch.object(build, "_repair_test_summary", side_effect=repair_test_summary), \
             patch.object(build, "_test_infrastructure_failed", side_effect=test_infrastructure_failed), \
             patch.object(build, "assert_equivalent_stored_workflow",
                          wraps=assert_equivalent_stored_workflow) as readback:
            result = build.execute(self.args("--test-cases", str(self.directory / "fixture.json")),
                                   generator=generator, client_factory=lambda **kwargs: client,
                                   test_runner=runner, deadline=deadline)
        return result, readback

    def test_local_infrastructure_gap_retains_original_candidate_and_partial_evidence(self):
        now = [0]
        generator = self.generator([plan()], now)
        client = FakeClient()
        # Top-level behavioral failure must not conceal a nested missing-evidence
        # gap. It used to reach the behavioral-regeneration path.
        injected = failure()
        injected["rows"] = [{"pass": False, "failures": copy.deepcopy(injected["failures"]),
                             "score": {"missing_evidence": ["injected local ledger gap"]}}]
        paths = []
        def runner(client, deployment, cases, output_dir, deadline):
            paths.append(output_dir)
            now[0] += 200
            (output_dir / "partial-ledger.json").write_text('{"calls": [], "injected": true}')
            return injected
        with self.assertRaises(build.HostedTestInfrastructureError):
            self.execute(generator, client, runner, Deadline(1800, clock=lambda: now[0]))
        run = self.run_directory()
        attempt = run / "attempts" / "plan-001"
        self.assertEqual(paths, [attempt])
        self.assertEqual(read(attempt / "partial-ledger.json"), {"calls": [], "injected": True})
        self.assertEqual(read(attempt / "tests.json"), injected)
        self.assertEqual(read(attempt / "plan.json"), plan())
        self.assertEqual(read(run / "plan.json"), read(attempt / "plan.json"))
        self.assertTrue(read(attempt / "deployment.json")["published"])
        self.assertEqual((generator.generate.call_count, generator.generate_plan.call_count), (1, 1))
        self.assertEqual((client.saves, client.exports), (1, 0))
        self.assertEqual(read(attempt / "attempt.json")["failure_kind"], "test_infrastructure")
        self.assertEqual(read(run / "manifest.json")["failure_kind"], "test_infrastructure")
        self.assertFalse((run / "attempts" / "plan-002").exists())
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        event = next(e for e in events if e["event"] == "test_infrastructure_failure")
        self.assertTrue(event["candidate_preserved"])
        self.assertFalse(event["model_repair_attempted"])
        self.assertEqual(events[-1]["status"], "failed")

    def test_behavioral_repair_republishes_verifies_both_attempts_and_exports_under_deadline(self):
        now = [0]
        first, second = plan(), plan()
        first["nodes"]["start"]["task_message"] = "Complete the approved contact flow."
        second["nodes"]["start"]["task_message"] = "Complete closing before reporting the call finished."
        generator = self.generator([first, second], now)
        class TimedClient(FakeClient):
            def save_workflow(inner, *args):
                self.assertGreater(1800 - now[0], build.EXPORT_RESERVE)
                now[0] += 30
                deployment = super().save_workflow(*args)
                deployment.update(version_id=f"v{inner.saves}", version_number=inner.saves)
                return deployment
            def export_workflow(inner, deployment, path):
                self.assertEqual(inner.reserve, 0)
                self.assertGreaterEqual(1800 - now[0], build.EXPORT_RESERVE)
                now[0] += 200
                return super().export_workflow(deployment, path)
        client, paths = TimedClient(), []
        private = "PRIVATE_CUSTOMER_SCRIPT_EXPECTED_JUDGE_8491"
        first_result = failure()
        first_result["failures"].append({"category": "conversation", "message": private,
                                          "rule_id": private, "expected": private, "script": private})
        def runner(client, deployment, cases, output_dir, deadline):
            self.assertGreater(deadline.remaining(), build.EXPORT_RESERVE)
            paths.append(output_dir)
            now[0] += 200
            (output_dir / "original-evaluator.txt").write_text(f"attempt={len(paths)}; {private}")
            return copy.deepcopy(first_result if len(paths) == 1 else PASS)
        deadline = Deadline(1800, clock=lambda: now[0])
        result, readback = self.execute(generator, client, runner, deadline)
        run = self.run_directory()
        attempts = [run / "attempts" / f"plan-{n:03d}" for n in (1, 2)]
        self.assertEqual(paths, attempts)
        self.assertEqual(readback.call_count, 2)
        self.assertEqual((client.saves, client.exports), (2, 1))
        self.assertEqual((result["repairs"], result["policy_repairs"], result["workflow_repairs"]), (1, 0, 1))
        self.assertEqual(result["selected_attempt_directory"], "attempts/plan-002")
        self.assertEqual(result["elapsed_seconds"], 1140)
        self.assertLessEqual(result["elapsed_seconds"], 1800)
        self.assertEqual(deadline.remaining(), 660)
        contexts = [read(attempt / "guard-context.json") for attempt in attempts]
        self.assertEqual(contexts[0]["contract"], contexts[1]["contract"])
        self.assertEqual(contexts[0]["execution_policy"], contexts[1]["execution_policy"])
        self.assertEqual(generator.generate.call_count, 1)
        self.assertEqual(read(run / "policy.json"), fixture()["policy"])
        self.assertEqual([read(attempt / "attempt.json")["status"] for attempt in attempts],
                         ["behavioral_failure", "exported"])
        for index, (attempt, proposed) in enumerate(zip(attempts, (first, second)), 1):
            self.assertEqual(read(attempt / "plan.json"), proposed)
            deployment = read(attempt / "deployment.json")
            self.assertEqual((deployment["published"], deployment["version_number"]), (True, index))
            self.assertEqual(deployment["workflow"]["agent_config"]["llmConfig"]["model"], "experimental-5")
            self.assertEqual(deployment["workflow"]["nodes"]["start"]["task_message"],
                             proposed["nodes"]["start"]["task_message"])
            self.assertEqual((attempt / "original-evaluator.txt").read_text(), f"attempt={index}; {private}")
        self.assertEqual(read(attempts[0] / "tests.json"), first_result)
        self.assertEqual(read(attempts[1] / "tests.json"), PASS)
        repair_prompt = generator.generate_plan.call_args_list[1].args[0]
        self.assertNotIn(private, repair_prompt)
        repair = json.loads(repair_prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1])["repair"]
        self.assertEqual(repair["previous_plan"], first)
        self.assertEqual(repair["test_summary"], repair_test_summary(first_result, fixture()["policy"]))
        self.assertNotEqual(repair["test_summary"]["failures"][0]["diagnostic"],
                            repair_test_summary(failure("unknown"))["failures"][0]["diagnostic"])
        self.assertEqual(read(run / "submission.workflow.json"), read(attempts[1] / "export.json"))
        self.assertEqual(result["export_sha256"], digest(read(attempts[1] / "export.json")))

    def test_unverified_deployment_pin_aborts_without_repair_and_retains_candidate(self):
        now = [0]
        generator = self.generator([plan()], now)
        client = FakeClient()
        injected = failure()
        injected["deployment_pin"] = {"verified": True, "per_call_version_verified": False}
        def runner(client, deployment, cases, output_dir, deadline):
            now[0] += 200
            (output_dir / "partial-pin-evidence.json").write_text(json.dumps(injected["deployment_pin"]))
            return injected
        with self.assertRaises(build.HostedTestInfrastructureError):
            self.execute(generator, client, runner, Deadline(1800, clock=lambda: now[0]))
        run = self.run_directory()
        attempt = run / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "plan.json"), plan())
        self.assertEqual(read(attempt / "tests.json"), injected)
        self.assertEqual(read(attempt / "partial-pin-evidence.json"), injected["deployment_pin"])
        self.assertEqual(read(attempt / "attempt.json")["failure_kind"], "test_infrastructure")
        self.assertEqual((generator.generate.call_count, generator.generate_plan.call_count), (1, 1))
        self.assertEqual((client.saves, client.exports), (1, 0))
        self.assertFalse((run / "attempts" / "plan-002").exists())

    def test_extraction_and_behavioral_repairs_share_two_repair_limit(self):
        now = [0]
        invalid = fixture()
        invalid["policy"]["sources"][0]["sha256"] = "0" * 64
        generator = self.generator([plan(), plan()], now, extraction=[invalid, fixture()])
        client = FakeClient()
        runner = Mock(return_value=failure())
        with self.assertRaisesRegex(ValueError, "still fail after bounded repairs"):
            self.execute(generator, client, runner, Deadline(1800, clock=lambda: now[0]))
        self.assertEqual(build.MAX_REPAIRS, 2)
        self.assertEqual((generator.generate.call_count, generator.generate_plan.call_count), (2, 2))
        self.assertEqual(runner.call_count, 2)
        self.assertEqual((client.saves, client.exports), (2, 0))
        run = self.run_directory()
        self.assertTrue((run / "policy.rejected-1.json").exists())
        self.assertEqual(sorted(path.name for path in (run / "attempts").iterdir()), ["plan-001", "plan-002"])
        self.assertEqual(read(run / "policy.json"), fixture()["policy"])
        self.assertNotIn("selected_attempt", read(run / "manifest.json"))

    def test_repair_admission_cannot_spend_reserved_export_seconds(self):
        now = [0]
        generator = self.generator([plan(), plan()], now)
        client = FakeClient()
        def runner(*args):
            now[0] = 1500
            return failure()
        with self.assertRaises(DeadlineExceeded):
            self.execute(generator, client, runner, Deadline(1800, clock=lambda: now[0]))
        self.assertEqual((generator.generate.call_count, generator.generate_plan.call_count), (1, 1))
        self.assertEqual((client.saves, client.exports), (1, 0))
        attempt = self.run_directory() / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "tests.json"), failure())
        self.assertEqual(read(attempt / "attempt.json")["status"], "behavioral_failure")
        self.assertFalse((attempt.parent / "plan-002").exists())

    def test_export_that_crosses_global_deadline_is_retained_and_rejected(self):
        now = [0]
        generator = self.generator([plan()], now)
        class LateExport(FakeClient):
            def export_workflow(inner, deployment, path):
                self.assertEqual(inner.reserve, 0)
                result = super().export_workflow(deployment, path)
                now[0] = 1801
                return result
        client = LateExport()
        with self.assertRaises(DeadlineExceeded):
            self.execute(generator, client, Mock(return_value=copy.deepcopy(PASS)),
                         Deadline(1800, clock=lambda: now[0]))
        run = self.run_directory()
        attempt = run / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "export.json"), read(run / "submission.workflow.json"))
        self.assertEqual(read(attempt / "attempt.json")["status"], "failed")
        self.assertEqual(read(attempt / "attempt.json")["phase"], "export")
        self.assertNotIn("selected_attempt", read(run / "manifest.json"))
        self.assertEqual((generator.generate_plan.call_count, client.exports), (1, 1))


if __name__ == "__main__":
    unittest.main()

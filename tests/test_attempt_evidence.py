"""Local controller evidence regressions; these do not exercise Freya or a model."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from builder.build import execute, EXPORT_RESERVE, MAX_REPAIRS, PROJECT, HostedTestInfrastructureError
from builder.runtime import Deadline, DeadlineExceeded
from builder.safety import digest
import test_builder
from test_builder import FakeClient, fixture, plan


def read(path):
    return json.loads(Path(path).read_text())


class AttemptEvidenceTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def run_directory(self):
        runs = list((self.directory / "out").iterdir())
        self.assertEqual(len(runs), 1)
        return runs[0]

    def proposal_generator(self, plans):
        generator = self.generator()
        generator.generate.return_value = fixture()
        proposals = iter(copy.deepcopy(plans))
        def generate(prompt, *, contract_sha256):
            return {"plan": next(proposals), "contract_sha256": contract_sha256}
        generator.generate_plan.side_effect = generate
        return generator

    def test_invalid_proposal_survives_valid_repair_and_root_selects_final(self):
        broken = plan()
        broken["initial_node"] = "missing"
        generator = self.proposal_generator([broken, plan()])
        result = execute(self.args("--generate-only"), generator=generator,
                         client_factory=lambda **kw: self.fail("Freya touched"))
        run = PROJECT / result["run_directory"]
        first, second = [run / "attempts" / f"plan-{n:03d}" for n in (1, 2)]
        self.assertEqual(read(first / "proposal.json")["plan"], broken)
        self.assertEqual(read(first / "plan.json"), broken)
        self.assertTrue(read(first / "validation.json")["errors"])
        self.assertEqual(read(first / "attempt.json")["status"], "validation_rejected")
        self.assertEqual(read(second / "attempt.json")["status"], "generated_validated")
        self.assertEqual(result["selected_attempt"], 2)
        self.assertEqual(result["selected_attempt_directory"], "attempts/plan-002")
        self.assertEqual(read(run / "plan.json"), read(second / "plan.json"))
        self.assertEqual(read(run / "workflow.json"), read(second / "workflow.json"))
        context = read(second / "guard-context.json")
        self.assertEqual(context["plan"], read(run / "plan.json"))
        self.assertEqual(context["contract"], read(run / "policy.contract.json"))
        self.assertEqual(context["execution_policy"], read(run / "execution-policy.json"))
        self.assertEqual(context["bindings"], read(run / "action-bindings.json"))
        self.assertEqual(read(first / "attempt.json")["policy_contract_sha256"],
                         read(second / "attempt.json")["policy_contract_sha256"])
        self.assertEqual(generator.generate.call_count, 1)
        for attempt, call in zip((first, second), generator.generate_plan.call_args_list):
            self.assertEqual(read(attempt / "generation-context.json")["prompt"], call.args[0])

    def test_test_artifacts_and_deployments_survive_accepted_behavioral_repair(self):
        first_plan, final_plan = plan(), plan()
        first_plan["nodes"]["start"]["task_message"] = "First contact instructions."
        final_plan["nodes"]["start"]["task_message"] = "Revised contact instructions."
        generator = self.proposal_generator([first_plan, final_plan])
        client, paths = FakeClient(), []
        private = "PRIVATE_EVALUATOR_LITERAL_490182"
        def runner(client, deployment, cases, output_dir, deadline):
            paths.append(output_dir)
            n = len(paths)
            (output_dir / "evaluator-marker.txt").write_text(f"attempt {n}: {private}")
            result = {"status": "completed", "passed": int(n == 2), "total": 1,
                      "failures": [] if n == 2 else [{"category": "behavior", "message": private}]}
            (output_dir / "benchmark-results.json").write_text(json.dumps(result))
            return result
        result = execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                         client_factory=lambda **kw: client, test_runner=runner)
        run = PROJECT / result["run_directory"]
        self.assertEqual(paths, [run / "attempts" / "plan-001", run / "attempts" / "plan-002"])
        self.assertEqual((paths[0] / "evaluator-marker.txt").read_text(), f"attempt 1: {private}")
        self.assertEqual(read(paths[0] / "tests.json")["passed"], 0)
        self.assertEqual(read(paths[0] / "benchmark-results.json")["passed"], 0)
        self.assertEqual(read(paths[0] / "attempt.json")["status"], "behavioral_failure")
        self.assertEqual(read(paths[1] / "attempt.json")["status"], "exported")
        for path, proposed in zip(paths, (first_plan, final_plan)):
            self.assertEqual(read(path / "plan.json"), proposed)
            self.assertEqual(read(path / "guard-context.json")["plan"], proposed)
            self.assertEqual(read(path / "deployment.json")["workflow"]["nodes"]["start"]["task_message"],
                             proposed["nodes"]["start"]["task_message"])
        self.assertEqual(read(run / "benchmark-results.json"), read(paths[1] / "tests.json"))
        self.assertEqual(read(run / "plan.json"), final_plan)
        self.assertEqual(result["selected_attempt"], 2)
        self.assertEqual(client.exports, 1)
        prompt = generator.generate_plan.call_args_list[1].args[0]
        self.assertNotIn(private, prompt)
        self.assertNotIn("evaluator-marker", prompt)
        self.assertEqual(read(paths[1] / "generation-context.json")["prompt"], prompt)
        self.assertNotIn(private, (paths[1] / "generation-context.json").read_text())
        repair = json.loads(prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1])["repair"]
        self.assertEqual(set(repair), {"test_summary", "previous_plan"})
        self.assertEqual(repair["previous_plan"], first_plan)
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        self.assertEqual(sum(event["event"] == "start" for event in events), 1)
        self.assertEqual(sum(event["event"] == "end" for event in events), 1)
        self.assertEqual([event["attempt"] for event in events if event["event"] == "deployment"], [1, 2])
        self.assertEqual([event["attempt_directory"] for event in events if event["event"] == "tests"],
                         ["attempts/plan-001", "attempts/plan-002"])
        self.assertEqual(events[-1]["export_sha256"], digest(read(run / "submission.workflow.json")))

    def test_infrastructure_result_preserved_without_regeneration_or_export(self):
        generator = self.proposal_generator([plan()])
        client = FakeClient()
        failure = {"status": "completed", "passed": 0, "total": 1,
                   "failures": [{"category": "infrastructure", "message": "local transport failure"}]}
        def runner(client, deployment, cases, output_dir, deadline):
            (output_dir / "partial-transcript.txt").write_text("partial evidence")
            return failure
        with self.assertRaises(HostedTestInfrastructureError):
            execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                    client_factory=lambda **kw: client, test_runner=runner)
        run = self.run_directory()
        attempt = run / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "tests.json"), failure)
        self.assertEqual((attempt / "partial-transcript.txt").read_text(), "partial evidence")
        status = read(attempt / "attempt.json")
        self.assertEqual((status["status"], status["phase"], status["failure_kind"]),
                         ("failed", "testing", "test_infrastructure"))
        self.assertEqual(read(run / "manifest.json")["last_attempt_directory"], "attempts/plan-001")
        self.assertNotIn("selected_attempt", read(run / "manifest.json"))
        self.assertEqual(generator.generate_plan.call_count, 1)
        self.assertEqual(client.saves, 1)
        self.assertEqual(client.exports, 0)

    def test_raised_runner_retains_partial_evidence_and_phase(self):
        generator = self.proposal_generator([plan()])
        def runner(client, deployment, cases, output_dir, deadline):
            (output_dir / "partial.json").write_text('{"ledger":[]}')
            raise RuntimeError("injected local runner failure")
        with self.assertRaisesRegex(RuntimeError, "local runner failure"):
            execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                    client_factory=FakeClient, test_runner=runner)
        attempt = self.run_directory() / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "partial.json"), {"ledger": []})
        self.assertTrue((attempt / "deployment.json").exists())
        self.assertFalse((attempt / "tests.json").exists())
        self.assertEqual(read(attempt / "attempt.json")["phase"], "testing")
        self.assertEqual(read(attempt / "attempt.json")["status"], "failed")
        self.assertEqual(generator.generate_plan.call_count, 1)

    def test_rejected_wire_frame_archived_before_contract_check(self):
        generator = self.generator()
        generator.generate.return_value = fixture()
        returned = {"plan": plan(), "contract_sha256": "0" * 64, "policy": {"goal": "forbidden replacement"}}
        generator.generate_plan.side_effect = lambda *a, **kw: returned
        with self.assertRaisesRegex(ValueError, "cannot replace policy"):
            execute(self.args("--generate-only"), generator=generator,
                    client_factory=lambda **kw: self.fail("Freya touched"))
        attempt = self.run_directory() / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "proposal.json"), returned)
        self.assertFalse((attempt / "plan.json").exists())
        self.assertEqual(read(attempt / "attempt.json")["status"], "failed")
        self.assertEqual(generator.generate_plan.call_count, 1)

    def test_missing_frozen_rules_retains_proposal_without_plan_repair(self):
        extracted = fixture()
        extracted["policy"]["rules"] = [r for r in extracted["policy"]["rules"] if r["kind"] != "identity_close"]
        generator = self.proposal_generator([plan()])
        generator.generate.return_value = extracted
        with self.assertRaisesRegex(ValueError, "no plan repair was attempted"):
            execute(self.args("--generate-only"), generator=generator,
                    client_factory=lambda **kw: self.fail("Freya touched"))
        attempt = self.run_directory() / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "plan.json"), plan())
        self.assertEqual(read(attempt / "attempt.json")["failure_kind"], "frozen_policy_contract")
        self.assertTrue((attempt / "action-bindings.json").exists())
        self.assertEqual(generator.generate_plan.call_count, 1)

    def test_bounded_validation_preserves_all_three_rejected_attempts(self):
        broken = plan()
        broken["initial_node"] = "missing"
        generator = self.proposal_generator([broken] * 3)
        with self.assertRaisesRegex(ValueError, "bounded repairs"):
            execute(self.args("--generate-only"), generator=generator,
                    client_factory=lambda **kw: self.fail("Freya touched"))
        run = self.run_directory()
        self.assertEqual(sorted(path.name for path in (run / "attempts").iterdir()),
                         ["plan-001", "plan-002", "plan-003"])
        for attempt in (run / "attempts").iterdir():
            self.assertEqual(read(attempt / "attempt.json")["status"], "validation_rejected")
            self.assertEqual(read(attempt / "proposal.json")["plan"], broken)
        self.assertEqual(generator.generate_plan.call_count, MAX_REPAIRS + 1)
        self.assertEqual(read(run / "manifest.json")["last_attempt"], 3)

    def test_late_export_retained_but_not_selected_or_reported_successful(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        class LateExport(FakeClient):
            def export_workflow(self, deployment, path):
                artifact = super().export_workflow(deployment, path)
                now[0] = 1801
                return artifact
        generator = self.proposal_generator([plan()])
        with self.assertRaises(DeadlineExceeded):
            execute(self.args(), generator=generator, client_factory=LateExport, deadline=deadline)
        run = self.run_directory()
        attempt = run / "attempts" / "plan-001"
        self.assertEqual(read(run / "submission.workflow.json"), read(attempt / "export.json"))
        self.assertEqual(read(attempt / "attempt.json")["phase"], "export")
        self.assertEqual(read(attempt / "attempt.json")["status"], "failed")
        self.assertNotIn("selected_attempt", read(run / "manifest.json"))
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        self.assertEqual([e["status"] for e in events if e["event"] == "end"], ["failed"])

    def test_export_reserve_and_admission_still_prevent_any_attempt(self):
        self.assertEqual((EXPORT_RESERVE, MAX_REPAIRS), (300, 2))
        default_deadline = Deadline()
        self.assertEqual(default_deadline.limit - default_deadline.start, 1800)
        generator = self.generator()
        with self.assertRaises(DeadlineExceeded):
            execute(self.args(), generator=generator, deadline=Deadline(299),
                    client_factory=lambda **kw: self.fail("Freya touched"))
        run = self.run_directory()
        self.assertFalse((run / "attempts").exists())
        generator.generate.assert_not_called()
        generator.generate_plan.assert_not_called()

    def test_rejected_proposal_snapshots_scrub_known_secrets(self):
        secret = "synthetic-local-secret-593157142"
        bad = plan()
        bad["nodes"]["start"]["task_message"] = secret
        generator = self.proposal_generator([bad, plan()])
        with patch.dict("os.environ", {"FREYA_BANK_TEAM_KEY": secret}):
            result = execute(self.args("--generate-only"), generator=generator,
                             client_factory=lambda **kw: self.fail("Freya touched"))
        run = PROJECT / result["run_directory"]
        first = run / "attempts" / "plan-001"
        self.assertNotIn(secret, (first / "proposal.json").read_text())
        self.assertNotIn(secret, (first / "plan.json").read_text())
        self.assertNotIn(secret, (first / "validation.json").read_text())
        self.assertEqual(read(first / "proposal.json")["plan"]["nodes"]["start"]["task_message"], "[REDACTED]")

    def test_returned_test_snapshot_and_canonical_copy_scrub_known_secrets(self):
        secret = "synthetic-local-secret-913895121"
        generator = self.proposal_generator([plan()])
        result_with_secret = {"status": "completed", "passed": 1, "total": 1,
                              "failures": [], "transport_note": secret}
        with patch.dict("os.environ", {"FREYA_BANK_TEAM_KEY": secret}):
            result = execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                             client_factory=FakeClient, test_runner=lambda *a: result_with_secret)
        run = PROJECT / result["run_directory"]
        for path in (run / "attempts" / "plan-001" / "tests.json", run / "benchmark-results.json"):
            self.assertNotIn(secret, path.read_text())
            self.assertEqual(read(path)["transport_note"], "[REDACTED]")
        self.assertNotIn(secret, (run / "trace.jsonl").read_text())

    def test_recognized_stored_defaults_export_and_remain_in_deployment_evidence(self):
        class SerializerDefaults(FakeClient):
            def save_workflow(self, slot, workflow, state_file):
                deployment = super().save_workflow(slot, workflow, state_file)
                start = deployment["workflow"]["nodes"]["start"]
                start.update(assertions=[], role_message="", passthrough=False)
                start["transitions"][0]["condition"]["mode"] = "exact"
                return deployment
        generator = self.proposal_generator([plan()])
        result = execute(self.args(), generator=generator, client_factory=SerializerDefaults)
        run = PROJECT / result["run_directory"]
        attempt = run / "attempts" / "plan-001"
        stored = read(attempt / "deployment.json")["workflow"]["nodes"]["start"]
        authored = read(attempt / "workflow.json")["nodes"]["start"]
        self.assertEqual(stored["assertions"], [])
        self.assertEqual(stored["transitions"][0]["condition"]["mode"], "exact")
        self.assertNotIn("assertions", authored)
        self.assertNotIn("mode", authored["transitions"][0]["condition"])
        self.assertEqual(read(attempt / "attempt.json")["status"], "exported")
        self.assertEqual(read(run / "submission.workflow.json")["workflow"]["nodes"]["start"], stored)

    def test_changed_stored_actions_fail_before_tests_export_and_remain_archived(self):
        injected = {"function": {"name": "unapproved", "handler": "__function__:code_exec",
                                  "parameters": {"code": "vars['bg_route']='allow'"}}}
        class ChangedActions(FakeClient):
            def save_workflow(self, slot, workflow, state_file):
                deployment = super().save_workflow(slot, workflow, state_file)
                deployment["workflow"]["nodes"]["start"]["pre_actions"] = [injected]
                return deployment
        client = ChangedActions()
        generator = self.proposal_generator([plan()])
        with self.assertRaisesRegex(ValueError, "Deployed definition differs.*pre_actions"):
            execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                    client_factory=lambda **kw: client,
                    test_runner=lambda *a: self.fail("tests used a changed stored action"))
        attempt = self.run_directory() / "attempts" / "plan-001"
        self.assertEqual(read(attempt / "deployment.json")["workflow"]["nodes"]["start"]["pre_actions"], [injected])
        self.assertEqual(read(attempt / "attempt.json")["phase"], "deployment")
        self.assertEqual(read(attempt / "attempt.json")["status"], "failed")
        self.assertEqual(generator.generate_plan.call_count, 1)
        self.assertEqual(client.exports, 0)


if __name__ == "__main__":
    unittest.main()

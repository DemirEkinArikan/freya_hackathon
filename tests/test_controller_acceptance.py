"""Controller-boundary regressions; fixtures are local and never live evidence."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from builder.build import PROJECT, execute, _load_inputs, make_policy_prompt
from builder.runtime import CodexGenerator, Deadline, DeadlineExceeded, GenerationError, Trace
from builder.safety import digest
import test_builder as fixtures
from test_builder import FakeClient, fixture, plan


class ControllerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.BuilderTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.directory = self.helper.directory

    def generator(self):
        generator = self.helper.generator()
        generator.generate.return_value = fixture()
        return generator

    def run_directory(self):
        return next((self.directory / "out").iterdir())

    def events(self):
        return [json.loads(line) for line in (self.run_directory() / "trace.jsonl").read_text().splitlines()]

    def test_missing_dependency_is_repaired_before_plan_and_freeze(self):
        generator = self.generator()
        incomplete = fixture()
        incomplete["policy"]["rules"] = [r for r in incomplete["policy"]["rules"] if r["kind"] != "record_notes"]
        generator.generate.side_effect = [incomplete, fixture()]
        result = execute(self.helper.args("--generate-only"), generator=generator,
                         client_factory=lambda **kw: self.fail("platform started"))
        self.assertEqual(result["policy_repairs"], 1)
        self.assertEqual(generator.generate_plan.call_count, 1)
        events = self.events()
        rejected = next(i for i, event in enumerate(events) if event["event"] == "policy_validation" and event["passed"] is False)
        checked = next(i for i, event in enumerate(events) if event["event"] == "policy_execution_requirements")
        frozen = next(i for i, event in enumerate(events) if event["event"] == "policy_frozen")
        self.assertLess(rejected, checked)
        self.assertLess(checked, frozen)
        descriptor = json.loads((self.run_directory() / "execution-requirements.json").read_text())
        self.assertEqual(result["execution_requirements_sha256"], descriptor["sha256"])
        self.assertEqual(events[frozen]["execution_requirements_sha256"], descriptor["sha256"])
        saved = json.loads((self.run_directory() / "policy.rejected-1.json").read_text())
        self.assertEqual(saved["policy"], incomplete["policy"])
        self.assertTrue(any("record_notes" in error for error in saved["validation_errors"]))

    def test_resumed_missing_dependency_stops_before_freeze_and_plan(self):
        incomplete = fixture()["policy"]
        incomplete["rules"] = [r for r in incomplete["rules"] if r["kind"] != "record_notes"]
        path = self.directory / "incomplete-policy.json"
        path.write_text(json.dumps(incomplete))
        generator = self.generator()
        with self.assertRaisesRegex(ValueError, "record_notes"):
            execute(self.helper.args("--policy-file", str(path)), generator=generator,
                    client_factory=lambda **kw: self.fail("platform started"))
        generator.generate.assert_not_called()
        generator.generate_plan.assert_not_called()
        self.assertFalse(any(event["event"] == "policy_frozen" for event in self.events()))

    def test_frozen_descriptor_is_identical_through_bounded_plan_repair(self):
        generator = self.generator()
        broken = plan(); broken["initial_node"] = "missing"
        contexts = []
        def generated(prompt, **kwargs):
            contexts.append(json.loads(prompt.split("AUTHORIZED INPUTS:\n", 1)[1]))
            return {"plan": broken if len(contexts) == 1 else plan(), "contract_sha256": kwargs["contract_sha256"]}
        generator.generate_plan.side_effect = generated
        result = execute(self.helper.args("--generate-only"), generator=generator,
                         client_factory=lambda **kw: self.fail("platform started"))
        self.assertEqual(result["workflow_repairs"], 1)
        self.assertEqual(generator.generate.call_count, 1)
        original = json.loads((self.run_directory() / "execution-requirements.json").read_text())
        self.assertEqual([context["execution_requirements"] for context in contexts], [original, original])
        self.assertEqual(original["policy_sha256"], digest(fixture()["policy"]))
        self.assertNotIn("sources", contexts[1]["repair"])
        self.assertNotIn("execution_requirements", contexts[1]["repair"])

    def test_plan_cannot_add_or_omit_frozen_selected_capabilities(self):
        for alteration in ("add", "omit"):
            with self.subTest(alteration=alteration):
                proposed = plan()
                extracted = fixture()
                if alteration == "omit":
                    next(r for r in extracted["policy"]["rules"] if r["kind"] == "execution_actions")["value"] = ["close_call", "stage_note"]
                    extracted["policy"]["rules"].append({"id": "note_reasons", "kind": "note_reasons", "value": ["refused"],
                        "source": copy.deepcopy(extracted["policy"]["rules"][0]["source"])})
                else:
                    proposed["nodes"]["identity"] = {"name": "identity", "node_type": "guard_action", "action": "verify_identity",
                        "inputs": {"field": "proposed_field", "answer": "proposed_answer"},
                        "success_node": "start", "blocked_node": "start", "failure_node": "start"}
                generator = self.generator()
                generator.generate.return_value = extracted
                generator.generate_plan.side_effect = lambda *a, **kw: {"plan": proposed, "contract_sha256": kw["contract_sha256"]}
                with self.assertRaisesRegex(ValueError, "execution_actions"):
                    execute(self.helper.args(), generator=generator,
                            client_factory=lambda **kw: self.fail("platform started"))
                self.assertEqual(generator.generate.call_count, 1)
                self.assertEqual(generator.generate_plan.call_count, 3)

    def test_real_generator_audit_failure_blocks_brief_and_client(self):
        process = Mock(side_effect=AssertionError("substantive model process started"))
        generator = CodexGenerator(self.directory, Trace(self.directory / "runtime-trace.jsonl"), Deadline(), run=process)
        with patch.object(generator, "check_capabilities"), \
                patch.object(generator, "generation_catalog", return_value=("{}", {"sha256": "catalog"})), \
                patch("builder.preflight.isolation_probe", return_value={"status": "fail", "reported_tools": ["shell"]}), \
                self.assertRaisesRegex(GenerationError, "no banking brief was sent"):
            execute(self.helper.args(), generator=generator,
                    client_factory=lambda **kw: self.fail("platform started"))
        process.assert_not_called()
        self.assertFalse(any(event["event"] == "policy_frozen" for event in self.events()))
        self.assertEqual(self.events()[-1]["status"], "failed")

    def test_ordered_duplicate_basenames_remain_distinct_source_inputs(self):
        first = self.directory / "base" / "policy.md"
        second = self.directory / "overlay" / "policy.md"
        first.parent.mkdir(); second.parent.mkdir()
        first.write_text("Original bank requirements")
        second.write_text("Later bank amendment")
        args = self.helper.args("--generate-only")
        args.brief = [second, first]
        sources, api = _load_inputs(args)
        self.assertEqual([s["id"] for s in sources], ["brief-1", "brief-2"])
        self.assertEqual([s["text"] for s in sources], [second.read_text(), first.read_text()])
        self.assertEqual([s["sha256"] for s in sources], [digest(second.read_text()), digest(first.read_text())])
        payload = json.loads(make_policy_prompt(sources, api).split("AUTHORIZED INPUTS:\n", 1)[1])
        self.assertEqual(payload["sources"], sources)
        self.assertEqual(set(payload), {"sources", "openapi", "execution_interface"})
        self.assertIn("close_call", payload["execution_interface"]["actions"])

    def test_resume_revalidates_inputs_before_any_plan_or_platform_call(self):
        policy = fixture()["policy"]
        policy["sources"][0]["sha256"] = "0" * 64
        path = self.directory / "policy.json"
        path.write_text(json.dumps(policy))
        generator = self.generator()
        with self.assertRaises(ValueError):
            execute(self.helper.args("--policy-file", str(path)), generator=generator,
                    client_factory=lambda **kw: self.fail("platform started"))
        generator.generate.assert_not_called()
        generator.generate_plan.assert_not_called()
        self.assertFalse(any(event["event"] == "policy_frozen" for event in self.events()))

    def test_plan_cannot_replace_frozen_contract_or_trigger_deployment(self):
        generator = self.generator()
        generator.generate_plan.side_effect = lambda *a, **kw: {"plan": plan(), "contract_sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "match the frozen contract"):
            execute(self.helper.args(), generator=generator,
                    client_factory=lambda **kw: self.fail("platform started"))
        frozen = [event for event in self.events() if event["event"] == "policy_frozen"]
        self.assertEqual(len(frozen), 1)
        self.assertEqual(frozen[0]["policy_sha256"], digest(fixture()["policy"]))
        self.assertEqual(generator.generate_plan.call_count, 1)

    def test_policy_and_plan_failures_share_two_repair_budget(self):
        generator = self.generator()
        invalid = fixture()
        invalid["policy"]["sources"][0]["sha256"] = "0" * 64
        generator.generate.side_effect = [invalid, fixture()]
        broken = plan(); broken["initial_node"] = "missing"
        generator.generate_plan.side_effect = lambda *a, **kw: {"plan": copy.deepcopy(broken), "contract_sha256": kw["contract_sha256"]}
        with self.assertRaisesRegex(ValueError, "bounded repairs"):
            execute(self.helper.args(), generator=generator,
                    client_factory=lambda **kw: self.fail("platform started"))
        self.assertEqual(generator.generate.call_count, 2)
        self.assertEqual(generator.generate_plan.call_count, 2)
        self.assertTrue((self.run_directory() / "policy.rejected-1.json").exists())
        self.assertEqual(len(list((self.run_directory() / "attempts").iterdir())), 2)

    def test_generation_crossing_export_reserve_stops_before_client(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        generator = self.generator()
        def generated(*args, **kwargs):
            now[0] = 1500
            return {"plan": plan(), "contract_sha256": kwargs["contract_sha256"]}
        generator.generate_plan.side_effect = generated
        with self.assertRaises(DeadlineExceeded):
            execute(self.helper.args(), generator=generator, deadline=deadline,
                    client_factory=lambda **kw: self.fail("export reserve consumed before deployment"))
        self.assertEqual(self.events()[-1]["status"], "failed")
        self.assertFalse((self.run_directory() / "submission.workflow.json").exists())

    def test_same_global_deadline_reaches_client_and_export_uses_reserved_time(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        generator = self.generator()
        def generated(*args, **kwargs):
            now[0] = 1498
            return {"plan": plan(), "contract_sha256": kwargs["contract_sha256"]}
        generator.generate_plan.side_effect = generated
        acceptance = self
        class TimedClient(FakeClient):
            def __init__(self, **kwargs):
                acceptance.assertIs(kwargs["deadline"], deadline)
                acceptance.assertEqual(kwargs["reserve"], 300)
                super().__init__(**kwargs)
            def export_workflow(self, deployment, path):
                acceptance.assertEqual(self.reserve, 0)
                now[0] = 1750
                return super().export_workflow(deployment, path)
        result = execute(self.helper.args(), generator=generator, deadline=deadline, client_factory=TimedClient)
        self.assertEqual(result["status"], "exported")
        self.assertEqual(result["elapsed_seconds"], 1750)
        self.assertFalse(result["hosted_behavior_verified"])
        self.assertEqual(self.events()[-1]["remaining_seconds"], 50)

    def test_controller_credentials_bind_after_generation_and_redact_failures(self):
        api_path = self.directory / "api.json"
        api = json.loads(api_path.read_text())
        api["components"] = {"securitySchemes": {"bankAuth": {"type": "apiKey", "in": "header", "name": "X-Bank-Team"}}}
        api["security"] = [{"bankAuth": []}]
        api_path.write_text(json.dumps(api))
        secret = "synthetic-controller-acceptance-key-123456"
        generator = self.generator()
        acceptance = self
        class RejectingClient(FakeClient):
            def validate_workflow(self, name, workflow):
                acceptance.assertIn(secret, json.dumps(workflow))
                raise RuntimeError("Synthetic transport failure: " + secret)
        with patch.dict("os.environ", {"FREYA_TOOL_AUTH_BANKAUTH": secret}), \
                self.assertRaisesRegex(RuntimeError, "Synthetic transport failure"):
            execute(self.helper.args(), generator=generator, client_factory=RejectingClient)
        for call in generator.generate.call_args_list + generator.generate_plan.call_args_list:
            self.assertNotIn(secret, call.args[0])
        for path in self.run_directory().rglob("*.json*"):
            with self.subTest(file=path.name):
                self.assertNotIn(secret, path.read_text())
        self.assertIn("[REDACTED]", (self.run_directory() / "manifest.json").read_text())


if __name__ == "__main__":
    unittest.main()

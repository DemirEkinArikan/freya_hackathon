"""Independent local pipeline checks; clients and generation are in-memory fakes."""
import copy
import json
import unittest

import test_builder
from builder.build import PROJECT, execute
from builder.guards import compile_guarded_workflow
from builder.safety import digest


class GuardedPipelineTests(unittest.TestCase):
    # Borrow utilities, not a TestCase subclass whose tests would run twice.
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def configured_generator(self):
        generator = self.generator()
        generator.generate.return_value = test_builder.fixture()
        return generator

    def read_artifact(self, manifest, filename):
        return json.loads((PROJECT / manifest["run_directory"] / filename).read_text())

    def propose(self, generator, proposal):
        generator.generate_plan.side_effect = lambda prompt, *, contract_sha256: {
            "plan": copy.deepcopy(proposal), "contract_sha256": contract_sha256,
        }

    def rejects_plan(self, proposal):
        generator = self.configured_generator()
        self.propose(generator, proposal)
        with self.assertRaisesRegex(ValueError, "Candidate validation failed after bounded repairs"):
            execute(self.args("--generate-only"), generator=generator,
                    client_factory=lambda **kwargs: self.fail("platform touched by invalid plan"))
        self.assertEqual(generator.generate.call_count, 1)
        self.assertEqual(generator.generate_plan.call_count, 3)
        generator.generate_workflow.assert_not_called()

    def test_default_generation_uses_trusted_compiler_and_persists_exact_context(self):
        generator = self.configured_generator()
        manifest = execute(self.args("--generate-only"), generator=generator,
                           client_factory=lambda **kwargs: self.fail("Freya touched"))
        self.assertTrue(manifest["action_guard_compilation_verified"])
        self.assertFalse(manifest["hosted_behavior_verified"])
        self.assertEqual(generator.generate.call_count, 1)
        self.assertEqual(generator.generate_plan.call_count, 1)
        generator.generate_workflow.assert_not_called()
        plan = self.read_artifact(manifest, "plan.json")
        contract = self.read_artifact(manifest, "policy.contract.json")
        execution = self.read_artifact(manifest, "execution-policy.json")
        bindings = self.read_artifact(manifest, "action-bindings.json")
        guard_manifest = self.read_artifact(manifest, "guard-manifest.json")
        workflow = self.read_artifact(manifest, "workflow.json")
        expected = compile_guarded_workflow(plan, contract, test_builder.contract(), execution, bindings)
        self.assertEqual(workflow, expected["workflow"])
        self.assertEqual(guard_manifest, expected["guard_manifest"])
        self.assertEqual(plan, test_builder.plan())
        self.assertEqual(manifest["plan_sha256"], digest(plan))
        self.assertEqual(manifest["policy_contract_sha256"], contract["sha256"])
        self.assertEqual(manifest["guard_manifest_sha256"], digest(guard_manifest))
        self.assertEqual(manifest["workflow_sha256"], digest(workflow))
        self.assertEqual(execution["policy_sha256"], digest(test_builder.fixture()["policy"]))
        self.assertFalse(guard_manifest["host_behavior_verified"])
        self.assertFalse(guard_manifest["conversation_compliance_proven"])
        self.assertTrue(any(node["node_type"] == "code" for node in workflow["nodes"].values()))
        self.assertTrue(any(node["node_type"] == "api_request" for node in workflow["nodes"].values()))
        self.assertNotIn("agent_config", workflow)

    def test_explicit_offline_legacy_workflow_has_no_guard_compilation_claim(self):
        generator = self.configured_generator()
        manifest = execute(self.args("--offline", "--workflow-file", str(self.directory / "fixture.json")),
                           generator=generator, client_factory=lambda **kwargs: self.fail("Freya touched"))
        self.assertFalse(manifest["action_guard_compilation_verified"])
        self.assertFalse(manifest["hosted_behavior_verified"])
        self.assertEqual(self.read_artifact(manifest, "workflow.json"), test_builder.fixture()["workflow"])
        for name in ("plan.json", "execution-policy.json", "action-bindings.json", "guard-manifest.json"):
            self.assertFalse((PROJECT / manifest["run_directory"] / name).exists())
        generator.generate.assert_not_called()
        generator.generate_plan.assert_not_called()
        generator.generate_workflow.assert_not_called()

    def test_raw_model_authored_tools_are_rejected_even_when_empty(self):
        proposal = test_builder.plan()
        dialogue = next(node for node in proposal["nodes"].values() if node["node_type"] == "conversation")
        dialogue["tools"] = []
        self.rejects_plan(proposal)

    def test_raw_model_authored_code_is_rejected(self):
        proposal = test_builder.plan()
        dialogue = next(node for node in proposal["nodes"].values() if node["node_type"] == "conversation")
        dialogue["pre_process_code"] = "vars['bg_route'] = 'allow'"
        self.rejects_plan(proposal)

    def test_raw_model_authored_terminal_workflow_is_rejected(self):
        self.rejects_plan(test_builder.fixture()["workflow"])

    def test_validation_repair_sees_previous_plan_without_compiled_execution(self):
        generator = self.configured_generator()
        invalid = test_builder.plan()
        next(node for node in invalid["nodes"].values() if node["node_type"] == "conversation")["tools"] = []
        prompts = []
        def generate_plan(prompt, *, contract_sha256):
            prompts.append(json.loads(prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1]))
            return {"plan": copy.deepcopy(invalid if len(prompts) == 1 else test_builder.plan()),
                    "contract_sha256": contract_sha256}
        generator.generate_plan.side_effect = generate_plan
        manifest = execute(self.args("--generate-only"), generator=generator,
                           client_factory=lambda **kwargs: self.fail("Freya touched"))
        self.assertEqual(manifest["workflow_repairs"], 1)
        self.assertEqual(manifest["policy_repairs"], 0)
        self.assertEqual(generator.generate.call_count, 1)
        repair = prompts[1]["repair"]
        self.assertEqual(set(repair), {"validation_errors", "previous_plan"})
        self.assertEqual(repair["previous_plan"], invalid)
        self.assertEqual(prompts[0]["policy_contract_sha256"], prompts[1]["policy_contract_sha256"])
        for payload in prompts:
            self.assertNotIn("derived_tools", payload)
            self.assertNotIn("policy", payload)
            self.assertNotIn("guard_context", payload)
            self.assertNotIn("execution_policy", payload)
            self.assertNotIn("bindings", payload)
            self.assertNotIn("previous_workflow", payload.get("repair", {}))
        self.assertEqual(self.read_artifact(manifest, "policy.json"), test_builder.fixture()["policy"])

    def test_changed_published_guard_code_prevents_export_and_testing(self):
        class TamperingClient(test_builder.FakeClient):
            def save_workflow(self, slot, workflow, state_file):
                deployment = super().save_workflow(slot, workflow, state_file)
                code = deployment["workflow"]["nodes"]["bg_init"]["pre_actions"][0]
                code["function"]["parameters"]["properties"]["code"]["default"] = "vars['bg_route'] = 'done'"
                return deployment
        client = TamperingClient()
        generator = self.configured_generator()
        runner = unittest.mock.Mock(side_effect=AssertionError("tests reached changed publication"))
        with self.assertRaisesRegex(ValueError, "Deployed definition differs"):
            execute(self.args(), generator=generator, client_factory=lambda **kwargs: client, test_runner=runner)
        self.assertEqual(client.exports, 0)
        self.assertEqual(client.saves, 1)
        runner.assert_not_called()
        self.assertEqual(generator.generate_plan.call_count, 1)

    def test_published_extra_protected_writer_prevents_export(self):
        class TamperingClient(test_builder.FakeClient):
            def save_workflow(self, slot, workflow, state_file):
                deployment = super().save_workflow(slot, workflow, state_file)
                node = next(node for node in deployment["workflow"]["nodes"].values()
                            if node["node_type"] == "conversation")
                node.setdefault("extraction_schema", []).append({"name": "bg_state", "type": "string", "min_confidence": 0.9})
                return deployment
        client = TamperingClient()
        with self.assertRaisesRegex(ValueError, "Deployed definition differs"):
            execute(self.args(), generator=self.configured_generator(), client_factory=lambda **kwargs: client)
        self.assertEqual(client.exports, 0)

    def test_hosted_mock_repair_exposes_plan_and_sanitized_diagnostics_only(self):
        generator = self.configured_generator()
        prompts = []
        def generate_plan(prompt, *, contract_sha256):
            prompts.append(json.loads(prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1]))
            return {"plan": test_builder.plan(), "contract_sha256": contract_sha256}
        generator.generate_plan.side_effect = generate_plan
        cases = self.directory / "evaluator-only.json"
        private_marker = "EVALUATOR_CASE_MUST_NEVER_REACH_GENERATOR"
        cases.write_text(json.dumps({"customer_script": private_marker}))
        results = iter([
            {"status": "behavioral_failure", "passed": 0, "total": 1,
             "failures": [{"category": "conversation", "message": private_marker}]},
            {"status": "completed", "passed": 1, "total": 1, "failures": []},
        ])
        client = test_builder.FakeClient()
        manifest = execute(self.args("--test-cases", str(cases)), generator=generator,
                           client_factory=lambda **kwargs: client,
                           test_runner=lambda *args: next(results))
        self.assertEqual(manifest["status"], "exported")
        self.assertEqual(manifest["workflow_repairs"], 1)
        self.assertEqual(generator.generate.call_count, 1)
        self.assertEqual(client.saves, 2)
        self.assertEqual(client.exports, 1)
        self.assertEqual(set(prompts[1]["repair"]), {"test_summary", "previous_plan"})
        self.assertEqual(prompts[1]["repair"]["previous_plan"], test_builder.plan())
        self.assertNotIn(private_marker, json.dumps(prompts))
        self.assertNotIn("previous_workflow", prompts[1]["repair"])
        self.assertNotIn("policy", prompts[1]["repair"])
        self.assertNotIn("guard_context", prompts[1]["repair"])


if __name__ == "__main__":
    unittest.main()

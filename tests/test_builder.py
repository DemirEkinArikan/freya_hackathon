import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from builder.build import execute, main, make_prompt, parser, PROJECT, HostedTestInfrastructureError
from builder.platform import PlatformError
from builder.runtime import Deadline, DeadlineExceeded
from builder.toolbindings import BindingError, derive_tools, resolve_refs


def fixture():
    workflow = {"initial_node": "start", "nodes": {
        "start": {"name": "start", "node_type": "conversation", "task_message": "Ask how to help with the supplied banking goal.",
                  "transitions": [{"target_node": "done", "description": "finished", "condition": {"operator": "matches_intent", "description": "The customer has finished"}}]},
        "done": {"name": "done", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                 "transitions": [], "terminal": {"type": "function", "function": {"name": "finished", "handler": "__function__:end_call",
                        "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}}}}
    policy = {"version": 1, "goal": "Banking contact", "allowed_actions": ["close_call"], "sources": [{"id": "brief-1", "file": "brief.md"}],
              "rules": [{"id": "r1", "kind": "purpose", "value": "Banking contact", "source": {"id": "brief-1", "quote": "Use the permitted banking contact flow."}}],
              "unsupported_constraints": []}
    return {"policy": policy, "workflow": workflow}


def contract():
    return {"openapi": "3.0.3", "servers": [{"url": "https://freya-bank-mock.freyavoice.workers.dev/v1"}], "paths": {"/close_call": {"post": {
        "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"outcome": {"type": "string", "enum": ["contacted", "unavailable"]}}, "required": ["outcome"]}}}}, "responses": {}}}}}


class FakeClient:
    def __init__(self, **kwargs):
        self.reserve = kwargs.get("reserve", 300)
        self.saves = self.exports = 0
        self.adoptions = []

    def validate_workflow(self, name, workflow):
        return {"valid": True}

    def adopt_workflow(self, slot, workflow_id, state_file):
        self.adoptions.append((slot, workflow_id))
        return {"workflow_id": workflow_id, "workspace_id": "workspace", "adopted": True}

    def save_workflow(self, slot, workflow, state_file):
        self.saves += 1
        stored = copy.deepcopy(workflow)
        stored["agent_config"] = {"llmConfig": {"provider": "experimental-5", "model": "experimental-5"}}
        return {"workflow_id": "w", "agent_id": "a", "version_id": "v", "version_number": 1,
                "published": True, "workflow": stored}

    def export_workflow(self, deployment, path):
        self.exports += 1
        artifact = {"workflow_id": "w", "agent_id": "a", "workflow": deployment["workflow"]}
        path.write_text(json.dumps(artifact))
        return artifact


class BuilderTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / ".test-tmp").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT / ".test-tmp")
        self.directory = Path(self.tmp.name)
        (self.directory / "brief.md").write_text("Use the permitted banking contact flow.")
        (self.directory / "api.json").write_text(json.dumps(contract()))
        (self.directory / "fixture.json").write_text(json.dumps(fixture()))

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *extra):
        return parser().parse_args(["--brief", str(self.directory / "brief.md"), "--tool-api", str(self.directory / "api.json"),
                                  "--output-dir", str(self.directory / "out"), *extra])

    def test_offline_fixture_never_constructs_client_or_calls_model(self):
        result = execute(self.args("--offline", "--workflow-file", str(self.directory / "fixture.json")),
                         client_factory=lambda **kw: self.fail("platform touched"))
        self.assertEqual(result["status"], "validated")
        self.assertFalse(result["hosted_behavior_verified"])

    def test_deadline_prevents_generation_when_export_reserve_is_unavailable(self):
        generator = unittest.mock.Mock()
        with self.assertRaises(DeadlineExceeded):
            execute(self.args(), generator=generator, deadline=Deadline(299), client_factory=lambda **kw: self.fail("platform touched"))
        generator.generate.assert_not_called()

    def test_invalid_candidate_gets_only_two_repairs(self):
        generator = unittest.mock.Mock()
        broken = fixture()
        broken["workflow"]["initial_node"] = "missing"
        generator.generate.return_value = broken
        with self.assertRaises(ValueError):
            execute(self.args(), generator=generator, client_factory=lambda **kw: self.fail("platform touched"))
        self.assertEqual(generator.generate.call_count, 3)

    def test_schema_enum_adapts_through_local_refs(self):
        api = contract()
        api["components"] = {"schemas": {"Closing": {"type": "object", "properties": {"disposition": {"type": "string", "enum": ["done", "deferred"]}}, "required": ["disposition"]}}}
        api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/Closing"}
        tool = derive_tools(api)[0]
        body = tool["function"]["parameters"]["properties"]["body"]
        self.assertEqual(body["required"], ["disposition"])
        self.assertEqual(body["properties"]["disposition"]["enum"], ["done", "deferred"])
        self.assertNotIn("outcome", body["properties"])

    def test_external_reference_never_fetches_network(self):
        with self.assertRaises(BindingError):
            resolve_refs({"$ref": "https://example.com/private.json"}, {})

    def test_synthetic_live_contract_is_rejected_before_model_or_client(self):
        synthetic = contract()
        synthetic["servers"][0]["url"] = "https://synthetic-bank.invalid/v1"
        (self.directory / "api.json").write_text(json.dumps(synthetic))
        generator = unittest.mock.Mock()
        with self.assertRaises(BindingError):
            execute(self.args(), generator=generator, client_factory=lambda **kw: self.fail("platform touched"))
        generator.generate.assert_not_called()

    def test_model_input_never_includes_benchmark_scripts_or_paths(self):
        prompt = make_prompt([{"id": "brief-1", "file": "brief.md", "text": "Banking contact policy"}], contract())
        self.assertNotIn("public-test-set.json", prompt)
        self.assertNotIn("Launchway", prompt)
        self.assertIn("Banking contact policy", prompt)

    def test_live_controller_exports_platform_snapshot_without_implicit_tests(self):
        generator = unittest.mock.Mock()
        generator.generate.return_value = fixture()
        result = execute(self.args(), generator=generator, client_factory=FakeClient,
                         test_runner=lambda *args: self.fail("implicit hosted test"))
        self.assertEqual(result["status"], "exported")
        self.assertEqual(result["model"], "gpt-6.1-sol")
        self.assertFalse(result["hosted_behavior_verified"])
        self.assertTrue((PROJECT / result["run_directory"] / "submission.workflow.json").exists())

    def test_generate_only_validates_output_without_freya_client(self):
        synthetic = contract()
        synthetic["servers"][0]["url"] = "https://synthetic-bank.invalid/v1"
        (self.directory / "api.json").write_text(json.dumps(synthetic))
        generator = unittest.mock.Mock()
        generator.generate.return_value = fixture()
        result = execute(self.args("--generate-only"), generator=generator,
                         client_factory=lambda **kw: self.fail("Freya client initialized"))
        self.assertEqual(result["status"], "generated_validated")
        self.assertFalse(result["hosted_behavior_verified"])
        self.assertFalse((PROJECT / result["run_directory"] / "submission.workflow.json").exists())

    def test_missing_live_credentials_fail_before_model_generation(self):
        generator = unittest.mock.Mock()
        with patch.dict("os.environ", {"FREYA_API_KEY": ""}), self.assertRaisesRegex(PlatformError, "before live workflow generation"):
            execute(self.args(), generator=generator)
        generator.generate.assert_not_called()

    def test_main_loads_local_model_default_before_parsing_without_overriding_cli(self):
        def load(path):
            self.assertEqual(path, PROJECT / ".env")
            os.environ["FREYA_BUILDER_MODEL"] = "explicit-local-model"
        base = ["--brief", str(self.directory / "brief.md"), "--tool-api", str(self.directory / "api.json"), "--generate-only"]
        for extra, expected in (([], "explicit-local-model"), (["--model", "explicit-cli-model"], "explicit-cli-model")):
            with self.subTest(extra=extra), patch.dict("os.environ", {"FREYA_BUILDER_MODEL": "", "BUILDER_MODEL": ""}), \
                    patch("builder.config.load_local_environment", side_effect=load), \
                    patch("builder.build.execute", return_value={"status": "generated_validated"}) as controller, patch("builtins.print"):
                self.assertEqual(main(base + extra), 0)
                self.assertEqual(controller.call_args.args[0].model, expected)

    def test_infrastructure_or_missing_evidence_never_regenerates(self):
        results = [
            {"status": "infrastructure_failure", "passed": 0, "total": 1, "failures": []},
            {"status": "completed", "passed": 0, "total": 1,
             "failures": [{"category": "infrastructure", "message": "temporary transport unavailable"}]},
            {"status": "completed", "passed": 0, "total": 1,
             "failures": [{"category": "evidence", "message": "identity arguments absent from ledger"}]},
            {"status": "completed", "passed": 0, "total": 0, "failures": []},
            {"status": "completed", "passed": 0, "total": 1, "failures": []},
            {"status": "completed", "passed": 1, "total": 1,
             "failures": [{"category": "behavior", "message": "identity factors were asked out of order"}]},
        ]
        for result in results:
            with self.subTest(result=result):
                generator = unittest.mock.Mock()
                generator.generate.return_value = fixture()
                client = FakeClient()
                runner = unittest.mock.Mock(return_value=result)
                with self.assertRaisesRegex(HostedTestInfrastructureError, "was not regenerated"):
                    execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                            client_factory=lambda **kw: client, test_runner=runner)
                self.assertEqual(generator.generate.call_count, 1)
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(client.saves, 1)
                self.assertEqual(client.exports, 0)
                run_dir = sorted((self.directory / "out").iterdir())[-1]
                self.assertEqual(json.loads((run_dir / "manifest.json").read_text())["failure_kind"], "test_infrastructure")
                self.assertTrue((run_dir / "workflow.json").exists())
                events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
                self.assertTrue(any(event.get("event") == "tests" and event.get("result") == result for event in events))

    def test_behavioral_repair_has_actionable_diagnostic_without_evaluator_details(self):
        generator = unittest.mock.Mock()
        generator.generate.side_effect = [fixture(), fixture()]
        private_reason = "CASE_PRIVATE customer SecretName expected 123456 on 2028-12-31"
        runner = unittest.mock.Mock(side_effect=[
            {"status": "completed", "passed": 0, "total": 1, "failures": [
                {"category": "behavior", "message": "identity factors were asked out of order", "rule_id": "r1"},
                {"category": "conversation", "message": private_reason, "rule_id": "CASE_PRIVATE"}]},
            {"status": "completed", "passed": 1, "total": 1, "failures": []}])
        result = execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                         client_factory=FakeClient, test_runner=runner)
        repair_prompt = generator.generate.call_args_list[1].args[0]
        self.assertIn("Ask identity factors in the order required by the supplied brief.", repair_prompt)
        self.assertNotIn(private_reason, repair_prompt)
        self.assertNotIn("CASE_PRIVATE", repair_prompt)
        self.assertNotIn("SecretName", repair_prompt)
        self.assertNotIn("123456", repair_prompt)
        self.assertNotIn("2028-12-31", repair_prompt)
        payload = json.loads(repair_prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1])
        self.assertEqual(payload["repair"]["test_summary"]["failures"][0]["rule_id"], "r1")
        self.assertNotIn("rule_id", payload["repair"]["test_summary"]["failures"][1])
        self.assertEqual(result["repairs"], 1)

    def test_explicit_adoption_is_done_once_across_behavioral_repair(self):
        generator = unittest.mock.Mock()
        generator.generate.side_effect = [fixture(), fixture()]
        client = FakeClient()
        runner = unittest.mock.Mock(side_effect=[
            {"status": "completed", "passed": 0, "total": 1,
             "failures": [{"category": "honesty", "message": "recorded-success claim precedes successful write"}]},
            {"status": "completed", "passed": 1, "total": 1, "failures": []}])
        execute(self.args("--adopt-workflow", "w", "--test-cases", str(self.directory / "fixture.json")),
                generator=generator, client_factory=lambda **kw: client, test_runner=runner)
        self.assertEqual(client.adoptions, [("brief", "w")])

    def test_generate_only_refuses_hosted_cases_before_model(self):
        generator = unittest.mock.Mock()
        with self.assertRaises(ValueError):
            execute(self.args("--generate-only", "--test-cases", str(self.directory / "fixture.json")), generator=generator)
        generator.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()

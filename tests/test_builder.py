import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from builder.build import execute, main, make_prompt, make_policy_prompt, parser, PROJECT, HostedTestInfrastructureError, _load_inputs
from builder.platform import PlatformError
from builder.runtime import Deadline, DeadlineExceeded
from builder.toolbindings import BindingError, derive_tools, resolve_refs
from builder.safety import digest


PROFILE = {
    "execution_actions": ["close_call"],
    "close_reasons": {"no_promise": ["refused"], "not_verified": ["failed"], "not_reached": ["unclear"]},
    "record_notes": False, "resolve_all_products": False,
    "product_order": {"mode": "account_order"}, "close_precedence": ["no_promise"],
    "identity_close": {"failed": {"outcome": "not_verified", "reason": "failed"},
                       "incomplete": {"outcome": "not_verified", "reason": "failed"},
                       "unreached": {"outcome": "not_reached", "reason": "unclear"}},
}
BRIEF_TEXT = "Use the permitted banking contact flow. Explicit synthetic closing policy: " + json.dumps(PROFILE, sort_keys=True)


def plan():
    return {"initial_node": "start", "nodes": {
        "start": {"name": "start", "node_type": "conversation", "task_message": "Ask how to help with the supplied banking goal.",
                  "transitions": [{"target_node": "done", "description": "finished", "condition": {"operator": "matches_intent", "description": "The customer has finished"}}]},
        "done": {"name": "done", "node_type": "guard_action", "action": "close_call", "inputs": {},
                 "success_node": "start", "blocked_node": "start", "failure_node": "start"}}}


def fixture():
    workflow = {"initial_node": "start", "nodes": {
        "start": {"name": "start", "node_type": "conversation", "task_message": "Ask how to help with the supplied banking goal.",
                  "transitions": [{"target_node": "done", "description": "finished", "condition": {"operator": "matches_intent", "description": "The customer has finished"}}]},
        "done": {"name": "done", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                 "transitions": [], "terminal": {"type": "function", "function": {"name": "finished", "handler": "__function__:end_call",
                        "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}}}}
    policy = {"version": 1, "goal": "Banking contact", "allowed_actions": ["close_call"], "sources": [{"id": "brief-1", "file": "brief.md", "sha256": digest(BRIEF_TEXT)}],
              "rules": [{"id": "r1", "kind": "purpose", "value": "Banking contact", "source": {"id": "brief-1", "quote": BRIEF_TEXT}}],
              "unsupported_constraints": []}
    policy["rules"].extend({"id": kind, "kind": kind, "value": copy.deepcopy(value),
                            "source": {"id": "brief-1", "quote": BRIEF_TEXT}}
                           for kind, value in PROFILE.items())
    return {"policy": policy, "workflow": workflow}


def contract():
    return {"openapi": "3.0.3", "servers": [{"url": "https://freya-bank-mock.freyavoice.workers.dev/v1"}], "paths": {"/close_call": {"post": {
        "operationId": "close_call",
        "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {
            "customer_id": {"type": "string"},
            "outcome": {"type": "string", "enum": ["no_promise", "not_verified", "not_reached"]},
            "reason": {"type": "string", "enum": ["refused", "failed", "unclear"]}}, "required": ["customer_id", "outcome", "reason"]}}}},
        "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {
            "ok": {"type": "boolean"}, "error": {"type": "string"}}}}}}}}}}}


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
        (self.directory / "brief.md").write_text(BRIEF_TEXT)
        (self.directory / "api.json").write_text(json.dumps(contract()))
        (self.directory / "fixture.json").write_text(json.dumps(fixture()))

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *extra):
        return parser().parse_args(["--brief", str(self.directory / "brief.md"), "--tool-api", str(self.directory / "api.json"),
                                  "--output-dir", str(self.directory / "out"), *extra])

    def generator(self):
        generator = unittest.mock.Mock()
        def workflow(prompt, *, contract_sha256):
            proposed = plan()
            # Preserve the invalid-initial-node regression through plan transport.
            proposed["initial_node"] = generator.generate.return_value["workflow"]["initial_node"]
            return {"plan": proposed, "contract_sha256": contract_sha256}
        generator.generate_plan.side_effect = workflow
        return generator

    def test_offline_fixture_never_constructs_client_or_calls_model(self):
        result = execute(self.args("--offline", "--workflow-file", str(self.directory / "fixture.json")),
                         client_factory=lambda **kw: self.fail("platform touched"))
        self.assertEqual(result["status"], "validated")
        self.assertFalse(result["hosted_behavior_verified"])
        run = PROJECT / result["run_directory"]
        compiled = json.loads((run / "policy.contract.json").read_text())
        self.assertEqual(result["policy_contract_sha256"], compiled["sha256"])
        self.assertEqual(compiled["permitted_operations"][0]["name"], "close_call")

    def test_deadline_prevents_generation_when_export_reserve_is_unavailable(self):
        generator = self.generator()
        with self.assertRaises(DeadlineExceeded):
            execute(self.args(), generator=generator, deadline=Deadline(299), client_factory=lambda **kw: self.fail("platform touched"))
        generator.generate.assert_not_called()

    def test_invalid_candidate_gets_only_two_repairs(self):
        generator = self.generator()
        broken = fixture()
        broken["workflow"]["initial_node"] = "missing"
        generator.generate.return_value = broken
        with self.assertRaises(ValueError):
            execute(self.args(), generator=generator, client_factory=lambda **kw: self.fail("platform touched"))
        self.assertEqual(generator.generate.call_count, 1)
        self.assertEqual(generator.generate_plan.call_count, 3)

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
        generator = self.generator()
        with self.assertRaises(BindingError):
            execute(self.args(), generator=generator, client_factory=lambda **kw: self.fail("platform touched"))
        generator.generate.assert_not_called()

    def test_model_input_never_includes_benchmark_scripts_or_paths(self):
        prompt = make_prompt([{"id": "brief-1", "file": "brief.md", "text": "Banking contact policy"}], contract())
        self.assertNotIn("public-test-set.json", prompt)
        self.assertNotIn("Launchway", prompt)
        self.assertIn("Banking contact policy", prompt)

    def test_protected_briefs_and_tool_apis_are_rejected_before_content_reads(self):
        protected = [
            self.directory / "benchmarks" / "fixtures" / "hosted.json",
            self.directory / "tests" / "fixture.json",
            self.directory / ".env",
            self.directory / ".env.local",
            self.directory / "meeting.md",
            self.directory / "meeting_summary.md",
            self.directory / "kickoff_email.txt",
            PROJECT / "build-the-builder" / "public-test-set.json",
        ]
        for path in protected[:-1]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("private evaluator/setup placeholder")
        for path in protected:
            for field in ("brief", "tool_api"):
                with self.subTest(path=path.name, field=field):
                    args = self.args("--generate-only")
                    setattr(args, field, [path] if field == "brief" else path)
                    with patch("pathlib.Path.read_text", side_effect=AssertionError("input content read")), \
                            patch("builder.build.read_json", side_effect=AssertionError("JSON input content read")), \
                            self.assertRaisesRegex(ValueError, "Protected evaluator"):
                        _load_inputs(args)

    def test_exported_evaluator_policy_inputs_remain_valid_and_cases_stay_separate(self):
        approved = self.directory / "artifacts" / "evaluator-inputs"
        approved.mkdir(parents=True)
        brief = approved / "bank-policy.md"
        api = approved / "tool-api.json"
        brief.write_text(BRIEF_TEXT)
        api.write_text(json.dumps(contract()))
        args = self.args("--test-cases", str(PROJECT / "build-the-builder" / "public-test-set.json"))
        args.brief, args.tool_api = [brief], api
        sources, openapi = _load_inputs(args)
        self.assertEqual(sources[0]["text"], BRIEF_TEXT)
        self.assertEqual(openapi["openapi"], "3.0.3")
        self.assertNotIn("test_cases", make_prompt(sources, openapi))

    def test_protected_generator_path_fails_before_model_or_client(self):
        generator = self.generator()
        args = self.args("--generate-only")
        args.brief = [PROJECT / "build-the-builder" / "public-test-set.json"]
        with self.assertRaisesRegex(ValueError, "Protected evaluator"):
            execute(args, generator=generator, client_factory=lambda **kw: self.fail("platform touched"))
        generator.generate.assert_not_called()

    def test_live_controller_exports_platform_snapshot_without_implicit_tests(self):
        generator = self.generator()
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
        generator = self.generator()
        generator.generate.return_value = fixture()
        result = execute(self.args("--generate-only"), generator=generator,
                         client_factory=lambda **kw: self.fail("Freya client initialized"))
        self.assertEqual(result["status"], "generated_validated")
        self.assertFalse(result["hosted_behavior_verified"])
        self.assertFalse((PROJECT / result["run_directory"] / "submission.workflow.json").exists())
        compiled = json.loads((PROJECT / result["run_directory"] / "policy.contract.json").read_text())
        self.assertEqual(result["policy_contract_sha256"], compiled["sha256"])

    def test_uncited_overlay_cannot_be_accepted_or_deployed(self):
        overlay = self.directory / 'amendment.md'
        overlay.write_text('The following amendment replaces the contact procedure.')
        generator = self.generator()
        generator.generate.return_value = fixture()
        args = self.args('--brief', str(overlay), '--generate-only')
        with self.assertRaises(ValueError):
            execute(args, generator=generator, client_factory=lambda **kw: self.fail('Freya touched'))
        self.assertEqual(generator.generate.call_count, 3)
        self.assertFalse(any((self.directory / 'out').glob('*/policy.contract.json')))

    def test_policy_bypass_cannot_be_accepted_or_deployed(self):
        candidate = fixture()
        candidate['policy']['identity_wrong_limit'] = 1000
        generator = self.generator()
        generator.generate.return_value = candidate
        with self.assertRaises(ValueError):
            execute(self.args('--generate-only'), generator=generator,
                    client_factory=lambda **kw: self.fail('Freya touched'))
        self.assertEqual(generator.generate.call_count, 3)

    def test_extraction_only_emits_contract_without_creating_a_workflow(self):
        generator = self.generator()
        # Even a model-provided graph in the unused transport field cannot run.
        generator.generate.return_value = fixture()
        with patch.dict('os.environ', {'FREYA_API_KEY': ''}):
            result = execute(self.args('--extract-policy-only'), generator=generator,
                             client_factory=lambda **kw: self.fail('Freya initialized'))
        self.assertEqual(result['status'], 'policy_extracted')
        self.assertFalse(result['workflow_created'])
        self.assertFalse(result['hosted_behavior_verified'])
        run = PROJECT / result['run_directory']
        self.assertTrue((run / 'policy.contract.json').exists())
        self.assertFalse((run / 'workflow.json').exists())
        self.assertFalse((run / 'submission.workflow.json').exists())
        payload = json.loads(generator.generate.call_args.args[0].split('\n\nAUTHORIZED INPUTS:\n', 1)[1])
        self.assertNotIn('runtime_references', payload)
        self.assertNotIn('derived_tools', payload)
        self.assertEqual(generator.generate.call_args.kwargs['purpose'], 'Extract banking policy and source/API bindings')

    def test_extraction_repairs_policy_errors_with_bounded_attempts(self):
        invalid = fixture()
        invalid['policy']['sources'][0]['sha256'] = '0' * 64
        generator = self.generator()
        generator.generate.side_effect = [invalid, fixture()]
        result = execute(self.args('--extract-policy-only'), generator=generator,
                         client_factory=lambda **kw: self.fail('Freya initialized'))
        self.assertEqual(result['repairs'], 1)
        rejected = json.loads((PROJECT / result['run_directory'] / 'policy.rejected-1.json').read_text())
        self.assertEqual(rejected['policy']['sources'][0]['sha256'], '0' * 64)
        self.assertTrue(rejected['validation_errors'])
        payload = json.loads(generator.generate.call_args_list[1].args[0].split('\n\nAUTHORIZED INPUTS:\n', 1)[1])
        self.assertIn('validation_errors', payload['repair'])
        self.assertNotIn('workflow', payload['repair'])

    def test_extraction_rejects_deployment_and_workflow_flags_before_model(self):
        for flags in (['--test-cases', str(self.directory / 'fixture.json')],
                      ['--adopt-workflow', 'w'], ['--workflow-file', str(self.directory / 'fixture.json')],
                      ['--generate-only']):
            with self.subTest(flags=flags):
                generator = self.generator()
                with self.assertRaises(ValueError):
                    execute(self.args('--extract-policy-only', *flags), generator=generator,
                            client_factory=lambda **kw: self.fail('Freya initialized'))
                generator.generate.assert_not_called()

    def test_missing_live_credentials_fail_before_model_generation(self):
        generator = self.generator()
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
                generator = self.generator()
                generator.generate.return_value = fixture()
                client = FakeClient()
                runner = unittest.mock.Mock(return_value=result)
                with self.assertRaisesRegex(HostedTestInfrastructureError, "was not regenerated"):
                    execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                            client_factory=lambda **kw: client, test_runner=runner)
                self.assertEqual(generator.generate.call_count, 1)
                self.assertEqual(generator.generate_plan.call_count, 1)
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(client.saves, 1)
                self.assertEqual(client.exports, 0)
                run_dir = sorted((self.directory / "out").iterdir())[-1]
                self.assertEqual(json.loads((run_dir / "manifest.json").read_text())["failure_kind"], "test_infrastructure")
                self.assertTrue((run_dir / "workflow.json").exists())
                events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
                self.assertTrue(any(event.get("event") == "tests" and event.get("result") == result for event in events))

    def test_behavioral_repair_has_actionable_diagnostic_without_evaluator_details(self):
        generator = self.generator()
        generator.generate.return_value = fixture()
        private_reason = "CASE_PRIVATE customer SecretName expected 123456 on 2028-12-31"
        runner = unittest.mock.Mock(side_effect=[
            {"status": "completed", "passed": 0, "total": 1, "failures": [
                {"category": "behavior", "message": "identity factors were asked out of order", "rule_id": "r1"},
                {"category": "conversation", "message": private_reason, "rule_id": "CASE_PRIVATE"}]},
            {"status": "completed", "passed": 1, "total": 1, "failures": []}])
        result = execute(self.args("--test-cases", str(self.directory / "fixture.json")), generator=generator,
                         client_factory=FakeClient, test_runner=runner)
        repair_prompt = generator.generate_plan.call_args_list[1].args[0]
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
        generator = self.generator()
        generator.generate.return_value = fixture()
        client = FakeClient()
        runner = unittest.mock.Mock(side_effect=[
            {"status": "completed", "passed": 0, "total": 1,
             "failures": [{"category": "honesty", "message": "recorded-success claim precedes successful write"}]},
            {"status": "completed", "passed": 1, "total": 1, "failures": []}])
        execute(self.args("--adopt-workflow", "w", "--test-cases", str(self.directory / "fixture.json")),
                generator=generator, client_factory=lambda **kw: client, test_runner=runner)
        self.assertEqual(client.adoptions, [("brief", "w")])

    def test_generate_only_refuses_hosted_cases_before_model(self):
        generator = self.generator()
        with self.assertRaises(ValueError):
            execute(self.args("--generate-only", "--test-cases", str(self.directory / "fixture.json")), generator=generator)
        generator.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()

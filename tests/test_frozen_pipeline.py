import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

from builder.build import execute, make_workflow_prompt, PROJECT, MAX_REPAIRS
from builder.contract import compile_policy_contract
from builder.runtime import CodexGenerator, Deadline, DeadlineExceeded, DISABLED_FEATURES, GenerationError, Trace
import test_builder
from test_generation_catalog import bundled_models
from test_builder import fixture, contract, plan, BRIEF_TEXT, PROFILE


class FrozenPipelineTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator
    def test_invalid_execution_shape_is_repaired_before_contract_freeze(self):
        invalid = fixture()
        next(rule for rule in invalid["policy"]["rules"] if rule["kind"] == "product_order")["value"] = "ambiguous sort prose"
        generator = self.generator()
        generator.generate.side_effect = [invalid, fixture()]
        generator.generate.return_value = fixture()
        result = execute(self.args("--generate-only"), generator=generator,
                         client_factory=lambda **kw: self.fail("Freya touched"))
        self.assertEqual(result["policy_repairs"], 1)
        self.assertEqual(generator.generate_plan.call_count, 1)
        saved = json.loads((PROJECT / result["run_directory"] / "policy.json").read_text())
        self.assertEqual(saved, fixture()["policy"])

    def test_resumed_invalid_execution_profile_stops_before_plan_generation(self):
        invalid = fixture()["policy"]
        next(rule for rule in invalid["rules"] if rule["kind"] == "product_order")["value"] = "ambiguous sort prose"
        policy_path = self.directory / "policy.json"
        policy_path.write_text(json.dumps(invalid))
        generator = self.generator()
        with self.assertRaises(ValueError):
            execute(self.args("--generate-only", "--policy-file", str(policy_path)), generator=generator)
        generator.generate.assert_not_called()
        generator.generate_plan.assert_not_called()

    def test_missing_execution_rules_exhaust_extraction_repairs_before_freeze(self):
        extracted = fixture()
        extracted["policy"]["rules"] = [rule for rule in extracted["policy"]["rules"]
                                         if rule["kind"] not in {"product_order", "identity_close"}]
        generator = self.generator()
        generator.generate.return_value = extracted
        with self.assertRaisesRegex(ValueError, "Policy extraction failed after bounded repairs"):
            execute(self.args(), generator=generator,
                    client_factory=lambda **kw: self.fail("Freya touched"))
        self.assertEqual(generator.generate.call_count, MAX_REPAIRS + 1)
        generator.generate_plan.assert_not_called()
        run = next((self.directory / "out").iterdir())
        self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "failed")
        for attempt in range(1, MAX_REPAIRS + 2):
            rejected = json.loads((run / f"policy.rejected-{attempt}.json").read_text())
            self.assertEqual(rejected["policy"], extracted["policy"])
            for kind in ("product_order", "identity_close"):
                self.assertTrue(any(kind in error for error in rejected["validation_errors"]))
        for name in ("policy.json", "policy.contract.json", "execution-requirements.json", "plan.json", "workflow.json", "attempts"):
            self.assertFalse((run / name).exists(), name)
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        self.assertFalse(any(event["event"] == "policy_frozen" for event in events))
        self.assertEqual([event["passed"] for event in events if event["event"] == "policy_validation"],
                         [False] * (MAX_REPAIRS + 1))
        self.assertEqual(events[-1]["status"], "failed")

    def test_export_finishing_after_global_deadline_cannot_report_success(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        class LateExport(test_builder.FakeClient):
            def export_workflow(self, deployment, path):
                result = super().export_workflow(deployment, path)
                now[0] = 1801
                return result
        generator = self.generator()
        generator.generate.return_value = fixture()
        with self.assertRaises(DeadlineExceeded):
            execute(self.args(), generator=generator, deadline=deadline, client_factory=LateExport)
        run = next((self.directory / "out").iterdir())
        self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "failed")
        self.assertTrue((run / "submission.workflow.json").exists())
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        self.assertEqual([event["status"] for event in events if event.get("event") == "end"], ["failed"])

    def test_contract_is_extracted_once_across_workflow_repairs(self):
        generator = self.generator()
        generator.generate.return_value = fixture()
        bad = copy.deepcopy(plan())
        bad['initial_node'] = 'missing'
        attempts = []
        def workflow(prompt, *, contract_sha256):
            attempts.append(json.loads(prompt.split('\n\nAUTHORIZED INPUTS:\n', 1)[1]))
            return {'plan': bad if len(attempts) == 1 else plan(), 'contract_sha256': contract_sha256}
        generator.generate_plan.side_effect = workflow
        result = execute(self.args('--generate-only'), generator=generator,
                         client_factory=lambda **kw: self.fail('Freya touched'))
        self.assertEqual(generator.generate.call_count, 1)
        self.assertEqual(result['workflow_repairs'], 1)
        self.assertEqual(result['policy_repairs'], 0)
        self.assertEqual(attempts[0]['policy_contract_sha256'], attempts[1]['policy_contract_sha256'])
        self.assertNotIn('previous_candidate', attempts[1]['repair'])
        self.assertNotIn('policy', attempts[1]['repair'])
        self.assertIn('previous_plan', attempts[1]['repair'])
        saved = json.loads((PROJECT / result['run_directory'] / 'policy.json').read_text())
        self.assertEqual(saved, fixture()['policy'])

    def test_changed_hash_or_returned_policy_stops_without_regeneration(self):
        for extra in ('changed_hash', 'replacement_policy'):
            with self.subTest(extra=extra):
                generator = self.generator()
                generator.generate.return_value = fixture()
                def workflow(prompt, *, contract_sha256):
                    value = {'plan': plan(), 'contract_sha256': contract_sha256}
                    if extra == 'changed_hash': value['contract_sha256'] = '0' * 64
                    else: value['policy'] = {'goal': 'changed by repair'}
                    return value
                generator.generate_plan.side_effect = workflow
                with self.assertRaisesRegex(ValueError, 'cannot replace policy'):
                    execute(self.args('--generate-only'), generator=generator,
                            client_factory=lambda **kw: self.fail('Freya touched'))
                self.assertEqual(generator.generate.call_count, 1)
                self.assertEqual(generator.generate_plan.call_count, 1)

    def test_workflow_context_contains_only_active_values_and_fingerprints(self):
        policy = fixture()['policy']
        source = {'id': 'brief-1', 'file': 'brief.md', 'sha256': policy['sources'][0]['sha256'],
                  'text': BRIEF_TEXT}
        compiled = compile_policy_contract(policy, [source], contract())
        payload = json.loads(make_workflow_prompt(compiled, contract()).split('\n\nAUTHORIZED INPUTS:\n', 1)[1])
        self.assertEqual(payload['active_rules'], {'purpose': 'Banking contact', **PROFILE})
        self.assertNotIn('text', payload['sources'][0])
        self.assertNotIn('provenance', payload)
        self.assertNotIn('runtime_references', payload)
        self.assertNotIn('Use the permitted banking contact flow.', json.dumps(payload))

    def test_explicit_policy_resume_is_revalidated_without_reextracting(self):
        policy_path = self.directory / 'policy.json'
        policy_path.write_text(json.dumps(fixture()['policy']))
        generator = self.generator()
        generator.generate.return_value = fixture()
        result = execute(self.args('--generate-only', '--policy-file', str(policy_path)), generator=generator,
                         client_factory=lambda **kw: self.fail('Freya touched'))
        generator.generate.assert_not_called()
        self.assertEqual(generator.generate_plan.call_count, 1)
        self.assertEqual(result['policy_origin'], 'provided_and_revalidated')
        invalid = fixture()['policy']
        invalid['sources'][0]['sha256'] = '0' * 64
        policy_path.write_text(json.dumps(invalid))
        generator.reset_mock()
        with self.assertRaises(ValueError):
            execute(self.args('--generate-only', '--policy-file', str(policy_path)), generator=generator)
        generator.generate.assert_not_called()
        generator.generate_plan.assert_not_called()

    def test_protected_policy_resume_never_reaches_model(self):
        path = self.directory / 'benchmarks' / 'policy.json'
        path.parent.mkdir()
        path.write_text(json.dumps(fixture()['policy']))
        generator = self.generator()
        with self.assertRaisesRegex(ValueError, 'Protected evaluator'):
            execute(self.args('--generate-only', '--policy-file', str(path)), generator=generator)
        generator.generate.assert_not_called()
        generator.generate_plan.assert_not_called()

    def test_workflow_finishing_after_global_deadline_cannot_report_success(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        generator = self.generator()
        generator.generate.return_value = fixture()
        def workflow(prompt, *, contract_sha256):
            now[0] = 1801
            return {'plan': plan(), 'contract_sha256': contract_sha256}
        generator.generate_plan.side_effect = workflow
        with self.assertRaises(DeadlineExceeded):
            execute(self.args('--generate-only'), generator=generator, deadline=deadline)
        manifest = json.loads(next((self.directory / 'out').glob('*/manifest.json')).read_text())
        self.assertEqual(manifest['status'], 'failed')


class WorkflowWireTests(unittest.TestCase):
    def generator(self, text):
        (PROJECT / '.test-tmp').mkdir(exist_ok=True)
        import tempfile
        temporary = tempfile.TemporaryDirectory(dir=PROJECT / '.test-tmp')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        calls = []
        def run(argv, **kwargs):
            if '--help' in argv:
                out = '--ignore-user-config --ignore-rules --ephemeral --output-schema --strict-config'
            elif argv[1:4] == ['debug', 'models', '--bundled']:
                out = json.dumps(bundled_models())
            elif 'features' in argv:
                out = '\n'.join(name + ' stable true' for name in (*DISABLED_FEATURES, 'skip_host_skill_discovery'))
            else:
                schema = json.loads(Path(argv[argv.index('--output-schema') + 1]).read_text())
                if 'available_tools' in schema['properties']:
                    response = {'available_tools': [], 'loaded_markers': [],
                                'forbidden_content': 'UNAVAILABLE', 'environment_content': 'UNAVAILABLE'}
                    out = json.dumps({'type': 'thread.started', 'thread_id': 'synthetic-thread'}) + '\n' + json.dumps({'type': 'turn.started'}) + '\n' + json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(response)}})
                    out += '\n' + json.dumps({'type': 'turn.completed'})
                else:
                    calls.append((schema, kwargs))
                    events = [{'type': 'thread.started', 'thread_id': 'synthetic-thread'}, {'type': 'turn.started'},
                              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}},
                              {'type': 'turn.completed'}]
                    out = '\n'.join(json.dumps(event) for event in events)
            return subprocess.CompletedProcess(argv, 0, out, '')
        return CodexGenerator(directory, Trace(directory / 'trace.jsonl'), Deadline(), run=run), calls

    def test_wire_schema_locks_contract_and_does_not_request_policy(self):
        sha = 'a' * 64
        generator, calls = self.generator(json.dumps({'plan_json': json.dumps({'nodes': {}}), 'contract_sha256': sha}))
        self.assertEqual(generator.generate_plan('approved contract', contract_sha256=sha, max_seconds=600),
                         {'plan': {'nodes': {}}, 'contract_sha256': sha})
        schema, kwargs = calls[0]
        self.assertEqual(set(schema['properties']), {'plan_json', 'contract_sha256'})
        self.assertEqual(schema['properties']['contract_sha256']['enum'], [sha])
        self.assertLessEqual(kwargs['timeout'], 600)
        self.assertEqual(kwargs['input'], 'approved contract')

    def test_wrong_hash_extra_policy_duplicate_keys_and_nonfinite_values_rejected(self):
        sha = 'a' * 64
        for wire in (
            json.dumps({'plan_json': '{}', 'contract_sha256': 'b' * 64}),
            json.dumps({'plan_json': '{}', 'contract_sha256': sha, 'policy_json': '{}'}),
            json.dumps({'plan_json': '{"nodes":{},"nodes":{}}', 'contract_sha256': sha}),
            json.dumps({'plan_json': '{"nodes":NaN}', 'contract_sha256': sha}),
        ):
            with self.subTest(wire=wire):
                generator, _ = self.generator(wire)
                with self.assertRaises(GenerationError):
                    generator.generate_plan('approved contract', contract_sha256=sha)

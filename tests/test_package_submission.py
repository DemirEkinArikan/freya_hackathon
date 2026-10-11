import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from builder.safety import digest
from builder.source_provenance import capture_source_provenance, bind_run_provenance
from tools import package_submission as package


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root_patch = patch.object(package, "ROOT", self.root)
        self.root_patch.start()
        self.origin_patch = patch.object(package, 'repository_url', return_value='https://github.com/test/example')
        self.origin_patch.start()

    def tearDown(self):
        self.origin_patch.stop(); self.root_patch.stop(); self.temp.cleanup()

    def test_allowlist_excludes_private_notes_env_and_generated_data(self):
        for name in ("README.md", ".env", "kickoff_email.txt", "meeting.md", "builder/build.py", "artifacts/raw.json", "benchmarks/holdout/private.json", "tools/.env.production.json", "builder/.env.local.txt"):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("safe")
        included = {str(p.relative_to(self.root)) for p in package.source_files()}
        self.assertEqual(included, {"README.md", "builder/build.py"})

    def test_external_and_symlink_paths_are_rejected(self):
        outside = Path(self.temp.name).parent / (self.root.name + "-outside")
        outside.write_text("safe")
        try:
            (self.root / "link.json").symlink_to(outside)
            with self.assertRaises(ValueError): package.safe_path(outside)
            with self.assertRaises(ValueError): package.safe_path(self.root / "link.json")
        finally:
            outside.unlink()

    def test_reviewed_shell_tools_are_reproduced_but_credential_scripts_are_excluded(self):
        for name in ('tools/diagnostic.sh', 'tools/env.sh', 'build-the-builder/env.sh'):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('#!/bin/sh\n')
        self.assertEqual({path.relative_to(self.root).as_posix() for path in package.source_files()}, {'tools/diagnostic.sh'})

    def evidence(self, status="exported", mode="live"):
        # This is fabricated consistency evidence. It never establishes a live build.
        (self.root / 'builder').mkdir(exist_ok=True)
        (self.root / 'benchmarks').mkdir(exist_ok=True)
        (self.root / 'builder/build.py').write_text('# Synthetic runtime fixture\n')
        config = {'mode': mode, 'model': 'synthetic-test-model', 'reasoning_effort': 'high',
                  'codex_executable': 'codex', 'deadline_seconds': 1800, 'export_reserve_seconds': 300,
                  'max_repairs': 2, 'controller_dependency_injection': False}
        (self.root / 'api.json').write_text('{}')
        for name in ("hidden-1", "hidden-2"):
            brief_name = 'brief.md' if name == 'hidden-1' else 'brief-2.md'
            brief_text = 'Synthetic policy input ' + name
            (self.root / brief_name).write_text(brief_text)
            sources = [{'id': 'brief-1', 'file': brief_name, 'sha256': digest(brief_text)}]
            wf = {'workflow_id': 'w-' + name, 'agent_id': 'a-' + name,
                  'workflow': {'initial_node': 'finish', 'nodes': {},
                               'agent_config': {'llmConfig': {'provider': 'experimental-5', 'model': 'experimental-5'}}}}
            deployment = {'workflow_id': wf['workflow_id'], 'agent_id': wf['agent_id'],
                          'version_id': 'v1-' + name, 'version_number': 1, 'published': True}
            policy = {'synthetic': name}
            contract = {'version': 1, 'sources': sources, 'synthetic': name}
            contract['sha256'] = digest(contract)
            plan = {'synthetic_plan': name}
            bindings, execution = {'synthetic_bindings': name}, {'synthetic_execution': name}
            guards = {'synthetic_guards': name}
            requirements = {'version': 1, 'policy_sha256': digest(policy), 'contract_sha256': contract['sha256'],
                            'openapi_sha256': digest({}), 'actions': ['close_call']}
            requirements['sha256'] = digest(requirements)
            source = capture_source_provenance(self.root, config)
            source['captured_at'] = '2026-10-10T12:00:00Z'
            (self.root / (name + ".json")).write_text(json.dumps(wf))
            # Packaging checks the required organizer trace evidence. The old
            # untimed three-event fixture did not represent an eligible run.
            events = [{"event": "start", "mode": mode, 'model': config['model'], "time": "2026-10-10T12:00:00Z"},
                      {'event': 'inputs', 'sources': sources, 'tool_api_sha256': digest({}), 'time': '2026-10-10T12:00:00Z'},
                      {'event': 'builder_source_snapshot', 'builder_source_sha256': source['builder_source_sha256'],
                       'builder_source_record_sha256': digest(source), 'resolved_config': source['resolved_config'], 'time': '2026-10-10T12:00:00Z'},
                      {'event': 'model_call', 'model': config['model'], 'reasoning_effort': 'high',
                       'purpose': 'Audit generation tools/context with synthetic isolation canaries', 'time': '2026-10-10T12:00:00Z'},
                      self.audit_event(),
                      {'event': 'generation_model_catalog', **self.catalog(), 'time': '2026-10-10T12:00:01Z'},
                      {"event": "model_call", "model": "synthetic-test-model", "purpose": "Synthetic offline trace fixture",
                       "prompt_characters": 100, 'reasoning_effort': 'high',
                       "time": "2026-10-10T12:00:01Z"},
                      {'event': 'action_guard_compilation', 'verified': True, 'plan_sha256': digest(plan),
                       'guard_manifest_sha256': digest(guards), 'time': '2026-10-10T12:00:01Z'},
                      {'event': 'deployment', **deployment, 'attempt': 1, "time": "2026-10-10T12:00:02Z"},
                      {"event": "end", "status": status, "export_sha256": digest(wf), 'remaining_seconds': 1797, "time": "2026-10-10T12:00:03Z"}]
            (self.root / (name + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events))
            run = {'mode': mode, 'status': status, 'sources': sources, 'tool_api_sha256': digest({}),
                   'policy_contract_sha256': contract['sha256'], 'selected_attempt': 1, 'selected_attempt_directory': 'attempts/plan-001',
                   'deployment': deployment, 'export_sha256': digest(wf), 'elapsed_seconds': 3, 'tests': None,
                   'hosted_behavior_verified': False}
            run.update(model=config['model'], reasoning_effort='high', builder_source_sha256=source['builder_source_sha256'],
                       builder_source_record_sha256=digest(source))
            run.update(action_guard_compilation_verified=True, plan_sha256=digest(plan),
                       guard_manifest_sha256=digest(guards), authored_workflow_sha256=digest(wf['workflow']),
                       execution_requirements_sha256=requirements['sha256'])
            attempt = {'number': 1, 'directory': 'attempts/plan-001', 'phase': 'complete', 'status': status,
                       'export_sha256': digest(wf), 'tool_api_sha256': digest({}), 'policy_contract_sha256': contract['sha256'],
                       'policy_sha256': digest(policy), 'plan_sha256': digest(plan), 'workflow_sha256': digest(wf['workflow'])}
            run_path = self.root / name
            attempt_path = run_path / 'attempts/plan-001'
            attempt_path.mkdir(parents=True, exist_ok=True)
            (run_path / 'manifest.json').write_text(json.dumps(run))
            (run_path / 'builder-source.json').write_text(json.dumps(source))
            (run_path / 'build-provenance.json').write_text(json.dumps(bind_run_provenance(source, run, events)))
            (run_path / 'policy.json').write_text(json.dumps(policy))
            (run_path / 'policy.contract.json').write_text(json.dumps(contract))
            (run_path / 'execution-requirements.json').write_text(json.dumps(requirements))
            artifacts = {'plan.json': plan, 'action-bindings.json': bindings, 'execution-policy.json': execution,
                         'guard-manifest.json': guards, 'workflow.json': wf['workflow'],
                         'execution-requirements.json': requirements,
                         'proposal.json': {'plan': plan, 'contract_sha256': contract['sha256']},
                         'guard-context.json': {'plan': plan, 'contract': contract, 'bindings': bindings, 'execution_policy': execution}}
            for filename, value in artifacts.items():
                (attempt_path / filename).write_text(json.dumps(value))
            for filename, value in [('attempt.json', attempt), ('deployment.json', {**deployment, 'workflow': wf['workflow']}), ('export.json', wf)]:
                (attempt_path / filename).write_text(json.dumps(value))
        return {"team": "synthetic", "repo": "https://github.com/test/example", "commit": "a" * 40,
                "run_command": "python3.11 -m builder.build --brief {brief_path} --tool-api {tool_api_path}",
                "workflows": {n: n + ".json" for n in ("hidden-1", "hidden-2")},
                "traces": {n: n + ".jsonl" for n in ("hidden-1", "hidden-2")},
                'runs': {n: n + '/manifest.json' for n in ('hidden-1', 'hidden-2')},
                'inputs': {n: {'briefs': ['brief.md' if n == 'hidden-1' else 'brief-2.md'], 'tool_api': 'api.json'} for n in ('hidden-1', 'hidden-2')}}

    @staticmethod
    def audit_event():
        return {'event': 'generation_isolation_audit', 'time': '2026-10-10T12:00:00Z',
                'status': 'pass', 'failures': [], 'canary_exposure': [], 'response_valid': True,
                'model_reported_tools': [], 'model_reported_tool_count': 0, 'unrecognized_tool_name_count': 0,
                'omitted_skill_count': 0, 'returncode': 0, 'completed_turn_count': 1,
                'observed_item_types': ['agent_message'], 'model_catalog': PackageTests.catalog()}

    @staticmethod
    def catalog():
        return {'source': 'installed_cli_bundled', 'model': 'synthetic-test-model',
                'source_model_sha256': 'c' * 64, 'generation_catalog_sha256': 'd' * 64,
                'restricted_capabilities': {'shell_type': 'disabled', 'apply_patch_tool_type': None,
                    'experimental_supported_tools': [], 'node_repl_disabled': True,
                    'tool_mode': 'direct', 'supports_search_tool': False}}

    def rebind_provenance(self, name='hidden-1'):
        run_path = self.root / name
        source = json.loads((run_path / 'builder-source.json').read_text())
        run = json.loads((run_path / 'manifest.json').read_text())
        events = [json.loads(line) for line in (self.root / (name + '.jsonl')).read_text().splitlines()]
        (run_path / 'build-provenance.json').write_text(json.dumps(bind_run_provenance(source, run, events)))

    def test_export_and_trace_must_match_and_failed_run_is_not_ready(self):
        with patch.object(subprocess, "run", return_value=SimpleNamespace(stdout="a" * 40)):
            self.assertEqual(len(package.check_manifest(self.evidence())), 42)
            with self.assertRaisesRegex(ValueError, "failed traces"):
                package.check_manifest(self.evidence(status="failed"))
            with self.assertRaisesRegex(ValueError, "generated-only"):
                package.check_manifest(self.evidence(mode="generate-only"))
            data = self.evidence()
            (self.root / "hidden-1.json").write_text(json.dumps({"workflow_id": "changed", "agent_id": "a", "workflow": {"nodes": {}}}))
            with self.assertRaisesRegex(ValueError, "does not match"):
                package.check_manifest(data)

    def test_real_credential_like_value_is_rejected_without_echo(self):
        p = self.root / "README.md"
        p.write_text("Authorization: Bearer REAL_TEST_ONLY_CREDENTIAL_123456")
        with self.assertRaisesRegex(ValueError, "requires review") as result:
            package.check_content(p)
        self.assertNotIn("REAL_TEST_ONLY", str(result.exception))

    def test_package_manifest_points_to_archived_evidence(self):
        (self.root / 'README.md').write_text('safe')
        manifest_path = self.root / 'submission.json'
        manifest_path.write_text(json.dumps(self.evidence()))
        output = self.root / 'artifacts' / 'package.zip'
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)), \
             patch.object(package, 'verify_committed'):
            package.main(['--manifest', str(manifest_path), '--output', str(output)])
        with zipfile.ZipFile(output) as archive:
            metadata = json.loads(archive.read('package-index.json'))
            for kind in ('workflows', 'traces', 'runs'):
                for entry in metadata['submission'][kind].values():
                    self.assertIn(entry, archive.namelist())
            for inputs in metadata['submission']['inputs'].values():
                for entry in inputs['briefs'] + [inputs['tool_api']]:
                    self.assertIn(entry, archive.namelist())
            for entry, fingerprint in metadata['evidence_files'].items():
                self.assertEqual(package.hashlib.sha256(archive.read(entry)).hexdigest(), fingerprint)
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))

    def test_structured_secrets_in_evidence_are_rejected_even_under_tests(self):
        for filename, content in [('export.json', {'headers': {'Authorization': 'opaque-private-value'}}),
                                  ('trace.jsonl', {'event': 'test', 'token': 'opaque-private-value'})]:
            path = self.root / 'tests' / filename
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(content))
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'requires review') as result:
                package.check_content(path, evidence=True)
            self.assertNotIn('opaque-private-value', str(result.exception))

    def test_input_fingerprints_cannot_drift(self):
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)):
            for filename, value in [('brief.md', 'Changed policy'), ('api.json', '{"changed":true}')]:
                data = self.evidence()
                (self.root / filename).write_text(value)
                with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'Input fingerprints'):
                    package.check_manifest(data)

    def test_selected_attempt_manifest_and_retained_evidence_must_agree(self):
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)):
            for filename, field, value in [
                ('manifest.json', 'selected_attempt', True),
                ('manifest.json', 'selected_attempt_directory', '../other'),
                ('manifest.json', 'export_sha256', '0' * 64),
                ('attempts/plan-001/attempt.json', 'status', 'failed'),
                ('attempts/plan-001/export.json', 'agent_id', 'another-agent'),
                ('attempts/plan-001/deployment.json', 'version_id', 'another-version'),
            ]:
                data = self.evidence()
                path = self.root / 'hidden-1' / filename
                record = json.loads(path.read_text()); record[field] = value
                path.write_text(json.dumps(record))
                with self.subTest(filename=filename, field=field), self.assertRaises(ValueError):
                    package.check_manifest(data)

    def test_deadline_evidence_cannot_be_missing_negative_nonfinite_or_over_budget(self):
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)):
            for value in (None, -1, True, '3', 1800.001, 0, 10):
                data = self.evidence()
                path = self.root / 'hidden-1/manifest.json'
                record = json.loads(path.read_text()); record['elapsed_seconds'] = value
                path.write_text(json.dumps(record))
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'deadline evidence'):
                    package.check_manifest(data)

    def test_final_manifest_requires_full_commit_and_run_evidence(self):
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)):
            for key in ('runs', 'inputs'):
                data = self.evidence(); data.pop(key)
                with self.subTest(key=key), self.assertRaises(ValueError):
                    package.check_manifest(data)
            data = self.evidence(); data['commit'] = 'a'
            with self.assertRaisesRegex(ValueError, 'commit'):
                package.check_manifest(data)

    def test_final_trace_test_attempt_and_retained_result_must_agree(self):
        from test_package_trace_validation import evidence
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)):
            for mutation in ('none', 'attempt', 'directory', 'result', 'claim', 'boolean_count'):
                data = self.evidence()
                workflow, rows = evidence(True)
                workflow['workflow']['agent_config'] = {'llmConfig': {'provider': 'experimental-5', 'model': 'experimental-5'}}
                rows[-1]['export_sha256'] = digest(workflow)
                for checked in (rows[-2]['result']['deployment_pin']['before'], rows[-2]['result']['deployment_pin']['after']):
                    checked['graph_sha256'] = digest(workflow['workflow'])
                run_path = self.root / 'hidden-1/manifest.json'
                run = json.loads(run_path.read_text())
                rows.insert(1, {'event': 'inputs', 'time': rows[0]['time'], 'sources': run['sources'], 'tool_api_sha256': run['tool_api_sha256']})
                original = [json.loads(line) for line in (self.root / 'hidden-1.jsonl').read_text().splitlines()]
                rows[2:2] = original[2:5]
                call_time = rows[5]['time']
                rows[5:5] = [{**original[5], 'time': call_time}]
                rows[7:7] = [{**original[7], 'time': call_time}]
                rows[0]['model'] = run['model']
                for row in rows:
                    if row.get('event') == 'model_call':
                        row.update(model=run['model'], reasoning_effort='high')
                rows[-1]['remaining_seconds'] = 1796
                rows[-2].update(attempt=1, attempt_directory='attempts/plan-001')
                rows[-3]['attempt'] = 1
                deployment = {key: rows[-3][key] for key in ('workflow_id', 'agent_id', 'version_id', 'version_number', 'published')}
                run.update(deployment=deployment, export_sha256=digest(workflow), tests=rows[-2]['result'], hosted_behavior_verified=True)
                run['authored_workflow_sha256'] = digest(workflow['workflow'])
                attempt_path = self.root / 'hidden-1/attempts/plan-001'
                attempt = json.loads((attempt_path / 'attempt.json').read_text())
                attempt['export_sha256'] = digest(workflow)
                attempt['workflow_sha256'] = digest(workflow['workflow'])
                (attempt_path / 'attempt.json').write_text(json.dumps(attempt))
                (attempt_path / 'workflow.json').write_text(json.dumps(workflow['workflow']))
                (attempt_path / 'export.json').write_text(json.dumps(workflow))
                (attempt_path / 'deployment.json').write_text(json.dumps({**deployment, 'workflow': workflow['workflow']}))
                (self.root / 'hidden-1.json').write_text(json.dumps(workflow))
                if mutation == 'attempt': rows[-2]['attempt'] = 2
                if mutation == 'directory': rows[-2]['attempt_directory'] = 'attempts/plan-002'
                if mutation == 'claim': run['hosted_behavior_verified'] = False
                run_path.write_text(json.dumps(run))
                (self.root / 'hidden-1.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
                tests = dict(run['tests'])
                if mutation == 'result': tests['status'] = 'failed'
                if mutation == 'boolean_count': tests['passed'] = True
                (attempt_path / 'tests.json').write_text(json.dumps(tests))
                self.rebind_provenance()
                with self.subTest(mutation=mutation):
                    if mutation == 'none':
                        package.check_manifest(data)
                    else:
                        with self.assertRaises(ValueError): package.check_manifest(data)

    def test_final_check_uses_real_git_bytes_for_source_runs_and_inputs(self):
        data = self.evidence()
        (self.root / 'README.md').write_text('safe')
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()
        git('init', '-q'); git('add', '.')
        git('-c', 'user.name=Synthetic Test', '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'Synthetic packaging evidence')
        data['commit'] = git('rev-parse', 'HEAD')
        manifest = self.root / 'submission.json'; manifest.write_text(json.dumps(data))
        package.main(['--check', '--manifest', str(manifest)])
        for name in ('README.md', 'brief.md', 'hidden-1/attempts/plan-001/export.json'):
            path = self.root / name
            before = path.read_bytes(); path.write_bytes(before + b'\n')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'committed checkout|Input fingerprints'):
                package.main(['--check', '--manifest', str(manifest)])
            path.write_bytes(before)

    def test_output_cannot_overwrite_packaged_source(self):
        source = self.root / 'README.md'; source.write_text('safe')
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            package.main(['--source-only', '--output', str(source)])
        self.assertEqual(source.read_text(), 'safe')

    def test_local_team_key_is_checked_without_exporting_environment(self):
        (self.root / '.env').write_text('FREYA_BANK_TEAM_KEY=synthetic-unpatterned-team-key\n')
        source = self.root / 'README.md'
        source.write_text('synthetic-unpatterned-team-key')
        with self.assertRaisesRegex(ValueError, 'requires review'):
            package.check_content(source)

    def test_short_overridden_local_credentials_and_passwords_remain_private(self):
        (self.root / '.env').write_text('FREYA_API_KEY=opaque7\n')
        source = self.root / 'README.md'
        with patch.dict(package.os.environ, {'FREYA_API_KEY': 'different-live-value', 'service_password': 'pass9'}):
            for value in ('opaque7', 'different-live-value', 'pass9'):
                source.write_text(value)
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'requires review') as result:
                    package.check_content(source)
                self.assertNotIn(value, str(result.exception))

    def test_final_package_bytes_must_match_git_commit(self):
        source = self.root / 'README.md'
        source.write_text('committed source')
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'committed source')):
            package.verify_committed([source])
        for result in (SimpleNamespace(returncode=1, stdout=b''), SimpleNamespace(returncode=0, stdout=b'older source')):
            with patch.object(subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(ValueError, 'match the committed checkout'):
                    package.verify_committed([source])


if __name__ == "__main__":
    unittest.main()

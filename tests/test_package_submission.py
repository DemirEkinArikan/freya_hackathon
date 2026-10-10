import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from builder.safety import digest
from tools import package_submission as package


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root_patch = patch.object(package, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop(); self.temp.cleanup()

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

    def evidence(self, status="exported", mode="live"):
        wf = {"workflow_id": "w", "agent_id": "a", "workflow": {"initial_node": "finish", "nodes": {}}}
        (self.root / 'brief.md').write_text('Synthetic policy input')
        (self.root / 'api.json').write_text('{}')
        sources = [{'id': 'brief-1', 'file': 'brief.md', 'sha256': digest('Synthetic policy input')}]
        deployment = {'workflow_id': 'w', 'agent_id': 'a', 'version_id': 'v1', 'version_number': 1, 'published': True}
        for name in ("hidden-1", "hidden-2"):
            (self.root / (name + ".json")).write_text(json.dumps(wf))
            # Packaging checks the required organizer trace evidence. The old
            # untimed three-event fixture did not represent an eligible run.
            events = [{"event": "start", "mode": mode, "time": "2026-10-10T12:00:00Z"},
                      {'event': 'inputs', 'sources': sources, 'tool_api_sha256': digest({}), 'time': '2026-10-10T12:00:00Z'},
                      {"event": "model_call", "model": "synthetic-test-model", "purpose": "Synthetic offline trace fixture",
                       "prompt_characters": 100,
                       "time": "2026-10-10T12:00:01Z"},
                      {"event": "deployment", "workflow_id": "w", "agent_id": "a", "version_id": "v1", "version_number": 1,
                       "published": True, 'attempt': 1, "time": "2026-10-10T12:00:02Z"},
                      {"event": "end", "status": status, "export_sha256": digest(wf), 'remaining_seconds': 1797, "time": "2026-10-10T12:00:03Z"}]
            (self.root / (name + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events))
            run = {'mode': mode, 'status': status, 'sources': sources, 'tool_api_sha256': digest({}),
                   'policy_contract_sha256': 'b' * 64, 'selected_attempt': 1, 'selected_attempt_directory': 'attempts/plan-001',
                   'deployment': deployment, 'export_sha256': digest(wf), 'elapsed_seconds': 3, 'tests': None,
                   'hosted_behavior_verified': False}
            attempt = {'number': 1, 'directory': 'attempts/plan-001', 'phase': 'complete', 'status': status,
                       'export_sha256': digest(wf), 'tool_api_sha256': digest({}), 'policy_contract_sha256': 'b' * 64}
            run_path = self.root / name
            attempt_path = run_path / 'attempts/plan-001'
            attempt_path.mkdir(parents=True, exist_ok=True)
            (run_path / 'manifest.json').write_text(json.dumps(run))
            for filename, value in [('attempt.json', attempt), ('deployment.json', {**deployment, 'workflow': wf['workflow']}), ('export.json', wf)]:
                (attempt_path / filename).write_text(json.dumps(value))
        return {"team": "synthetic", "repo": "https://github.com/test/example", "commit": "a" * 40,
                "run_command": "python3.11 builder/build.py --brief {brief_path} --tool-api {tool_api_path}",
                "workflows": {n: n + ".json" for n in ("hidden-1", "hidden-2")},
                "traces": {n: n + ".jsonl" for n in ("hidden-1", "hidden-2")},
                'runs': {n: n + '/manifest.json' for n in ('hidden-1', 'hidden-2')},
                'inputs': {n: {'briefs': ['brief.md'], 'tool_api': 'api.json'} for n in ('hidden-1', 'hidden-2')}}

    def test_export_and_trace_must_match_and_failed_run_is_not_ready(self):
        with patch.object(subprocess, "run", return_value=SimpleNamespace(stdout="a" * 40)):
            self.assertEqual(len(package.check_manifest(self.evidence())), 16)
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
            for mutation in ('none', 'attempt', 'directory', 'result', 'claim'):
                data = self.evidence()
                workflow, rows = evidence(True)
                run_path = self.root / 'hidden-1/manifest.json'
                run = json.loads(run_path.read_text())
                rows.insert(1, {'event': 'inputs', 'time': rows[0]['time'], 'sources': run['sources'], 'tool_api_sha256': run['tool_api_sha256']})
                rows[-1]['remaining_seconds'] = 1796
                rows[-2].update(attempt=1, attempt_directory='attempts/plan-001')
                rows[-3]['attempt'] = 1
                deployment = {key: rows[-3][key] for key in ('workflow_id', 'agent_id', 'version_id', 'version_number', 'published')}
                run.update(deployment=deployment, export_sha256=digest(workflow), tests=rows[-2]['result'], hosted_behavior_verified=True)
                attempt_path = self.root / 'hidden-1/attempts/plan-001'
                attempt = json.loads((attempt_path / 'attempt.json').read_text())
                attempt['export_sha256'] = digest(workflow)
                (attempt_path / 'attempt.json').write_text(json.dumps(attempt))
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
                (attempt_path / 'tests.json').write_text(json.dumps(tests))
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

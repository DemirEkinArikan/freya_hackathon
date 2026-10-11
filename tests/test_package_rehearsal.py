"""Synthetic two-build packaging with real Git binding; no live evidence claim."""
import contextlib
import hashlib
import io
import json
import subprocess
import unittest
import zipfile

from builder.safety import digest
from tools import package_submission as package
import test_package_submission as fixtures


class CommittedPackageRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.manifest = self.fixture.evidence()
        self.fixture.origin_patch.stop()

    def git(self, *arguments):
        return subprocess.run(['git', *arguments], cwd=self.root, check=True,
                              text=True, capture_output=True).stdout.strip()

    def commit_evidence(self):
        self.git('init', '-q')
        self.git('remote', 'add', 'origin', 'https://github.com/test/example.git')
        self.git('add', '.')
        self.git('-c', 'user.name=Synthetic package check', '-c',
                 'user.email=synthetic@example.invalid', 'commit', '-qm',
                 'Retain synthetic source and two selected build records')
        self.manifest['commit'] = self.git('rev-parse', 'HEAD')
        path = self.root / 'submission.json'
        path.write_text(json.dumps(self.manifest))
        return path

    def select_second_attempt(self):
        run_path = self.root / 'hidden-2'
        attempt_path = run_path / 'attempts/plan-002'
        (run_path / 'attempts/plan-001').rename(attempt_path)
        run_file = run_path / 'manifest.json'
        run = json.loads(run_file.read_text())
        run.update(selected_attempt=2, selected_attempt_directory='attempts/plan-002')
        run_file.write_text(json.dumps(run))
        record_path = attempt_path / 'attempt.json'
        record = json.loads(record_path.read_text())
        record.update(number=2, directory='attempts/plan-002')
        record_path.write_text(json.dumps(record))
        trace_path = self.root / 'hidden-2.jsonl'
        events = [json.loads(line) for line in trace_path.read_text().splitlines()]
        for event in events:
            if event['event'] == 'deployment':
                event['attempt'] = 2
        trace_path.write_text('\n'.join(json.dumps(event) for event in events))
        self.fixture.rebind_provenance('hidden-2')
        failed = run_path / 'attempts/plan-001/attempt.json'
        failed.parent.mkdir()
        failed.write_text(json.dumps({'number': 1, 'status': 'failed',
                                      'phase': 'validation', 'reason': 'Synthetic failure'}))
        return failed

    def test_two_selected_attempts_package_and_real_commit_drift_rejection(self):
        failed = self.select_second_attempt()
        original_failure = failed.read_bytes()
        manifest_path = self.commit_evidence()
        output = self.root / 'artifacts/synthetic-two-builds.zip'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(package.main(['--check', '--manifest', str(manifest_path)]), 0)
            self.assertEqual(package.main(['--manifest', str(manifest_path),
                                           '--output', str(output)]), 0)
        original_archive = output.read_bytes()
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            index = json.loads(archive.read('package-index.json'))
            self.assertFalse(index['source_only'])
            self.assertEqual(index['submission']['commit'], self.manifest['commit'])
            inventory = {**index['source_files'], **index['evidence_files']}
            self.assertEqual(set(archive.namelist()), set(inventory) | {'package-index.json'})
            for name, expected in inventory.items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
            for slot, number in [('hidden-1', 1), ('hidden-2', 2)]:
                run = json.loads(archive.read(index['submission']['runs'][slot]))
                self.assertEqual(run['selected_attempt'], number)
                selected = f'runs/{slot}/attempts/plan-{number:03d}/export.json'
                workflow = json.loads(archive.read(index['submission']['workflows'][slot]))
                self.assertEqual(digest(json.loads(archive.read(selected))), digest(workflow))
                self.assertIn(index['submission']['traces'][slot], inventory)
                for path in index['submission']['inputs'][slot]['briefs']:
                    self.assertIn(path, inventory)
                self.assertIn(index['submission']['inputs'][slot]['tool_api'], inventory)
            self.assertNotIn('runs/hidden-2/attempts/plan-001/attempt.json', inventory)
        self.assertEqual(failed.read_bytes(), original_failure)

        source = self.root / 'builder/build.py'
        source.write_text('# Drift after evidence commit\n')
        with self.assertRaisesRegex(ValueError, 'runtime source inventory|committed checkout'):
            package.main(['--manifest', str(manifest_path), '--output', str(output)])
        self.assertEqual(output.read_bytes(), original_archive)
        self.assertEqual(failed.read_bytes(), original_failure)


if __name__ == '__main__':
    unittest.main()

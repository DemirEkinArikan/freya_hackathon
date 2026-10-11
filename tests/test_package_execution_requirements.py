"""Original execution-requirements records in final packages; synthetic, never live proof."""
import contextlib
import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from builder.safety import digest
from tools import package_submission as package
import test_package_submission as fixtures

RECORDS = ('execution-requirements.json', 'attempts/plan-001/execution-requirements.json')


class PackageExecutionRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.PackageTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.root = self.helper.root
        self.directory = self.root / 'hidden-1'
        self.manifest = self.helper.evidence()
        git = patch.object(package.subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40))
        git.start()
        self.addCleanup(git.stop)

    def record(self):
        return json.loads((self.directory / RECORDS[0]).read_text())

    def write_records(self, record, names=RECORDS, *, bind_manifest=False):
        # Rehash, so only the relation under test differs.
        record = {key: value for key, value in record.items() if key != 'sha256'}
        record['sha256'] = digest(record)
        for name in names:
            (self.directory / name).write_text(json.dumps(record))
        if bind_manifest:
            run = json.loads((self.directory / 'manifest.json').read_text())
            run['execution_requirements_sha256'] = record['sha256']
            (self.directory / 'manifest.json').write_text(json.dumps(run))
            self.helper.rebind_provenance()

    def assert_rejected(self):
        with self.assertRaisesRegex(ValueError, 'execution requirements'):
            package.check_manifest(self.manifest)

    def test_both_records_are_archived_for_each_build(self):
        (self.root / 'README.md').write_text('safe')
        manifest_path = self.root / 'submission.json'
        manifest_path.write_text(json.dumps(self.manifest))
        output = self.root / 'artifacts/package.zip'
        with patch.object(package, 'verify_committed'), contextlib.redirect_stdout(io.StringIO()):
            package.main(['--manifest', str(manifest_path), '--output', str(output)])
        with zipfile.ZipFile(output) as archive:
            evidence = json.loads(archive.read('package-index.json'))['evidence_files']
            for slot in ('hidden-1', 'hidden-2'):
                run = json.loads(archive.read(f'runs/{slot}/manifest.json'))
                for name in RECORDS:
                    with self.subTest(slot=slot, name=name):
                        raw = archive.read(f'runs/{slot}/{name}')
                        self.assertEqual(package.hashlib.sha256(raw).hexdigest(), evidence[f'runs/{slot}/{name}'])
                        self.assertEqual(json.loads(raw)['sha256'], run['execution_requirements_sha256'])

    def test_missing_record_fails_closed(self):
        for name in RECORDS:
            path = self.directory / name
            contents = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'does not exist'):
                package.check_manifest(self.manifest)
            path.write_bytes(contents)

    def test_attempt_snapshot_must_equal_the_frozen_record(self):
        self.write_records({**self.record(), 'actions': ['add_note', 'close_call']}, RECORDS[1:])
        self.assert_rejected()

    def test_stale_self_fingerprint_is_rejected(self):
        stale = {**self.record(), 'actions': ['add_note', 'close_call']}
        for name in RECORDS:
            (self.directory / name).write_text(json.dumps(stale))
        self.assert_rejected()

    def test_record_must_equal_the_manifest_fingerprint(self):
        self.write_records({**self.record(), 'actions': ['add_note', 'close_call']})
        self.assert_rejected()
        self.manifest = self.helper.evidence()
        run = json.loads((self.directory / 'manifest.json').read_text())
        run.pop('execution_requirements_sha256')
        (self.directory / 'manifest.json').write_text(json.dumps(run))
        self.helper.rebind_provenance()
        self.assert_rejected()

    def test_record_must_bind_the_retained_policy_contract_and_api(self):
        for key in ('policy_sha256', 'contract_sha256', 'openapi_sha256'):
            self.manifest = self.helper.evidence()
            self.write_records({**self.record(), key: '0' * 64}, bind_manifest=True)
            with self.subTest(key=key):
                self.assert_rejected()


if __name__ == '__main__':
    unittest.main()

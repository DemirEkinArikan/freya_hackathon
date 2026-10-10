"""Final-package metadata, selection and archive safety regressions."""
import json
import subprocess
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from tools import package_submission as package
import test_package_submission as package_fixtures


class PackageArchiveTests(unittest.TestCase):
    # Compose the maintained controller-evidence fixture without inheriting or
    # importing its TestCase into this module's unittest discovery namespace.
    def setUp(self):
        self.fixture = package_fixtures.PackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root

    def evidence(self):
        return self.fixture.evidence()

    def test_submission_metadata_requires_nonempty_strings(self):
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)):
            for field in ('team', 'repo', 'run_command'):
                for invalid in (True, 1, ['synthetic'], {'synthetic': 'value'}, '   ', '\n'):
                    manifest = self.evidence()
                    manifest[field] = invalid
                    with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                        package.check_manifest(manifest)

    def test_submission_repository_must_be_a_github_repository_url(self):
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)):
            for invalid in ('https://example.invalid/team/source', 'https://github.com',
                            'https://github.com/team', 'https://github.com/team/source/issues/19'):
                manifest = self.evidence()
                manifest['repo'] = invalid
                with self.subTest(repo=invalid), self.assertRaisesRegex(ValueError, 'repo|GitHub'):
                    package.check_manifest(manifest)

    def test_submission_repo_must_match_checkout_origin(self):
        manifest = self.evidence()
        manifest['repo'] = 'https://github.com/other/source'
        with self.assertRaisesRegex(ValueError, 'origin'):
            package.check_manifest(manifest)

    def test_repository_origin_normalizes_github_ssh_and_https(self):
        self.fixture.origin_patch.stop()
        for origin in ('https://github.com/test/example.git', 'git@github.com:test/example.git',
                       'ssh://git@github.com/test/example.git', 'https://github.com/test/example/'):
            with self.subTest(origin=origin), patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout=origin)):
                self.assertEqual(package.repository_url(), 'https://github.com/test/example')
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='https://example.invalid/test/example')):
            with self.assertRaisesRegex(ValueError, 'origin'):
                package.repository_url()

    def test_two_slots_cannot_reuse_one_selected_export_trace_or_run(self):
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)):
            for field in ('workflows', 'traces', 'runs'):
                manifest = self.evidence()
                manifest[field]['hidden-2'] = manifest[field]['hidden-1']
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'distinct|different|reuse|duplicate'):
                    package.check_manifest(manifest)

    def test_two_slots_cannot_reuse_one_input_bundle(self):
        manifest = self.evidence()
        manifest['inputs']['hidden-2'] = manifest['inputs']['hidden-1']
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)), \
             self.assertRaisesRegex(ValueError, 'distinct|different|reuse|duplicate'):
            package.check_manifest(manifest)

    def test_copying_input_files_does_not_create_a_second_hidden_build(self):
        manifest = self.evidence()
        first = manifest['inputs']['hidden-1']
        (self.root / 'copied-brief.md').write_bytes((self.root / first['briefs'][0]).read_bytes())
        (self.root / 'copied-api.json').write_bytes((self.root / first['tool_api']).read_bytes())
        manifest['inputs']['hidden-2'] = {'briefs': ['copied-brief.md'], 'tool_api': 'copied-api.json'}
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)), \
             self.assertRaisesRegex(ValueError, 'distinct|different|reuse|duplicate'):
            package.check_manifest(manifest)

    def _archive_manifest(self):
        manifest_path = self.root / 'submission.json'
        manifest_path.write_text(json.dumps(self.evidence()))
        return manifest_path

    def test_reserved_package_index_collision_fails_for_check_and_write(self):
        manifest_path = self._archive_manifest()
        evidence_path = self.root / 'evidence.json'
        evidence_path.write_text('{}')
        output = self.root / 'artifacts/package.zip'
        for mode in ('check', 'write'):
            command = ['--manifest', str(manifest_path), '--output', str(output)]
            if mode == 'check':
                command.append('--check')
            with self.subTest(mode=mode), \
                 patch.object(package, 'source_files', return_value=[]), \
                 patch.object(package, 'check_manifest', return_value=[(evidence_path, 'package-index.json')]), \
                 patch.object(package, 'verify_committed'), \
                 self.assertRaisesRegex(ValueError, 'collid|collision|reserved'):
                package.main(command)
            self.assertFalse(output.exists())

    def test_source_evidence_name_collision_fails_during_check(self):
        manifest_path = self._archive_manifest()
        source = self.root / 'README.md'
        source.write_text('safe source')
        evidence = self.root / 'evidence.json'
        evidence.write_text('{}')
        with patch.object(package, 'source_files', return_value=[source]), \
             patch.object(package, 'check_manifest', return_value=[(evidence, 'README.md')]), \
             patch.object(package, 'verify_committed'), \
             self.assertRaisesRegex(ValueError, 'collid|collision'):
            package.main(['--check', '--manifest', str(manifest_path)])

    def test_failed_write_preserves_previous_verified_archive(self):
        source = self.root / 'README.md'
        source.write_text('safe source')
        builder = self.root / 'builder/build.py'
        builder.parent.mkdir()
        builder.write_text('# safe source\n')
        output = self.root / 'artifacts/package.zip'
        output.parent.mkdir()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('verified.txt', 'previous verified package')
        original = output.read_bytes()
        real_write = zipfile.ZipFile.write
        written = 0

        def interrupted_write(archive, *args, **kwargs):
            nonlocal written
            written += 1
            if written == 2:
                raise OSError('Synthetic interrupted package write')
            return real_write(archive, *args, **kwargs)

        with patch.object(zipfile.ZipFile, 'write', new=interrupted_write), \
             self.assertRaisesRegex(OSError, 'Synthetic interrupted'):
            package.main(['--source-only', '--output', str(output)])
        self.assertGreaterEqual(written, 2)
        self.assertEqual(output.read_bytes(), original)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read('verified.txt'), b'previous verified package')

    def test_json_test_fixtures_receive_credential_pattern_checks(self):
        fixture = self.root / 'tests/fixtures/credential.json'
        fixture.parent.mkdir(parents=True)
        fixture.write_text(json.dumps({'headers': {'Authorization': 'opaque-synthetic-private-value'}}))
        with self.assertRaisesRegex(ValueError, 'requires review') as result:
            package.check_content(fixture)
        self.assertNotIn('opaque-synthetic-private-value', str(result.exception))

    def test_source_drift_during_zip_write_preserves_previous_archive(self):
        source = self.root / 'README.md'
        source.write_text('safe source')
        builder = self.root / 'builder/build.py'
        builder.parent.mkdir()
        builder.write_text('# original source\n')
        output = self.root / 'artifacts/package.zip'
        output.parent.mkdir()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('verified.txt', 'previous verified package')
        original = output.read_bytes()
        real_write = zipfile.ZipFile.write
        written = 0

        def drifting_write(archive, filename, *args, **kwargs):
            nonlocal written
            written += 1
            if written == 2:
                # Preflight has already scanned/hashed both source files.
                # Change the bytes just before ZipFile rereads this source.
                package.Path(filename).write_text('# changed after preflight\n')
            return real_write(archive, filename, *args, **kwargs)

        with patch.object(zipfile.ZipFile, 'write', new=drifting_write), \
             self.assertRaisesRegex(ValueError, 'hash|fingerprint|changed|differ'):
            package.main(['--source-only', '--output', str(output)])
        self.assertGreaterEqual(written, 2)
        self.assertEqual(output.read_bytes(), original)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read('verified.txt'), b'previous verified package')


if __name__ == '__main__':
    unittest.main()

"""Package preflight must accept the same namespace as verified extraction."""
import contextlib
import io
import json
import subprocess
import unittest
import zipfile
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from tools import package_submission as package
import test_package_submission as package_fixtures


class PackagePathTests(unittest.TestCase):
    def setUp(self):
        self.fixture = package_fixtures.PackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.source = self.root / 'README.md'
        self.source.write_text('Safe synthetic package source\n')
        self.evidence = self.root / 'evidence.json'
        self.evidence.write_text('{}')
        self.other_evidence = self.root / 'other-evidence.json'
        self.other_evidence.write_text('{}')
        self.manifest_path = self.root / 'submission.json'
        self.manifest_path.write_text('{}')
        self.output = self.root / 'artifacts/package.zip'
        self.output.parent.mkdir()
        with zipfile.ZipFile(self.output, 'w') as archive:
            archive.writestr('verified.txt', 'Previous verified archive')
        self.original_archive = self.output.read_bytes()

    def assert_preflight_rejects(self, entries, error='path|collid|collision|reserved'):
        for mode in ('check', 'write'):
            command = ['--manifest', str(self.manifest_path), '--output', str(self.output)]
            if mode == 'check':
                command.append('--check')
            with self.subTest(mode=mode), \
                 patch.object(package, 'source_files', return_value=[self.source]), \
                 patch.object(package, 'check_manifest', return_value=entries), \
                 patch.object(package, 'verify_committed') as committed, \
                 patch.object(package.tempfile, 'mkstemp') as temporary, \
                 self.assertRaisesRegex(ValueError, error):
                package.main(command)
            committed.assert_not_called()
            temporary.assert_not_called()
            self.assertEqual(self.output.read_bytes(), self.original_archive)
            self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_case_only_source_and_evidence_filename_difference_is_rejected(self):
        self.assert_preflight_rejects([(self.evidence, 'readme.md')])

    def test_case_only_evidence_filename_difference_is_rejected(self):
        self.assert_preflight_rejects([
            (self.evidence, 'inputs/policy.json'),
            (self.other_evidence, 'INPUTS/POLICY.JSON'),
        ])

    def test_case_only_alias_of_reserved_index_is_rejected(self):
        self.assert_preflight_rejects([(self.evidence, 'PACKAGE-INDEX.JSON')])

    def test_unsafe_platform_and_noncanonical_paths_are_rejected(self):
        invalid_names = (
            '', '.', '/', '/absolute.json', '../outside.json', 'safe/../outside.json',
            './file.json', 'safe/./file.json', 'safe//file.json', 'safe/file.json/',
            'safe\\file.json', '\\server\\file.json', 'C:/file.json', 'file.json:stream',
            'file.json\x00ignored',
        )
        for name in invalid_names:
            with self.subTest(name=name):
                self.assert_preflight_rejects([(self.evidence, name)])

    def test_casefold_file_parent_conflicts_are_rejected_in_either_order(self):
        entries = [
            (self.evidence, 'inputs/policy'),
            (self.other_evidence, 'INPUTS/POLICY/brief.json'),
        ]
        for ordered in (entries, list(reversed(entries))):
            with self.subTest(names=[name for _, name in ordered]):
                self.assert_preflight_rejects(ordered)

    def test_source_file_and_reserved_index_cannot_be_parents(self):
        for name in ('README.md/brief.json', 'PACKAGE-INDEX.JSON/brief.json'):
            with self.subTest(name=name):
                self.assert_preflight_rejects([(self.evidence, name)])

    def test_valid_two_run_mappings_keep_shared_api_without_duplicate_members(self):
        manifest = self.fixture.evidence()
        self.manifest_path.write_text(json.dumps(manifest))
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)), \
             patch.object(package, 'verify_committed'), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(package.main([
                '--check', '--manifest', str(self.manifest_path), '--output', str(self.output),
            ]), 0)
            self.assertEqual(self.output.read_bytes(), self.original_archive)
            self.assertEqual(package.main([
                '--manifest', str(self.manifest_path), '--output', str(self.output),
            ]), 0)
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            folded = {name.casefold() for name in names}
            self.assertEqual(len(names), len(folded))
            self.assertEqual(names.count('api.json'), 1)
            for name in names:
                member = PurePosixPath(name)
                self.assertEqual(member.as_posix(), name)
                self.assertNotIn('\\', name)
                self.assertFalse(any(parent.parts and parent.as_posix().casefold() in folded
                                     for parent in member.parents))
            index = json.loads(archive.read('package-index.json'))
            submission = index['submission']
            for slot in ('hidden-1', 'hidden-2'):
                self.assertEqual(submission['workflows'][slot], f'workflows/{slot}.json')
                self.assertEqual(submission['traces'][slot], f'traces/{slot}.jsonl')
                self.assertEqual(submission['runs'][slot], f'runs/{slot}/manifest.json')
                brief = 'brief.md' if slot == 'hidden-1' else 'brief-2.md'
                self.assertEqual(submission['inputs'][slot], {'briefs': [brief], 'tool_api': 'api.json'})
            for name, fingerprint in {**index['source_files'], **index['evidence_files']}.items():
                self.assertEqual(package.hashlib.sha256(archive.read(name)).hexdigest(), fingerprint)


if __name__ == '__main__':
    unittest.main()

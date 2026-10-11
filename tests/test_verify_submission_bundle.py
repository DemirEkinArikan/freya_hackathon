"""Adversarial checks for an independent, read-only ZIP verifier.

These fixtures establish local consistency only. They do not represent live
model generation, publishing, hosted calls, or competition readiness.
"""
import contextlib
import hashlib
import io
import json
import stat
import subprocess
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import package_submission as package


class IndependentBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'trusted'
        self.root.mkdir()
        self.marker = Path(self.temp.name) / 'executed-untrusted-code'
        (self.root / 'builder').mkdir()
        (self.root / 'benchmarks').mkdir()
        (self.root / 'tools').mkdir()
        (self.root / 'README.md').write_bytes(b'Synthetic source preparation fixture.\r\n')
        # Neither importing this file nor executing the advertised source is
        # necessary to check an archive. A valid bundle must remain inert.
        (self.root / 'builder/build.py').write_text(
            'from pathlib import Path\n'
            f'Path({str(self.marker)!r}).write_text("unexpected execution")\n'
        )
        (self.root / 'tools/package_submission.py').write_text(
            'raise RuntimeError("archive-controlled packager was imported")\n'
        )
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Verifier Test')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.commit = self.commit_sources()
        self.archive = Path(self.temp.name) / 'source.zip'
        generated = self.root / 'artifacts/source.zip'
        with patch.object(package, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            package.main(['--source-only', '--output', str(generated)])
        self.archive.write_bytes(generated.read_bytes())
        generated.unlink()
        generated.parent.rmdir()
        with zipfile.ZipFile(self.archive) as archive:
            self.entries = [(info, archive.read(info)) for info in archive.infolist()]

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit_sources(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Synthetic verifier fixture')
        return self.git('rev-parse', 'HEAD')

    def verify(self, archive=None, commit=None, source_root=None):
        from tools import verify_submission_bundle as verifier
        return verifier.verify_bundle(archive or self.archive, source_root or self.root,
                                      commit or self.commit)

    def rewrite(self, replacements=None, *, drop=(), extra=()):
        replacements = replacements or {}
        target = Path(self.temp.name) / 'adversarial.zip'
        with warnings.catch_warnings(), zipfile.ZipFile(target, 'w') as archive:
            warnings.simplefilter('ignore', UserWarning)
            for info, raw in self.entries:
                if info.filename not in drop:
                    archive.writestr(info, replacements.get(info.filename, raw))
            for name, raw in extra:
                archive.writestr(name, raw)
        return target

    def index(self):
        return json.loads(dict((info.filename, raw) for info, raw in self.entries)['package-index.json'])

    def rehash_source(self, name, raw):
        index = self.index()
        index['source_files'][name] = hashlib.sha256(raw).hexdigest()
        return {'package-index.json': json.dumps(index).encode(), name: raw}

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_source_preparation_is_read_only_and_preserves_original_bytes(self):
        original = self.archive.read_bytes()
        checkout = self.snapshot()
        status = self.git('status', '--porcelain')
        report = self.verify()
        self.assertEqual(report['status'], 'preparation_verified')
        self.assertEqual(report['mode'], 'source-preparation')
        self.assertIs(report['source_only'], True)
        self.assertEqual(report['commit'], self.commit)
        self.assertEqual(report['archive_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(self.snapshot(), checkout)
        self.assertEqual(self.git('status', '--porcelain'), status)
        self.assertFalse(self.marker.exists())

    def test_rejection_is_read_only_and_preserves_original_archive(self):
        archive = self.rewrite({'README.md': b'tampered source'})
        original, checkout = archive.read_bytes(), self.snapshot()
        with self.assertRaises(ValueError):
            self.verify(archive)
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(self.snapshot(), checkout)
        self.assertFalse(self.marker.exists())

    def test_archive_controlled_python_and_packager_are_never_executed(self):
        self.verify()
        self.assertFalse(self.marker.exists())

    def test_a_self_consistent_hash_index_cannot_replace_trusted_source(self):
        archive = self.rewrite(self.rehash_source('README.md', b'forged but internally consistent source\n'))
        with self.assertRaises(ValueError):
            self.verify(archive)

    def test_a_consistent_index_cannot_omit_committed_allowlisted_source(self):
        index = self.index()
        del index['source_files']['README.md']
        archive = self.rewrite({'package-index.json': json.dumps(index).encode()}, drop=['README.md'])
        with self.assertRaises(ValueError):
            self.verify(archive)

    def test_report_hash_describes_the_bytes_actually_verified(self):
        from tools import verify_submission_bundle as verifier
        original = self.archive.read_bytes()
        original_check = verifier._verify_source
        def replace_after_source_check(*args, **kwargs):
            original_check(*args, **kwargs)
            self.archive.write_bytes(b'replaced after validation; these bytes are not a ZIP')
        with patch.object(verifier, '_verify_source', side_effect=replace_after_source_check):
            try:
                report = self.verify()
            except ValueError:
                # Refusing a changed file is also valid; a success report must
                # identify the immutable input that was actually inspected.
                return
        self.assertEqual(report['archive_sha256'], hashlib.sha256(original).hexdigest())

    def test_expected_commit_must_be_a_full_existing_trusted_commit(self):
        for commit in ('0' * 40, self.commit[:12], 'HEAD', '--help', '../untrusted'):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                self.verify(commit=commit)

    def test_trusted_source_root_cannot_be_a_symlink(self):
        linked = Path(self.temp.name) / 'linked-source'
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.verify(source_root=linked)

    def test_duplicate_members_including_identical_bytes_are_rejected(self):
        for name in ('README.md', 'package-index.json'):
            raw = dict((info.filename, raw) for info, raw in self.entries)[name]
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify(self.rewrite(extra=[(name, raw)]))

    def test_unsafe_member_names_are_rejected_without_extraction(self):
        for name in ('../escaped.py', '/absolute.py', 'C:/windows.py',
                     'builder\\escape.py', 'builder/../escape.py',
                     './escape.py', 'builder//escape.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify(self.rewrite(extra=[(name, b'safe payload')]))
        self.assertFalse((Path(self.temp.name) / 'escaped.py').exists())

    def test_symlink_member_is_rejected_even_when_payload_hash_matches(self):
        linked = zipfile.ZipInfo('README.md')
        linked.create_system = 3
        linked.external_attr = (stat.S_IFLNK | 0o777) << 16
        raw = dict((info.filename, raw) for info, raw in self.entries)['README.md']
        with self.assertRaises(ValueError):
            self.verify(self.rewrite(drop=['README.md'], extra=[(linked, raw)]))

    def test_member_count_and_uncompressed_size_are_bounded_before_reading(self):
        from tools import verify_submission_bundle as verifier
        for constant, limit in (('MAX_FILES', len(self.entries) - 1),
                                ('MAX_FILE_BYTES', 1),
                                ('MAX_BYTES', self.archive.stat().st_size - 1)):
            with self.subTest(constant=constant), patch.object(verifier, constant, limit), \
                 patch.object(zipfile.ZipFile, 'open', side_effect=AssertionError('oversized payload was read')), \
                 self.assertRaisesRegex(ValueError, 'archive_structure'):
                self.verify()

    def test_case_collisions_and_file_parent_collisions_are_structural_failures(self):
        for name in ('builder/BUILD.py', 'builder', 'Tools/extra.py'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'archive_structure'):
                self.verify(self.rewrite(extra=[(name, b'safe payload')]))

    def test_missing_and_unindexed_payloads_are_rejected(self):
        for mutation in ('missing', 'unindexed'):
            archive = (self.rewrite(drop=['README.md']) if mutation == 'missing' else
                       self.rewrite(extra=[('undeclared.txt', b'safe but unindexed')]))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.verify(archive)

    def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self):
        for index in (b'{"source_only":true,"source_only":false}',
                      b'{"source_only":true,"unexpected":NaN}',
                      b'{"source_only":true,"unexpected":Infinity}'):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.verify(self.rewrite({'package-index.json': index}))

    def test_source_only_flag_cannot_be_relabelled_as_a_final_bundle(self):
        index = self.index()
        index['source_only'] = False
        with self.assertRaises(ValueError):
            self.verify(self.rewrite({'package-index.json': json.dumps(index).encode()}))

    def test_index_source_and_evidence_inventories_cannot_overlap(self):
        index = self.index()
        index['evidence_files']['README.md'] = index['source_files']['README.md']
        with self.assertRaises(ValueError):
            self.verify(self.rewrite({'package-index.json': json.dumps(index).encode()}))

    def test_credential_diagnostic_never_echoes_payload_or_local_credential(self):
        private = 'OPAQUE_SYNTHETIC_TEST_PRIVATE_73194'
        raw = ('Authorization: Bearer ' + private + '\n').encode()
        (self.root / 'README.md').write_bytes(raw)
        commit = self.commit_sources()
        archive = self.rewrite(self.rehash_source('README.md', raw))
        with patch.dict('os.environ', {'FREYA_API_KEY': private}), self.assertRaises(ValueError) as raised:
            self.verify(archive, commit=commit)
        self.assertNotIn(private, str(raised.exception))
        self.assertNotIn(raw.decode().strip(), str(raised.exception))

    def test_index_metadata_is_scanned_for_credentials_without_echo(self):
        private = 'synthetic-private-index-value-83912'
        index = self.index()
        index['metadata'] = {'Authorization': private}
        with self.assertRaises(ValueError) as raised:
            self.verify(self.rewrite({'package-index.json': json.dumps(index).encode()}))
        self.assertNotIn(private, str(raised.exception))

    def test_structured_evidence_is_scanned_even_with_unknown_extension(self):
        private = 'synthetic-private-retained-value-90427'
        for suffix, raw in (('data', json.dumps({'Authorization': private}).encode()),
                            ('log', (json.dumps({'safe': True}) + '\n' +
                                     json.dumps({'Authorization': private}) + '\n').encode())):
            name = 'retained/payload.' + suffix
            index = self.index()
            index['source_only'] = False
            index['evidence_files'][name] = hashlib.sha256(raw).hexdigest()
            archive = self.rewrite({'package-index.json': json.dumps(index).encode()},
                                   extra=[(name, raw)])
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, '^archive_content$') as raised:
                self.verify(archive)
            self.assertNotIn(private, str(raised.exception))

    def test_short_opaque_local_environment_credential_is_rejected(self):
        private = 'opaque7'
        raw = ('Synthetic README ' + private + '\n').encode()
        (self.root / 'README.md').write_bytes(raw)
        commit = self.commit_sources()
        (self.root / '.env').write_text('FREYA_API_KEY=' + private + '\n')
        archive = self.rewrite(self.rehash_source('README.md', raw))
        with self.assertRaises(ValueError) as raised:
            self.verify(archive, commit=commit)
        self.assertNotIn(private, str(raised.exception))


class SyntheticFinalConsistencyTests(unittest.TestCase):
    """Exercise retained-record bindings, with synthetic graph checks isolated.

    The fixture is deliberately fabricated. Policy, action, execution and guard
    compilation, execution-requirement coverage, workflow semantic validation,
    and validator-source pinning are
    mocked. Archive, git, provenance, trace, input, stored-graph comparison and
    selected-attempt checks remain real. A passing result establishes consistency
    of these synthetic records and never live authenticity.
    """
    git = IndependentBundleTests.git
    commit_sources = IndependentBundleTests.commit_sources
    rewrite = IndependentBundleTests.rewrite
    index = IndependentBundleTests.index
    snapshot = IndependentBundleTests.snapshot

    def setUp(self):
        IndependentBundleTests.setUp(self)
        # Runtime provenance requires both directories after reconstruction.
        # ZIPs retain files, so an empty benchmarks directory would vanish and
        # prevent every synthetic final from reaching its semantic checks.
        (self.root / 'benchmarks/evidence.py').write_text('# Synthetic retained runtime source\n')
        try:
            import test_package_submission as package_tests
        except ModuleNotFoundError:
            from tests import test_package_submission as package_tests
        fixture = package_tests.PackageTests(methodName='runTest')
        fixture.root = self.root
        data = fixture.evidence()
        # The export contains the platform phone model; the authored graph does
        # not. Retain that distinction so real stored-graph comparison is used.
        from builder.safety import digest
        from builder.source_provenance import bind_run_provenance
        for name in ('hidden-1', 'hidden-2'):
            directory = self.root / name
            selected = directory / 'attempts/plan-001'
            authored = {'initial_node': 'finish', 'nodes': {}}
            (selected / 'workflow.json').write_text(json.dumps(authored))
            attempt = json.loads((selected / 'attempt.json').read_text())
            attempt['workflow_sha256'] = digest(authored)
            (selected / 'attempt.json').write_text(json.dumps(attempt))
            run = json.loads((directory / 'manifest.json').read_text())
            run['authored_workflow_sha256'] = digest(authored)
            (directory / 'manifest.json').write_text(json.dumps(run))
            events = [json.loads(line) for line in (self.root / (name + '.jsonl')).read_text().splitlines()]
            source = json.loads((directory / 'builder-source.json').read_text())
            (directory / 'build-provenance.json').write_text(json.dumps(bind_run_provenance(source, run, events)))
        self.git('remote', 'add', 'origin', 'https://github.com/test/example')
        self.commit = self.commit_sources()
        data['commit'] = self.commit
        manifest = self.root / 'submission.json'
        manifest.write_text(json.dumps(data))
        output = self.root / 'artifacts/final.zip'
        with patch.object(package, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            package.main(['--manifest', str(manifest), '--output', str(output)])
        self.archive.write_bytes(output.read_bytes())
        output.unlink()
        output.parent.rmdir()
        with zipfile.ZipFile(self.archive) as archive:
            self.entries = [(info, archive.read(info)) for info in archive.infolist()]

    def verify(self, archive=None, commit=None, source_root=None, *, coverage_problems=None):
        from tools import verify_submission_bundle as verifier
        def synthetic_compile(plan, *args, **kwargs):
            name = plan['synthetic_plan']
            return {'workflow': {'initial_node': 'finish', 'nodes': {}},
                    'guard_manifest': {'synthetic_guards': name}}
        with patch.object(verifier, '_verify_validator_source'), \
             patch.object(verifier, 'compile_policy_contract', side_effect=lambda policy, *args, **kwargs:
                          self.evidence_json('runs/' + policy['synthetic'] + '/policy.contract.json')), \
             patch.object(verifier, 'compile_execution_requirements', side_effect=lambda policy, *args, **kwargs:
                          {'synthetic_requirements': policy['synthetic']}) as requirements_compile, \
             patch.object(verifier, 'validate_plan_execution', return_value=coverage_problems or []) as coverage_validate, \
             patch.object(verifier, 'compile_action_bindings', side_effect=lambda policy, *args, **kwargs:
                          {'synthetic_bindings': policy['synthetic']}), \
             patch.object(verifier, 'compile_execution_policy', side_effect=lambda policy, *args, **kwargs:
                          {'synthetic_execution': policy['synthetic']}), \
             patch.object(verifier, 'required_execution_kinds', return_value=[]), \
             patch.object(verifier, 'validate_workflow', return_value=[]), \
             patch.object(verifier, 'compile_guarded_workflow', side_effect=synthetic_compile):
            self.requirements_compile = requirements_compile
            self.coverage_validate = coverage_validate
            return IndependentBundleTests.verify(self, archive, commit, source_root)

    def rehash_evidence(self, replacements):
        index = self.index()
        for name, raw in replacements.items():
            index['evidence_files'][name] = hashlib.sha256(raw).hexdigest()
            # Commit the mutated records as deliberately invalid trusted test
            # inputs, so the subsequent semantic check cannot pass merely by
            # rejecting evidence that is absent from the trusted commit.
            path = self.root / 'retained-test-inputs' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.commit = self.commit_sources()
        index['submission']['commit'] = self.commit
        index['source_binding']['submission_commit'] = self.commit
        return self.rewrite({**replacements, 'package-index.json': json.dumps(index).encode()})

    def evidence_json(self, name):
        return json.loads(dict((info.filename, raw) for info, raw in self.entries)[name])

    def rebound_run(self, *, run=None, events=None):
        from builder.source_provenance import bind_run_provenance
        name = 'runs/hidden-1/manifest.json'
        trace_name = 'traces/hidden-1.jsonl'
        run = self.evidence_json(name) if run is None else run
        events = ([json.loads(line) for line in
                   dict((info.filename, raw) for info, raw in self.entries)[trace_name].decode().splitlines()]
                  if events is None else events)
        source = self.evidence_json('runs/hidden-1/builder-source.json')
        return {name: json.dumps(run).encode(),
                trace_name: '\n'.join(json.dumps(event) for event in events).encode(),
                'runs/hidden-1/build-provenance.json': json.dumps(bind_run_provenance(source, run, events)).encode()}

    def test_synthetic_final_records_check_consistency_and_preserve_bytes(self):
        original, checkout = self.archive.read_bytes(), self.snapshot()
        report = self.verify()
        self.assertEqual(report['status'], 'consistency_verified')
        self.assertEqual(report['mode'], 'final-consistency')
        self.assertIs(report['source_only'], False)
        self.assertTrue(report['limitations'])
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(self.snapshot(), checkout)
        self.assertFalse(self.marker.exists())

    def test_self_consistent_reindexed_evidence_requires_committed_original_bytes(self):
        name = 'runs/hidden-1/manifest.json'
        run = self.evidence_json(name)
        run['synthetic_uncommitted_change'] = True
        raw = json.dumps(run).encode()
        index = self.index()
        index['evidence_files'][name] = hashlib.sha256(raw).hexdigest()
        archive = self.rewrite({name: raw, 'package-index.json': json.dumps(index).encode()})
        with self.assertRaisesRegex(ValueError, 'evidence_commit'):
            self.verify(archive)

    def test_final_consistency_calls_execution_requirement_coverage_gate(self):
        with self.assertRaisesRegex(ValueError, '^guard_compilation$'):
            self.verify(coverage_problems=['synthetic_missing_execution_obligation'])
        self.requirements_compile.assert_called_once()
        self.coverage_validate.assert_called_once()
        plan, requirements, policy, sources, api = self.coverage_validate.call_args.args
        self.assertEqual(plan, {'synthetic_plan': 'hidden-1'})
        self.assertEqual(requirements, {'synthetic_requirements': 'hidden-1'})
        self.assertEqual(policy, {'synthetic': 'hidden-1'})
        self.assertEqual(sources[0]['text'], 'Synthetic policy input hidden-1')
        self.assertEqual(api, {})

    def test_final_bundle_requires_both_distinct_hidden_builds(self):
        for mapping in ('workflows', 'traces', 'runs', 'inputs'):
            for mutation in ('missing-second', 'reused-first'):
                index = self.index()
                values = index['submission'][mapping]
                if mutation == 'missing-second':
                    del values['hidden-2']
                else:
                    values['hidden-2'] = values['hidden-1']
                with self.subTest(mapping=mapping, mutation=mutation), self.assertRaisesRegex(ValueError, '^final_manifest$'):
                    self.verify(self.rewrite({'package-index.json': json.dumps(index).encode()}))

    def test_final_submission_and_source_binding_cannot_relabel_trusted_commit(self):
        for location in ('submission', 'source_binding'):
            index = self.index()
            index[location]['commit' if location == 'submission' else 'submission_commit'] = '0' * 40
            with self.subTest(location=location), self.assertRaisesRegex(ValueError, '^final_manifest$'):
                self.verify(self.rewrite({'package-index.json': json.dumps(index).encode()}))

    def test_final_repository_must_match_the_trusted_source_origin(self):
        index = self.index()
        index['submission']['repo'] = 'https://github.com/other/forged-origin'
        with self.assertRaisesRegex(ValueError, '^final_manifest$'):
            self.verify(self.rewrite({'package-index.json': json.dumps(index).encode()}))

    def test_deadline_is_checked_after_consistently_rebinding_provenance(self):
        for elapsed in (1800.001, -1, True, None):
            run = self.evidence_json('runs/hidden-1/manifest.json')
            run['elapsed_seconds'] = elapsed
            with self.subTest(elapsed=elapsed), self.assertRaisesRegex(ValueError, '^final_evidence$'):
                self.verify(self.rehash_evidence(self.rebound_run(run=run)))

    def test_missing_generation_isolation_is_rejected_after_provenance_rebinding(self):
        trace = dict((info.filename, raw) for info, raw in self.entries)['traces/hidden-1.jsonl']
        events = [json.loads(line) for line in trace.decode().splitlines()]
        events = [event for event in events if event.get('event') != 'generation_isolation_audit']
        with self.assertRaisesRegex(ValueError, '^final_evidence$'):
            self.verify(self.rehash_evidence(self.rebound_run(events=events)))

    def test_selected_attempt_cannot_point_to_another_directory(self):
        run = self.evidence_json('runs/hidden-1/manifest.json')
        run['selected_attempt_directory'] = 'attempts/plan-002'
        with self.assertRaisesRegex(ValueError, '^final_evidence$'):
            self.verify(self.rehash_evidence(self.rebound_run(run=run)))

    def test_swapped_selected_attempt_export_is_rejected_even_with_updated_zip_hash(self):
        first = 'runs/hidden-1/attempts/plan-001/export.json'
        second = self.evidence_json('runs/hidden-2/attempts/plan-001/export.json')
        with self.assertRaisesRegex(ValueError, '^final_evidence$'):
            self.verify(self.rehash_evidence({first: json.dumps(second).encode()}))

    def test_wrong_phone_model_and_tool_credentials_are_rejected(self):
        for mutation in ('phone-model', 'tool-credential'):
            workflow = self.evidence_json('workflows/hidden-1.json')
            if mutation == 'phone-model':
                workflow['workflow']['agent_config']['llmConfig']['model'] = 'unapproved-phone-model'
            else:
                workflow['workflow']['tools'] = [{'headers': {'Authorization': 'opaque-synthetic-private-value'}}]
            category = '^final_evidence$' if mutation == 'phone-model' else '^archive_content$'
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, category) as raised:
                self.verify(self.rehash_evidence({'workflows/hidden-1.json': json.dumps(workflow).encode()}))
            self.assertNotIn('opaque-synthetic-private-value', str(raised.exception))


if __name__ == '__main__':
    unittest.main()

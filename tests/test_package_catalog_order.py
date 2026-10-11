"""Producer/packager ordering checks using synthetic evidence, never live proof."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from builder.runtime import CodexGenerator, Deadline, Trace
from builder.source_provenance import bind_run_provenance
from tools import package_submission as package
import test_package_submission as fixtures
from test_runtime import completed_stream


class PackageCatalogOrderTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.PackageTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.helper.evidence()
        self.directory = self.helper.root / 'hidden-1'
        self.source = json.loads((self.directory / 'builder-source.json').read_text())
        self.run = json.loads((self.directory / 'manifest.json').read_text())
        original = [json.loads(s) for s in (self.helper.root / 'hidden-1.jsonl').read_text().splitlines()]
        self.prefix = [e for e in original if e['event'] in (
            'start', 'inputs', 'builder_source_snapshot', 'generation_isolation_audit')
            or e['event'] == 'model_call' and not e.get('prompt_characters')]
        self.suffix = [e for e in original if e['event'] in ('action_guard_compilation', 'deployment', 'end')]
        self.call = next(e for e in original if e['event'] == 'model_call' and e.get('prompt_characters'))
        self.catalog = next(e for e in original if e['event'] == 'generation_model_catalog')

    def check(self, middle):
        events = copy.deepcopy(self.prefix + middle + self.suffix)
        (self.directory / 'build-provenance.json').write_text(json.dumps(
            bind_run_provenance(self.source, self.run, events)))
        return package.validate_provenance(self.directory / 'manifest.json', self.run, events)

    def test_actual_runtime_emitted_catalog_then_call_is_accepted(self):
        # Exercise the production emitter with an in-memory subprocess result;
        # no Codex executable, network, account, or bank material is accessed.
        trace = Trace(self.helper.root / 'synthetic-runtime.jsonl')
        generator = CodexGenerator(self.helper.root / 'generation', trace, Deadline(1800),
                                   model=self.source['resolved_config']['model'], reasoning_effort='high',
                                   run=lambda argv, **kwargs: subprocess.CompletedProcess(
                                       argv, 0, completed_stream({'policy': {}, 'workflow': {}}), ''))
        generator._checked = True
        generator._isolation_checked = True
        generator._catalog = ('{}', self.helper.catalog())
        generator.generate('Synthetic source interface only')
        emitted = [json.loads(s) for s in trace.path.read_text().splitlines()]
        middle = [e for e in emitted if e['event'] in ('generation_model_catalog', 'model_call')]
        self.assertEqual([e['event'] for e in middle], ['generation_model_catalog', 'model_call'])
        for event in middle:
            event['time'] = self.call['time']
        self.assertEqual(len(self.check(middle)), 2)

    def test_two_calls_each_with_their_own_pre_call_catalog_are_accepted(self):
        second = {**self.call, 'purpose': 'Synthetic plan stage'}
        self.assertEqual(len(self.check([self.catalog, self.call, self.catalog, second])), 2)

    def test_post_call_catalog_is_not_pre_call_restriction_evidence(self):
        with self.assertRaisesRegex(ValueError, 'catalog'):
            self.check([self.call, self.catalog])

    def test_missing_duplicate_and_cross_call_catalogs_are_rejected(self):
        second = {**self.call, 'purpose': 'Synthetic plan stage'}
        for middle in ([self.call], [self.catalog, self.catalog, self.call],
                       [self.catalog, self.call, second],
                       [self.catalog, self.catalog, self.call, second]):
            with self.subTest(events=[e['event'] for e in middle]), self.assertRaisesRegex(ValueError, 'catalog'):
                self.check(middle)

    def test_catalog_before_isolation_audit_cannot_authorize_a_call(self):
        catalog = copy.deepcopy(self.catalog)
        catalog['time'] = self.prefix[-1]['time']
        self.prefix.insert(len(self.prefix) - 1, catalog)
        with self.assertRaisesRegex(ValueError, 'catalog'):
            self.check([self.call])

    def test_changed_catalog_cannot_authorize_generation(self):
        for key, value in (('model', 'different-model'), ('generation_catalog_sha256', '0' * 64),
                           ('restricted_capabilities', {'shell_type': 'enabled'})):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'catalog'):
                self.check([{**self.catalog, key: value}, self.call])


if __name__ == '__main__':
    unittest.main()

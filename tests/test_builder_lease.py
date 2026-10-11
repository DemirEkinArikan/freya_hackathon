"""Shared simulation lease selection does not widen generator file access."""
from pathlib import Path
import tempfile
import unittest

from builder.build import execute, _load_inputs
import test_builder as fixtures


class BuilderLeaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BuilderTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def generator(self):
        generator = self.fixture.generator()
        generator.generate.return_value = fixtures.fixture()
        return generator

    def test_external_shared_lock_is_forwarded_with_one_deadline_and_export_reserve(self):
        with tempfile.TemporaryDirectory() as shared:
            lease = Path(shared) / '.simulation.lock'
            observed = []
            def client_factory(**kwargs):
                observed.append(kwargs)
                return fixtures.FakeClient(**kwargs)
            result = execute(self.fixture.args('--simulation-lease', str(lease)),
                             generator=self.generator(), client_factory=client_factory)
            self.assertEqual(result['status'], 'exported')
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]['lease_path'], lease.resolve())
            self.assertEqual(observed[0]['reserve'], 300)
            self.assertEqual(observed[0]['deadline'].duration_seconds, 1800)
            self.assertFalse(lease.exists())  # injected local client does no real simulation

    def test_default_lease_stays_next_to_output(self):
        observed = []
        def client_factory(**kwargs):
            observed.append(kwargs['lease_path'])
            return fixtures.FakeClient(**kwargs)
        execute(self.fixture.args(), generator=self.generator(), client_factory=client_factory)
        self.assertEqual(observed, [self.fixture.directory / 'out/.simulation.lock'])

    def test_explicit_external_lease_does_not_admit_external_inputs_or_artifacts(self):
        with tempfile.TemporaryDirectory() as shared:
            root = Path(shared)
            brief = root / 'brief.md'
            brief.write_text('External input must remain inaccessible')
            lease = root / '.simulation.lock'
            args = self.fixture.args('--simulation-lease', str(lease), '--brief', str(brief))
            with self.assertRaisesRegex(ValueError, 'standalone project'):
                _load_inputs(args)
            args = self.fixture.args('--simulation-lease', str(lease), '--output-dir', str(root / 'out'))
            with self.assertRaisesRegex(ValueError, 'standalone project'):
                execute(args, generator=self.generator())

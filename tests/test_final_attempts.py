"""T18 parallel attempts: launch, declared-order selection, manifest and packaging.

Every run here is synthetic. No builder, model, platform or simulation is started.
"""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools import final_attempts as final
from tools import package_submission as package


class FinalAttemptsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for target in (final, package):
            patcher = patch.object(target, 'ROOT', self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(package, 'repository_url', return_value='https://github.com/test/example')
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = self.root / 'artifacts/hidden-inputs/spec.json'
        spec.parent.mkdir(parents=True)
        spec.write_text(json.dumps({'hidden-1': {'briefs': ['brief.md'], 'tool_api': 'api.json'},
                                    'hidden-2': {'briefs': ['brief-2.md'], 'tool_api': 'api.json'}}))
        for name, text in (('brief.md', 'Synthetic policy input hidden-1'), ('brief-2.md', 'Synthetic policy input hidden-2'),
                           ('api.json', '{}')):
            (self.root / name).write_text(text)
        self.env_file = self.root / 'private.env'
        self.env_file.write_text('FREYA_API_KEY=synthetic-launch-key\n')
        self.failures, self.alive = set(), False

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = final.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def launch(self, *extra):
        popen = MagicMock(side_effect=[SimpleNamespace(pid=7100 + number) for number in range(20)])
        with patch.object(final.subprocess, 'Popen', popen), patch.object(final.shutil, 'which', return_value='/bin/codex'), \
             patch.object(final, '_alive', side_effect=lambda pid: self.alive), \
             patch.dict(final.os.environ, {'OPENAI_API_KEY': 'synthetic-openai-value'}):
            final.os.environ.pop('FREYA_API_KEY', None)  # Only the --env-file supplies the key here.
            result = self.run_main('launch', '--env-file', str(self.env_file), '--model', 'synthetic-model',
                                   '--reasoning-effort', 'high', '--simulation-lease', '/shared/.simulation.lock', *extra)
        return result, popen

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in (self.root / 'artifacts/final').rglob('*') if path.is_file() and path.name != 'selection.json'}

    def test_launch_starts_unattended_attempts_with_own_directories_and_slots(self):
        (code, _, _), popen = self.launch()
        self.assertEqual(code, 0)
        self.assertEqual(popen.call_count, 6)
        slots = set()
        for call in popen.call_args_list:
            argv, kwargs = call.args[0], call.kwargs
            self.assertEqual(argv[1], 'builder/build.py')
            label = 'hidden-1' if argv[argv.index('--brief') + 1] == 'brief.md' else 'hidden-2'
            directory = argv[argv.index('--output-dir') + 1]
            self.assertRegex(directory, rf'^artifacts/final/{label}/attempt-[123]$')
            self.assertEqual(argv[argv.index('--tool-api') + 1], 'api.json')
            self.assertEqual(argv[argv.index('--simulation-lease') + 1], '/shared/.simulation.lock')
            self.assertNotIn('--policy-file', argv)
            slots.add(argv[argv.index('--slot') + 1])
            self.assertEqual(kwargs['cwd'], self.root)
            self.assertIs(kwargs['start_new_session'], True)
            self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(kwargs['env']['FREYA_API_KEY'], 'synthetic-launch-key')
            self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
            record = json.loads((self.root / directory / 'launch.json').read_text())
            self.assertEqual(record['argv'], argv)
            self.assertEqual(record['pid'] - 7100, popen.call_args_list.index(call))
        self.assertEqual(len(slots), 6)
        self.assertNotEqual(final.os.environ.get('FREYA_API_KEY'), 'synthetic-launch-key')
        # A retry round continues the numbering; earlier attempts are never reused.
        (code, _, _), popen = self.launch('--labels', 'hidden-2', '--attempts', '1')
        self.assertEqual(code, 0)
        self.assertIn('artifacts/final/hidden-2/attempt-4', popen.call_args.args[0])

    def test_launch_refuses_over_the_machine_cap_or_without_credentials(self):
        self.launch()
        self.alive = True  # All six builders are still running.
        before = self.snapshot()
        (code, _, error), popen = self.launch('--labels', 'hidden-1', '--attempts', '1')
        self.assertEqual(code, 1)
        self.assertIn('--max-running 6', error)
        popen.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.alive = False
        self.env_file.write_text('FREYA_API_URL=https://example.invalid\n')
        (code, _, error), popen = self.launch()
        self.assertEqual(code, 1)
        self.assertIn('FREYA_API_KEY is not available', error)
        popen.assert_not_called()
        self.env_file.write_text('FREYA_API_KEY=synthetic-launch-key\n')
        (self.root / 'brief-2.md').unlink()
        (code, _, error), popen = self.launch('--labels', 'hidden-2')
        self.assertEqual(code, 1)
        self.assertIn('input file in spec.json is missing', error)
        popen.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def attempt(self, label, number, *, status='exported', errors=0, declared=0,
                finished='2026-10-11T17:20:00+00:00', running=False):
        directory = self.root / 'artifacts/final' / label / f'attempt-{number}'
        run = directory / f'20261011T1700{number:02d}000000Z'
        run.mkdir(parents=True)
        (directory / 'launch.json').write_text(json.dumps({'label': label, 'attempt': number, 'pid': 7000 + number,
                                                           'argv': ['python3.11', 'builder/build.py']}))
        events = [{'event': 'start', 'mode': 'live', 'time': '2026-10-11T17:00:00+00:00'}]
        if errors:
            events.append({'event': 'validation', 'passed': False, 'errors': ['synthetic'] * errors,
                           'time': '2026-10-11T17:10:00+00:00'})
        if not running:
            events.append({'event': 'end', 'status': status, 'time': finished})
            (run / 'manifest.json').write_text(json.dumps({'status': status, 'error': 'synthetic failure'}
                                                          if status == 'failed' else {'status': status}))
            (run / 'policy.json').write_text(json.dumps({'unsupported_constraints': ['synthetic'] * declared}))
            if status == 'exported':
                (run / 'submission.workflow.json').write_text(json.dumps({'workflow_id': f'w{number}', 'agent_id': 'a',
                                                                          'workflow': {}}))
                (run / 'build-provenance.json').write_text('{}')
        (run / 'trace.jsonl').write_text('\n'.join(json.dumps(event) for event in events) + '\n')
        return run

    def injected_failure(self, run_dir, kind):
        if (run_dir.parent.name, kind) in self.failures:
            raise ValueError('synthetic ' + kind + ' failure')

    def select(self, *labels):
        with patch.object(final.verifier, 'check_recompilation', side_effect=lambda run_dir, *_: self.injected_failure(run_dir, 'local')), \
             patch.object(final.package, 'validate_trace'), \
             patch.object(final.package, 'validate_run', side_effect=lambda path, *_: self.injected_failure(path.parent, 'package')), \
             patch.object(final, '_alive', side_effect=lambda pid: self.alive):
            return self.run_main('select', '--labels', *(labels or ['hidden-1']))

    def record(self, label='hidden-1'):
        return json.loads((self.root / 'artifacts/final' / label / 'selection.json').read_text())

    def selected_files(self, record, label='hidden-1'):
        run = self.root / record['attempts'][record['selected'] - 1]['run']
        return {key: (run / name).read_bytes() for key, name in
                (('manifest', 'manifest.json'), ('trace', 'trace.jsonl'), ('export', 'submission.workflow.json'))}

    def test_declared_order_selects_and_preserves_every_attempt(self):
        late, early = '2026-10-11T17:28:00+00:00', '2026-10-11T17:05:00+00:00'
        cases = {
            'exported first': ([dict(status='failed', finished=early), dict(errors=3, finished=late)], set(), 2),
            'local validation': ([dict(finished=early), dict(errors=2, finished=late)], {('attempt-1', 'local')}, 2),
            'package validation': ([dict(finished=early), dict(errors=2, finished=late)], {('attempt-1', 'package')}, 2),
            'fewest diagnostics': ([dict(errors=2, finished=early), dict(declared=1), dict(finished=late)], set(), 3),
            'earliest finish': ([dict(finished=late), dict(finished=early)], set(), 2),
            'attempt number': ([dict(finished=early), dict(finished=early)], set(), 1),
        }
        for case, (attempts, failures, expected) in cases.items():
            shutil.rmtree(self.root / 'artifacts/final', ignore_errors=True)
            for number, options in enumerate(attempts, 1):
                self.attempt('hidden-1', number, **options)
            self.failures = failures
            before = self.snapshot()
            with self.subTest(case=case):
                code, out, _ = self.select()
                self.assertEqual(code, 0, out)
                record = self.record()
                self.assertEqual((record['selected'], record['attempt_count']), (expected, len(attempts)))
                self.assertEqual([entry['attempt'] for entry in record['attempts']], list(range(1, len(attempts) + 1)))
                self.assertEqual(record['order'], list(package.ATTEMPT_ORDER))
                self.assertIs(package.validate_selection(record, 'hidden-1', self.selected_files(record)), record)
                self.assertEqual(self.snapshot(), before)
                written = (self.root / 'artifacts/final/hidden-1/selection.json').read_bytes()
                self.select()  # Deterministic: a re-run reproduces the record byte for byte.
                self.assertEqual((self.root / 'artifacts/final/hidden-1/selection.json').read_bytes(), written)

    def test_diagnostics_count_rejections_and_declarations(self):
        self.attempt('hidden-1', 1, errors=2, declared=1)
        self.attempt('hidden-1', 2, status='failed', errors=3)
        self.assertEqual(self.select()[0], 0)
        self.assertEqual([entry['diagnostics'] for entry in self.record()['attempts']], [3, 3])

    def test_selection_waits_for_running_and_refuses_without_an_eligible_attempt(self):
        self.attempt('hidden-1', 1)
        self.attempt('hidden-1', 2, running=True)
        self.alive = True
        code, out, _ = self.select()
        self.assertEqual(code, 2)
        self.assertIn('"status": "running"', out)
        self.assertFalse((self.root / 'artifacts/final/hidden-1/selection.json').exists())
        # An exported builder still writing its provenance after the end event is running.
        (self.attempt('hidden-1', 3) / 'build-provenance.json').unlink()
        code, out, _ = self.select()
        self.assertEqual(code, 2)
        self.assertEqual(out.count('"status": "running"'), 2)
        shutil.rmtree(self.root / 'artifacts/final/hidden-1/attempt-3')
        # A builder that died without a manifest is ineligible, never still running.
        self.alive = False
        self.failures = {('attempt-1', 'package')}
        code, out, _ = self.select()
        self.assertEqual(code, 3)
        record = self.record()
        self.assertIsNone(record['selected'])
        self.assertEqual([entry['status'] for entry in record['attempts']], ['exported', 'no_manifest'])
        with self.assertRaisesRegex(ValueError, 'selects no attempt'):
            package.validate_selection(record, 'hidden-1', {})

    def test_selection_refuses_missing_or_ambiguous_attempts(self):
        self.attempt('hidden-1', 1)
        self.attempt('hidden-1', 3)
        code, _, error = self.select()
        self.assertEqual(code, 1)
        self.assertIn('none missing', error)
        self.attempt('hidden-1', 2)
        (self.root / 'artifacts/final/hidden-1/attempt-2/second-run').mkdir()
        (self.root / 'artifacts/final/hidden-1/attempt-2/second-run/trace.jsonl').write_text('{}\n')
        code, _, error = self.select()
        self.assertEqual(code, 1)
        self.assertIn('exactly one builder run', error)

    def test_selection_record_rejects_tampering(self):
        self.attempt('hidden-1', 1, errors=1)
        self.attempt('hidden-1', 2)
        self.select()
        files = self.selected_files(self.record())
        def mutated(change):
            record = self.record()
            change(record)
            return record
        for case, change in {
            'not the best': lambda r: r.update(selected=1),
            'best ineligible': lambda r: r['attempts'][1].update(package_validation=False),
            'diagnostics rewritten': lambda r: r['attempts'][0].update(diagnostics=0) or r.update(selected=1),
            'other run bytes': lambda r: r['attempts'][1].update(trace_sha256='0' * 64),
            'renumbered': lambda r: r['attempts'][0].update(attempt=3),
            'count': lambda r: r.update(attempt_count=3),
            'order': lambda r: r.update(order=list(reversed(package.ATTEMPT_ORDER))),
            'label': lambda r: r.update(label='hidden-2'),
            'integer flag': lambda r: r['attempts'][1].update(exported=1),
        }.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                package.validate_selection(mutated(change), 'hidden-1', files)

    def test_synthetic_end_to_end_selection_manifest_and_package(self):
        # Attempt directories come from the launcher (with no process started).
        self.launch('--labels', 'hidden-1', '--attempts', '3')
        self.launch('--labels', 'hidden-2', '--attempts', '1')
        try:
            import test_package_submission as package_tests
        except ModuleNotFoundError:
            from tests import test_package_submission as package_tests
        fixture = package_tests.PackageTests(methodName='runTest')
        fixture.root = self.root
        final_dir = self.root / 'artifacts/final'

        def place(label, number, run_id):
            run = final_dir / label / f'attempt-{number}' / run_id
            (self.root / label).rename(run)
            (self.root / (label + '.json')).rename(run / 'submission.workflow.json')
            (self.root / (label + '.jsonl')).rename(run / 'trace.jsonl')

        fixture.evidence()
        trace = self.root / 'hidden-1.jsonl'
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        events.insert(7, {'event': 'policy_validation', 'passed': False, 'errors': ['synthetic repaired rule'],
                          'time': events[7]['time']})
        trace.write_text('\n'.join(json.dumps(event) for event in events))
        fixture.rebind_provenance('hidden-1')
        place('hidden-1', 1, '20261011T170201000000Z')
        shutil.rmtree(self.root / 'hidden-2')
        failed = final_dir / 'hidden-1/attempt-2/20261011T170202000000Z'
        failed.mkdir()
        (failed / 'manifest.json').write_text(json.dumps({'mode': 'live', 'status': 'failed', 'error': 'synthetic'}))
        (failed / 'trace.jsonl').write_text('{"event": "start", "time": "2026-10-10T12:00:00Z"}\n'
                                            '{"event": "end", "status": "failed", "time": "2026-10-10T12:00:01Z"}\n')
        fixture.evidence()
        place('hidden-1', 3, '20261011T170203000000Z')
        place('hidden-2', 1, '20261011T170204000000Z')

        git = patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40, returncode=0))
        with git, patch.object(final.verifier, 'check_recompilation'):
            code, out, _ = self.run_main('select')
            self.assertEqual(code, 0, out)
            self.assertEqual((self.record()['selected'], self.record()['attempt_count']), (3, 3))
            self.assertEqual([(entry['exported'], entry['package_validation'], entry['diagnostics'])
                              for entry in self.record()['attempts']], [(True, True, 1), (False, False, 0), (True, True, 0)])
            self.assertEqual((self.record('hidden-2')['selected'], self.record('hidden-2')['attempt_count']), (1, 1))
            self.assertEqual(self.run_main('manifest', '--team', 'synthetic')[0], 0)
            manifest_path = self.root / 'artifacts/submission/manifest.json'
            value = json.loads(manifest_path.read_text())
            self.assertTrue(value['runs']['hidden-1'].startswith('artifacts/final/hidden-1/attempt-3/'))
            self.assertIn('--output-dir artifacts/final/hidden-1/attempt-3', value['run_command'])
            self.assertEqual(value['selections']['hidden-2'], 'artifacts/final/hidden-2/selection.json')
            retained = package.check_manifest(value)
            self.assertIn('selections/hidden-1.json', {name for _, name in retained})
            # Every listed attempt must still be on disk, unchanged.
            preserved = failed / 'trace.jsonl'
            original = preserved.read_bytes()
            preserved.write_bytes(original + b'\n')
            with self.assertRaisesRegex(ValueError, 'attempt 2 was not preserved'):
                package.check_manifest(value)
            preserved.unlink()
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                package.check_manifest(value)
            preserved.write_bytes(original)
            with patch.object(package, 'verify_committed'), contextlib.redirect_stdout(io.StringIO()):
                package.main(['--manifest', str(manifest_path), '--output', str(self.root / 'artifacts/final.zip')])
        with zipfile.ZipFile(self.root / 'artifacts/final.zip') as archive:
            names = archive.namelist()
            index = json.loads(archive.read('package-index.json'))
        self.assertEqual(sorted(name for name in names if name.endswith('.jsonl')),
                         ['traces/hidden-1.jsonl', 'traces/hidden-2.jsonl'])
        self.assertEqual(index['submission']['selections'],
                         {'hidden-1': 'selections/hidden-1.json', 'hidden-2': 'selections/hidden-2.json'})
        self.assertIn('selections/hidden-1.json', index['evidence_files'])


if __name__ == '__main__':
    unittest.main()

"""T18 parallel attempts: launch, declared-order selection, manifest and packaging.

Every run here is synthetic. No builder, model, platform or simulation is started.
"""
import argparse
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


def platform_error(message):
    from builder.platform import PlatformError
    return PlatformError(message)


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
        self.client, self.sleep = MagicMock(), MagicMock()  # Staging probes and pauses never really happen here.
        self.builds, self.relaxed = [], ['--declarations', 'relaxed']  # Builder process counts; the builder's option.

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = final.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def launch(self, *extra):
        popen = MagicMock(side_effect=[SimpleNamespace(pid=7100 + number) for number in range(20)])
        with patch.object(final.subprocess, 'Popen', popen), patch.object(final.shutil, 'which', return_value='/bin/codex'), \
             patch.object(final, '_alive', side_effect=lambda pid: self.alive), \
             patch.object(final, '_codex_builds', side_effect=lambda: self.builds.pop(0) if self.builds else 0), \
             patch.object(final, '_relaxed_option', side_effect=lambda: self.relaxed), \
             patch.object(final, 'FreyaClient', self.client), patch.object(final.time, 'sleep', self.sleep), \
             patch.dict(final.os.environ, {'OPENAI_API_KEY': 'synthetic-openai-value'}):
            final.os.environ.pop('FREYA_API_KEY', None)  # Only the --env-file supplies the key here.
            result = self.run_main('launch', '--env-file', str(self.env_file), '--model', 'synthetic-model',
                                   '--simulation-lease', '/shared/.simulation.lock',
                                   *([] if '--round' in extra else ['--round', '1']), *extra)
        return result, popen

    @staticmethod
    def started(popen):
        """(brief, effort, relaxed option) of each start, in order."""
        return [(argv[argv.index('--brief') + 1], argv[argv.index('--reasoning-effort') + 1],
                 argv[argv.index('--reasoning-effort') + 2:]) for argv in (call.args[0] for call in popen.call_args_list)]

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in (self.root / 'artifacts/final').rglob('*') if path.is_file() and path.name != 'selection.json'}

    def test_round_one_starts_strict_high_and_medium_per_brief_in_own_directories_and_slots(self):
        (code, _, _), popen = self.launch()
        self.assertEqual(code, 0)
        self.assertEqual(popen.call_count, 4)
        slots = set()
        for call in popen.call_args_list:
            argv, kwargs = call.args[0], call.kwargs
            self.assertEqual(argv[1], 'builder/build.py')
            label = 'hidden-1' if argv[argv.index('--brief') + 1] == 'brief.md' else 'hidden-2'
            directory = argv[argv.index('--output-dir') + 1]
            self.assertRegex(directory, rf'^artifacts/final/{label}/attempt-[12]$')
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
            self.assertEqual((record['round'], record['mode'], record['reasoning_effort'], record['builder_processes_before_start']),
                             (1, 'strict', argv[argv.index('--reasoning-effort') + 1], 0))
        self.assertEqual(len(slots), 4)
        self.assertNotEqual(final.os.environ.get('FREYA_API_KEY'), 'synthetic-launch-key')
        # The matrix, interleaved and spread: both briefs start at high first, and starts are a pause apart.
        self.assertEqual(self.started(popen), [('brief.md', 'high', []), ('brief-2.md', 'high', []),
                                               ('brief.md', 'medium', []), ('brief-2.md', 'medium', [])])
        self.assertEqual([call.args for call in self.sleep.call_args_list], [(60,)] * 3)
        # One staging probe before each start, recorded in its launch record and the health log.
        self.assertEqual(self.client.call_args.kwargs['api_key'], 'synthetic-launch-key')
        health = [json.loads(line) for line in (self.root / 'artifacts/final/health.jsonl').read_text().splitlines()]
        self.assertEqual([record['ok'] for record in health], [True] * 4)
        self.assertIs(json.loads((self.root / 'artifacts/final/hidden-2/attempt-2/launch.json').read_text())
                      ['staging_health']['ok'], True)

    def test_starts_wait_while_five_builder_processes_run(self):
        self.builds = [5, 7, 4, 3]  # Process counts seen before the first start (three checks) and the second.
        (code, out, _), popen = self.launch('--labels', 'hidden-1')
        self.assertEqual((code, popen.call_count), (0, 2))
        self.assertEqual([call.args for call in self.sleep.call_args_list], [(20,), (20,), (60,)])
        self.assertEqual(out.count('"waiting"'), 2)
        self.assertEqual([json.loads((self.root / f'artifacts/final/hidden-1/attempt-{number}/launch.json').read_text())
                          ['builder_processes_before_start'] for number in (1, 2)], [4, 3])
        # The count is the rules' own: every process whose command line names builder/build.py.
        with patch.object(final.subprocess, 'run', return_value=SimpleNamespace(stdout='4101\n4102\n')) as run:
            self.assertEqual(final._codex_builds(), 2)
        self.assertEqual(run.call_args.args[0], ['pgrep', '-f', 'builder/build.py'])

    def test_relaxed_option_comes_from_the_frozen_builder_parser(self):
        def parser(*arguments):
            out = argparse.ArgumentParser()
            out.add_argument('--reasoning-effort', choices=('low', 'medium', 'high', 'xhigh'))
            for name, options in arguments:
                out.add_argument(name, **options)
            return out
        for arguments, expected in (
                ((), None),
                ((('--declarations', {'choices': ('strict', 'relaxed'), 'default': 'strict'}),), ['--declarations', 'relaxed'])):
            with self.subTest(arguments=arguments), patch.object(final, 'builder_parser', lambda: parser(*arguments)):
                self.assertEqual(final._relaxed_option(), expected)
        self.assertIn(type(final._relaxed_option()), (list, type(None)))  # The real builder parser is readable.

    def test_staging_failures_are_recorded_by_status_and_never_block_a_launch(self):
        self.client.return_value.request.side_effect = platform_error(
            'GET /api/v2/workspaces: HTTP 504: <html>opaque-gateway-body</html>')
        (code, _, _), popen = self.launch('--labels', 'hidden-1', '--stagger-seconds', '0')
        self.assertEqual(code, 0)
        self.assertEqual(popen.call_count, 2)
        log = (self.root / 'artifacts/final/health.jsonl').read_text()
        self.assertEqual([json.loads(line)['error'] for line in log.splitlines()], ['HTTP 504', 'HTTP 504'])
        self.assertNotIn('opaque-gateway-body', log + (self.root / 'artifacts/final/hidden-1/attempt-1/launch.json').read_text())
        self.client.return_value.request.side_effect = None
        with patch.object(final, 'FreyaClient', self.client):
            code, out, _ = self.run_main('health', '--env-file', str(self.env_file))
        self.assertEqual((code, json.loads(out)['ok']), (0, True))
        self.assertEqual(len((self.root / 'artifacts/final/health.jsonl').read_text().splitlines()), 3)

    def test_launch_refuses_over_the_machine_cap_or_without_credentials(self):
        self.launch('--labels', 'hidden-1')
        self.alive = True  # Both hidden-1 builders are still running.
        before = self.snapshot()
        (code, _, error), popen = self.launch('--labels', 'hidden-2', '--max-running', '3')
        self.assertEqual(code, 1)
        self.assertIn('2 builders are running; 2 more would exceed --max-running 3', error)
        popen.assert_not_called()
        self.alive = False
        self.env_file.write_text('FREYA_API_URL=https://example.invalid\n')
        (code, _, error), popen = self.launch('--labels', 'hidden-2')
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

    def test_round_two_runs_relaxed_only_where_round_one_left_no_selected_attempt(self):
        (code, _, error), popen = self.launch('--round', '2')
        self.assertEqual(code, 1)
        self.assertIn('hidden-1: round 2 runs only after round 1', error)
        self.attempt('hidden-1', 1, status='failed')
        self.attempt('hidden-1', 2, status='failed', effort='medium')
        self.attempt('hidden-2', 1)
        self.attempt('hidden-2', 2, status='failed', effort='medium')
        before = self.snapshot()
        (code, _, error), popen = self.launch('--round', '2', '--labels', 'hidden-1')
        self.assertEqual(code, 1)
        self.assertIn('run select once every attempt has finished', error)
        self.assertEqual(self.select('hidden-1', 'hidden-2')[0], 3)
        # A brief with a selected strict export gets no further round.
        for round_number in ('1', '2'):
            (code, _, error), popen = self.launch('--round', round_number, '--labels', 'hidden-2')
            self.assertEqual(code, 1)
            self.assertIn(f'hidden-2: attempt 1 (strict) is selected; round {round_number} cannot improve it', error)
        # Round 2 needs the frozen builder's relaxed declaration option.
        self.relaxed = None
        (code, _, error), popen = self.launch('--round', '2', '--labels', 'hidden-1')
        self.assertEqual(code, 1)
        self.assertIn('no relaxed declaration option', error)
        self.assertEqual(self.snapshot(), before)
        self.relaxed = ['--declarations', 'relaxed']
        (code, _, _), popen = self.launch('--round', '2', '--labels', 'hidden-1')
        self.assertEqual(code, 0)
        self.assertEqual(self.started(popen), [('brief.md', 'high', ['--declarations', 'relaxed']),
                                               ('brief.md', 'medium', ['--declarations', 'relaxed'])])
        launched = [json.loads((self.root / f'artifacts/final/hidden-1/attempt-{number}/launch.json').read_text())
                    for number in (3, 4)]
        self.assertEqual([(record['round'], record['mode'], record['reasoning_effort']) for record in launched],
                         [(2, 'relaxed', 'high'), (2, 'relaxed', 'medium')])
        # The relaxed export is selected; then only another strict round could still improve the brief.
        for number in (3, 4):
            shutil.rmtree(self.root / f'artifacts/final/hidden-1/attempt-{number}')
        self.attempt('hidden-1', 3, mode='relaxed')
        self.attempt('hidden-1', 4, status='failed', mode='relaxed', effort='medium')
        self.assertEqual(self.select('hidden-1')[0], 0)
        self.assertEqual((self.record()['selected'], self.record()['attempts'][2]['mode']), (3, 'relaxed'))
        (code, _, error), popen = self.launch('--round', '2', '--labels', 'hidden-1')
        self.assertEqual(code, 1)
        self.assertIn('attempt 3 (relaxed) is selected; round 2 cannot improve it', error)
        (code, _, _), popen = self.launch('--labels', 'hidden-1')
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0][call.args[0].index('--output-dir') + 1] for call in popen.call_args_list],
                         ['artifacts/final/hidden-1/attempt-5', 'artifacts/final/hidden-1/attempt-6'])

    def attempt(self, label, number, *, status='exported', errors=0, declared=0,
                finished='2026-10-11T17:20:00+00:00', running=False, mode='strict', effort='high'):
        directory = self.root / 'artifacts/final' / label / f'attempt-{number}'
        run = directory / f'20261011T1700{number:02d}000000Z'
        run.mkdir(parents=True)
        (directory / 'launch.json').write_text(json.dumps({'label': label, 'attempt': number, 'mode': mode,
                                                           'reasoning_effort': effort, 'pid': 7000 + number,
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
            'strict before relaxed': ([dict(mode='relaxed', finished=early), dict(errors=2, declared=1, finished=late)], set(), 2),
            'relaxed only without an eligible strict export': (
                [dict(status='failed', finished=early), dict(errors=3, finished=late), dict(mode='relaxed', errors=1, finished=late)],
                {('attempt-2', 'package')}, 3),
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
                self.assertEqual([(entry['mode'], entry['reasoning_effort']) for entry in record['attempts']],
                                 [(options.get('mode', 'strict'), 'high') for options in attempts])
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
            'selected demoted to relaxed': lambda r: r['attempts'][1].update(mode='relaxed'),
            'unknown mode': lambda r: r['attempts'][0].update(mode='lenient'),
            'effort type': lambda r: r['attempts'][0].update(reasoning_effort=3),
        }.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                package.validate_selection(mutated(change), 'hidden-1', files)

    def test_synthetic_end_to_end_matrix_selection_manifest_and_package(self):
        # Round 1 attempt directories come from the launcher (with no process started).
        self.launch()
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

        def fail(label, number):
            failed = final_dir / label / f'attempt-{number}' / f'20261011T1702{number:02d}000000Z'
            failed.mkdir()
            (failed / 'manifest.json').write_text(json.dumps({'mode': 'live', 'status': 'failed', 'error': 'synthetic'}))
            (failed / 'trace.jsonl').write_text('{"event": "start", "time": "2026-10-10T12:00:00Z"}\n'
                                                '{"event": "end", "status": "failed", "time": "2026-10-10T12:00:01Z"}\n')
            return failed

        fixture.evidence()
        trace = self.root / 'hidden-1.jsonl'
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        events.insert(7, {'event': 'policy_validation', 'passed': False, 'errors': ['synthetic repaired rule'],
                          'time': events[7]['time']})
        trace.write_text('\n'.join(json.dumps(event) for event in events))
        fixture.rebind_provenance('hidden-1')
        place('hidden-1', 1, '20261011T170201000000Z')  # strict high, one diagnostic
        shutil.rmtree(self.root / 'hidden-2')
        failed = fail('hidden-2', 1)  # Neither strict attempt of hidden-2 exports.
        fail('hidden-2', 2)
        fixture.evidence()
        place('hidden-1', 2, '20261011T170202000000Z')  # strict medium, clean

        git = patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40, returncode=0))
        with git, patch.object(final.verifier, 'check_recompilation'):
            code, out, _ = self.run_main('select')
            self.assertEqual(code, 3, out)
            self.assertEqual((self.record()['selected'], self.record()['attempt_count']), (2, 2))
            self.assertEqual([(entry['mode'], entry['reasoning_effort'], entry['diagnostics'])
                              for entry in self.record()['attempts']], [('strict', 'high', 1), ('strict', 'medium', 0)])
            self.assertIsNone(self.record('hidden-2')['selected'])
            # Round 2 only for the brief left without a strict export.
            (code, _, error), popen = self.launch('--round', '2')
            self.assertEqual(code, 1)
            self.assertIn('hidden-1: attempt 2 (strict) is selected', error)
            popen.assert_not_called()
            (code, _, _), popen = self.launch('--round', '2', '--labels', 'hidden-2')
            self.assertEqual(code, 0)
            place('hidden-2', 3, '20261011T173003000000Z')  # relaxed high, clean
            fail('hidden-2', 4)
            code, out, _ = self.run_main('select')
            self.assertEqual(code, 0, out)
            record = self.record('hidden-2')
            self.assertEqual((record['selected'], record['attempt_count']), (3, 4))
            self.assertEqual([(entry['mode'], entry['reasoning_effort'], entry['exported']) for entry in record['attempts']],
                             [('strict', 'high', False), ('strict', 'medium', False), ('relaxed', 'high', True),
                              ('relaxed', 'medium', False)])
            self.assertEqual(self.run_main('manifest', '--team', 'synthetic')[0], 0)
            manifest_path = self.root / 'artifacts/submission/manifest.json'
            value = json.loads(manifest_path.read_text())
            self.assertTrue(value['runs']['hidden-1'].startswith('artifacts/final/hidden-1/attempt-2/'))
            self.assertTrue(value['runs']['hidden-2'].startswith('artifacts/final/hidden-2/attempt-3/'))
            commands = value['run_command'].splitlines()
            self.assertIn('--output-dir artifacts/final/hidden-1/attempt-2', commands[0])
            self.assertTrue(commands[0].endswith('--reasoning-effort medium'))
            self.assertTrue(commands[1].endswith('--reasoning-effort high --declarations relaxed'))
            self.assertEqual(value['selections']['hidden-2'], 'artifacts/final/hidden-2/selection.json')
            retained = package.check_manifest(value)
            self.assertIn('selections/hidden-1.json', {name for _, name in retained})
            # Every listed attempt must still be on disk, unchanged.
            preserved = failed / 'trace.jsonl'
            original = preserved.read_bytes()
            preserved.write_bytes(original + b'\n')
            with self.assertRaisesRegex(ValueError, 'attempt 1 was not preserved'):
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
            packaged = json.loads(archive.read('selections/hidden-2.json'))
        self.assertEqual(sorted(name for name in names if name.endswith('.jsonl')),
                         ['traces/hidden-1.jsonl', 'traces/hidden-2.jsonl'])
        self.assertEqual(index['submission']['selections'],
                         {'hidden-1': 'selections/hidden-1.json', 'hidden-2': 'selections/hidden-2.json'})
        self.assertIn('selections/hidden-1.json', index['evidence_files'])
        self.assertEqual(packaged, record)  # Every attempt's mode and effort travel with the package.


if __name__ == '__main__':
    unittest.main()

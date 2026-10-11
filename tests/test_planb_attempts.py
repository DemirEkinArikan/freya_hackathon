"""Plan B attempts: staggered fake builder processes, the shared lease, the hard timeout, selection and export.

Every builder here is a fake process from a temporary planb folder. No model, Freya workspace or simulation is used.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tools import planb_attempts

FAKE_BUILDER = r'''
import fcntl, json, os, pathlib, subprocess, sys, time
kit = pathlib.Path.cwd()
name = kit.parent.name
plan = json.loads(pathlib.Path(sys.argv[1]).read_text())[name.rsplit("-a", 1)[1]]
def note(**fields):
    with open(os.environ["PLANB_FAKE_LOG"], "a") as log:
        log.write(json.dumps({"attempt": name, **fields}) + "\n")
seeded = kit / ".freya-agents.json"
note(event="start", time=time.time(), argv=sys.argv[1:], lease=os.environ.get("FREYA_LEASE"),
     concurrency=os.environ.get("FREYA_SIM_CONCURRENCY"), claude=os.environ.get("ANTHROPIC_API_KEY"),
     config=os.environ.get("CLAUDE_CONFIG_DIR"), leaked=[k for k in ("FREYA_API_KEY_2", "CLAUDECODE") if k in os.environ],
     seeded=json.loads(seeded.read_text()) if seeded.exists() else None)
if plan.get("hold"):   # one simulation: the kit client holds the workspace lease meanwhile
    with open(os.environ["FREYA_LEASE"], "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        began = time.time(); time.sleep(plan["hold"]); note(event="lease", start=began, end=time.time())
slot = pathlib.Path(sys.argv[1]).stem
(kit / "traces").mkdir()
(kit / "traces" / "20261011-200000.jsonl").write_text(json.dumps({"start": "now", "model": "fake"}) + "\n" + json.dumps({"echo": os.environ["FREYA_API_KEY"]}) + "\n")
(kit / "traces" / "20261011-200000.txt").write_text("trace notes\n")
if plan.get("workflow"):
    ids = {"workflow_id": plan["workflow"], "agent_id": "agent-" + plan["workflow"]}
    (kit / ".freya-agents.json").write_text(json.dumps({slot: ids}))
    (kit / "out").mkdir()
    (kit / "out" / (slot + ".export.json")).write_text(json.dumps({**ids, "workflow": {}}))
if plan.get("total"):
    (kit / "results").mkdir()
    (kit / "results" / "log.jsonl").write_text(json.dumps({"passed": 0, "total": 9}) + "\n" + json.dumps({"passed": plan["passed"], "total": plan["total"]}) + "\n")
if plan.get("grandchild"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    note(event="grandchild", pid=child.pid)
time.sleep(plan.get("sleep", 0))
sys.exit(plan.get("exit", 0))
'''
FAKE_FREYA = '''
import json
def export_workflow(workflow_id, path):
    open(path, "w").write(json.dumps({"workflow_id": workflow_id, "exported": True})); return path
'''
CLAUDE_KEY, FREYA_KEY, SECOND_KEY, TEAM_KEY = 'sk-ant-synthetic-0001', 'fak_synthetic_one_0002', 'fak_synthetic_two_0003', 'team-key-synthetic-0004'


class PlanBAttemptsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        planb = self.root / 'planb'
        (planb / '.claude' / 'skills').mkdir(parents=True)
        (planb / 'agent_builder.py').write_text(FAKE_BUILDER)
        (planb / 'freya.py').write_text(FAKE_FREYA)
        for name, value in (('PLANB', planb), ('ROOT', self.root)):
            patcher = patch.object(planb_attempts, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {'PLANB_FAKE_LOG': str(self.root / 'fake.log'), 'CLAUDECODE': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in [key for key in os.environ if key.startswith(('FREYA_', 'ANTHROPIC_'))]:
            del os.environ[key]
        (self.root / 'private.env').write_text(f'FREYA_API_KEY={FREYA_KEY}\nFREYA_API_KEY_2={SECOND_KEY}\nFREYA_BANK_TEAM_KEY={TEAM_KEY}\n')
        (self.root / 'claude.key').write_text(CLAUDE_KEY + '\n')
        (self.root / 'api.json').write_text('{}')

    def brief(self, label, plan):
        path = self.root / f'{label}.md'
        path.write_text(json.dumps(plan))
        return ['--brief', str(path), str(self.root / 'api.json')]

    def run_main(self, *argv, stagger=0.2, timeout=20):
        options = ['--stagger', str(stagger), '--timeout', str(timeout), '--work-dir', str(self.root / 'work'),
                   '--out-dir', str(self.root / 'out'), '--env-file', str(self.root / 'private.env'),
                   '--lease', str(self.root / 'lease' / '.simulation.lock'), '--anthropic-key-file', str(self.root / 'claude.key')]
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = planb_attempts.main([*argv, *options])
        for secret in (CLAUDE_KEY, FREYA_KEY, SECOND_KEY, TEAM_KEY):
            self.assertNotIn(secret, out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def events(self, kind):
        lines = (self.root / 'fake.log').read_text().splitlines()
        return [event for event in map(json.loads, lines) if event['event'] == kind]

    def summary(self):
        return json.loads((self.root / 'out' / 'planb-summary.json').read_text())

    def test_selects_by_pass_rate_then_elapsed_and_exports_with_redacted_trace(self):
        code, _, _ = self.run_main(
            *self.brief('one', {'1': {'workflow': 'wf-1a', 'passed': 3, 'total': 4},
                                '2': {'workflow': 'wf-1b', 'passed': 4, 'total': 4, 'sleep': 0.8}}),
            *self.brief('two', {'1': {'workflow': 'wf-2a', 'passed': 4, 'total': 4, 'sleep': 1.5},
                                '2': {'workflow': 'wf-2b', 'passed': 4, 'total': 4}}),
            *self.brief('three', {'1': {'exit': 1}, '2': {'passed': 2, 'total': 2}}))
        self.assertEqual(code, 3)   # brief three never saved a workflow
        briefs = self.summary()['briefs']
        self.assertEqual((briefs['one']['selected'], briefs['two']['selected'], briefs['three']['selected']), (2, 2, None))
        self.assertNotIn('workflow', briefs['three'])
        first = briefs['one']['attempts'][0]
        self.assertEqual((first['exit'], first['timed_out'], first['pass_rate'], first['workflow_id'], first['agent_id']),
                         (0, False, 0.75, 'wf-1a', 'agent-wf-1a'))
        self.assertTrue(first['workflow_file'].endswith('one.export.json') and first['trace'].endswith('.jsonl'))
        self.assertEqual(briefs['three']['attempts'][0]['exit'], 1)
        out = self.root / 'out'
        self.assertEqual(briefs['one']['workflow'], str(out / 'one.workflow.json'))
        self.assertEqual(json.loads((out / 'one.workflow.json').read_text()), {'workflow_id': 'wf-1b', 'exported': True})
        self.assertEqual(json.loads((out / 'two.workflow.json').read_text())['workflow_id'], 'wf-2b')
        trace = (out / 'one.trace.jsonl').read_text()
        self.assertNotIn(FREYA_KEY, trace)
        self.assertIn('[REDACTED]', trace)
        self.assertEqual(briefs['one']['trace_redactions'], 1)
        self.assertEqual((out / 'one.trace.txt').read_text(), 'trace notes\n')
        self.assertEqual(Path(briefs['one']['trace']), out / 'one.trace.jsonl')

    def test_starts_are_staggered_and_share_one_lease(self):
        hold = {'workflow': 'wf', 'hold': 0.4}
        code, _, _ = self.run_main(*self.brief('one', {'1': hold, '2': hold}), *self.brief('two', {'1': hold, '2': hold}), stagger=0.3)
        self.assertEqual(code, 0)
        launched = sorted((datetime.fromisoformat(attempt['started_at']), f'{label}-a{attempt["attempt"]}')
                          for label, brief in self.summary()['briefs'].items() for attempt in brief['attempts'])
        self.assertEqual([name for _, name in launched], ['one-a1', 'two-a1', 'one-a2', 'two-a2'])
        gaps = [(later - earlier).total_seconds() for (earlier, _), (later, _) in zip(launched, launched[1:])]
        self.assertTrue(all(gap >= 0.29 for gap in gaps), gaps)
        starts = self.events('start')
        lease = str(self.root / 'lease' / '.simulation.lock')
        for event in starts:
            self.assertEqual((event['lease'], event['concurrency'], event['claude'], event['leaked']), (lease, '10', CLAUDE_KEY, []))
            self.assertEqual(event['argv'], [f'briefs/{event["attempt"][:-3]}.md', 'briefs/api.json'])
        self.assertEqual(len({event['config'] for event in starts}), 4)
        held = sorted((event['start'], event['end']) for event in self.events('lease'))
        self.assertEqual(len(held), 4)
        self.assertTrue(all(end <= start + 0.01 for (_, end), (start, _) in zip(held, held[1:])), held)

    def test_timeout_kills_the_builder_and_its_detached_children(self):
        started = time.monotonic()
        code, _, _ = self.run_main(*self.brief('one', {'1': {'workflow': 'wf-t', 'passed': 1, 'total': 2, 'grandchild': True, 'sleep': 60}}),
                                   '--attempts', '1', timeout=2)
        self.assertLess(time.monotonic() - started, 30)
        result = self.summary()['briefs']['one']['attempts'][0]
        self.assertTrue(result['timed_out'])
        self.assertNotEqual(result['exit'], 0)
        self.assertLess(result['elapsed_s'], 10)
        self.assertEqual((code, self.summary()['briefs']['one']['selected']), (0, 1))   # its saved workflow still counts
        pid = self.events('grandchild')[0]['pid']
        for _ in range(50):
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
            if not state or state.startswith('Z'):   # gone, or killed and waiting for init to reap it
                break
            time.sleep(0.1)
        else:
            self.fail('the detached child of a timed-out builder still runs')

    def test_each_attempt_reuses_its_own_workflow_on_the_next_run(self):
        plan = self.brief('one', {'1': {'workflow': 'wf-a'}, '2': {'workflow': 'wf-b'}})
        self.run_main(*plan)
        time.sleep(1.1)   # a new run directory
        self.run_main(*plan)
        seeded = {event['attempt']: event['seeded'] for event in self.events('start')}
        self.assertEqual(seeded, {'one-a1': {'one': {'workflow_id': 'wf-a', 'agent_id': 'agent-wf-a'}},
                                  'one-a2': {'one': {'workflow_id': 'wf-b', 'agent_id': 'agent-wf-b'}}})
        self.assertEqual(len([event for event in self.events('start') if event['seeded'] is None]), 2)

    def test_refuses_without_keys(self):
        (self.root / 'claude.key').unlink()
        code, out, err = self.run_main(*self.brief('one', {'1': {}}))
        self.assertEqual((code, out), (1, ''))
        self.assertIn('needs the Claude key file', err)
        self.assertFalse((self.root / 'fake.log').exists())


if __name__ == '__main__':
    unittest.main()

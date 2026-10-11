"""planb/build.py with a fake Claude Agent SDK, a fake Freya client and a fake mock. No model, platform or network."""
import asyncio
import contextlib
import dataclasses
import io
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from planb import build
import freya   # planb/freya.py, on the path through planb/build.py

MOCK = 'https://bank-mock.example.workers.dev'
KEYS = {'ANTHROPIC_API_KEY': 'sk-ant-test-0123456789abcdef', 'FREYA_API_KEY': 'fak_test0123456789', 'FREYA_BANK_TEAM_KEY': 'team-key-0123456789'}


@dataclasses.dataclass
class TextBlock:
    text: str


@dataclasses.dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclasses.dataclass
class AssistantMessage:
    content: list
    model: str
    message_id: str = None


@dataclasses.dataclass
class SystemMessage:
    subtype: str
    data: dict


@dataclasses.dataclass
class ResultMessage:
    subtype: str
    num_turns: int
    duration_ms: int
    total_cost_usd: float
    model_usage: dict
    result: str


class ClaudeAgentOptions:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Health:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def export(urls):
    def write(workflow_id, path):
        tool = lambda name, url: {'type': 'function', 'function': {'name': name, 'parameters': {'properties': {'url': {'type': 'string', 'default': url}}}}}
        w = {'nodes': {'setup': {'pre_actions': [{'function': {'name': 'code_exec'}}]},
                       'identity': {'tools': [tool('verify_identity', urls[0]), {'function': {'name': 'end_call'}}]}},
             'tools': [tool('close_call', urls[1])]}
        Path(path).write_text(json.dumps({'workflow_id': workflow_id, 'agent_id': 'ag-1', 'updated_at': 'now', 'workflow': w}))
        return path
    return write


class PlanBBuildTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.kit = self.repo / 'planb'
        (self.kit / '.claude/skills/build-phone-agent').mkdir(parents=True)
        (self.kit / '.claude/skills/build-phone-agent/SKILL.md').write_text('skill')
        (self.kit / 'builders').mkdir()
        (self.kit / 'builders/old_generator.py').write_text('# an earlier run')
        self.work = self.repo / 'work'
        self.case = self.repo / 'case'
        self.case.mkdir()
        (self.case / 'BRIEF.md').write_text('# Brief\n')
        (self.case / 'README.md').write_text('BRIEF.md is the start\n')
        self.seen = {}
        for name, value in (('KIT', self.kit), ('REPO', self.repo), ('WORKFLOW', self.repo / 'submission/final.workflow.json'),
                            ('TRACE', self.repo / 'traces/final.log'), ('WORK', self.work), ('KEYFILE', self.repo / 'no-key-file')):
            p = patch.object(build, name, value)
            p.start()
            self.addCleanup(p.stop)
        for p in (patch.dict(os.environ, {**KEYS, 'CLAUDECODE': '1', 'CLAUDE_EFFORT': 'low', 'FREYA_LEASE': str(self.repo / 'artifacts/.simulation.lock')}),
                  patch.object(build, 'read_local_environment', return_value={}),
                  patch.object(freya, 'workspace_id', return_value='ws-1')):
            p.start()
            self.addCleanup(p.stop)

    def sdk(self, query):
        module = types.ModuleType('claude_agent_sdk')
        for cls in (TextBlock, ToolUseBlock, AssistantMessage, SystemMessage, ResultMessage, ClaudeAgentOptions):
            setattr(module, cls.__name__, cls)
        module.query = query
        return patch.dict(sys.modules, {'claude_agent_sdk': module})

    def claude(self, save=True, wait=0):
        async def query(prompt, options):
            self.seen.update(prompt=prompt, options=options, env=dict(os.environ))
            kit = Path(options.cwd)
            try:
                yield SystemMessage('init', {'model': 'claude-opus-5-5', 'skills': ['build-phone-agent']})
                yield AssistantMessage([ToolUseBlock('t1', 'Read', {'file_path': 'out/case/INDEX.md'})], 'claude-opus-5-5', 'm1')
                yield AssistantMessage([TextBlock('Key ' + KEYS['FREYA_API_KEY'] + ' must never show.')], 'claude-opus-5-5', 'm1')
                await asyncio.sleep(wait)
                if save:
                    (kit / '.freya-agents.json').write_text(json.dumps({'final': {'workflow_id': 'wf-1', 'agent_id': 'ag-1', 'version': 2}}))
                    (kit / 'results').mkdir()
                    (kit / 'results/log.jsonl').write_text(json.dumps({'at': '20:10', 'agent_id': 'ag-1', 'passed': 5, 'total': 6, 'seconds': 120, 'failed': ['C-3']}) + '\n')
                    (kit / 'out/final.notes.txt').write_text('smoke 3/3, full 5/6\n')
                yield AssistantMessage([ToolUseBlock('t2', 'Bash', {'command': 'python3 tools/run_cases.py ag-1 --cases results/selftest.json'})], 'claude-sonnet-5-5', 'm2')
                yield ResultMessage('success', 9, 600000, 4.5, {'claude-opus-5-5': {'inputTokens': 10, 'outputTokens': 20, 'costUSD': 4.5},
                                                                'claude-haiku-5-5': {'inputTokens': 3, 'outputTokens': 1, 'costUSD': 0.01}}, 'workflow wf-1 agent ag-1')
            finally:
                self.seen['closed'] = True
        return query

    def run_build(self, query, health=None, urls=(MOCK + '/v1/verify_identity', MOCK + '/v1/close_call'), mock=MOCK):
        out = io.StringIO()
        opener = patch.object(build.urllib.request, 'urlopen', side_effect=health or (lambda *a, **k: Health({'base_path': '/v1', 'customers': 6, 'operations': [1, 2]})))
        with self.sdk(query), opener, patch.object(freya, 'export_workflow', side_effect=export(urls)) as exp, contextlib.redirect_stdout(out):
            code = build.main(str(self.case), mock)
        trace = (self.repo / 'traces/final.log').read_text()
        self.assertEqual(out.getvalue(), trace)
        for key in KEYS.values():
            self.assertNotIn(key, trace)
        return code, trace, exp

    def test_full_run_prints_the_official_trace_and_exports(self):
        code, trace, exp = self.run_build(self.claude())
        self.assertEqual(code, 0)
        exp.assert_called_once_with('wf-1', str(self.repo / 'submission/final.workflow.json'))
        for line in ('start: ', 'command: python3 planb/build.py', 'mock: tool base URL ' + MOCK + '/v1; the mock reports 6 customers and 2 operations',
                     'Freya simulations in workspace ws-1, at most 10 calls at once', 'case: 2 documents (2 text)',
                     'model call 1 (claude-opus-5-5): Read: out/case/INDEX.md', '      also: says: Key [key removed] must never show.',
                     'model call 2 (claude-sonnet-5-5): Bash: python3 tools/run_cases.py', 'model claude-opus-5-5: 10 input (+0 cached), 20 output tokens, $4.50\n',
                     "model claude-haiku-5-5: 3 input (+0 cached), 1 output tokens, $0.01; Claude Code's own helper calls",
                     'self-test at 20:10: 5 / 6 passed', '  smoke 3/3, full 5/6', 'created on Freya: workflow id wf-1, agent id ag-1',
                     'steps: 2 (setup, identity); tools: 3 (close_call, end_call, verify_identity)', 'tool URLs: 2, all on the mock',
                     'export: submission/final.workflow.json', 'result: OK'):
            self.assertIn(line, trace)
        self.assertIn(MOCK + '/v1 followed by the operation', self.seen['prompt'])
        env = self.seen['env']
        self.assertEqual((env['MOCK_URL'], env['FREYA_BANK_HOST']), (MOCK + '/v1', MOCK))
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(self.work / 'claude-config'))
        self.assertNotIn('CLAUDE_EFFORT', env)
        self.assertTrue(env['PATH'].startswith(str(Path(sys.executable).parent)))
        kit = Path(self.seen['options'].cwd)
        self.assertEqual(kit.parent.parent, self.work)   # a copy outside the repository
        self.assertTrue((kit / '.claude/skills/build-phone-agent/SKILL.md').is_file())
        self.assertFalse((kit / 'builders').exists())
        self.assertTrue((kit / 'out/case/INDEX.md').is_file())
        self.assertEqual(json.loads((self.work / 'freya-agents.json').read_text())['final']['workflow_id'], 'wf-1')
        self.assertEqual(len(list((self.repo / 'submission').glob('final-*.workflow.json'))), 1)
        self.assertEqual(len(list((self.repo / 'traces').glob('final-*.log'))), 1)

    def test_a_mock_url_with_a_path_is_kept(self):
        code, trace, _ = self.run_build(self.claude(), mock=MOCK + '/api/', urls=(MOCK + '/api/verify_identity', MOCK + '/api/close_call'))
        self.assertEqual(code, 0)
        self.assertEqual(self.seen['env']['MOCK_URL'], MOCK + '/api')

    def test_no_save_means_no_export(self):
        self.work.mkdir()
        (self.work / 'freya-agents.json').write_text(json.dumps({'final': {'workflow_id': 'wf-old', 'agent_id': 'ag-old', 'version': 1}}))
        code, trace, exp = self.run_build(self.claude(save=False))
        self.assertEqual(code, 1)
        exp.assert_not_called()
        self.assertIn('no workflow was saved in this run', trace)
        self.assertIn('result: FAILED', trace)

    def test_tool_urls_off_the_mock_fail_the_run(self):
        code, trace, _ = self.run_build(self.claude(), urls=(MOCK + '/v1/verify_identity', 'https://bank.example.com/v1/close_call'))
        self.assertEqual(code, 1)
        self.assertIn("NOT on the mock: ['https://bank.example.com/v1/close_call']", trace)

    def test_time_limit_stops_claude_and_still_exports(self):
        async def query(prompt, options):
            try:
                (Path(options.cwd) / '.freya-agents.json').write_text(json.dumps({'final': {'workflow_id': 'wf-1', 'agent_id': 'ag-1', 'version': 1}}))
                yield AssistantMessage([TextBlock('saved, now testing')], 'claude-opus-5-5', 'm1')
                await asyncio.sleep(30)
                yield AssistantMessage([TextBlock('never reached')], 'claude-opus-5-5', 'm2')
            finally:
                self.seen['closed'] = True
        with patch.object(build, 'LIMIT', 1):
            code, trace, exp = self.run_build(query)
        self.assertEqual(code, 0)
        self.assertTrue(self.seen['closed'])
        self.assertIn('stopped: the time limit (1 s) was reached', trace)
        self.assertNotIn('never reached', trace)
        exp.assert_called_once()

    def test_a_mock_that_does_not_answer_stops_before_claude(self):
        query = self.claude()
        code, trace, exp = self.run_build(query, health=urllib.error.URLError('connection refused'))
        self.assertEqual(code, 1)
        self.assertIn('does not answer', trace)
        self.assertNotIn('prompt', self.seen)
        exp.assert_not_called()

    def test_missing_keys_stop_at_once(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}), self.assertRaises(SystemExit) as stop:
            build.main(str(self.case), MOCK)
        self.assertIn('ANTHROPIC_API_KEY', str(stop.exception))


if __name__ == '__main__':
    unittest.main()

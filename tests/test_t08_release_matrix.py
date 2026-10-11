"""Evaluator matrix tests using controller outputs from mocked generation, not T07 evidence."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from builder.build import execute, parser
from builder.safety import digest
from test_replay_guarded import authored, frame, scenario
from development_protocol_samples import protocol_response
from tools.t08_release_matrix import ROOT, MatrixError, REQUIREMENTS, evaluate, load_candidate, _relevant

TEXT = 'Independent invented renamed-operation contract witness.'


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


class ReleaseMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'artifacts').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='t08-matrix-', dir=ROOT / 'artifacts')
        cls.directory = Path(cls.temp.name)
        _, context, api, cls.aliases = authored(True)
        cls.plan = deepcopy(context['plan'])
        del cls.plan['nodes']['callback']
        cls.plan['nodes']['talk']['transitions'] = [row for row in cls.plan['nodes']['talk']['transitions']
                                                   if row['target_node'] != 'callback']
        profile = {key: deepcopy(row['value']) for key, row in context['contract']['active_rules'].items()}
        profile['execution_actions'] = [node['action'] for node in cls.plan['nodes'].values()
                                         if node['node_type'] == 'guard_action']
        api['paths']['/create_promise']['post']['responses']['503'] = {'description': 'No promise has been recorded.'}
        profile['nonpersistent_failures'][cls.aliases['create_promise']][0]['api_source'] = {
            'pointer': '/paths/~1create_promise/post/responses/503/description', 'quote': 'No promise has been recorded.'}
        cls.brief = cls.directory / 'invented-aliases.txt'
        cls.brief.write_text(TEXT, encoding='utf-8')
        cls.api = cls.directory / 'api.json'
        write(cls.api, api)
        policy = {'version': 1, 'goal': context['contract']['goal'],
                  'allowed_actions': [row['name'] for row in context['contract']['permitted_operations']],
                  'sources': [{'id': 'brief-1', 'file': cls.brief.name, 'sha256': digest(TEXT)}],
                  'rules': [{'id': key, 'kind': key, 'value': value, 'source': {'id': 'brief-1', 'quote': TEXT}}
                            for key, value in profile.items()], 'unsupported_constraints': []}
        policy_path = cls.directory / 'input-policy.json'
        write(policy_path, policy)
        generator = Mock()
        generator.generate_plan.side_effect = lambda prompt, *, contract_sha256: {
            'plan': deepcopy(cls.plan), 'contract_sha256': contract_sha256}
        args = parser().parse_args(['--brief', str(cls.brief), '--tool-api', str(cls.api),
                                   '--policy-file', str(policy_path), '--output-dir', str(cls.directory / 'runs'), '--generate-only'])
        manifest = execute(args, generator=generator, client_factory=lambda **kw: (_ for _ in ()).throw(AssertionError('no network')))
        cls.run_dir = ROOT / manifest['run_directory']
        cls.inputs = [{'id': 'brief-1', 'path': str(cls.brief), 'sha256': digest(TEXT)}]
        cls.candidate = load_candidate(cls.run_dir, cls.api, cls.inputs)
        cls.context = cls.candidate['values']['guard_context']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.candidate = deepcopy(type(self).candidate)
        self.cases = {'version': 1, 'candidate_bindings': deepcopy(self.candidate['bindings']),
                      'source_inputs': deepcopy(self.inputs), 'cases': []}

    def ready(self, script=None, requirements=('confirmed_final_terms',), assertions=None):
        # Independent literal terms and calls, never learned from the result.
        script = script or scenario(self.context, self.aliases, renamed=True)
        a = self.aliases
        expected = [
            {'tool': a['verify_identity'], 'arguments': {'input_customer_id': 'synthetic-session', 'input_field': 'challenge_A', 'input_answer': 'synthetic-answer'}},
            {'tool': a['verify_identity'], 'arguments': {'input_customer_id': 'synthetic-session', 'input_field': 'challenge_B', 'input_answer': 'synthetic-answer'}},
            {'tool': a['get_account'], 'arguments': {'input_customer_id': 'synthetic-session'}},
            {'tool': a['create_promise'], 'arguments': {'input_customer_id': 'synthetic-session', 'input_product_id': 'synthetic-product', 'input_amount': 20.0, 'input_date': '2031-05-06'}},
            {'tool': a['close_call'], 'arguments': {'input_customer_id': 'synthetic-session', 'input_outcome': 'C_A'}},
        ]
        row = {'id': 'confirmed-synthetic-terms', 'requirements': list(requirements), 'status': 'ready',
               'source_review': {'rationale': 'Independent synthetic case checks exact confirmed terms through original aliases.',
                                 'source_refs': [{'id': 'brief-1', 'quote': TEXT}]},
               'script': script, 'expected_calls': expected,
               'assertions': assertions or [{'path': '/final_state/recorded_promises', 'equals': {'synthetic-product': {'amount_cents': 2000, 'date': '2031-05-06'}}}]}
        self.cases['cases'].append(row)
        return row

    def test_original_generation_bindings_and_order_are_retained(self):
        row = self.ready()
        result = evaluate(self.candidate, self.cases)
        case = result['cases'][0]
        self.assertEqual(case['status'], 'PASS')
        self.assertEqual(result['status'], 'PENDING')
        self.assertEqual(result['requirements']['confirmed_final_terms']['local_assertion_status'], 'PASS')
        self.assertEqual(case['replay']['calls'][3]['arguments'], row['expected_calls'][3]['arguments'])
        self.assertIn('bg_commit_confirm', [step['node'] for step in case['replay']['steps']])
        self.assertTrue(result['original_artifact_recompilation_verified'])
        for name in ('source_semantics_verified', 't07_acceptance_verified', 'task_acceptance_verified',
                     'issue_closure_authorized', 'hosted_behavior_verified', 'network_executed', 'model_executed', 'builder_budget_extended'):
            self.assertFalse(result[name], name)
        for name in ('policy_sha256', 'plan_sha256', 'contract_sha256', 'execution_policy_sha256',
                     'action_bindings_sha256', 'workflow_sha256', 'guard_context_sha256', 'trace_sha256',
                     'input_bindings_sha256', 'build_provenance_sha256'):
            self.assertIn(name, result['candidate_bindings'])
        self.assertEqual(result['cases_sha256'], digest(self.cases))
        self.assertIn('trace.jsonl', result['raw_file_sha256'])

    def test_missing_and_unsupported_requirements_remain_pending(self):
        self.cases['cases'] = [{'id': key, 'requirements': [key], 'status': 'unsupported',
                                'reason': 'Original candidate integration is still pending.'} for key in REQUIREMENTS]
        with patch('tools.t08_release_matrix.replay', side_effect=AssertionError('pending cannot execute')):
            result = evaluate(self.candidate, self.cases)
        self.assertEqual(result['status'], 'PENDING')
        self.assertTrue(all(not row['executed'] and row['status'] == 'PENDING' for row in result['cases']))

    def test_state_and_api_argument_mismatches_fail(self):
        for mode in ('state', 'argument', 'order'):
            self.cases['cases'] = []
            row = self.ready()
            if mode == 'state':
                row['assertions'][0]['equals']['synthetic-product']['amount_cents'] = 9999
            elif mode == 'argument':
                row['expected_calls'][3]['arguments']['input_amount'] = 99
            else:
                row['expected_calls'][0], row['expected_calls'][2] = row['expected_calls'][2], row['expected_calls'][0]
            with self.subTest(mode=mode):
                self.assertEqual(evaluate(self.candidate, self.cases)['status'], 'FAIL')

    def test_unused_script_evidence_and_step_limit_cannot_pass(self):
        for mode in ('unused', 'steps'):
            self.cases['cases'] = []
            row = self.ready()
            kwargs = {}
            if mode == 'unused':
                row['script']['responses'].append(deepcopy(row['script']['responses'][-1]))
            else:
                kwargs['max_steps'] = 1
            with self.subTest(mode=mode):
                try:
                    result = evaluate(self.candidate, self.cases, **kwargs)
                except MatrixError:
                    continue  # A missing relevant execution witness is rejected.
                self.assertEqual(result['status'], 'FAIL')

    def test_ready_rows_cannot_omit_call_inputs_bindings_or_source_review(self):
        for mode in ('input', 'binding', 'rationale', 'quote', 'assertions', 'irrelevant', 'path'):
            self.setUp()
            row = self.ready()
            if mode == 'input':
                del row['script']['initial_inputs']['customer_id']
            elif mode == 'binding':
                self.cases['candidate_bindings'].pop('plan_sha256')
            elif mode == 'rationale':
                row['source_review'].pop('rationale')
            elif mode == 'quote':
                row['source_review']['source_refs'][0]['quote'] = 'Invented absent quotation'
            elif mode == 'assertions':
                row['assertions'] = []
            elif mode == 'path':
                row['assertions'][0]['path'] = 42
            else:
                row['assertions'] = [{'path': '/final_state/closed', 'equals': True}]
            with self.subTest(mode=mode), self.assertRaises((MatrixError, ValueError)):
                evaluate(self.candidate, self.cases)

    def test_self_asserted_results_and_duplicate_ids_are_rejected(self):
        for mode in ('top', 'row', 'status', 'duplicate'):
            self.setUp()
            row = self.ready()
            if mode == 'top':
                self.cases['hosted_behavior_verified'] = True
            elif mode == 'row':
                row['pass'] = True
            elif mode == 'status':
                row['status'] = 'PASS'
            else:
                self.cases['cases'].append(deepcopy(row))
            with self.subTest(mode=mode), self.assertRaises(MatrixError):
                evaluate(self.candidate, self.cases)

    def test_original_artifact_tampering_rejected_before_replay(self):
        for filename in ('workflow.json', 'policy.json', 'trace.jsonl', 'builder-source.json',
                         'attempts/plan-001/guard-context.json', 'action-bindings.json'):
            with tempfile.TemporaryDirectory(dir=self.directory) as temp:
                copied = Path(temp) / 'run'
                shutil.copytree(self.run_dir, copied)
                path = copied / filename
                if filename == 'trace.jsonl':
                    path.write_text(path.read_text() + '{"event":"forged"}\n')
                else:
                    value = json.loads(path.read_text())
                    if filename.endswith('guard-context.json'):
                        value.pop('bindings')
                    else:
                        value['forged'] = True
                    write(path, value)
                with self.subTest(filename=filename), patch('tools.t08_release_matrix.replay', side_effect=AssertionError('must not execute')):
                    with self.assertRaises((ValueError, KeyError)):
                        load_candidate(copied, self.api, self.inputs)

    def test_changed_openapi_and_original_source_inputs_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as temp:
            api = Path(temp) / 'changed.json'
            value = json.loads(self.api.read_text())
            value['info'] = {'title': 'changed'}
            write(api, value)
            with self.assertRaisesRegex(MatrixError, 'OpenAPI'):
                load_candidate(self.run_dir, api, self.inputs)
            changed = deepcopy(self.inputs)
            changed[0]['sha256'] = '0' * 64
            with self.assertRaisesRegex(MatrixError, 'source text hash'):
                load_candidate(self.run_dir, self.api, changed)

    def test_transfer_discards_staged_note_and_unwritten_commitment(self):
        script = scenario(self.context, self.aliases, renamed=True)
        other = deepcopy(script['responses'][2]['envelope']['data']['evidence_products'][0])
        other.update(asset_reference='synthetic-second', delay_marker=4)
        script['responses'][2]['envelope']['data']['evidence_products'].append(other)
        script['frames'] = script['frames'][:4] + [frame(self.context, 'note', product='synthetic-second', note_reason='NOTE_R'),
            frame(self.context, 'transfer', queue='QUEUE_G'), frame(self.context, 'handoff'), frame(self.context, 'close')]
        def response(role):
            envelope = protocol_response(role)
            envelope['data'] = {'evidence_' + key: value for key, value in envelope['data'].items()}
            return {'operation': self.aliases[role], 'envelope': envelope}
        script['responses'] = script['responses'][:3] + [response('transfer_to_agent'), response('close_call')]
        row = self.ready(script, ('deferred_notes_transfer',), [
            {'path': '/final_state/pending_notes', 'equals': {}}, {'path': '/final_state/pending_promises', 'equals': {}},
            {'path': '/final_state/recorded_promises', 'equals': {}}])
        row['expected_calls'] = row['expected_calls'][:3] + [
            {'tool': self.aliases['transfer_to_agent'], 'arguments': {'input_customer_id': 'synthetic-session', 'input_queue': 'QUEUE_G'}},
            {'tool': self.aliases['close_call'], 'arguments': {'input_customer_id': 'synthetic-session', 'input_outcome': 'C_C', 'input_reason': 'WHY_H'}}]
        report = evaluate(self.candidate, self.cases)
        self.assertEqual(report['cases'][0]['status'], 'PASS')
        self.assertNotIn(self.aliases['add_note'], [call['tool'] for call in report['cases'][0]['replay']['calls']])

    def test_partial_requirement_needs_recorded_state_and_closing_api(self):
        row = self.ready(requirements=('partial_success_close_once',), assertions=[
            {'path': '/final_state/recorded_promises', 'equals': {'synthetic-product': {'amount_cents': 2000, 'date': '2031-05-06'}}},
            {'path': '/final_state/closed', 'equals': True}])
        result = evaluate(self.candidate, self.cases)
        self.assertEqual(result['cases'][0]['status'], 'PASS')
        self.assertFalse(result['requirements']['partial_success_close_once']['independent_adequacy_verified'])
        row['assertions'].pop()
        with self.assertRaises(MatrixError):
            evaluate(self.candidate, self.cases)

    def test_empty_call_expectations_are_explicit_but_do_not_hide_dispatch(self):
        row = self.ready()
        row['expected_calls'] = []
        self.assertEqual(evaluate(self.candidate, self.cases)['status'], 'FAIL')

    def test_callback_and_no_dispatch_guard_witnesses_are_relevant(self):
        context = deepcopy(self.context)
        context['plan']['nodes']['callback'] = {'action': 'schedule_callback'}
        assertions = [{'path': '/final_state/callbacks', 'equals': []}]
        no_dispatch = {'calls': [], 'steps': [{'node': 'callback'}]}
        self.assertTrue(_relevant('confirmed_final_terms', no_dispatch, assertions, context))
        dispatched = {'calls': [{'tool': self.aliases['schedule_callback']}], 'steps': []}
        self.assertTrue(_relevant('confirmed_final_terms', dispatched, assertions, context))
        self.assertFalse(_relevant('confirmed_final_terms', {'calls': [], 'steps': []}, assertions, context))
        self.assertFalse(_relevant('confirmed_final_terms', no_dispatch,
                                  [{'path': '/final_state/closed', 'equals': False}], context))

    def test_partial_success_then_qualified_failure_preserves_recorded_promise(self):
        script = scenario(self.context, self.aliases, renamed=True)
        second = deepcopy(script['responses'][2]['envelope']['data']['evidence_products'][0])
        # The approved readback labels products by kind; a second KIND_A product
        # would be indistinguishable and the guard correctly refuses consent.
        second.update(asset_reference='synthetic-second', kind_wire='KIND_B', delay_marker=4, floor_beta=20)
        script['responses'][2]['envelope']['data']['evidence_products'].append(second)
        script['frames'].insert(4, frame(self.context, 'stage', product='synthetic-second', amount='20.00', day='2031-05-06'))
        failed = deepcopy(script['responses'][3])
        failed['envelope'] = {'status_code': 503, 'data': {'evidence_ok': False, 'evidence_error': 'ERR_BUSY'}}
        note = protocol_response('add_note')
        note['data'] = {'evidence_' + key: value for key, value in note['data'].items()}
        script['responses'][4:4] = [failed, deepcopy(failed), {'operation': self.aliases['add_note'], 'envelope': note}]
        row = self.ready(script, ('partial_success_close_once',), [
            {'path': '/final_state/recorded_promises', 'equals': {'synthetic-product': {'amount_cents': 2000, 'date': '2031-05-06'}}},
            {'path': '/final_state/pending_promises', 'equals': {}}, {'path': '/final_state/closed', 'equals': True}])
        second_call = deepcopy(row['expected_calls'][3])
        second_call['arguments']['input_product_id'] = 'synthetic-second'
        row['expected_calls'][4:4] = [second_call, deepcopy(second_call), {
            'tool': self.aliases['add_note'], 'arguments': {'input_customer_id': 'synthetic-session',
            'input_product_id': 'synthetic-second', 'input_reason': 'NOTE_S'}}]
        result = evaluate(self.candidate, self.cases)
        self.assertEqual(result['cases'][0]['status'], 'PASS')
        calls = result['cases'][0]['replay']['calls']
        self.assertEqual(sum(call['tool'] == self.aliases['close_call'] for call in calls), 1)
        self.assertEqual([call['arguments']['input_product_id'] for call in calls
                          if call['tool'] == self.aliases['create_promise']],
                         ['synthetic-product', 'synthetic-second', 'synthetic-second'])

    def test_partial_success_then_unknown_result_quarantines_without_close(self):
        # Undeclared HTTP 500: persistence unknown, so no retry, note or close.
        script = scenario(self.context, self.aliases, renamed=True)
        second = deepcopy(script['responses'][2]['envelope']['data']['evidence_products'][0])
        second.update(asset_reference='synthetic-second', kind_wire='KIND_B', delay_marker=4, floor_beta=20)
        script['responses'][2]['envelope']['data']['evidence_products'].append(second)
        script['frames'].insert(4, frame(self.context, 'stage', product='synthetic-second', amount='20.00', day='2031-05-06'))
        script['frames'] = script['frames'][:7]
        unknown = deepcopy(script['responses'][3])
        unknown['envelope'] = {'status_code': 500, 'data': {'evidence_ok': False, 'evidence_error': 'ERR_BUSY'}}
        script['responses'][4:] = [unknown]
        terms = {'amount_cents': 2000, 'date': '2031-05-06'}
        row = self.ready(script, ('partial_success_close_once',), [
            {'path': '/final_state/recorded_promises', 'equals': {'synthetic-product': terms}},
            {'path': '/final_state/pending_promises', 'equals': {'synthetic-second': terms}},
            {'path': '/final_state/quarantined', 'equals': True}, {'path': '/final_state/closed', 'equals': False}])
        second_call = deepcopy(row['expected_calls'][3])
        second_call['arguments']['input_product_id'] = 'synthetic-second'
        row['expected_calls'][4:] = [second_call]
        result = evaluate(self.candidate, self.cases)
        self.assertEqual(result['cases'][0]['status'], 'PASS')
        self.assertEqual(result['requirements']['partial_success_close_once']['local_assertion_status'], 'PASS')

    def test_cli_persists_pending_local_report_and_never_overwrites(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as temp:
            cases_path = Path(temp) / 'cases.json'
            write(cases_path, self.cases)
            report_path = Path(temp) / 'report.json'
            command = [sys.executable, str(ROOT / 'tools/t08_release_matrix.py'), '--candidate-dir', str(self.run_dir),
                       '--openapi', str(self.api), '--cases', str(cases_path), '--output', str(report_path)]
            first = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
            self.assertEqual(first.returncode, 1, first.stderr)
            result = json.loads(report_path.read_text())
            self.assertEqual(result['status'], 'PENDING')
            self.assertEqual(result['cases_sha256'], digest(self.cases))
            before = report_path.read_bytes()
            second = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
            self.assertEqual(second.returncode, 1)
            self.assertEqual(report_path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()

"""Independent source-backed write budgets through real compiled node code.

No evaluator cases, model calls, bank sessions, or hosted evidence are used.
"""
import copy
import json
import unittest

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.policy import PolicyError, compile_execution_policy
from test_execution_requirements import add_rule, packet, rule
from test_goal_requirements import SealedSession, compiled, witness
import test_guard_aliases as aliases


class WireSession(SealedSession):
    @property
    def body(self):
        return json.loads(self.variables.get('bg_body') or '{}')

    def result(self, action, data, status=200):
        entry = self.entries[action]
        node = self.workflow['nodes']['bg_' + entry + '_api']
        envelope = {'status_code': status, 'data': copy.deepcopy(data)}
        exec(node['pre_actions'][0]['post_process_code'], {'result': envelope})
        for mapping in node['response_mapping']:
            self.variables[mapping['variable']] = envelope['data'].get(mapping['path'][5:], '')
        self.code('bg_' + entry + '_result')

    def confirm(self, action, answer='yes'):
        self.variables['bg_consent'] = answer
        self.code('bg_' + self.entries[action] + '_accept')


def callback_packet(*, cap=1, scope='call', resume=True, opaque=False, confirmation=True,
                    qualified=True, scopes=True):
    policy, sources, api = packet(['schedule_callback', 'close_call'], local=True, narrow=True)
    rule(policy, 'close_reasons')['value'] = {'not_reached': ['callback', 'no_callback']}
    rule(policy, 'identity_close')['value'] = {
        key: {'outcome': 'not_reached', 'reason': 'no_callback'}
        for key in ['failed', 'incomplete', 'unreached']}
    rule(policy, 'wire_codes')['value'] = {'outcomes': {'not_reached': 'not_reached'},
                                         'reasons': {'callback': 'callback', 'no_callback': 'no_callback'}}
    add_rule(policy, 'identity_close_choices', {key: [
        {'outcome': 'not_reached', 'reason': 'callback', 'requires': 'recorded_callback'},
        {'outcome': 'not_reached', 'reason': 'no_callback', 'requires': 'no_recorded_callback'}]
        for key in ['unreached', 'incomplete']})
    add_rule(policy, 'callback_failure_limit', cap)
    if scopes:
        add_rule(policy, 'attempt_scopes', {'schedule_callback': scope})
    if resume:
        add_rule(policy, 'definite_failure_resume', ['schedule_callback'])
    if confirmation:
        add_rule(policy, 'confirmation_actions', ['schedule_callback'])
    close = api['paths']['/close_call']['post']['requestBody']['content']['application/json']['schema']
    close['properties']['outcome']['enum'] = ['not_reached']
    close['properties']['reason']['enum'] = ['callback', 'no_callback']
    callback = api['paths']['/schedule_callback']['post']
    quote = 'This independently authored rejection is definite and persists no callback.'
    callback['responses']['200']['description'] = quote
    callback['responses']['200']['content']['application/json']['schema']['properties']['error'] = {
        'type': 'string', 'enum': ['NOT_PERSISTED']}
    if qualified:
        add_rule(policy, 'nonpersistent_failures', {'schedule_callback': [{
            'http_status': 200, 'error': 'NOT_PERSISTED',
            'api_source': {'pointer': '/paths/~1schedule_callback/post/responses/200/description',
                           'quote': quote}}]})
    if opaque:
        tool_aliases = {'schedule_callback': 'reserve_contact', 'close_call': 'finish_contact'}
        request_aliases = {'schedule_callback': {'customer_id': 'session_ref', 'date': 'pay_on', 'time': 'at'},
                           'close_call': {'customer_id': 'session_ref', 'outcome': 'result_code', 'reason': 'reason_code'}}
        result_aliases = {role: {'ok': 'accepted', 'error': 'failure_code'} for role in tool_aliases}
        for role, actual in tool_aliases.items():
            operation = api['paths']['/' + role]['post']
            operation['operationId'] = actual
            request = operation['requestBody']['content']['application/json']['schema']
            mapping = request_aliases[role]
            request['properties'] = {mapping[key]: value for key, value in request['properties'].items()}
            request['required'] = [mapping[key] for key in request['required']]
            response = operation['responses']['200']['content']['application/json']['schema']
            response['properties'] = {result_aliases[role][key]: value for key, value in response['properties'].items()}
            response['properties']['accepted'] = {'type': 'string', 'enum': ['true', 'false']}
        close['properties']['result_code']['enum'] = ['END_X']
        close['properties']['reason_code']['enum'] = ['DONE_X', 'NONE_X']
        rule(policy, 'wire_codes')['value'] = {'outcomes': {'not_reached': 'END_X'},
                                             'reasons': {'callback': 'DONE_X', 'no_callback': 'NONE_X'}}
        rule(policy, 'close_reasons')['value'] = {'END_X': ['DONE_X', 'NONE_X']}
        for row in rule(policy, 'identity_close')['value'].values():
            row.update(outcome='END_X', reason='NONE_X')
        for choices in rule(policy, 'identity_close_choices')['value'].values():
            for row in choices:
                row['outcome'] = 'END_X'
                row['reason'] = 'DONE_X' if row['requires'] == 'recorded_callback' else 'NONE_X'
        add_rule(policy, 'tool_aliases', tool_aliases)
        add_rule(policy, 'action_fields', request_aliases)
        add_rule(policy, 'result_fields', result_aliases)
        policy['allowed_actions'] = list(tool_aliases.values())
        if qualified:
            guarantees = rule(policy, 'nonpersistent_failures')['value']
            guarantees['reserve_contact'] = guarantees.pop('schedule_callback')
    witness(policy, sources)
    return policy, sources, api


def callback_session(**kwargs):
    values = callback_packet(**kwargs)
    _, context, workflow = compiled(*values)
    return values, context, WireSession(context, workflow)


class GuardAttemptScopeTests(unittest.TestCase):
    def book(self, session, date='2031-05-06', time='12:00', *, confirmation=True):
        session.action('schedule_callback', date=date, time=time)
        if confirmation:
            self.assertEqual(session.variables['bg_route'], 'confirm')
            session.confirm('schedule_callback')
        self.assertEqual(session.variables['bg_route'], 'allow')

    def test_call_cap_counts_only_dispatch_and_cannot_reset_by_changed_slot(self):
        _, _, session = callback_session()
        session.action('schedule_callback', date='2031-05-06', time='12:00')
        self.assertEqual(session.state['attempt_counts'], {})
        session.confirm('schedule_callback', 'no')
        self.assertEqual(session.state['attempt_counts'], {})
        self.book(session)
        self.assertEqual(session.state['attempt_counts'], {'schedule_callback:call': 1})
        session.result('schedule_callback', {'ok': True})
        recorded = copy.deepcopy(session.state['callbacks'])
        session.action('schedule_callback', date='2031-05-07', time='13:00')
        self.assertEqual(session.variables['bg_route'], 'blocked')
        self.assertFalse(session.body)
        self.assertEqual(session.state['callbacks'], recorded)
        self.assertEqual(session.state['attempt_counts'], {'schedule_callback:call': 1})
        session.action('close_call', outcome='not_reached', reason='no_callback')
        self.assertEqual(session.body['reason'], 'callback')

    def test_exact_request_scope_does_not_impose_unsourced_call_scope(self):
        _, _, session = callback_session(scope='exact_request', confirmation=False)
        self.book(session, confirmation=False)
        session.result('schedule_callback', {'ok': True})
        self.book(session, date='2031-05-07', time='13:00', confirmation=False)
        self.assertEqual(sorted(session.state['attempt_counts'].values()), [1, 1])
        session.result('schedule_callback', {'ok': True})
        session.action('schedule_callback', date='2031-05-06', time='12:00')
        self.assertFalse(session.body)
        self.assertEqual(len(session.state['callbacks']), 2)

    def test_cap_one_qualified_rejection_resumes_without_success_then_closes(self):
        for opaque in [False, True]:
            with self.subTest(opaque=opaque):
                _, _, session = callback_session(opaque=opaque)
                self.book(session)
                session.result('schedule_callback', {'accepted': 'false', 'failure_code': 'NOT_PERSISTED'}
                               if opaque else {'ok': False, 'error': 'NOT_PERSISTED'})
                self.assertEqual(session.variables['bg_route'], 'failure')
                self.assertFalse(session.state['quarantined'])
                self.assertFalse(session.state['callbacks'])
                self.assertFalse(session.state['inflight'])
                self.assertFalse(session.state['batch'])
                self.assertEqual(next(iter(session.state['attempts'].values()))['count'], 1)
                session.action('close_call', outcome='not_reached', reason='no_callback')
                self.assertEqual(session.variables['bg_route'], 'allow')
                self.assertEqual(session.body.get('reason_code', session.body.get('reason')), 'NONE_X' if opaque else 'no_callback')
                session.result('close_call', {'accepted': 'true'} if opaque else {'ok': True})
                self.assertTrue(session.state['closed'])

    def test_qualified_retry_reuses_frozen_confirmed_request_and_caps_actual_dispatches(self):
        _, _, session = callback_session(cap=2, opaque=True)
        self.book(session)
        original = copy.deepcopy(session.body)
        session.variables[session.inputs['schedule_callback']['date']] = '2040-01-01'
        session.result('schedule_callback', {'accepted': 'false', 'failure_code': 'NOT_PERSISTED'})
        self.assertEqual(session.variables['bg_route'], 'allow')
        self.assertEqual(session.body, original)
        self.assertEqual(session.state['attempt_counts'], {'schedule_callback:call': 2})
        self.assertEqual(next(iter(session.state['attempts'].values()))['count'], 2)
        session.result('schedule_callback', {'accepted': 'false', 'failure_code': 'NOT_PERSISTED'})
        self.assertEqual(session.variables['bg_route'], 'failure')
        self.assertFalse(session.state['quarantined'])
        session.action('schedule_callback', date='2031-05-07', time='14:00')
        self.assertFalse(session.body)
        self.assertEqual(session.state['attempt_counts'], {'schedule_callback:call': 2})

    def test_absent_resume_or_unqualified_negative_stays_quarantined_without_retry(self):
        for options, receipt, status in [({'resume': False}, {'ok': False, 'error': 'NOT_PERSISTED'}, 200),
                                         ({'qualified': False}, {'ok': False, 'error': 'NOT_PERSISTED'}, 200),
                                         ({}, {'ok': False, 'error': 'OTHER'}, 200),
                                         ({}, {'ok': False, 'error': 'NOT_PERSISTED'}, 500),
                                         ({}, {'ok': None, 'error': 'NOT_PERSISTED'}, 200)]:
            with self.subTest(options=options, receipt=receipt, status=status):
                _, _, session = callback_session(cap=2, **options)
                self.book(session)
                session.result('schedule_callback', receipt, status)
                self.assertTrue(session.state['quarantined'])
                self.assertEqual(session.state['attempt_counts'], {'schedule_callback:call': 1})
                self.assertFalse(session.state['callbacks'])
                session.action('close_call', outcome='not_reached', reason='no_callback')
                self.assertFalse(session.body)

    def test_positive_error_or_malformed_error_quarantines_callback_and_close(self):
        for action in ['schedule_callback', 'close_call']:
            for error in ['NOT_PERSISTED', 'UNKNOWN', ' ', None, 0, True, [], {}]:
                with self.subTest(action=action, error=error):
                    _, _, session = callback_session(opaque=True)
                    if action == 'schedule_callback':
                        self.book(session)
                    else:
                        self.book(session)
                        session.result('schedule_callback', {'accepted': 'true'})
                        session.action('close_call', outcome='not_reached', reason='callback')
                    previous = copy.deepcopy(session.state['callbacks'])
                    inflight = copy.deepcopy(session.state['inflight'])
                    session.result(action, {'accepted': 'true', 'failure_code': error})
                    self.assertTrue(session.state['quarantined'])
                    self.assertFalse(session.state['closed'])
                    self.assertEqual(session.state['callbacks'], previous)
                    self.assertEqual(session.state['inflight'], inflight)
                    self.assertFalse(session.body)
                    if action == 'close_call':
                        self.assertEqual(session.variables['bg_route'], 'closed')
                    session.action('schedule_callback', date='2031-05-07', time='13:00')
                    self.assertFalse(session.body)

    def test_malformed_counter_or_pending_budget_cannot_grant_resume_or_new_write(self):
        for corrupt in [True, -1, '1', 0, 2]:
            with self.subTest(counter=corrupt):
                _, _, session = callback_session()
                self.book(session)
                state = session.state
                state['attempt_counts']['schedule_callback:call'] = corrupt
                session.variables['bg_state'] = json.dumps(state)
                session.result('schedule_callback', {'ok': False, 'error': 'NOT_PERSISTED'})
                self.assertTrue(session.state['quarantined'])
                self.assertFalse(session.body)
        _, _, session = callback_session()
        self.book(session)
        state = session.state
        state['attempt_counts'].clear()
        session.variables['bg_state'] = json.dumps(state)
        session.result('schedule_callback', {'ok': False, 'error': 'NOT_PERSISTED'})
        self.assertTrue(session.state['quarantined'])
        self.assertFalse(session.body)
        _, _, session = callback_session()
        state = session.state
        state['attempt_counts']['schedule_callback:call'] = True
        session.variables['bg_state'] = json.dumps(state)
        session.action('close_call', outcome='not_reached', reason='no_callback')
        self.assertTrue(session.state['quarantined'])
        self.assertFalse(session.body)
        for key, value in [('attempt_limit', 4), ('attempt_scope', 'exact_request')]:
            with self.subTest(key=key):
                _, _, session = callback_session()
                self.book(session)
                state = session.state
                state['batch'][key] = value
                session.variables['bg_state'] = json.dumps(state)
                session.result('schedule_callback', {'ok': False, 'error': 'NOT_PERSISTED'})
                self.assertTrue(session.state['quarantined'])
                self.assertFalse(session.body)

    def test_resume_requires_sourced_scope_and_positive_limit_not_api_availability(self):
        for options in [{'scopes': False}, {'cap': True}, {'cap': 0}, {'scope': 'product'}]:
            with self.subTest(options=options):
                with self.assertRaises((ExecutionRequirementsError, PolicyError, GuardCompileError)):
                    compiled(*callback_packet(**options))
        for kind, values in [('attempt_scopes', [{}, {'close_call': 'product'}, {'verify_identity': 'call'}, {'schedule_callback': []}]),
                             ('definite_failure_resume', [['close_call'], ['commit_promises'], ['schedule_callback', 'schedule_callback'], 'schedule_callback'])]:
            for value in values:
                with self.subTest(kind=kind, value=value):
                    with self.assertRaises(PolicyError):
                        compile_execution_policy({kind: value})

    def test_legacy_without_scopes_or_resume_does_not_gain_a_callback_limit(self):
        _, _, session = callback_session(scopes=False, resume=False, confirmation=False)
        self.book(session, confirmation=False)
        session.result('schedule_callback', {'ok': True})
        self.book(session, date='2031-05-07', time='13:00', confirmation=False)
        self.assertEqual(session.state['attempt_counts'], {})

    def test_optional_frozen_scope_cannot_be_omitted_from_execution_profile(self):
        values, context, unused = callback_session()
        context = copy.deepcopy(context)
        context['execution_policy']['profile'].pop('attempt_scopes')
        from builder.safety import digest
        context['execution_policy']['sha256'] = digest({key: value for key, value in context['execution_policy'].items() if key != 'sha256'})
        with self.assertRaises(GuardCompileError) as caught:
            compile_guarded_workflow(**context)
        self.assertIn('active execution rule omitted', str(caught.exception))


class OtherWriteReceiptTests(unittest.TestCase):
    def harness(self, *, action=None, scope='call', cap=1):
        harness = aliases.GuardAliasTests(methodName='runTest')
        harness.setUp()
        if action:
            role, limit_kind = {'commit_transfer': ('transfer_to_agent', 'transfer_failure_limit'),
                                'finalize_notes': ('add_note', 'note_failure_limit'),
                                'commit_promises': ('create_promise', 'promise_failure_limit')}[action]
            context = copy.deepcopy(harness.context)
            profile = copy.deepcopy(context['execution_policy']['profile'])
            profile['attempt_scopes'] = {action: scope}
            profile[limit_kind] = cap
            if action != 'commit_promises':
                profile['definite_failure_resume'] = [action]
            quote = 'This invented exact rejection persists no write.'
            context['openapi']['paths']['/' + role]['post']['responses']['200']['description'] = quote
            profile['nonpersistent_failures'][harness.aliases[role]] = [{
                'http_status': 200, 'error': 'ERR_NONPERSISTENT',
                'api_source': {'pointer': '/paths/~1' + role + '/post/responses/200/description', 'quote': quote}}]
            harness.context = aliases.recompile_context(context, profile)
            harness.workflow = compile_guarded_workflow(**harness.context)['workflow']
            harness.variables = {'customer_id': 'INVENTED-SOURCE-SESSION', 'now': '2031-05-02'}
            harness.code('bg_init')
        return harness

    def promise(self, harness, *, record='EARLIER-RECORD'):
        harness.stage()
        harness.code('commit')
        harness.variables['bg_consent'] = 'yes'
        harness.code('bg_commit_accept')
        harness.api_result('commit', {'evidence_ok': True, 'evidence_promise_id': record})

    def test_transfer_retry_and_exhausted_resume_discard_only_unwritten_handoff(self):
        h = self.harness(action='commit_transfer', cap=2)
        h.account()
        self.promise(h)
        previous = copy.deepcopy(json.loads(h.variables['bg_state'])['recorded_promises'])
        h.variables['queue'] = 'QUEUE_S'
        h.code('transfer')
        h.code('handoff')
        body = h.variables['bg_body']
        h.api_result('handoff', {'evidence_ok': False, 'evidence_error': 'ERR_NONPERSISTENT'})
        self.assertEqual(h.variables['bg_route'], 'allow')
        self.assertEqual(h.variables['bg_body'], body)
        h.api_result('handoff', {'evidence_ok': False, 'evidence_error': 'ERR_NONPERSISTENT'})
        state = json.loads(h.variables['bg_state'])
        self.assertFalse(state['quarantined'])
        self.assertFalse(state['transfer'])
        self.assertEqual(state['recorded_promises'], previous)
        self.assertEqual(state['attempt_counts'], {'commit_transfer:call': 2})
        h.code('close')
        self.assertFalse(h.variables['bg_body'])  # Remaining product resolution is not waived by resume.
        h.variables.update(product='internal-product-A', note_reason='NOTE_R')
        h.code('note')
        h.code('finish')
        h.api_result('finish', {'evidence_ok': True})
        h.code('close')
        self.assertEqual(json.loads(h.variables['bg_body'])['input_outcome'], 'C_A')

    def test_product_note_caps_preserve_earlier_notes_and_do_not_fake_failed_records(self):
        h = self.harness(action='finalize_notes', scope='product', cap=1)
        h.account()
        for pid in ['internal-product-Z', 'internal-product-A']:
            h.variables.update(product=pid, note_reason='NOTE_R')
            h.code('note')
        h.code('finish')
        self.assertEqual(json.loads(h.variables['bg_body'])['input_product_id'], 'internal-product-Z')
        h.api_result('finish', {'evidence_ok': True})
        self.assertEqual(json.loads(h.variables['bg_body'])['input_product_id'], 'internal-product-A')
        h.api_result('finish', {'evidence_ok': False, 'evidence_error': 'ERR_NONPERSISTENT'})
        state = json.loads(h.variables['bg_state'])
        self.assertFalse(state['quarantined'])
        self.assertEqual(state['recorded_notes'], {'internal-product-Z': 'refused'})
        self.assertEqual(state['attempt_counts'], {'finalize_notes:product:internal-product-Z': 1,
                                                  'finalize_notes:product:internal-product-A': 1})
        h.code('close')
        self.assertFalse(h.variables['bg_body'])  # All-products rule still applies; no fallback reason invented.

    def test_call_note_cap_counts_successes_across_products_before_next_dispatch(self):
        h = self.harness(action='finalize_notes', scope='call', cap=1)
        h.account()
        for pid in ['internal-product-Z', 'internal-product-A']:
            h.variables.update(product=pid, note_reason='NOTE_R')
            h.code('note')
        h.code('finish')
        h.api_result('finish', {'evidence_ok': True})
        state = json.loads(h.variables['bg_state'])
        self.assertTrue(state['quarantined'])
        self.assertEqual(state['recorded_notes'], {'internal-product-Z': 'refused'})
        self.assertEqual(state['attempt_counts'], {'finalize_notes:call': 1})
        self.assertEqual(len(state['attempts']), 1)
        self.assertFalse(h.variables['bg_body'])

    def test_promise_existing_failure_disposition_is_preserved_with_explicit_product_scope(self):
        h = self.harness(action='commit_promises', scope='product', cap=2)
        h.account()
        h.stage()
        h.code('commit')
        h.variables['bg_consent'] = 'yes'
        h.code('bg_commit_accept')
        for unused in range(2):
            h.api_result('commit', {'evidence_ok': False, 'evidence_error': 'ERR_NONPERSISTENT'})
        state = json.loads(h.variables['bg_state'])
        self.assertFalse(state['quarantined'])
        self.assertFalse(state['pending_promises'])
        self.assertEqual(state['pending_notes'], {'internal-product-Z': 'service_error'})
        self.assertEqual(state['attempt_counts'], {'commit_promises:product:internal-product-Z': 2})

    def test_contradictory_second_promise_preserves_earlier_actual_success(self):
        h = self.harness()
        h.account()
        self.promise(h)
        before = copy.deepcopy(json.loads(h.variables['bg_state'])['recorded_promises'])
        h.stage('internal-product-A', '82.10')
        h.code('commit')
        h.variables['bg_consent'] = 'yes'
        h.code('bg_commit_accept')
        h.api_result('commit', {'evidence_ok': True, 'evidence_promise_id': 'FALSE-SECOND',
                               'evidence_error': 'ERR_UNKNOWN'})
        state = json.loads(h.variables['bg_state'])
        self.assertTrue(state['quarantined'])
        self.assertEqual(state['recorded_promises'], before)
        self.assertFalse(h.variables['bg_body'])

    def test_positive_error_never_records_identity_account_promise_note_or_transfer(self):
        for role in ['identity', 'account', 'commit', 'finish', 'handoff']:
            with self.subTest(role=role):
                h = self.harness()
                if role != 'identity':
                    h.account()
                if role == 'identity':
                    h.variables.update(answer_field='challenge_A', answer_text='provided-answer')
                elif role == 'commit':
                    h.stage()
                elif role == 'finish':
                    for pid in ['internal-product-Z', 'internal-product-A']:
                        h.variables.update(product=pid, note_reason='NOTE_R')
                        h.code('note')
                elif role == 'handoff':
                    h.variables['queue'] = 'QUEUE_S'
                    h.code('transfer')
                h.code(role)
                if role == 'commit':
                    h.variables['bg_consent'] = 'yes'
                    h.code('bg_commit_accept')
                before = json.loads(h.variables['bg_state'])
                h.api_result(role, {'evidence_ok': True, 'evidence_error': 'ERR_CONTRADICTION',
                                    'evidence_verified': True, 'evidence_wrong_answers_left': 4,
                                    'evidence_promise_id': 'UNTRUSTED', 'evidence_products': []})
                after = json.loads(h.variables['bg_state'])
                self.assertTrue(after['quarantined'])
                for key in ['verified_fields', 'products', 'recorded_promises', 'recorded_notes', 'callbacks']:
                    self.assertEqual(after[key], before[key])
                self.assertEqual(after['transfer'], before['transfer'])
                self.assertEqual(after['inflight'], before['inflight'])
                self.assertFalse(h.variables['bg_body'])


if __name__ == '__main__':
    unittest.main()

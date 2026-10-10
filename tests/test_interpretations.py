import copy
import json
import unittest

from builder.build import execute, make_workflow_prompt, PROJECT, UnresolvedPolicyError
from builder.contract import compile_policy_contract, PolicyContractError
from builder.interpretations import review_errors
from builder.safety import digest
import test_builder
from test_builder import fixture, contract, BRIEF_TEXT


def candidate():
    value = fixture()
    value['policy']['interpretations'] = [{
        'id': 'boundary-1', 'kind': 'boundary_completion',
        'issue': 'The opening phrase is unspecified.',
        'scope': 'At the beginning of the permitted contact flow.',
        'rule_ids': ['r1', 'close_reasons'], 'preserved_rule_ids': ['close_reasons'],
        'resolution': 'Ask the customer how to help before the permitted contact flow.',
        'rationale': 'An opening question completes dialogue without granting a new action.'}]
    return value


def accepted(policy):
    return {'policy_sha256': digest(policy), 'decisions': [
        {'id': 'boundary-1', 'verdict': 'accept', 'preserves_constraints': True,
         'rationale': 'The opening question changes no source constraint.'}], 'unresolved_constraints': []}


class InterpretationContractTests(unittest.TestCase):
    def compile(self, p):
        return compile_policy_contract(p, [{'id': 'brief-1', 'file': 'brief.md',
            'text': BRIEF_TEXT, 'sha256': digest(BRIEF_TEXT)}], contract())

    def test_separate_frozen_inference_preserves_authoritative_values(self):
        p = candidate()['policy']; before = copy.deepcopy(p)
        result = self.compile(p); original = self.compile(fixture()['policy'])
        self.assertEqual(result['active_rules'], original['active_rules'])
        self.assertNotEqual(result['sha256'], original['sha256'])
        self.assertEqual(p, before)
        record = result['interpretations'][0]
        self.assertEqual(record['authority'], 'model_inference')
        self.assertEqual(record['provenance'][0]['quote'], BRIEF_TEXT)
        payload = json.loads(make_workflow_prompt(result, contract()).split('AUTHORIZED INPUTS:\n')[1])
        self.assertEqual(payload['interpretations'], result['interpretations'])
        p['interpretations'][0]['resolution'] += ' A changed inference.'
        self.assertNotEqual(self.compile(p)['sha256'], result['sha256'])

    def test_derived_execution_rules_are_labelled_and_cannot_override_sources(self):
        p = candidate()['policy']; item = p['interpretations'][0]
        item['derived_rule_ids'] = ['r1']
        result = self.compile(p)
        self.assertEqual(result['active_rules']['purpose']['authority'], 'model_inference')
        self.assertEqual(result['active_rules']['purpose']['interpretation_ids'], ['boundary-1'])
        for refs in (['missing'], ['identity_close'], ['r1', 'r1']):
            item['derived_rule_ids'] = refs
            with self.assertRaises(PolicyContractError): self.compile(p)
        item['derived_rule_ids'] = ['r1']
        old = copy.deepcopy(p['rules'][0]); old['id'] = 'old-purpose'
        p['rules'].append(old); p['rules'][0]['overrides'] = ['old-purpose']
        with self.assertRaises(PolicyContractError): self.compile(p)

    def test_source_metadata_cannot_replace_interpretation_witness_values(self):
        p = candidate()['policy']
        p['rules'][0]['source'].update(rule_id='forged', kind='forged', value='forged')
        witness = self.compile(p)['interpretations'][0]['provenance'][0]
        self.assertEqual(witness['rule_id'], 'r1')
        self.assertEqual(witness['kind'], 'purpose')
        self.assertEqual(witness['value'], 'Banking contact')

    def test_malformed_unbound_overriding_and_duplicate_inferences_rejected(self):
        for change in ('unknown', 'empty', 'kind', 'override', 'preserved', 'duplicate', 'superseded', 'missing'):
            with self.subTest(change=change):
                p = candidate()['policy']; item = p['interpretations'][0]
                if change == 'unknown': item['rule_ids'] = ['invented']
                if change == 'empty': item['scope'] = ''
                if change == 'kind': item['kind'] = 'rule_override'
                if change == 'override': item['overrides'] = ['close_reasons']
                if change == 'preserved': item['preserved_rule_ids'] = ['identity_close']
                if change == 'duplicate': p['interpretations'].append(copy.deepcopy(item))
                if change == 'missing': del item['rationale']
                if change == 'superseded':
                    rule = copy.deepcopy(p['rules'][0]); rule.update(id='replacement', overrides=['r1'])
                    p['rules'].append(rule)
                with self.assertRaises(PolicyContractError): self.compile(p)

    def test_interpretation_cannot_erase_an_unresolved_constraint(self):
        p = candidate()['policy']; p['unsupported_constraints'] = ['An explicit conflict remains.']
        with self.assertRaises(PolicyContractError): self.compile(p)

    def test_inferences_do_not_relax_calendar_amount_or_consent_guards(self):
        from test_guards import GuardExecutionTests
        from builder.guards import _profile
        runner = GuardExecutionTests('test_stable_product_ids_floor_dates_and_integer_cents')
        runner.setUp()
        context = runner.context
        context['contract']['interpretations'] = [{
            'authority': 'model_inference', 'resolution': 'Complete an unspecified dialogue boundary.'}]
        context['contract']['sha256'] = digest({k:v for k,v in context['contract'].items() if k != 'sha256'})
        profile = _profile(context['contract'], context['execution_policy'], context['plan'], None)
        self.assertEqual(profile, runner.profile)
        runner.profile = profile
        runner.account()
        for day in ('2031-05-01', '2031-05-03', '2031-05-05', '2031-05-20'):
            with self.subTest(day=day), self.assertRaises(ValueError):
                runner.stage(day=day)
        with self.assertRaises(ValueError): runner.stage(amount='1')
        runner.stage()
        self.assertEqual(runner.advance('prepare', 'commit_promises')[1], 'confirm')
        with self.assertRaises(ValueError): runner.step('confirm', inputs={'consent': 'no'})

    def test_nonblocking_scope_observations_remain_distinct_from_conflicts(self):
        p = candidate()['policy']; review = accepted(p)
        review['observations'] = ['Behavior outside the declared scope was not inferred.']
        self.assertEqual(review_errors(review, p), [])
        review['unresolved_constraints'] = ['The proposed interpretation contradicts a source limit.']
        self.assertTrue(review_errors(review, p))

    def test_review_rejects_missing_stale_rejected_duplicate_or_unresolved_decisions(self):
        p = candidate()['policy']
        self.assertEqual(review_errors(accepted(p), p), [])
        for change in ('missing', 'stale', 'rejected', 'weakened', 'duplicate', 'unresolved', 'unknown', 'shape'):
            with self.subTest(change=change):
                r = accepted(p)
                if change == 'missing': r['decisions'] = []
                if change == 'stale': r['policy_sha256'] = '0' * 64
                if change == 'rejected': r['decisions'][0]['verdict'] = 'reject'
                if change == 'weakened': r['decisions'][0]['preserves_constraints'] = False
                if change == 'duplicate': r['decisions'] *= 2
                if change == 'unresolved': r['unresolved_constraints'] = ['Conflict']
                if change == 'unknown': r['decisions'][0]['id'] = 'other'
                if change == 'shape': r['rules'] = []
                self.assertTrue(review_errors(r, p))


class InterpretationBuildTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def test_review_precedes_frozen_plan_and_preserves_artifact(self):
        gen = self.generator(); gen.generate.return_value = fixture()
        p = candidate(); gen.generate.side_effect = [p, {'policy': accepted(p['policy'])}]
        result = execute(self.args('--generate-only'), generator=gen)
        run = PROJECT / result['run_directory']
        review = json.loads((run / 'interpretation-review.json').read_text())
        self.assertTrue(review['accepted'])
        self.assertFalse(review['organizer_approval_verified'])
        frozen = json.loads((run / 'policy.contract.json').read_text())
        self.assertEqual(frozen['interpretations'][0]['authority'], 'model_inference')
        self.assertIn('interpretations', gen.generate_plan.call_args.args[0])
        self.assertEqual(gen.generate.call_args_list[-1].kwargs['max_seconds'], 180)

    def test_rejection_stops_before_plan_or_platform(self):
        p = candidate(); review = accepted(p['policy']); review['decisions'][0]['verdict'] = 'reject'
        gen = self.generator(); gen.generate.return_value = fixture()
        gen.generate.side_effect = [p, {'policy': review}] * 3
        with self.assertRaises(UnresolvedPolicyError):
            execute(self.args(), generator=gen, client_factory=lambda **kw: self.fail('Freya touched'))
        gen.generate_plan.assert_not_called()
        run = next((self.directory / 'out').iterdir())
        self.assertFalse(json.loads((run / 'interpretation-review.json').read_text())['accepted'])

    def test_review_feedback_gets_one_bounded_pre_freeze_repair(self):
        first = candidate(); review = accepted(first['policy'])
        review['decisions'][0]['verdict'] = 'reject'
        review['decisions'][0]['rationale'] = 'Narrow the boundary to preserve existing branches.'
        corrected = copy.deepcopy(first)
        corrected['policy']['interpretations'][0]['scope'] += ' Only where the source gives no opening words.'
        gen = self.generator(); gen.generate.return_value = fixture()
        gen.generate.side_effect = [first, {'policy': review}, corrected,
                                   {'policy': accepted(corrected['policy'])}]
        result = execute(self.args('--generate-only'), generator=gen)
        self.assertEqual(result['policy_repairs'], 1)
        self.assertIn('interpretation_review', gen.generate.call_args_list[2].args[0])
        self.assertEqual(gen.generate_plan.call_count, 1)
        run = PROJECT / result['run_directory']
        self.assertFalse(json.loads((run/'interpretation-review-1.json').read_text())['accepted'])
        self.assertTrue(json.loads((run/'interpretation-review-2.json').read_text())['accepted'])
        self.assertNotEqual(json.loads((run/'policy.interpretation-candidate-1.json').read_text()),
                            json.loads((run/'policy.interpretation-candidate-2.json').read_text()))

    def test_cross_rule_profile_errors_are_repaired_before_freezing(self):
        invalid = fixture()
        rule = next(r for r in invalid['policy']['rules'] if r['kind'] == 'identity_close')
        rule['value']['unreached'].pop('reason')
        gen = self.generator(); gen.generate.return_value = fixture()
        gen.generate.side_effect = [invalid, fixture()]
        result = execute(self.args('--generate-only'), generator=gen)
        self.assertEqual(result['policy_repairs'], 1)
        self.assertIn('identity_close disposition conflicts', gen.generate.call_args_list[1].args[0])
        self.assertEqual(gen.generate_plan.call_count, 1)

    def test_supplied_policy_cannot_bypass_interpretation_review(self):
        p = candidate()['policy']; path = self.directory / 'policy.json'; path.write_text(json.dumps(p))
        gen = self.generator(); gen.generate.return_value = fixture()
        gen.generate.side_effect = [{'policy': accepted(p)}]
        execute(self.args('--generate-only', '--policy-file', str(path)), generator=gen)
        self.assertEqual(gen.generate.call_count, 1)

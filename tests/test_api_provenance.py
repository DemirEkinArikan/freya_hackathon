import copy
import json
from pathlib import Path
import unittest

from builder.build import execute, make_policy_prompt, PROJECT, UnresolvedPolicyError
from builder.contract import api_provenance_source, compile_policy_contract, PolicyContractError
from builder.safety import digest
import test_builder
from test_builder import fixture, contract, BRIEF_TEXT


class ApiProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.api=contract()
        self.api['paths']['/close_call']['post']['responses']['503']={'description':'The rejected request did not persist a record.'}
        self.sources=[{'id':'brief-1','file':'brief.md','text':BRIEF_TEXT,'sha256':digest(BRIEF_TEXT)}]
        self.policy=fixture()['policy']
        source=api_provenance_source(self.api)
        self.policy['sources'].append({k:v for k,v in source.items() if k!='text'})
        self.policy['rules'].append({'id':'api-guarantee','kind':'nonpersistent_failures','value':{'close_call':[{'http_status':503}]},'source':{'id':'tool-api','quote':'The rejected request did not persist a record.'}})

    def test_optional_api_quote_bound_to_actual_contract(self):
        compiled=compile_policy_contract(self.policy,self.sources,self.api)
        self.assertEqual(compiled['active_rules']['nonpersistent_failures']['provenance'][0]['id'],'tool-api')
        self.assertEqual({s['id'] for s in compiled['sources']},{'brief-1','tool-api'})
        self.assertEqual(len(self.sources),1)

    def test_changed_api_hash_or_fabricated_quote_rejected(self):
        for change in ('api','hash','quote','file'):
            with self.subTest(change=change):
                p=copy.deepcopy(self.policy);api=copy.deepcopy(self.api)
                if change=='api':api['info']={'title':'Changed contract'}
                if change=='hash':p['sources'][-1]['sha256']='0'*64
                if change=='file':p['sources'][-1]['file']='invented.json'
                if change=='quote':p['rules'][-1]['source']['quote']='Invented persistence guarantee'
                with self.assertRaises(PolicyContractError):compile_policy_contract(p,self.sources,api)

    def test_api_source_does_not_replace_brief_provenance(self):
        self.policy['sources']=[self.policy['sources'][-1]]
        self.policy['rules']=[self.policy['rules'][-1]]
        with self.assertRaises(PolicyContractError):compile_policy_contract(self.policy,self.sources,self.api)

    def test_unused_api_source_not_required(self):
        result=compile_policy_contract(fixture()['policy'],self.sources,self.api)
        self.assertEqual([s['id'] for s in result['sources']],['brief-1'])

    def test_prompt_exposes_exact_optional_source_and_compiler_requirements(self):
        payload=json.loads(make_policy_prompt(self.sources,self.api).split('\n\nAUTHORIZED INPUTS:\n',1)[1])
        self.assertEqual(payload['api_source'],api_provenance_source(self.api))
        self.assertIn('nonpersistent_failures',payload['execution_rule_requirements']['commit_promises'])
        self.assertEqual(payload['sources'],self.sources)


class ExtractionDiagnosticsTests(unittest.TestCase):
    setUp=test_builder.BuilderTests.setUp
    tearDown=test_builder.BuilderTests.tearDown
    args=test_builder.BuilderTests.args
    generator=test_builder.BuilderTests.generator

    def test_source_and_execution_errors_share_one_repair(self):
        invalid=fixture();invalid['policy']['unsupported_constraints']=['Unresolved supplied condition']
        next(r for r in invalid['policy']['rules'] if r['kind']=='identity_close')['value'].pop('unreached')
        gen=self.generator();gen.generate.return_value=fixture();gen.generate.side_effect=[invalid,fixture()]
        result=execute(self.args('--generate-only'),generator=gen)
        self.assertEqual(result['policy_repairs'],1)
        repair=json.loads(gen.generate.call_args_list[1].args[0].split('\n\nAUTHORIZED INPUTS:\n',1)[1])['repair']
        self.assertTrue(any('unsupported constraints' in s for s in repair['validation_errors']))
        self.assertTrue(any('identity_close' in s for s in repair['validation_errors']))

    def test_persistent_source_ambiguity_stops_after_one_review_without_deployment(self):
        invalid=fixture();invalid['policy']['unsupported_constraints']=['Source gives incompatible dates for this scope']
        gen=self.generator();gen.generate.return_value=invalid
        with self.assertRaisesRegex(UnresolvedPolicyError,'incompatible dates'):
            execute(self.args('--generate-only'),generator=gen,client_factory=lambda **kw:self.fail('Freya touched'))
        self.assertEqual(gen.generate.call_count,2)
        gen.generate_plan.assert_not_called()
        run=next((self.directory/'out').iterdir())
        self.assertEqual(json.loads((run/'manifest.json').read_text())['failure_kind'],'unsupported_policy')
        self.assertTrue((run/'policy.rejected-1.json').exists())
        self.assertTrue((run/'policy.rejected-2.json').exists())

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from builder.readback import ReadbackMismatch, verify_readback
from builder.build import execute, PROJECT
import test_builder
from test_builder import FakeClient, fixture


EVIDENCE = json.loads((Path(__file__).parent / "fixtures/platform-readback.json").read_text())


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.authored = copy.deepcopy(EVIDENCE["authored"])
        self.stored = copy.deepcopy(EVIDENCE["stored"])

    def test_actual_live_defaults_accepted_without_mutating_inputs(self):
        before = copy.deepcopy((self.authored, self.stored))
        self.assertEqual(verify_readback(self.authored, self.stored), 38)
        self.assertEqual(before, (self.authored, self.stored))

    def test_exact_definition_needs_no_defaults(self):
        exact = copy.deepcopy(self.authored)
        exact["agent_config"] = self.stored["agent_config"]
        self.assertEqual(verify_readback(self.authored, exact), 0)

    def test_authored_fields_cannot_be_removed_even_when_default_matches(self):
        self.authored['nodes']['greeting']['tools'] = []
        del self.stored['nodes']['greeting']['tools']
        with self.assertRaisesRegex(ReadbackMismatch, 'authored field missing'):
            verify_readback(self.authored, self.stored)

    def test_authored_value_changes_rejected(self):
        for field, value in [('task_message', 'changed'), ('node_type', 'code'),
                             ('on_enter', {'type':'silent'})]:
            with self.subTest(field=field):
                stored = copy.deepcopy(self.stored)
                stored['nodes']['greeting'][field] = value
                with self.assertRaises(ReadbackMismatch):
                    verify_readback(self.authored, stored)

    def test_defaults_require_exact_types_and_values(self):
        for key,value in [('interruptible',1),('passthrough',0),('tools',None),
                          ('max_node_turns',10),('truncation_window_size',10.0),
                          ('role_message','do something'),('assertions',[{}])]:
            with self.subTest(key=key):
                stored=copy.deepcopy(self.stored);stored['nodes']['greeting'][key]=value
                with self.assertRaises(ReadbackMismatch):verify_readback(self.authored,stored)

    def test_added_execution_and_state_rejected(self):
        mutations = [
            lambda w: w.update(tools=[{'function':{'handler':'__function__:api_call'}}]),
            lambda w: w['nodes']['greeting'].update(tools=[{'function':{'name':'extra'}}]),
            lambda w: w['nodes']['greeting'].update(pre_actions=[{'function':{'name':'extra'}}]),
            lambda w: w['nodes']['greeting'].update(pre_process_code='vars.clear()'),
            lambda w: w['nodes'].update(extra={'node_type':'code'}),
            lambda w: w['global_nodes'].update(extra={}),
            lambda w: w['test_variables'].update(bg_state='forged'),
            lambda w: w['nodes']['greeting']['transitions'].append({'target_node':'done'}),
            lambda w: w['nodes']['greeting']['transitions'][0].update(target_node='greeting'),
            lambda w: w['nodes']['greeting']['transitions'][0]['condition'].update(mode='fuzzy'),
            lambda w: w['nodes']['done']['terminal']['function'].update(handler='__function__:api_call'),
            lambda w: w['nodes']['done']['terminal']['function']['parameters'].update(tools=[]),
            lambda w: w.update(unknown=None),
        ]
        for i,mutation in enumerate(mutations):
            with self.subTest(mutation=i):
                stored=copy.deepcopy(self.stored);mutation(stored)
                with self.assertRaises(ReadbackMismatch):verify_readback(self.authored,stored)

    def test_no_blanket_null_empty_list_or_description_allowance(self):
        for parent in [('nodes','greeting','on_enter'),('nodes','greeting','transitions',0,'condition')]:
            for key,value in [('unknown',None),('tools',[]),('description','')]:
                with self.subTest(parent=parent,key=key):
                    stored=copy.deepcopy(self.stored);cursor=stored
                    for part in parent:cursor=cursor[part]
                    cursor[key]=value
                    with self.assertRaises(ReadbackMismatch):verify_readback(self.authored,stored)

    def test_order_and_node_membership_preserved(self):
        self.authored['nodes']['greeting']['transitions'].append({'target_node':'greeting','condition':{'operator':'always'}})
        self.stored['nodes']['greeting']['transitions'].append({'target_node':'greeting','condition':{'operator':'always'}})
        self.stored['nodes']['greeting']['transitions'].reverse()
        with self.assertRaises(ReadbackMismatch):verify_readback(self.authored,self.stored)
        self.stored=copy.deepcopy(EVIDENCE['stored']);del self.stored['nodes']['done']
        with self.assertRaises(ReadbackMismatch):verify_readback(EVIDENCE['authored'],self.stored)

    def test_unobserved_node_types_and_condition_shapes_fail_closed(self):
        for node_type in ('code','api_request','unknown'):
            with self.subTest(node_type=node_type):
                a=copy.deepcopy(self.authored);s=copy.deepcopy(self.stored)
                a['nodes']['greeting']['node_type']=s['nodes']['greeting']['node_type']=node_type
                with self.assertRaises(ReadbackMismatch):verify_readback(a,s)
        self.authored['nodes']['greeting']['transitions'][0]['condition']={'operator':'always'}
        self.stored['nodes']['greeting']['transitions'][0]['condition']={'operator':'always','mode':'exact'}
        with self.assertRaises(ReadbackMismatch):verify_readback(self.authored,self.stored)

    def test_authored_defaults_remain_immutable(self):
        self.authored['nodes']['greeting']['passthrough']=True
        with self.assertRaises(ReadbackMismatch):verify_readback(self.authored,self.stored)

    def test_authored_scalar_types_are_exact(self):
        self.authored['nodes']['greeting']['max_node_turns']=1
        self.stored['nodes']['greeting']['max_node_turns']=True
        with self.assertRaises(ReadbackMismatch):verify_readback(self.authored,self.stored)

    def test_fixed_event_model_required(self):
        for config in (None, {}, {'llmConfig':{'provider':'experimental-5','model':'different'}}):
            with self.subTest(config=config):
                self.stored['agent_config']=config
                with self.assertRaisesRegex(ReadbackMismatch,'event model'):verify_readback(self.authored,self.stored)

    def test_explicit_authored_agent_config_is_checked(self):
        self.authored['agent_config']=copy.deepcopy(self.stored['agent_config'])
        self.stored['agent_config']['extra']=True
        with self.assertRaises(ReadbackMismatch):verify_readback(self.authored,self.stored)

    def test_credentials_not_in_error_messages(self):
        secret='synthetic-credential-do-not-log'
        self.authored['nodes']['done']['terminal']['function']['parameters']['token']=secret
        self.stored['nodes']['done']['terminal']['function']['parameters']['token']='changed'
        with self.assertRaises(ReadbackMismatch) as caught:verify_readback(self.authored,self.stored)
        self.assertNotIn(secret,str(caught.exception))

    def test_nonfinite_or_nonobject_input_rejected(self):
        for value in (None, [], {'value':float('nan')}):
            with self.subTest(value=value), self.assertRaises(ValueError):verify_readback(self.authored,value)


class ControllerReadbackTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def test_defaults_flow_through_controller_and_export_unchanged(self):
        class DefaultClient(FakeClient):
            def save_workflow(self,*args):
                self.deployment=super().save_workflow(*args)
                w=self.deployment['workflow'];w['test_variables']={};w['global_nodes']={}
                w['nodes']['start']['tools']=[]
                w['nodes']['start']['transitions'][0]['condition']['mode']='exact'
                return self.deployment
        client=DefaultClient();gen=self.generator();gen.generate.return_value=fixture()
        result=execute(self.args(),generator=gen,client_factory=lambda **kw:client)
        path=PROJECT/result['run_directory']
        self.assertEqual(json.loads((path/'submission.workflow.json').read_text())['workflow'],client.deployment['workflow'])
        events=[json.loads(s) for s in (path/'trace.jsonl').read_text().splitlines()]
        self.assertIn({'verified':True,'accepted_default_fields':4},[{k:v for k,v in e.items() if k in {'verified','accepted_default_fields'}} for e in events if e['event']=='deployment_readback'])
        self.assertTrue(result['action_guard_compilation_verified'])

    def test_drift_stops_before_hosted_tests_export_or_model_repair(self):
        class ChangedClient(FakeClient):
            def save_workflow(self,*args):
                d=super().save_workflow(*args)
                d['workflow']['nodes']['start']['tools']=[{'function':{'name':'extra'}}]
                return d
        client=ChangedClient();gen=self.generator();gen.generate.return_value=fixture();runner=Mock()
        with self.assertRaises(ReadbackMismatch):
            execute(self.args('--test-cases',str(self.directory/'fixture.json')),generator=gen,client_factory=lambda **kw:client,test_runner=runner)
        runner.assert_not_called();self.assertEqual(client.exports,0)
        self.assertEqual(gen.generate_plan.call_count,1)
        run=next((self.directory/'out').iterdir())
        self.assertEqual(json.loads((run/'manifest.json').read_text())['status'],'failed')

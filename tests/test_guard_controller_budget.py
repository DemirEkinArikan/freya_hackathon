"""Execute administrative bounds without pretending they are bank outcomes."""
from copy import deepcopy
import json
import unittest

import test_guard_aliases as aliases
from builder.guards import (CONTROLLED_ENTRY_LIMIT, compile_guarded_workflow,
                            validate_guarded_workflow)
from builder.validation import validate_workflow
from tools.replay_guarded import replay


class ControllerBudgetTests(unittest.TestCase):
    def setUp(self):
        self.h = aliases.GuardAliasTests(methodName="runTest")
        self.h.setUp()

    @property
    def state(self):
        return json.loads(self.h.variables["bg_state"])

    def dispatch(self):
        self.h.account()
        self.h.stage()
        self.h.code("commit")
        self.h.variables["bg_consent"] = "yes"
        self.h.code("bg_commit_accept")
        self.assertTrue(self.state["inflight"])

    def test_repeated_unmatched_turns_end_without_an_invented_bank_close(self):
        context = deepcopy(self.h.context)
        talk = context["plan"]["nodes"]["talk"]
        del talk["max_node_turns"]
        del talk["max_turn_transition"]
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(validate_guarded_workflow(workflow, **context), [])
        self.assertEqual(validate_workflow(workflow, context["openapi"]), [])
        script = {"version": 1,
                  "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"},
                  "frames": [{"node": "talk", "values": {}} for _ in range(10)], "responses": []}
        report = replay(workflow, {k: v for k, v in context.items() if k != "openapi"}, context["openapi"], script)
        self.assertEqual(report["status"], "terminated")
        self.assertTrue(report["final_state"]["quarantined"])
        self.assertFalse(report["final_state"]["close_attempted"])
        self.assertFalse(report["calls"])

    def test_authored_turn_cap_is_kept_when_no_fallback_was_supplied(self):
        context = deepcopy(self.h.context)
        context["plan"]["nodes"]["talk"]["max_node_turns"] = 3
        del context["plan"]["nodes"]["talk"]["max_turn_transition"]
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(workflow["nodes"]["talk"]["max_node_turns"], 3)
        script = {"version": 1,
                  "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"},
                  "frames": [{"node": "talk", "values": {}} for _ in range(3)], "responses": []}
        report = replay(workflow, {k: v for k, v in context.items() if k != "openapi"}, context["openapi"], script)
        self.assertEqual(report["status"], "terminated")
        self.assertFalse(report["calls"])

    def test_existing_turn_cap_and_selected_fallback_remain_operative(self):
        talk = self.h.workflow["nodes"]["talk"]
        self.assertEqual(talk["max_node_turns"], self.h.context["plan"]["nodes"]["talk"]["max_node_turns"])
        gate = talk["max_turn_transition"]
        self.h.code(gate)
        route = self.h.workflow["nodes"][gate]["transitions"][0]
        self.assertEqual(self.h.variables["bg_budget_route"], "continue")
        self.assertEqual(route["target_node"], "close")

    def test_exhaustion_before_preparation_or_confirmation_never_dispatches(self):
        self.h.account()
        self.h.stage()
        staged = deepcopy(self.state["pending_promises"])
        self.h.code("commit")
        before = deepcopy(self.h.variables)
        for node in ("commit", "bg_commit_accept"):
            with self.subTest(node=node):
                self.h.variables = deepcopy(before)
                self.h.variables.update(bg_control_entries=str(CONTROLLED_ENTRY_LIMIT), bg_consent="yes")
                self.h.code(node)
                self.assertEqual(self.h.variables["bg_route"], "blocked")
                self.assertFalse(self.h.variables["bg_body"])
                self.assertFalse(self.state["inflight"])
                self.assertFalse(self.state["attempts"])
                self.assertEqual(self.state["pending_promises"], staged)

    def test_positive_receipt_is_reconciled_before_budget_termination(self):
        self.dispatch()
        self.h.variables["bg_control_entries"] = str(CONTROLLED_ENTRY_LIMIT)
        self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "actual-record"})
        self.assertIn("internal-product-Z", self.state["recorded_promises"])
        self.assertFalse(self.state["inflight"])
        self.assertTrue(self.state["quarantined"])
        self.assertFalse(self.h.variables["bg_body"])
        self.assertEqual(self.h.variables["bg_route"], "blocked")

    def test_malformed_budget_cannot_erase_a_dispatched_positive_receipt(self):
        self.dispatch()
        before = deepcopy(self.h.variables)
        for value in (None, "garbage", "-1", "99999999"):
            with self.subTest(counter=value):
                self.h.variables = deepcopy(before)
                self.h.variables["bg_control_entries"] = value
                self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "actual-record"})
                self.assertIn("internal-product-Z", self.state["recorded_promises"])
                self.assertFalse(self.state["inflight"])
                self.assertTrue(self.state["quarantined"])
                self.assertFalse(self.h.variables["bg_body"])

    def test_gate_preserves_recorded_success_and_an_uncertain_second_write(self):
        self.dispatch()
        self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "actual-record"})
        self.h.stage("internal-product-A", "82.10")
        self.h.code("commit")
        self.h.variables["bg_consent"] = "yes"
        self.h.code("bg_commit_accept")
        original = self.state
        gate = self.h.workflow["nodes"]["talk"]["max_turn_transition"]
        self.h.variables["bg_control_entries"] = str(CONTROLLED_ENTRY_LIMIT)
        self.h.code(gate)
        self.assertEqual(self.h.variables["bg_budget_route"], "stop")
        self.assertEqual(self.state["recorded_promises"], original["recorded_promises"])
        self.assertEqual(self.state["inflight"], original["inflight"])
        self.assertTrue(self.state["quarantined"])
        self.assertFalse(self.h.variables["bg_body"])

    def test_controller_gate_remains_exactly_authenticated(self):
        tampered = deepcopy(self.h.workflow)
        gate = tampered["nodes"]["talk"]["max_turn_transition"]
        parameters = tampered["nodes"][gate]["pre_actions"][0]["function"]["parameters"]
        parameters["properties"]["code"]["default"] += "\nvars['bg_control_entries'] = '0'\n"
        self.assertTrue(validate_guarded_workflow(tampered, **self.h.context))


if __name__ == "__main__":
    unittest.main()

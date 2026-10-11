"""Execute administrative bounds without pretending they are bank outcomes."""
from copy import deepcopy
import json
import unittest

import test_guard_aliases as aliases
from builder.guards import (BUDGET_CLOSE, CONTROLLED_ENTRY_LIMIT, DEFAULT_CONVERSATION_TURNS,
                            compile_guarded_workflow, validate_guarded_workflow)
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

    def capped(self, turns, cap=None):
        context = deepcopy(self.h.context)
        talk = context["plan"]["nodes"]["talk"]
        del talk["max_node_turns"], talk["max_turn_transition"]
        if cap:
            talk["max_node_turns"] = cap
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(validate_guarded_workflow(workflow, **context), [])
        self.assertEqual(validate_workflow(workflow, context["openapi"]), [])
        self.assertEqual((workflow["nodes"]["talk"]["max_node_turns"], workflow["nodes"]["talk"]["max_turn_transition"]),
                         (cap or DEFAULT_CONVERSATION_TURNS, BUDGET_CLOSE))
        script = {"version": 1,
                  "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"},
                  "frames": [{"node": "talk", "values": {}} for _ in range(turns)],
                  "responses": [{"operation": self.h.aliases["close_call"],
                                 "envelope": {"status_code": 200, "data": {"evidence_ok": True}}}]}
        return replay(workflow, {k: v for k, v in context.items() if k != "openapi"}, context["openapi"], script)

    def test_repeated_unmatched_turns_close_from_recorded_facts_only(self):
        report = self.capped(DEFAULT_CONVERSATION_TURNS)
        self.assertEqual((report["status"], report["terminal_node"]), ("terminated", "bg_terminal"))
        # Unreached identity: the policy's fixed no-contact row, with no proposal and no reason.
        self.assertEqual([sorted(call["arguments"].values()) for call in report["calls"]], [["C_F", "synthetic-session"]])
        self.assertEqual(report["final_state"]["close"], {"outcome": "not_reached"})
        self.assertEqual([report["final_state"][key] for key in ("closed", "close_attempted", "quarantined")], [True, True, False])
        self.assertEqual(self.capped(DEFAULT_CONVERSATION_TURNS - 1)["status"], "missing_conversation_frame")

    def test_authored_turn_cap_is_kept_when_no_fallback_was_supplied(self):
        report = self.capped(3, cap=3)
        self.assertEqual((report["status"], report["terminal_node"]), ("terminated", "bg_terminal"))
        self.assertEqual(len(report["calls"]), 1)

    def test_budget_close_without_an_eligible_close_is_flagged(self):
        # Unresolved products leave no eligible close: refused, flagged, nothing sent.
        self.h.account()
        self.h.code(BUDGET_CLOSE)
        self.assertEqual((self.h.variables["bg_route"], self.h.variables["bg_body"], self.h.variables["bg_quarantined"]),
                         ("blocked", "", "yes"))
        self.assertEqual([self.state[key] for key in ("quarantined", "close_attempted", "attempts")], [True, False, {}])
        self.assertEqual(self.h.workflow["nodes"][BUDGET_CLOSE]["transitions"][0]["target_node"], "bg_quarantine")

    def test_exhausted_gate_closes_from_recorded_facts_and_malformed_counts_are_flagged(self):
        gate = self.h.workflow["nodes"]["talk"]["max_turn_transition"]
        self.assertEqual(self.h.workflow["nodes"][gate]["transitions"][-1]["target_node"], BUDGET_CLOSE)
        before = deepcopy(self.h.variables)
        for value in (str(CONTROLLED_ENTRY_LIMIT), None, "garbage"):
            with self.subTest(counter=value):
                self.h.variables = deepcopy(before)
                self.h.variables["bg_control_entries"] = value
                self.h.code(gate)
                self.assertEqual(self.h.variables["bg_budget_route"], "stop")
                self.h.code(BUDGET_CLOSE)
                if value is None or value == "garbage":
                    self.assertEqual((self.h.variables["bg_body"], self.h.variables["bg_quarantined"]), ("", "yes"))
                    self.assertFalse(self.state["close_attempted"])
                    continue
                self.assertEqual((self.h.variables["bg_route"], self.h.variables["bg_quarantined"]), ("allow", ""))
                self.h.api_result(BUDGET_CLOSE, {"evidence_ok": True})
                self.assertEqual((self.state["close"], self.state["closed"]), ({"outcome": "not_reached"}, True))

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
        for value in (None, "garbage", "-1", "99999999", "²", "١"):
            with self.subTest(counter=value):
                self.h.variables = deepcopy(before)
                self.h.variables["bg_control_entries"] = value
                self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "actual-record"})
                self.assertIn("internal-product-Z", self.state["recorded_promises"])
                self.assertFalse(self.state["inflight"])
                self.assertTrue(self.state["quarantined"])
                self.assertFalse(self.h.variables["bg_body"])

    def test_budget_after_first_batch_success_does_not_invent_a_second_dispatch(self):
        self.h.account()
        self.h.stage()
        self.h.stage("internal-product-A", "82.10")
        self.h.code("commit")
        self.h.variables["bg_consent"] = "yes"
        self.h.code("bg_commit_accept")
        self.h.variables["bg_control_entries"] = str(CONTROLLED_ENTRY_LIMIT)
        self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "first-record"})
        self.assertEqual(set(self.state["recorded_promises"]), {"internal-product-Z"})
        self.assertFalse(self.state["inflight"])
        self.assertEqual(len(self.state["attempts"]), 1)
        self.assertEqual(next(iter(self.state["attempts"].values())), {"status": "recorded", "count": 1})
        self.assertIn("internal-product-A", self.state["pending_promises"])
        self.assertTrue(self.state["quarantined"])
        self.assertFalse(self.h.variables["bg_body"])

    def test_budget_after_qualified_failure_does_not_invent_a_retry(self):
        self.dispatch()
        self.h.variables["bg_control_entries"] = str(CONTROLLED_ENTRY_LIMIT)
        self.h.api_result("commit", {"evidence_ok": False, "evidence_error": "ERR_BUSY"}, status=503)
        self.assertFalse(self.state["inflight"])
        self.assertFalse(self.state["recorded_promises"])
        self.assertEqual(len(self.state["attempts"]), 1)
        self.assertEqual(next(iter(self.state["attempts"].values())), {"status": "nonpersistent_failed", "count": 1})
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
        self.h.code(BUDGET_CLOSE)  # an in-flight write refuses the budget close
        self.assertEqual(self.h.variables["bg_quarantined"], "yes")
        self.assertFalse(self.state["close_attempted"])
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

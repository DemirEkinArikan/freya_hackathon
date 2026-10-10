"""A partial source priority must not erase disjoint protected outcomes."""
import copy
import json
import unittest

from builder.guards import compile_guarded_workflow
from builder.safety import digest
from test_local_dispositions import local_context


class PartialClosingPrecedenceTests(unittest.TestCase):
    def setUp(self):
        context, policy, _ = local_context()
        context["execution_policy"]["profile"]["close_precedence"] = ["transferred", "promise"]
        context["execution_policy"]["sha256"] = digest({k:v for k,v in context["execution_policy"].items() if k != "sha256"})
        context["contract"]["active_rules"]["close_precedence"]["value"] = ["transferred", "promise"]
        context["contract"]["sha256"] = digest({k:v for k,v in context["contract"].items() if k != "sha256"})
        self.context = context
        self.workflow = compile_guarded_workflow(**context)["workflow"]
        self.variables = {"customer_id": "invented-reference", "now": "2031-05-02"}
        self.code("bg_init")
        state = json.loads(self.variables["bg_state"])
        state.update(identity_status="verified", verified_fields=["challenge_A", "challenge_B"], account_loaded=True,
                     products=[{"product_id":"asset-one", "type":"revolving", "days_late":8, "overdue_amount":300, "minimum_due":37.25}],
                     notes_finalized=True)
        self.state = state

    def code(self, node):
        code = self.workflow["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(code, {"vars":self.variables})

    def close(self, state):
        self.variables["bg_state"] = json.dumps(state)
        self.variables.update(outcome="forged_outcome", close_reason="forged_reason")
        self.code("close")
        return json.loads(self.variables["bg_state"])

    def test_unique_local_refusal_uses_protected_reason_without_bank_note_permission(self):
        state = copy.deepcopy(self.state)
        state["pending_notes"] = {"asset-one":"refused"}
        after = self.close(state)
        self.assertEqual(self.variables["bg_route"], "allow")
        body = json.loads(self.variables["bg_body"])
        self.assertEqual(body["input_outcome"], "C_D")
        self.assertEqual(body["input_reason"], "WHY_R")
        self.assertTrue(after["close_attempted"])
        self.assertNotIn("bg_finish_api", self.workflow["nodes"])

    def test_unique_paid_fact_omits_reason_and_remains_at_most_once(self):
        state = copy.deepcopy(self.state);state["pending_notes"] = {"asset-one":"paid"}
        after = self.close(state)
        self.assertEqual(self.variables["bg_route"], "allow")
        body = json.loads(self.variables["bg_body"])
        self.assertEqual(body["input_outcome"], "C_B")
        self.assertNotIn("input_reason", body)
        self.close(after)
        self.assertNotEqual(self.variables["bg_route"], "allow")

    def test_missing_or_unfinalized_facts_cannot_use_model_proposal(self):
        for finalized in (False,True):
            state = copy.deepcopy(self.state);state["notes_finalized"] = finalized
            if not finalized:state["pending_notes"] = {"asset-one":"refused"}
            self.close(state)
            self.assertNotEqual(self.variables["bg_route"], "allow")
            self.assertEqual(self.variables["bg_body"], "")

    def test_multiple_unranked_protected_facts_remain_blocked(self):
        context = copy.deepcopy(self.context)
        context["execution_policy"]["profile"]["close_precedence"] = ["paid"]
        context["execution_policy"]["sha256"] = digest({k:v for k,v in context["execution_policy"].items() if k != "sha256"})
        context["contract"]["active_rules"]["close_precedence"]["value"] = ["paid"]
        context["contract"]["sha256"] = digest({k:v for k,v in context["contract"].items() if k != "sha256"})
        self.workflow = compile_guarded_workflow(**context)["workflow"]
        state = copy.deepcopy(self.state)
        state["recorded_promises"] = {"asset-one":{"product_id":"asset-one", "amount":50, "date":"2031-05-02", "promise_id":"receipt"}}
        state["transfer"] = {"status":"recorded", "queue":"helpdesk", "reason":"dispute"}
        after = self.close(state)
        self.assertNotEqual(self.variables["bg_route"], "allow")
        self.assertEqual(self.variables["bg_body"], "")
        self.assertFalse(after["close_attempted"])
        self.assertEqual(after["recorded_promises"], state["recorded_promises"])

    def test_ranked_transfer_still_dominates_recorded_promise_and_local_facts(self):
        state = copy.deepcopy(self.state);state["pending_notes"] = {"asset-one":"refused"}
        state["recorded_promises"] = {"asset-other":{"product_id":"asset-other", "amount":50, "date":"2031-05-02", "promise_id":"receipt"}}
        state["transfer"] = {"status":"recorded", "queue":"helpdesk", "reason":"dispute"}
        self.close(state)
        self.assertEqual(self.variables["bg_route"], "allow")
        self.assertEqual(json.loads(self.variables["bg_body"])["input_outcome"], "C_C")


if __name__ == "__main__":
    unittest.main()

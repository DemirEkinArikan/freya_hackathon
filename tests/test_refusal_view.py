"""Invented tests: a guard policy refusal keeps the runtime view with one sanitized reason code."""
import copy
import json
import unittest

from test_dialogue_facts import CUSTOMER, PRODUCTS
from test_guards import fixture
from builder.guards import _KERNEL, _catalogue, _code, _profile, compile_guarded_workflow, validate_guarded_workflow
from builder.validation import validate_code

DEGRADED = {"version": 1, "route": "blocked", "call_status": "unavailable"}


class RefusalViewTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.profile = _profile(self.context["contract"], self.context["execution_policy"], self.context["plan"], None)
        self.catalog = _catalogue(self.context["contract"], self.context["openapi"], None)
        self.fields = self.profile["identity_fields"]
        self.entries = None

    def step(self, phase, state=None, action="", inputs=None, data=None):
        inputs, data = inputs or {}, data or {}
        variables = {"customer_id": CUSTOMER, "now": "2031-05-02T11:00", "bg_facts": "STALE",
                     "bg_state": state if isinstance(state, str) else json.dumps(state)}
        if self.entries is not None:
            variables["bg_control_entries"] = self.entries
        public = {key: "proposal_" + key for key in inputs}
        variables.update({public[key]: value for key, value in inputs.items()})
        mapped = {key: "mapped_" + key for key in data}
        variables.update({mapped[key]: value for key, value in data.items()})
        exec(_code(phase, action, public, self.profile, self.catalog, {}, mapped), {"vars": variables})
        self.entries = variables["bg_control_entries"]
        return variables

    def state(self, variables):
        return json.loads(variables["bg_state"])

    def verified(self, account=True):
        state = self.state(self.step("init"))
        for field in self.fields:
            state = self.state(self.step("prepare", state, "verify_identity", {"field": field, "answer": "invented"}))
            state = self.state(self.step("result", state, "verify_identity", data={
                "ok": True, "verified": True, "wrong_answers_left": self.profile["identity_wrong_limit"]}))
        if account:
            state = self.state(self.step("prepare", state, "get_account"))
            state = self.state(self.step("result", state, "get_account", data={"ok": True, "products": copy.deepcopy(PRODUCTS)}))
        return state

    def refuse(self, state, action, inputs, reason):
        variables = self.step("prepare", state, action, inputs)
        facts = json.loads(variables["bg_facts"])
        self.assertEqual(facts["latest_action"], {"action": action, "status": "refused", "reason": reason})
        self.assertEqual((facts["route"], facts["call_status"]), ("blocked", "active"))
        # The guard still refused: no request, no write, no quarantine, state unchanged.
        self.assertEqual((variables["bg_route"], variables["bg_body"], variables["bg_quarantined"]), ("blocked", "", ""))
        self.assertTrue(all(value == "" for key, value in variables.items() if key.startswith("bg_arg_")))
        self.assertEqual(self.state(variables), state)
        self.assertNotIn(CUSTOMER, variables["bg_facts"])
        return facts

    def test_identity_refusals_keep_progress_next_factor_and_reason(self):
        state = self.state(self.step("init"))
        facts = self.refuse(state, "verify_identity", {"field": self.fields[1], "answer": "invented"}, "order")
        self.assertEqual((facts["identity"]["next_factor"], facts["identity"]["verified_factor_count"]), (self.fields[0], 0))
        self.refuse(state, "verify_identity", {"field": self.fields[0], "answer": " "}, "missing_input")
        facts = self.refuse(state, "get_account", {}, "not_verified")
        self.assertEqual((facts["account"]["loaded"], facts["account"]["products"]), (False, []))
        self.assertEqual(facts["identity"]["next_factor"], self.fields[0])

    def test_account_step_refusals_keep_constraints_and_reason(self):
        state = self.verified()
        first, second = PRODUCTS[1]["product_id"], PRODUCTS[0]["product_id"]
        facts = self.refuse(state, "stage_promise", {"product_id": first, "amount": "1.00", "date": "2031-05-06"}, "out_of_policy")
        reference = facts["internal_action_references"]["products"][0]
        self.assertEqual((reference["proposal_constraints_status"], reference["proposal_constraints"]["floor_amount_cents"]), ("available", 3725))
        self.assertEqual(facts["product_states"][0]["promise"], "none")
        self.refuse(state, "stage_promise", {"product_id": first, "amount": "50.00", "date": "2031-06-30"}, "out_of_policy")
        self.refuse(state, "stage_promise", {"product_id": first, "amount": "50.00", "date": "next Friday"}, "missing_input")
        self.refuse(state, "stage_promise", {"product_id": first, "amount": "fifty", "date": "2031-05-06"}, "missing_input")
        self.refuse(state, "stage_promise", {"product_id": second, "amount": "90.00", "date": "2031-05-06"}, "order")
        self.refuse(state, "commit_promises", {}, "order")
        self.refuse(state, "get_account", {}, "closed")
        self.refuse(state, "verify_identity", {"field": self.fields[0], "answer": "invented"}, "closed")
        self.refuse(self.verified(account=False), "stage_promise", {"product_id": first, "amount": "50.00", "date": "2031-05-06"}, "order")
        # The call continues from the kept state.
        variables = self.step("prepare", state, "stage_promise", {"product_id": first, "amount": "50.00", "date": "2031-05-06"})
        self.assertEqual(variables["bg_route"], "done")
        self.assertEqual(self.state(variables)["pending_promises"], {first: {"amount_cents": 5000, "date": "2031-05-06"}})

    def test_refusal_text_and_caller_words_never_reach_the_view(self):
        state = self.verified()
        variables = self.step("prepare", state, "stage_promise", {"product_id": PRODUCTS[1]["product_id"],
                                                                  "amount": "50.00", "date": "PRIVATE_CALLER_WORDS"})
        self.assertNotIn("PRIVATE_CALLER_WORDS", variables["bg_facts"])
        self.assertNotIn("isoformat", variables["bg_facts"])

    def test_corruption_and_unlisted_faults_keep_the_degraded_view(self):
        state = self.verified()
        malformed = dict(state)
        malformed.pop("attempts")
        for bad, action in (("malformed-state", "get_account"), (json.dumps(malformed), "get_account"), (state, "unlisted_action")):
            with self.subTest(action=action):
                facts = json.loads(self.step("prepare", bad, action)["bg_facts"])
                self.assertEqual(facts, dict(DEGRADED, latest_action={"action": action, "status": "blocked_or_malformed"}))
        namespace = {}
        exec(_KERNEL, namespace)
        self.assertEqual(namespace["guard_refusal"](state, "schedule_callback", {"date": "2031-05-06", "time": "12:00"},
                                                    "malformed protected dispatch history"), "")

    def test_refused_close_keeps_its_own_termination_path(self):
        state = self.state(self.step("prepare", self.verified(), "stage_promise",
                                     {"product_id": PRODUCTS[1]["product_id"], "amount": "50.00", "date": "2031-05-06"}))
        facts = json.loads(self.step("prepare", state, "close_call")["bg_facts"])
        self.assertEqual(facts["latest_action"], {"action": "close_call", "status": "blocked_or_malformed"})

    def test_compiled_refusal_handling_passes_code_validation(self):
        bundle = compile_guarded_workflow(**self.context)
        self.assertEqual(validate_guarded_workflow(bundle["workflow"], **self.context), [])
        code = _code("prepare", "verify_identity", {"field": "f", "answer": "a"}, self.profile, self.catalog, {}, {})
        self.assertEqual(validate_code(code), [])


if __name__ == "__main__":
    unittest.main()

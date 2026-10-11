"""Hosted readiness H2: guards do not wait for extraction of controller-known values."""
import copy
import unittest

from test_guards import fixture
from builder.fallback_plan import deterministic_plan
from builder.guards import PROPOSAL_MIN_CONFIDENCE, compile_guarded_workflow, run_guard_step, validate_guarded_workflow

PROFILE = {"identity_fields": ["factor_alpha", "factor_beta"], "identity_wrong_limit": 4}
CATALOG = {"verify_identity": {"name": "invented_check_op", "fields": {
    "customer_id": "member_wire", "field": "factor_wire", "answer": "answer_wire"},
    "request_schema": {"type": "object", "properties": {
        "member_wire": {"type": "string"},
        "factor_wire": {"type": "string", "enum": ["factor_alpha", "factor_beta"]},
        "answer_wire": {"type": "string"}},
        "required": ["member_wire", "factor_wire", "answer_wire"]}}}


class ControllerFactorTests(unittest.TestCase):
    def setUp(self):
        self.profile = copy.deepcopy(PROFILE)
        self.state = run_guard_step("init", None, profile=self.profile, catalog=CATALOG,
                                    customer_id="invented-member", now="2031-05-02T10:00:00")[0]

    def prepare(self, inputs):
        return run_guard_step("prepare", self.state, "verify_identity", inputs, profile=self.profile, catalog=CATALOG)

    def sent_factor(self, inputs):
        state, route, body, _ = self.prepare(inputs)
        self.assertEqual(route, "allow")
        self.assertEqual(body["answer_wire"], inputs["answer"])
        return state, body["factor_wire"]

    def pass_first(self):
        self.state = self.prepare({"answer": "first"})[0]
        self.state = run_guard_step("result", self.state, "verify_identity", result={
            "_http_status": 200, "ok": True, "verified": True, "wrong_answers_left": 4},
            profile=self.profile, catalog=CATALOG)[0]
        self.assertEqual(self.state["verified_fields"], ["factor_alpha"])

    def test_absent_or_undeclared_proposal_sends_the_next_factor(self):
        for inputs in ({"answer": "spoken"}, {"field": "", "answer": "spoken"}, {"field": None, "answer": "spoken"},
                       {"field": "date", "answer": "spoken"}, {"field": "factor_alpha", "answer": "spoken"}):
            self.assertEqual(self.sent_factor(inputs)[1], "factor_alpha")
        self.pass_first()
        self.assertEqual(self.sent_factor({"answer": "digits"})[1], "factor_beta")

    def test_stale_passed_factor_cannot_block_or_reopen_without_latest_check(self):
        self.pass_first()
        state, factor = self.sent_factor({"field": "factor_alpha", "answer": "digits"})
        self.assertEqual((factor, state["verified_fields"]), ("factor_beta", ["factor_alpha"]))

    def test_proposed_later_factor_and_empty_answer_stay_refused(self):
        for inputs in ({"field": "factor_beta", "answer": "spoken"}, {"answer": " "}, {"field": "factor_alpha", "answer": ""}):
            with self.assertRaisesRegex(ValueError, "policy order"):
                self.prepare(inputs)

    def test_latest_check_correction_still_names_the_passed_factor(self):
        self.profile["identity_factor_state"] = "latest_check"
        self.pass_first()
        state, factor = self.sent_factor({"field": "factor_alpha", "answer": "corrected"})
        self.assertEqual((factor, state["verified_fields"]), ("factor_alpha", []))
        self.assertEqual(self.sent_factor({"answer": "digits"})[1], "factor_beta")


class ProposalConfidenceTests(unittest.TestCase):
    def compile(self, context):
        args = (context["plan"], context["contract"], context["openapi"], context["execution_policy"])
        workflow = compile_guarded_workflow(*args)["workflow"]
        self.assertEqual(validate_guarded_workflow(workflow, *args), [])
        return workflow

    def test_plan_may_omit_the_factor_binding(self):
        context = fixture()
        del context["plan"]["nodes"]["identity"]["inputs"]["field"]
        self.assertIn("identity", self.compile(context)["nodes"])

    def test_free_value_proposals_are_capped_and_others_kept(self):
        context = fixture()
        schema = context["plan"]["nodes"]["talk"]["extraction_schema"]
        schema += [{"name": "amount", "type": "float", "description": "Proposed amount.", "min_confidence": 0.5},
                   {"name": "queue", "type": "string", "enum_values": ["desk"], "description": "Proposed queue.", "min_confidence": 0.95},
                   {"name": "free_note", "type": "string", "description": "Unbound text.", "min_confidence": 0.9}]
        compiled = {e["name"]: e["min_confidence"] for e in self.compile(context)["nodes"]["talk"]["extraction_schema"]}
        self.assertEqual(compiled, {"answer_text": PROPOSAL_MIN_CONFIDENCE, "amount": 0.5, "queue": 0.95, "free_note": 0.9})
        self.assertEqual(context["plan"]["nodes"]["talk"]["extraction_schema"][0]["min_confidence"], 0.95)

    def test_fallback_plan_binds_the_factor_only_for_latest_check(self):
        for factor_state, inputs in ((None, {"answer": "proposal_identity_answer"}),
                                     ("latest_check", {"field": "proposal_identity_field", "answer": "proposal_identity_answer"})):
            rules = {"identity_fields": {"value": ["factor_alpha", "factor_beta"]}}
            if factor_state:
                rules["identity_factor_state"] = {"value": factor_state}
            plan = deterministic_plan({"goal": "An invented goal", "active_rules": rules}, {"actions": ["verify_identity", "close_call"]})
            self.assertEqual(plan["nodes"]["verify_identity"]["inputs"], inputs)
            names = [entry["name"] for entry in plan["nodes"]["dialogue"]["extraction_schema"]]
            self.assertEqual("proposal_identity_field" in names, factor_state is not None)


if __name__ == "__main__":
    unittest.main()

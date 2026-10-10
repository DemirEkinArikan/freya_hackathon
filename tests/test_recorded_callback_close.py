"""Invented frozen closing declarations exercise the identical sealed kernel."""
import copy
import json
import unittest

from builder.guards import _code, run_guard_step


def context():
    # The wire vocabulary is intentionally unrelated to public-bank values.
    outcomes = {"not_reached": "Z_REACH", "not_verified": "Z_ID", "transferred": "Z_HAND",
                "promise": "Z_PLAN", "paid": "Z_CLEAR", "no_promise": "Z_NONE"}
    reasons = {"callback": "Q_BOOKED", "no_callback": "Q_UNBOOKED", "declined": "Q_STOP",
               "failed": "Q_LIMIT", "unclear": "Q_UNKNOWN", "human": "Q_HELP",
               "refused": "Q_REFUSED"}
    maps = {group: {"semantic_to_wire": values, "wire_to_semantic": {wire: semantic for semantic, wire in values.items()}}
            for group, values in (("outcomes", outcomes), ("reasons", reasons))}
    profile = {"record_notes": False, "resolve_all_products": True, "require_confirmation": False,
        "policy_applicability": {"products": "single"},
        "close_precedence": ["transferred", "promise"],
        "close_reasons": {"not_reached": ["callback", "no_callback", "unclear"],
            "not_verified": ["declined", "failed"], "transferred": ["human"],
            "promise": [], "paid": [], "no_promise": ["refused"]},
        "identity_close": {"unreached": {"outcome": "not_reached", "reason": "unclear"},
            "incomplete": {"outcome": "not_verified", "reason": "declined"},
            "failed": {"outcome": "not_verified", "reason": "failed"}},
        "identity_close_choices": {"unreached": [
            {"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"},
            {"outcome": "not_reached", "reason": "no_callback", "requires": "no_recorded_callback"},
            {"outcome": "not_reached", "reason": "unclear", "requires": "none"}]},
        "callback_window": {"start": "09:00", "end": "17:00", "allow_today": True, "next_business_days": 1},
        "business_weekdays": [0, 1, 2, 3, 4], "holidays": []}
    def operation(fields, enum_fields=None):
        properties = {wire: {"type": "string"} for wire in fields.values()}
        for wire, values in (enum_fields or {}).items():
            properties[wire]["enum"] = values
        return {"fields": fields, "request_schema": {"type": "object", "properties": properties,
                "required": [fields["customer_id"]]}}
    catalog = {
        "schedule_callback": operation({"customer_id": "member_wire", "date": "day_wire", "time": "clock_wire"}),
        "close_call": operation({"customer_id": "member_wire", "outcome": "result_wire", "reason": "cause_wire"},
                                {"result_wire": list(outcomes.values()), "cause_wire": list(reasons.values())})}
    state = run_guard_step("init", None, profile=profile, catalog=catalog,
                           customer_id="invented-member", now="2031-05-02T10:00:00+00:00")[0]
    return state, profile, catalog, maps


class RecordedCallbackCloseTests(unittest.TestCase):
    def setUp(self):
        self.state, self.profile, self.catalog, self.maps = context()

    def step(self, phase, action, inputs=None, result=None):
        return run_guard_step(phase, self.state, action, inputs, result,
                              profile=self.profile, catalog=self.catalog, maps=self.maps)

    def record_callback(self):
        self.state, route, _, _ = self.step("prepare", "schedule_callback", {"date": "2031-05-02", "time": "12:00"})
        self.assertEqual(route, "allow")
        self.state, route, _, _ = self.step("result", "schedule_callback", result={"ok": True, "_http_status": 200})
        self.assertEqual(route, "done")
        self.assertEqual(len(self.state["callbacks"]), 1)

    def close(self, proposal=None):
        return self.step("prepare", "close_call", proposal or {})

    def assert_callback_close(self, proposal=None):
        before = copy.deepcopy((self.state, self.profile, self.catalog, self.maps, proposal))
        new_state, route, body, _ = self.close(proposal)
        self.assertEqual(route, "allow")
        self.assertEqual(body, {"member_wire": "invented-member", "result_wire": "Z_REACH", "cause_wire": "Q_BOOKED"})
        self.assertEqual(new_state["close"], {"outcome": "not_reached", "reason": "callback"})
        self.assertTrue(new_state["close_attempted"])
        self.assertEqual((self.state, self.profile, self.catalog, self.maps, proposal), before)

    def test_recorded_callback_derives_frozen_choice_without_dialogue_proposal(self):
        self.record_callback()
        self.assert_callback_close()

    def test_compiled_hook_derives_absent_extraction_and_seals_single_close_attempt(self):
        self.record_callback()
        before = copy.deepcopy(self.state)
        variables = {"bg_state": json.dumps(self.state)}
        code = _code("prepare", "close_call", {"outcome": "proposal_outcome", "reason": "proposal_reason"},
                     self.profile, self.catalog, self.maps, {})
        exec(code, {"vars": variables})
        self.assertEqual(variables["bg_route"], "allow")
        self.assertEqual(json.loads(variables["bg_body"]),
                         {"member_wire": "invented-member", "result_wire": "Z_REACH", "cause_wire": "Q_BOOKED"})
        sealed = json.loads(variables["bg_state"])
        self.assertTrue(sealed["close_attempted"])
        self.assertEqual(json.loads(variables["bg_facts"])["recorded_counts"]["callbacks"], 1)
        self.assertEqual(self.state, before)
        exec(code, {"vars": variables})
        self.assertEqual(variables["bg_route"], "blocked")
        self.assertEqual(variables["bg_body"], "")
        self.assertEqual(json.loads(variables["bg_state"])["callbacks"], before["callbacks"])

    def test_stale_forged_and_wrong_wire_proposals_cannot_hide_callback(self):
        self.record_callback()
        for proposal in ({"outcome": "Z_ID", "reason": "Q_STOP"},
                         {"outcome": "Z_REACH", "reason": "Q_UNBOOKED"},
                         {"outcome": "Z_REACH", "reason": "Q_UNKNOWN"},
                         {"outcome": "forged", "reason": "forged"},
                         {"outcome": None, "reason": {"forged": True}}):
            with self.subTest(proposal=proposal):
                self.assert_callback_close(proposal)

    def test_missing_recorded_callback_cannot_claim_declared_callback_success(self):
        for proposal in ({"outcome": "Z_REACH", "reason": "Q_BOOKED"},
                         {"outcome": "not_reached", "reason": "callback"}):
            with self.subTest(proposal=proposal), self.assertRaisesRegex(ValueError, "callback evidence"):
                self.close(proposal)

    def test_without_callback_existing_declared_no_callback_choice_still_works(self):
        _, route, body, _ = self.close({"outcome": "Z_REACH", "reason": "Q_UNBOOKED"})
        self.assertEqual((route, body["cause_wire"]), ("allow", "Q_UNBOOKED"))

    def test_missing_or_ambiguous_callback_choice_blocks_free_fallback(self):
        self.record_callback()
        original = copy.deepcopy(self.profile["identity_close_choices"]["unreached"])
        for choices in (None, original[1:], original + [dict(original[0], reason="unclear")]):
            with self.subTest(choices=choices):
                self.profile["identity_close_choices"] = {} if choices is None else {"unreached": choices}
                with self.assertRaisesRegex(ValueError, "one declared closing choice"):
                    self.close({"outcome": "Z_REACH", "reason": "Q_UNKNOWN"})

    def test_fixed_explicit_callback_row_remains_a_supported_source_declaration(self):
        self.record_callback()
        del self.profile["identity_close_choices"]
        self.profile["identity_close"]["unreached"] = {"outcome": "not_reached", "reason": "callback"}
        self.assert_callback_close()

    def test_fixed_callback_row_still_requires_recorded_evidence(self):
        del self.profile["identity_close_choices"]
        self.profile["identity_close"]["unreached"] = {"outcome": "not_reached", "reason": "callback"}
        with self.assertRaisesRegex(ValueError, "recorded callback"):
            self.close({"outcome": "Z_REACH"})

    def test_partial_identity_and_wrong_answers_cannot_borrow_unreached_choice(self):
        self.record_callback()
        original = copy.deepcopy(self.state)
        for updates in ({"verified_fields": ["member_factor"]}, {"wrong_answers": 1}):
            with self.subTest(updates=updates):
                self.state = dict(copy.deepcopy(original), **updates)
                with self.assertRaisesRegex(ValueError, "actual identity phase"):
                    self.close({"outcome": "Z_REACH", "reason": "Q_BOOKED"})

    def test_partial_identity_uses_its_own_explicit_callback_choice(self):
        self.record_callback()
        self.state["verified_fields"] = ["member_factor"]
        self.profile["close_reasons"]["not_verified"].append("callback")
        self.profile["identity_close_choices"]["incomplete"] = [
            {"outcome": "not_verified", "reason": "callback", "requires": "recorded_callback"}]
        _, route, body, _ = self.close()
        self.assertEqual((route, body["result_wire"], body["cause_wire"]), ("allow", "Z_ID", "Q_BOOKED"))

    def test_failed_identity_keeps_its_own_frozen_close(self):
        self.record_callback()
        self.state["identity_status"] = "failed"
        _, route, body, _ = self.close({"outcome": "Z_REACH", "reason": "Q_BOOKED"})
        self.assertEqual((route, body["result_wire"], body["cause_wire"]), ("allow", "Z_ID", "Q_LIMIT"))

    def test_loaded_account_or_completed_identity_cannot_bypass_protected_outcome(self):
        self.record_callback()
        original = copy.deepcopy(self.state)
        for updates in ({"identity_status": "verified"}, {"account_loaded": True},
                        {"account_loaded": True, "identity_status": "failed"}):
            with self.subTest(updates=updates):
                self.state = dict(copy.deepcopy(original), **updates)
                with self.assertRaisesRegex(ValueError, "protected evidence"):
                    self.close({"outcome": "Z_REACH", "reason": "Q_BOOKED"})

    def test_transfer_promise_and_paid_protected_facts_keep_priority(self):
        self.record_callback()
        original = copy.deepcopy(self.state)
        examples = [({"transfer": {"status": "recorded", "reason": "human"}}, "Z_HAND"),
                    ({"recorded_promises": {"invented-product": {"date": "2031-05-02"}}}, "Z_PLAN"),
                    ({"products": [{"product_id": "invented-product"}], "pending_notes": {"invented-product": "paid"},
                      "notes_finalized": True}, "Z_CLEAR")]
        for updates, wire in examples:
            with self.subTest(wire=wire):
                self.state = dict(copy.deepcopy(original), **updates)
                _, route, body, _ = self.close()
                self.assertEqual((route, body["result_wire"]), ("allow", wire))

    def test_ineligible_declared_callback_reason_fails_final_frozen_vocabulary_check(self):
        self.record_callback()
        self.profile["close_reasons"]["not_reached"].remove("callback")
        with self.assertRaisesRegex(ValueError, "reason outside policy"):
            self.close()


if __name__ == "__main__":
    unittest.main()

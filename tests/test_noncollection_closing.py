"""Closing for non-collections goals: callback, identity-only, information-only.

No model, network or platform calls. The callback cases replay the binding
rules of the original failed fresh callback build (run
20261011T063051905603Z; policy.rejected-3.json sha256 a605c4d3..., the same
values as attempt 1) against its exact inherited API, the in-repo train-2
profile (canonical sha256 eb561508...). Every other code here is invented.
"""
import copy
import unittest

from builder.action_bindings import ActionBindingError, compile_action_bindings
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import execution_action_catalogue
from builder.safety import digest, read_json
from benchmarks.generate import ROOT
from test_execution_requirements import add_rule, rule
from test_goal_requirements import PRODUCT, SealedSession, compiled, synthetic, witness


ORIGINAL = {
    "execution_actions": ["schedule_callback", "close_call"],
    "business_weekdays": [0, 1, 2, 3, 4], "holidays": ["2026-12-24", "2026-12-31"],
    "callback_window": {"start": "09:15", "end": "16:45", "allow_today": True, "next_business_days": 1},
    "tool_aliases": {"schedule_callback": "schedule_callback", "close_call": "close_call"},
    "action_fields": {"schedule_callback": {"customer_id": "session_ref", "date": "pay_on", "time": "time"},
                      "close_call": {"customer_id": "session_ref", "outcome": "result_code", "reason": "reason_code"}},
    "result_fields": {"schedule_callback": {"ok": "accepted", "error": "failure_code"},
                      "close_call": {"ok": "accepted", "error": "failure_code"}},
    "record_notes": False,
    "wire_codes": {"outcomes": {"not_reached": "TRAIN_2_O6"},
                   "reasons": {"callback": "TRAIN_2_R10", "no_callback": "TRAIN_2_R11"}},
    "close_reasons": {"not_reached": ["callback", "no_callback"]},
    "identity_close": {scope: {"outcome": "not_reached", "reason": "no_callback"}
                       for scope in ("failed", "incomplete", "unreached")},
    "identity_close_choices": {scope: [
        {"outcome": "not_reached", "reason": "no_callback", "requires": "no_recorded_callback"},
        {"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"}]
        for scope in ("unreached", "incomplete")},
}


def original_callback(changes=None):
    api = next(bank["openapi"] for bank in read_json(ROOT / "fixtures/offline.json")["profiles"]
               if bank["profile_id"] == "train-2")
    assert digest(api) == "eb561508532884e4e8e7c1162e6e4cd4b11ffcefa60d4c4d88f1be512e143761"
    values = {**copy.deepcopy(ORIGINAL), **copy.deepcopy(changes or {})}
    sources = [{"id": "brief-1", "file": "original-callback-rules.txt"}]
    policy = {"version": 1, "goal": "Arrange a requested callback or end without one.",
              "allowed_actions": ["schedule_callback", "close_call"], "sources": [],
              "rules": [{"id": kind, "kind": kind, "value": value} for kind, value in values.items()],
              "unsupported_constraints": []}
    witness(policy, sources)
    return policy, sources, api


def labelled(actions, outcomes, close, verified, choices=None, reasons=None, **changes):
    """Invented goal whose extra close codes carry only source-defined labels."""
    policy, sources, api = synthetic(actions)
    rule(policy, "close_reasons")["value"].update(close)
    codes = rule(policy, "wire_codes")["value"]
    codes["outcomes"] = {**{value: value for value in rule(policy, "close_reasons")["value"] if value not in outcomes},
                         **outcomes}
    codes["reasons"].update(reasons or {})
    rule(policy, "identity_close")["value"]["verified"] = verified
    if choices is not None:
        add_rule(policy, "identity_close_choices", {"verified": choices})
    for kind, value in changes.items():
        rule(policy, kind)["value"] = value
    schema = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"]["outcome"]["enum"].extend(outcomes.values())
    schema["properties"]["reason"]["enum"].extend((reasons or {}).values())
    witness(policy, sources)
    return policy, sources, api


def identity_only(verified=None, choices=None):
    return labelled(["verify_identity", "close_call"], {"identity_confirmed": "ID_CONFIRMED"},
                    {"identity_confirmed": ["factors_matched"]},
                    verified or {"outcome": "identity_confirmed", "reason": "factors_matched"}, choices,
                    reasons={"factors_matched": "BOTH_MATCHED"})


INFORMATION_CHOICES = [
    {"outcome": "details_read", "requires": "account_loaded"},
    {"outcome": "read_stopped", "reason": "refused", "requires": "none"},
    {"outcome": "lookup_failed", "reason": "service_error", "requires": "no_account_loaded"}]


def information_only(choices=INFORMATION_CHOICES):
    return labelled(["verify_identity", "get_account", "close_call"],
                    {"details_read": "INFO_READ", "read_stopped": "INFO_STOPPED", "lookup_failed": "INFO_FAILED"},
                    {"details_read": [], "read_stopped": ["refused"], "lookup_failed": ["service_error"]},
                    {"outcome": "read_stopped", "reason": "refused"}, choices,
                    resolve_all_products=False, product_order={"mode": "account_order"})


class NoncollectionClosingTests(unittest.TestCase):
    def session(self, values):
        return SealedSession(*compiled(*values)[1:])

    def verify(self, session):
        for factor in ("postal_code", "memorable_word"):
            session.action("verify_identity", field=factor, answer="synthetic-correct")
            session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        self.assertEqual((session.state["identity_status"], session.state["wrong_answers"]), ("verified", 0))

    def assert_close(self, session, body):
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body, {"customer_id": "SYNTHETIC-GOAL-SESSION", **body})
        self.assertTrue(session.state["close_attempted"])

    def assert_refused(self, session):
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.variables["bg_body"])
        self.assertFalse(session.state["close_attempted"])

    def test_original_callback_vocabulary_named_by_meaning_binds_to_api_codes(self):
        bindings = compile_action_bindings(*original_callback()[::2])
        self.assertEqual(bindings["categories"]["outcomes"]["semantic_to_wire"], {"not_reached": "TRAIN_2_O6"})
        self.assertEqual(bindings["categories"]["reasons"]["semantic_to_wire"],
                         {"callback": "TRAIN_2_R10", "no_callback": "TRAIN_2_R11"})
        self.assertEqual(compile_execution_requirements(*original_callback())["actions"], ["close_call", "schedule_callback"])
        for close, error in (({"not_reached": ["callback"], "not_verified": []}, "does not cover policy outcomes"),
                             ({"not_reached": ["callback"], "TRAIN_2_O6": ["callback"]}, "name one bound code twice")):
            with self.subTest(close=close), self.assertRaisesRegex(ActionBindingError, error):
                compile_action_bindings(*original_callback({"close_reasons": close})[::2])

    def test_original_callback_rules_close_with_inherited_codes_from_protected_evidence(self):
        session = self.session(original_callback())
        session.action("schedule_callback", date="2031-05-05", time="12:00")
        self.assertEqual(session.variables["bg_route"], "allow")
        session.result("schedule_callback", {"accepted": "true"})
        self.assertEqual(len(session.state["callbacks"]), 1)
        session.action("close_call", outcome="not_reached", reason="no_callback")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body, {"session_ref": "SYNTHETIC-GOAL-SESSION", "result_code": "TRAIN_2_O6",
                                        "reason_code": "TRAIN_2_R10"})
        unbooked = self.session(original_callback())
        unbooked.action("close_call", outcome="TRAIN_2_O6", reason="TRAIN_2_R10")
        self.assert_refused(unbooked)
        unbooked = self.session(original_callback())
        unbooked.action("close_call", outcome="TRAIN_2_O6", reason="TRAIN_2_R11")
        self.assertEqual(unbooked.body["reason_code"], "TRAIN_2_R11")

    def test_s03_reproduction_verified_identity_closes_with_frozen_supplied_pair(self):
        # Without a sourced verified disposition the kernel still invents none.
        absent = self.session(synthetic(["verify_identity", "close_call"]))
        self.verify(absent)
        absent.action("close_call", outcome="promise", reason="refused")
        self.assert_refused(absent)
        session = self.session(identity_only())
        self.verify(session)
        session.action("close_call", outcome="promise", reason="refused")  # dialogue proposal is ignored
        self.assert_close(session, {"outcome": "ID_CONFIRMED", "reason": "BOTH_MATCHED"})
        self.assertFalse(session.state["account_loaded"])
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])
        session.action("close_call", outcome="identity_confirmed", reason="factors_matched")
        self.assertEqual(session.variables["bg_route"], "blocked")

    def test_verified_choices_refuse_undeclared_pairs_and_unverified_identity(self):
        choices = [{"outcome": "identity_confirmed", "reason": "factors_matched", "requires": "none"}]
        unverified = self.session(identity_only(choices=choices))
        unverified.action("close_call", outcome="identity_confirmed", reason="factors_matched")
        # Before verification only the sourced pre-verification row applies.
        self.assert_close(unverified, {"outcome": "not_verified", "reason": "identity_failed"})
        session = self.session(identity_only(choices=choices))
        self.verify(session)
        for outcome, reason in (("identity_confirmed", "refused"), ("not_verified", "identity_failed"), ("ID_CONFIRMED", "")):
            session.action("close_call", outcome=outcome, reason=reason)
            self.assert_refused(session)
        session.action("close_call", outcome="ID_CONFIRMED", reason="BOTH_MATCHED")
        self.assert_close(session, {"outcome": "ID_CONFIRMED", "reason": "BOTH_MATCHED"})

    def test_information_only_close_requires_loaded_account_and_cannot_hide_it(self):
        session = self.session(information_only())
        self.verify(session)
        session.action("close_call", outcome="details_read", reason="")
        self.assert_refused(session)
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        self.assertTrue(session.state["account_loaded"])
        session.action("close_call", outcome="lookup_failed", reason="service_error")
        self.assert_refused(session)
        session.action("close_call", outcome="details_read", reason="")
        self.assert_close(session, {"outcome": "INFO_READ"})
        failed = self.session(information_only())
        self.verify(failed)
        failed.action("get_account")
        failed.result("get_account", {"ok": False, "error": "unavailable"})
        failed.action("close_call", outcome="INFO_FAILED", reason="service_error")
        self.assert_close(failed, {"outcome": "INFO_FAILED", "reason": "service_error"})

    def test_payment_goals_cannot_use_verified_endings_and_still_need_the_recorded_write(self):
        policy, sources, api = synthetic(list(execution_action_catalogue()), narrow=False)
        rule(policy, "identity_close")["value"]["verified"] = {"outcome": "not_verified", "reason": "identity_failed"}
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "only to goals without commitment or disposition"):
            compile_execution_requirements(policy, sources, api)
        for verified, choices in (({"outcome": "promise"}, None),
                                  (None, [{"outcome": "paid", "requires": "none"}])):
            with self.subTest(verified=verified), self.assertRaisesRegex(
                    ExecutionRequirementsError, "cannot use a protected-fact outcome"):
                compile_execution_requirements(*identity_only(verified, choices))
        policy, _, api = identity_only()
        rule(policy, "close_reasons")["value"] = {"identity_confirmed": []}
        rule(policy, "wire_codes")["value"]["outcomes"] = {"identity_confirmed": "promise"}
        with self.assertRaisesRegex(ActionBindingError, "conflicting meanings"):
            compile_action_bindings(policy, api)
        policy, sources, api = identity_only()
        add_rule(policy, "identity_close_choices", {"incomplete": [
            {"outcome": "not_verified", "reason": "identity_failed", "requires": "account_loaded"}]})
        witness(policy, sources)
        with self.assertRaises(ExecutionRequirementsError):  # account evidence exists only after verification
            compile_execution_requirements(policy, sources, api)
        session = self.session(synthetic(list(execution_action_catalogue()), narrow=False))
        self.verify(session)
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        session.action("close_call", outcome="promise", reason="")
        self.assert_refused(session)
        session.action("stage_promise", product_id="GOAL-PRODUCT", amount="37.25", date="2031-05-06")
        session.action("commit_promises")
        session.variables["bg_consent"] = "yes"
        session.code("bg_" + session.entries["commit_promises"] + "_accept")
        session.result("commit_promises", {"ok": True, "promise_id": "GOAL-PROMISE"})
        session.action("close_call", outcome="not_verified", reason="identity_failed")
        self.assert_close(session, {"outcome": "promise"})


if __name__ == "__main__":
    unittest.main()

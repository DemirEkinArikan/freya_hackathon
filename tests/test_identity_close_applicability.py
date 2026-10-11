"""Invented source/API execution for optional identity closing applicability.

A silent technical terminal is not a recorded bank close or evidence that an
explicit source obligation to close every call has been satisfied.
"""
import copy
import unittest

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import (GuardCompileError, _profile, compile_guarded_workflow,
                            execution_action_catalogue, required_execution_kinds)
from builder.policy import PolicyError, compile_execution_policy
from builder.safety import digest
from test_execution_requirements import add_rule
from test_goal_requirements import (PRODUCT, SealedSession, compiled, rehash,
                                    remove, synthetic, witness)


FIXED = {"failed": {"outcome": "not_verified", "reason": "identity_failed"},
         "incomplete": {"outcome": "not_verified", "reason": "identity_failed"},
         "unreached": {"outcome": "not_reached"}}


def invented(actions, *, narrow=True, fixed=None):
    policy, sources, api = synthetic(actions, narrow=narrow)
    remove(policy, {"identity_close"})
    if fixed is not None:
        add_rule(policy, "identity_close", fixed)
    witness(policy, sources)
    return policy, sources, api


class IdentityCloseApplicabilityTests(unittest.TestCase):
    def session(self, values):
        requirements, context, workflow = compiled(*values)
        return requirements, context, SealedSession(context, workflow)

    def assert_silent_end(self, session, node, expected="bg_terminal"):
        self.assertEqual(session.target(node), expected)
        terminal = session.workflow["nodes"][expected]
        self.assertEqual(terminal["node_type"], "end_call")
        self.assertEqual(terminal["on_enter"], {"type": "silent"})
        self.assertEqual(terminal["transitions"], [])

    def assert_no_bank_close(self, session, before):
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.variables["bg_body"])
        self.assertFalse(session.state["close_attempted"])
        self.assertFalse(session.state["closed"])
        # A refused close keeps every record and ends at the silent quarantine stop with the flag.
        self.assertEqual(session.state, dict(before, quarantined=True))
        self.assert_silent_end(session, session.entries["close_call"], "bg_quarantine")

    def test_absent_table_is_not_invented_in_frozen_profile_or_requirements(self):
        for actions in (["close_call"], ["verify_identity", "close_call"],
                        ["stage_transfer", "commit_transfer", "close_call"]):
            with self.subTest(actions=actions):
                values = invented(actions)
                before = copy.deepcopy(values)
                requirements, context, _ = self.session(values)
                self.assertNotIn("identity_close", requirements["required_kinds"])
                self.assertNotIn("identity_close", required_execution_kinds(context["plan"]))
                self.assertNotIn("identity_close", context["execution_policy"]["profile"])
                runtime = _profile(context["contract"], context["execution_policy"], context["plan"], context["bindings"])
                self.assertEqual(runtime["identity_close"], {})
                runtime["identity_close"]["failed"] = {"outcome": "invented-value"}
                self.assertNotIn("identity_close", context["execution_policy"]["profile"])
                self.assertEqual(values, before)
                self.assertEqual(requirements["policy_sha256"], digest(values[0]))
                for kind in ("policy_applicability", "product_order", "resolve_all_products"):
                    self.assertNotIn(kind, context["execution_policy"]["profile"])

    def test_recorded_preidentity_transfer_closes_without_any_identity_table(self):
        _, _, session = self.session(invented(["stage_transfer", "commit_transfer", "close_call"]))
        session.action("stage_transfer", queue="helpdesk")
        session.action("commit_transfer")
        session.result("commit_transfer", {"ok": True})
        self.assertEqual(session.state["transfer"]["status"], "recorded")
        session.action("close_call", outcome="not_verified", reason="identity_failed")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("transferred", "dispute"))
        self.assertFalse(session.state["account_loaded"])
        self.assertNotEqual(session.state["identity_status"], "verified")
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])
        self.assert_silent_end(session, "bg_" + session.entries["close_call"] + "_result")

    def recorded_promise(self):
        _, context, session = self.session(invented(list(execution_action_catalogue()), narrow=False))
        for field in ("postal_code", "memorable_word"):
            session.action("verify_identity", field=field, answer="invented-answer")
            session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        session.action("stage_promise", product_id="GOAL-PRODUCT", amount="37.25", date="2031-05-06")
        session.action("commit_promises")
        self.assertEqual(session.variables["bg_route"], "confirm")
        session.variables["bg_consent"] = "yes"
        session.code("bg_" + session.entries["commit_promises"] + "_accept")
        session.result("commit_promises", {"ok": True, "promise_id": "INDEPENDENT-PROMISE"})
        return context, session

    def test_recorded_confirmed_promise_closes_without_identity_table(self):
        context, session = self.recorded_promise()
        self.assertNotIn("identity_close", context["execution_policy"]["profile"])
        ledger = copy.deepcopy(session.state["recorded_promises"])
        session.action("close_call", outcome="not_reached", reason="unclear")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["outcome"], "promise")
        self.assertNotIn("reason", session.body)
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])
        self.assertEqual(session.state["recorded_promises"], ledger)
        self.assert_silent_end(session, "bg_" + session.entries["close_call"] + "_result")

    def test_no_evidence_cannot_select_a_public_identity_outcome_without_source_table(self):
        for outcome, reason in (("not_reached", "unclear"), ("not_verified", "identity_failed"), ("promise", "")):
            with self.subTest(outcome=outcome):
                _, _, session = self.session(invented(["close_call"]))
                before = session.state
                session.action("close_call", outcome=outcome, reason=reason)
                self.assert_no_bank_close(session, before)
                self.assertTrue(session.state["quarantined"])  # no eligible close: the honest flag
                self.assertFalse(session.state["attempts"])

    def test_actual_identity_exhaustion_blocks_without_fabricating_bank_disposition(self):
        _, _, session = self.session(invented(["verify_identity", "close_call"]))
        for remaining in (3, 2, 1, 0):
            session.action("verify_identity", field="postal_code", answer="invented-wrong-answer")
            session.result("verify_identity", {"ok": True, "verified": False, "wrong_answers_left": remaining})
        self.assertEqual(session.state["identity_status"], "failed")
        self.assertEqual(session.state["wrong_answers"], 4)
        before = session.state
        session.action("close_call", outcome="not_verified", reason="identity_failed")
        self.assert_no_bank_close(session, before)
        self.assertFalse(session.state["account_loaded"])

    def test_ambiguous_unranked_recorded_facts_still_refuse_close_and_preserve_ledger(self):
        _, session = self.recorded_promise()
        session.action("stage_transfer", queue="helpdesk")
        session.action("commit_transfer")
        session.result("commit_transfer", {"ok": True})
        before = session.state
        self.assertTrue(before["recorded_promises"])
        self.assertEqual(before["transfer"]["status"], "recorded")
        session.action("close_call", outcome="transferred", reason="dispute")
        self.assert_no_bank_close(session, before)

    def test_active_fixed_table_is_frozen_and_still_operative(self):
        values = invented(["close_call"], fixed=FIXED)
        requirements, context, session = self.session(values)
        self.assertEqual(context["execution_policy"]["profile"]["identity_close"], FIXED)
        self.assertNotIn("identity_close", requirements["required_kinds"])
        session.action("close_call", outcome="not_reached", reason="")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["outcome"], "not_reached")
        for mode in ("omit", "change", "stale-hash"):
            changed = copy.deepcopy(context)
            if mode == "omit":
                del changed["execution_policy"]["profile"]["identity_close"]
            else:
                changed["execution_policy"]["profile"]["identity_close"]["unreached"] = {"outcome": "not_verified", "reason": "identity_failed"}
            if mode != "stale-hash":
                rehash(changed["execution_policy"])
            with self.subTest(mode=mode), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**changed)

    def test_explicit_empty_partial_or_malformed_table_is_not_absence(self):
        for table in ({}, {"failed": FIXED["failed"]}, [],
                      {**FIXED, "extra": FIXED["failed"]},
                      {**FIXED, "incomplete": {"outcome": False}},
                      {**FIXED, "unreached": {"outcome": "not_reached", "reason": ""}}):
            with self.subTest(table=table):
                policy, sources, api = invented(["close_call"], fixed=table)
                with self.assertRaises(PolicyError):
                    compile_execution_policy(policy)
                with self.assertRaises(ExecutionRequirementsError):
                    compile_execution_requirements(policy, sources, api)

    def test_choices_without_fixed_scope_still_reject_even_with_optional_table(self):
        policy, sources, api = invented(["close_call"])
        add_rule(policy, "identity_close_choices", {"unreached": [
            {"outcome": "not_reached", "requires": "none"}]})
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "outcome must match its fixed scope"):
            compile_execution_requirements(policy, sources, api)


if __name__ == "__main__":
    unittest.main()

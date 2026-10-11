"""Terminal closes for non-payment goals; invented codes, no model, bank or platform I/O."""
import json
import os
import unittest
from pathlib import Path

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.safety import digest, read_json
from test_execution_requirements import add_rule, packet, rule
import test_goal_requirements as goal_requirements
from test_goal_requirements import PRODUCT, synthetic, witness


def extend_enum(api, field, *codes):
    schema = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"][field]["enum"].extend(code for code in codes if code not in schema["properties"][field]["enum"])


def goal(actions, close, rows=None, **changes):
    policy, sources, api = synthetic(actions)
    rule(policy, "close_reasons")["value"].update(close)
    for outcome, reasons in close.items():
        extend_enum(api, "outcome", outcome)
        extend_enum(api, "reason", *reasons)
    for kind, value in changes.items():
        rule(policy, kind)["value"] = value
    if rows is not None:
        add_rule(policy, "goal_close", rows)
    witness(policy, sources)
    return policy, sources, api


class GoalTerminalCloseTests(unittest.TestCase):
    session = goal_requirements.GoalRequirementsTests.session
    assert_silent_terminal = goal_requirements.GoalRequirementsTests.assert_silent_terminal

    def verify(self, session):
        for factor in ("postal_code", "memorable_word"):
            session.action("verify_identity", field=factor, answer="synthetic-correct")
            session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        self.assertEqual(session.state["identity_status"], "verified")

    def assert_refused(self, session):
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.variables["bg_body"])
        self.assertFalse(session.state["close_attempted"])

    def test_identity_only_success_without_sourced_row_still_refuses_close(self):
        # S03 reproduction on 4ef4e627/3f2399d: verified identity, no account and
        # no collections fact reach 'closing lacks protected evidence'.
        _, _, session = self.session(synthetic(["verify_identity", "close_call"]))
        self.verify(session)
        session.action("close_call", outcome="promise", reason="refused")
        self.assert_refused(session)
        self.assertFalse(session.state["account_loaded"])

    def test_identity_only_success_closes_once_with_the_sourced_pair(self):
        rows = {"verified": [{"outcome": "GOAL_DONE", "reason": "GOAL_MATCH", "requires": "none"}]}
        _, _, session = self.session(goal(["verify_identity", "close_call"], {"GOAL_DONE": ["GOAL_MATCH"]}, rows))
        self.verify(session)
        session.action("close_call", outcome="promise", reason="refused")  # dialogue cannot pick the pair
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["outcome"], "GOAL_DONE")
        self.assertEqual(session.body["reason"], "GOAL_MATCH")
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])
        self.assert_silent_terminal(session, "bg_" + session.entries["close_call"] + "_result")
        session.action("close_call", outcome="GOAL_DONE", reason="GOAL_MATCH")
        self.assertEqual(session.variables["bg_route"], "blocked")

    def test_verified_row_is_unreachable_before_identity_success_or_after_failure(self):
        rows = {"verified": [{"outcome": "GOAL_DONE", "reason": "GOAL_MATCH", "requires": "none"}]}
        values = goal(["verify_identity", "close_call"], {"GOAL_DONE": ["GOAL_MATCH"]}, rows)
        _, _, session = self.session(values)
        session.action("close_call", outcome="GOAL_DONE", reason="GOAL_MATCH")
        self.assertEqual(session.body["outcome"], "not_verified")  # existing pre-identity table, never the goal row
        _, _, session = self.session(values)
        session.action("verify_identity", field="postal_code", answer="synthetic-wrong")
        session.result("verify_identity", {"ok": True, "verified": False, "wrong_answers_left": 0})
        session.action("close_call", outcome="GOAL_DONE", reason="GOAL_MATCH")
        self.assertEqual(session.body["outcome"], "not_verified")

    def information_values(self):
        rows = {"verified": [{"outcome": "INFO_PART", "reason": "refused", "requires": "none"}],
                "account_loaded": [{"outcome": "INFO_DONE", "requires": "none"},
                                   {"outcome": "INFO_PART", "reason": "refused", "requires": "none"}]}
        return goal(["verify_identity", "get_account", "close_call"],
                    {"INFO_DONE": [], "INFO_PART": ["refused"]}, rows, resolve_all_products=False)

    def test_information_only_close_after_account_read_uses_a_declared_choice(self):
        _, _, session = self.session(self.information_values())
        self.verify(session)
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        self.assertTrue(session.state["account_loaded"])
        session.action("close_call", outcome="not_reached", reason="")
        self.assert_refused(session)
        session.action("close_call", outcome="INFO_DONE", reason="")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["outcome"], "INFO_DONE")
        self.assertNotIn("reason", session.body)
        session.result("close_call", {"ok": "uncertain"})
        self.assertFalse(session.state["closed"])
        self.assertTrue(session.state["quarantined"])
        session.action("close_call", outcome="INFO_PART", reason="refused")
        self.assertEqual(session.variables["bg_route"], "blocked")

    def test_information_goal_stopping_before_account_read_uses_verified_row(self):
        _, _, session = self.session(self.information_values())
        self.verify(session)
        session.action("close_call", outcome="INFO_DONE", reason="")
        self.assertEqual(session.body["outcome"], "INFO_PART")
        self.assertEqual(session.body["reason"], "refused")

    def callback_semantic_keys(self, *, wire_keyed=False):
        # Shape of the three rejected extractions of run 20261011T063051905603Z
        # (policy.rejected-1.json sha256 5d0bdb24...c58183): wire_codes bind the
        # sourced meanings to opaque codes; close vocabularies cite the keys.
        policy, sources, api = synthetic(["schedule_callback", "close_call"])
        codes = {"not_reached": "CB_END", "callback": "CB_YES", "no_callback": "CB_NO"}
        close = {"not_reached": ["callback", "no_callback"]}
        if wire_keyed:
            close = {codes[k]: [codes[r] for r in v] for k, v in close.items()}
        rule(policy, "close_reasons")["value"] = close
        rule(policy, "wire_codes")["value"] = {"outcomes": {"not_reached": "CB_END"},
                                              "reasons": {"callback": "CB_YES", "no_callback": "CB_NO"}}
        rule(policy, "identity_close")["value"] = {scope: {"outcome": "not_reached", "reason": "no_callback"}
                                                   for scope in ("failed", "incomplete", "unreached")}
        add_rule(policy, "identity_close_choices", {scope: [
            {"outcome": "not_reached", "reason": "no_callback", "requires": "no_recorded_callback"},
            {"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"}]
            for scope in ("unreached", "incomplete")})
        add_rule(policy, "confirmation_actions", ["schedule_callback"])
        schema = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        schema["properties"]["outcome"]["enum"] = ["CB_END"]
        schema["properties"]["reason"]["enum"] = ["CB_YES", "CB_NO"]
        witness(policy, sources)
        return policy, sources, api

    def test_callback_close_vocabulary_may_cite_declared_semantic_keys(self):
        for wire_keyed in (False, True):
            with self.subTest(wire_keyed=wire_keyed):
                _, _, session = self.session(self.callback_semantic_keys(wire_keyed=wire_keyed))
                session.action("schedule_callback", date="2031-05-06", time="12:00")
                self.assertEqual(session.variables["bg_route"], "confirm")
                session.variables["bg_consent"] = "yes"
                session.code("bg_" + session.entries["schedule_callback"] + "_accept")
                session.result("schedule_callback", {"ok": True})
                session.action("close_call", outcome="CB_END", reason="CB_NO")
                self.assertEqual(session.body["outcome"], "CB_END")
                self.assertEqual(session.body["reason"], "CB_YES")  # protected record, not the claim

    def test_undeclared_close_codes_remain_rejected_with_an_actionable_message(self):
        policy, sources, api = self.callback_semantic_keys()
        rule(policy, "close_reasons")["value"]["no_promise"] = []  # canonical name, neither key nor wire
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "policy outcomes vocabulary has a code"):
            compile_execution_requirements(policy, sources, api)
        policy, sources, api = self.callback_semantic_keys()
        rule(policy, "close_reasons")["value"]["CB_END"] = ["CB_NO"]  # same outcome by key and by code
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "cites one outcome code twice"):
            compile_execution_requirements(policy, sources, api)
        policy, sources, api = self.callback_semantic_keys()
        rule(policy, "wire_codes")["value"]["outcomes"] = {"invented_meaning": "CB_END"}  # not a builder meaning
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "wire_codes.outcomes keys must be builder meanings"):
            compile_execution_requirements(policy, sources, api)

    def test_callback_goal_cannot_leave_close_codes_unbound(self):
        # The same codes compile in an identity-only goal. With schedule_callback every
        # close code needs a builder meaning, so callback evidence checks always apply.
        for category, close in (("outcomes", {"GOAL_DONE": []}),
                                ("reasons", {"not_verified": ["identity_failed", "GOAL_MATCH"]})):
            with self.subTest(category=category):
                compile_execution_requirements(*goal(["verify_identity", "close_call"], close))
                with self.assertRaisesRegex(ExecutionRequirementsError, "policy " + category + " vocabulary has a code"):
                    compile_execution_requirements(*goal(["verify_identity", "schedule_callback", "close_call"], close))
        with self.assertRaisesRegex(ExecutionRequirementsError, "policy reasons vocabulary has a code"):
            compile_execution_requirements(*goal(["verify_identity", "close_call"], {"GOAL_DONE": ["paid"]}))

    def test_recorded_callback_closes_only_through_a_recorded_callback_row(self):
        policy, sources, api = goal(["verify_identity", "schedule_callback", "close_call"], {"not_reached": ["refused"]})
        rule(policy, "identity_close")["value"]["unreached"]["reason"] = "refused"
        add_rule(policy, "goal_close", {"verified": [{"outcome": "not_reached", "reason": "refused", "requires": "none"}]})
        witness(policy, sources)
        for booked in (False, True):
            with self.subTest(booked=booked):
                _, _, session = self.session((policy, sources, api))
                self.verify(session)
                if booked:
                    session.action("schedule_callback", date="2031-05-06", time="12:00")
                    session.result("schedule_callback", {"ok": True})
                    self.assertEqual(len(session.state["callbacks"]), 1)
                session.action("close_call", outcome="not_reached", reason="refused")
                if booked:
                    self.assert_refused(session)  # a 'none' row cannot hide the recorded callback
                else:
                    self.assertEqual((session.body["outcome"], session.body["reason"]), ("not_reached", "refused"))

    def test_payment_goal_cannot_leave_close_codes_unbound(self):
        policy, sources, api = packet(local=True)
        rule(policy, "close_reasons")["value"]["GOAL_DONE"] = []
        extend_enum(api, "outcome", "GOAL_DONE")
        with self.assertRaisesRegex(ExecutionRequirementsError, "policy outcomes vocabulary has a code"):
            compile_execution_requirements(policy, sources, api)

    def test_goal_rows_are_refused_with_payment_roles_or_collection_meanings(self):
        policy, sources, api = packet(local=True)
        add_rule(policy, "goal_close", {"account_loaded": [{"outcome": "not_reached", "requires": "none"}]})
        with self.assertRaisesRegex(ExecutionRequirementsError, "goal_close cannot be combined"):
            compile_execution_requirements(policy, sources, api)
        for outcome in ("promise", "paid"):
            with self.subTest(outcome=outcome):
                values = goal(["verify_identity", "close_call"], {},
                              {"verified": [{"outcome": outcome, "requires": "none"}]},
                              close_reasons={"promise": [], "paid": [], "not_verified": ["identity_failed"], "not_reached": []})
                with self.assertRaisesRegex(ExecutionRequirementsError, "non-collection outcomes"):
                    compile_execution_requirements(*values)
        values = goal(["verify_identity", "close_call"], {"GOAL_DONE": []},
                      {"verified": [{"outcome": "GOAL_DONE", "requires": "none"},
                                    {"outcome": "GOAL_DONE", "requires": "no_recorded_callback"}]})
        with self.assertRaisesRegex(ExecutionRequirementsError, "goal closing rows are duplicated"):
            compile_execution_requirements(*values)

    def test_payment_close_still_requires_the_confirmed_recorded_promise(self):
        values = synthetic(["verify_identity", "get_account", "stage_promise", "commit_promises", "close_call"])
        _, _, session = self.session(values)
        self.verify(session)
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        session.action("stage_promise", product_id="GOAL-PRODUCT", amount="37.25", date="2031-05-06")
        session.action("close_call", outcome="promise", reason="")
        self.assert_refused(session)  # staged but unrecorded promise
        session.action("commit_promises")
        session.variables["bg_consent"] = "yes"
        session.code("bg_" + session.entries["commit_promises"] + "_accept")
        session.result("commit_promises", {"ok": True})  # no persisted-record identity
        self.assertTrue(session.state["quarantined"])
        session.action("close_call", outcome="promise", reason="")
        self.assert_refused(session)


@unittest.skipUnless(os.environ.get("FREYA_CALLBACK_FAILURE_RUN"), "original ignored run artifacts are local only")
class OriginalCallbackFailureReplay(unittest.TestCase):
    """Read-only replay of the failed run's own rejected extractions; no model call."""

    def test_rejected_extractions_now_compile_except_the_declared_unsupported_one(self):
        run = Path(os.environ["FREYA_CALLBACK_FAILURE_RUN"])
        inputs = Path(os.environ["FREYA_CALLBACK_INPUTS"])
        text = (inputs / "callback-policy.approved.md").read_text(encoding="utf-8")
        sources = [{"id": "brief-1", "file": "callback-policy.approved.md", "sha256": digest(text), "text": text}]
        api = read_json(inputs / "bank.openapi.json")
        for attempt in (1, 2, 3):
            policy = json.loads((run / f"policy.rejected-{attempt}.json").read_text(encoding="utf-8"))["policy"]
            with self.subTest(attempt=attempt):
                if attempt == 2:
                    with self.assertRaisesRegex(ExecutionRequirementsError, "unsupported constraints"):
                        compile_execution_requirements(policy, sources, api)
                else:
                    self.assertEqual(compile_execution_requirements(policy, sources, api)["actions"],
                                     ["close_call", "schedule_callback"])


if __name__ == "__main__":
    unittest.main()

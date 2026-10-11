"""A protected closing outcome needs its recorded fact; invented codes, no model, bank or platform I/O."""
import unittest

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import run_guard_step
from test_execution_requirements import add_rule, rule
import test_goal_requirements as goal_requirements
from test_goal_requirements import PRODUCT, synthetic, witness
from test_goal_terminal_close import goal
from test_recorded_callback_close import context

REFUSED = "identity closing rows cannot use a protected-fact outcome"
LACKS = "protected closing outcome lacks its recorded fact"
REASON = {"promise": None, "paid": None, "transferred": "hardship", "no_promise": "refused"}
CODES = {"promise": "X_PLAN", "paid": "X_CLEAR", "transferred": "X_HAND", "no_promise": "X_NONE",
         "not_verified": "X_ID", "not_reached": "X_REACH"}


def enum(api, field):
    return api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"][field]["enum"]


def identity_row(scope, outcome, *, bound=False, actions=("verify_identity", "close_call")):
    policy, sources, api = synthetic(list(actions))
    row = {"outcome": CODES[outcome] if bound else outcome}
    if REASON[outcome]:
        row["reason"] = REASON[outcome]
    if bound:  # the protected meaning is carried by an opaque API code
        rule(policy, "wire_codes")["value"]["outcomes"] = dict(CODES)
        enum(api, "outcome")[:] = list(CODES.values())
    rule(policy, "identity_close")["value"][scope] = row
    witness(policy, sources)
    return policy, sources, api


class CompiledProtectedOutcomeTests(unittest.TestCase):
    session = goal_requirements.GoalRequirementsTests.session

    def verify(self, session):
        for factor in ("postal_code", "memorable_word"):
            session.action("verify_identity", field=factor, answer="synthetic-correct")
            session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})

    def test_identity_rows_cannot_use_protected_outcomes_in_any_scope(self):
        # Hedge review A4: such a row compiled and closed with 0 recorded facts.
        for scope in ("failed", "incomplete", "unreached"):
            for outcome in REASON:
                for bound in (False, True):
                    with self.subTest(scope=scope, outcome=outcome, bound=bound):
                        with self.assertRaisesRegex(ExecutionRequirementsError, REFUSED):
                            compile_execution_requirements(*identity_row(scope, outcome, bound=bound))

    def test_original_probe_close_only_goal_no_longer_closes_as_promise(self):
        with self.assertRaisesRegex(ExecutionRequirementsError, REFUSED):
            self.session(identity_row("unreached", "promise", actions=("close_call",)))

    def test_identity_choice_rows_cannot_use_protected_outcomes(self):
        policy, sources, api = synthetic(["verify_identity", "close_call"])
        add_rule(policy, "identity_close_choices", {"unreached": [{"outcome": "not_reached", "requires": "none"},
                                                                  {"outcome": "promise", "requires": "none"}]})
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, REFUSED):
            compile_execution_requirements(policy, sources, api)

    def test_hedge_design_shapes_stay_refused_on_main(self):
        # The unmerged hedge's verified identity scope and free labels never compile.
        for change, message in (
                (lambda p: rule(p, "identity_close")["value"].update(verified={"outcome": "not_reached"}),
                 "identity_close has an invalid structured value"),
                (lambda p: rule(p, "wire_codes")["value"].update(outcomes={"promise_to_pay": "promise"}),
                 "keys must be builder meanings"),
                (lambda p: rule(p, "wire_codes")["value"].update(outcomes={"not_reached": "not_verified", "not_verified": "X_ID"}),
                 "conflicting meanings")):
            with self.subTest(message=message):
                policy, sources, api = synthetic(["verify_identity", "close_call"])
                change(policy)
                witness(policy, sources)
                with self.assertRaisesRegex(ExecutionRequirementsError, message):
                    compile_execution_requirements(policy, sources, api)
        policy, sources, api = goal(["verify_identity", "close_call"], {"callback": []})
        with self.assertRaisesRegex(ExecutionRequirementsError, "policy outcomes vocabulary has a code"):
            compile_execution_requirements(policy, sources, api)  # unbound code named like a builder meaning

    def test_legitimate_closes_still_send_their_rows_and_recorded_facts(self):
        rows = {"verified": [{"outcome": "GOAL_DONE", "reason": "GOAL_MATCH", "requires": "none"}]}
        _, _, session = self.session(goal(["verify_identity", "close_call"], {"GOAL_DONE": ["GOAL_MATCH"]}, rows))
        self.verify(session)
        session.action("close_call", outcome="", reason="")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("GOAL_DONE", "GOAL_MATCH"))

        rows = {"account_loaded": [{"outcome": "INFO_DONE", "requires": "none"}]}
        _, _, session = self.session(goal(["verify_identity", "get_account", "close_call"], {"INFO_DONE": []}, rows,
                                          resolve_all_products=False))
        self.verify(session)
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        session.action("close_call", outcome="", reason="")
        self.assertEqual(session.body["outcome"], "INFO_DONE")

        _, _, session = self.session(goal_requirements.GoalRequirementsTests.callback_values(self))
        session.action("schedule_callback", date="2031-05-06", time="12:00")
        session.result("schedule_callback", {"ok": True})
        session.action("close_call", outcome="not_reached", reason="callback")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("not_reached", "callback"))

        _, _, session = self.session(synthetic(["stage_transfer", "commit_transfer", "close_call"]))
        session.action("stage_transfer", queue="helpdesk")
        session.action("commit_transfer")
        session.result("commit_transfer", {"ok": True})
        session.action("close_call", outcome="", reason="")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("transferred", "dispute"))

        _, _, session = self.session(synthetic(["verify_identity", "get_account", "stage_promise", "commit_promises", "close_call"]))
        self.verify(session)
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        session.action("stage_promise", product_id="GOAL-PRODUCT", amount="37.25", date="2031-05-06")
        session.action("commit_promises")
        session.variables["bg_consent"] = "yes"
        session.code("bg_" + session.entries["commit_promises"] + "_accept")
        session.result("commit_promises", {"ok": True, "promise_id": "P-1"})
        session.action("close_call", outcome="", reason="")
        self.assertEqual(session.body["outcome"], "promise")
        self.assertEqual(len(session.state["recorded_promises"]), 1)


class KernelProtectedOutcomeTests(unittest.TestCase):
    """The sealed kernel re-checks every close, even for a profile that bypassed _profile."""

    def setUp(self):
        self.state, self.profile, self.catalog, self.maps = context()

    def close(self, proposal=None):
        return run_guard_step("prepare", self.state, "close_call", proposal or {},
                              profile=self.profile, catalog=self.catalog, maps=self.maps)

    def test_identity_and_choice_rows_cannot_send_a_protected_outcome(self):
        del self.profile["identity_close_choices"]
        self.profile["identity_close"]["unreached"] = {"outcome": "promise"}
        with self.assertRaisesRegex(ValueError, LACKS):
            self.close({"outcome": "Z_PLAN"})
        self.profile["identity_close"]["failed"] = {"outcome": "transferred", "reason": "human"}
        self.state["identity_status"] = "failed"
        with self.assertRaisesRegex(ValueError, LACKS):
            self.close()
        self.state, self.profile, self.catalog, self.maps = context()
        self.profile["identity_close"]["unreached"] = {"outcome": "no_promise", "reason": "refused"}
        self.profile["identity_close_choices"]["unreached"] = [{"outcome": "no_promise", "reason": "refused", "requires": "none"}]
        with self.assertRaisesRegex(ValueError, LACKS):
            self.close({"outcome": "Z_NONE", "reason": "Q_REFUSED"})

    def test_goal_rows_cannot_send_a_protected_outcome(self):
        # Hedge review A1: only a compile-time word check stood before the kernel.
        self.state.update(identity_status="verified", verified_fields=["member_factor"])
        self.profile["goal_close"] = {"verified": [{"outcome": "paid", "requires": "none"}],
                                      "account_loaded": [{"outcome": "transferred", "reason": "human", "requires": "none"}]}
        with self.assertRaisesRegex(ValueError, LACKS):
            self.close({"outcome": "Z_CLEAR"})
        self.state["account_loaded"] = True
        with self.assertRaisesRegex(ValueError, LACKS):
            self.close({"outcome": "Z_HAND", "reason": "Q_HELP"})

    def test_recorded_facts_still_close_with_their_protected_outcome(self):
        for updates, wire in (({"transfer": {"status": "recorded", "reason": "human"}}, ("Z_HAND", "Q_HELP")),
                              ({"recorded_promises": {"P-1": {"promise_id": "R-1"}}}, ("Z_PLAN", None))):
            with self.subTest(wire=wire):
                self.state, self.profile, self.catalog, self.maps = context()
                self.state.update(updates)
                _, route, body, _ = self.close()
                self.assertEqual((route, body["result_wire"], body.get("cause_wire")), ("allow",) + wire)


if __name__ == "__main__":
    unittest.main()

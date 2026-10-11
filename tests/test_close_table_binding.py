"""A goal's own closing table binds in every goal (the stress-dev-3 callback shape: booked,
declined and failed outcome codes), while a booking code still needs a recorded callback.

Invented codes only; no model, network, bank or platform I/O.
"""
import unittest

from builder.action_bindings import compile_action_bindings
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from test_execution_requirements import rule
from test_goal_requirements import witness
from test_goal_terminal_close import GoalTerminalCloseTests, goal

UNBOUND = "a goal with schedule_callback leaves close codes unbound only when"


def callback_table(booked="recorded_callback", bind=True):
    close = {"booked": [], "declined": ["refused_callback"], "failed": ["system_error"]}
    rows = [{"outcome": "booked", "requires": booked},
            {"outcome": "declined", "reason": "refused_callback", "requires": "none"},
            {"outcome": "failed", "reason": "system_error", "requires": "none"}]
    policy, sources, api = goal(["verify_identity", "schedule_callback", "close_call"], close, {"verified": rows})
    rule(policy, "close_reasons")["value"] = dict(close, not_verified=["identity_failed"], not_reached=[])
    outcomes = {"not_verified": "not_verified", "not_reached": "not_reached"}
    rule(policy, "wire_codes")["value"]["outcomes"] = dict(outcomes, callback="booked") if bind else outcomes
    witness(policy, sources)
    return policy, sources, api


class CloseTableBindingTests(unittest.TestCase):
    session = GoalTerminalCloseTests.session
    verify = GoalTerminalCloseTests.verify
    assert_refused = GoalTerminalCloseTests.assert_refused

    def test_goal_codes_bind_and_close_only_through_their_rows(self):
        policy, sources, api = callback_table()
        compile_execution_requirements(policy, sources, api)
        outcomes = compile_action_bindings(policy, api)["categories"]["outcomes"]
        self.assertEqual(outcomes["semantic_to_wire"]["callback"], "booked")
        self.assertEqual(outcomes["unbound_codes"], ["declined", "failed"])  # reason meaning names, not outcome ones
        _, _, session = self.session((policy, sources, api))
        self.verify(session)
        session.action("close_call", outcome="booked", reason="")
        self.assert_refused(session)  # no recorded callback: the booking code is never sent
        session.action("close_call", outcome="declined", reason="refused_callback")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("declined", "refused_callback"))
        _, _, session = self.session((policy, sources, api))
        self.verify(session)
        session.action("schedule_callback", date="2031-05-06", time="12:00")
        session.result("schedule_callback", {"ok": True})
        session.action("close_call", outcome="declined", reason="refused_callback")
        self.assertEqual(session.body["outcome"], "booked")  # the recorded callback, not the claim
        self.assertFalse(session.body.get("reason"))

    def test_a_booking_code_still_needs_recorded_callback_evidence(self):
        with self.assertRaisesRegex(ExecutionRequirementsError, "callback reasons require matching callback evidence"):
            compile_execution_requirements(*callback_table(booked="none"))
        policy, sources, api = callback_table()
        rule(policy, "identity_close")["value"]["unreached"] = {"outcome": "booked"}  # a fixed row claiming a booking
        witness(policy, sources)
        _, _, session = self.session((policy, sources, api))
        session.action("close_call", outcome="booked", reason="")
        self.assert_refused(session)  # the kernel refuses the callback outcome without its record
        for code in ("No-Promise", "paid"):  # spelled like a protected outcome: never an unbound code
            policy, sources, api = callback_table()
            rule(policy, "close_reasons")["value"][code] = []
            witness(policy, sources)
            with self.subTest(code=code), self.assertRaisesRegex(ExecutionRequirementsError, "policy outcomes vocabulary has a code"):
                compile_execution_requirements(policy, sources, api)

    def test_unbound_booking_code_is_refused_in_strict_mode_and_recorded_in_relaxed_mode(self):
        policy, sources, api = callback_table(bind=False)
        with self.assertRaisesRegex(ExecutionRequirementsError, UNBOUND):
            compile_execution_requirements(policy, sources, api)
        binding = compile_action_bindings(policy, api, declarations="relaxed")
        self.assertEqual(binding["declaration_mode"], "relaxed")
        self.assertEqual(binding["categories"]["outcomes"]["unbound_codes"], ["booked", "declined", "failed"])


if __name__ == "__main__":
    unittest.main()

"""A goal's own closing table binds in every goal (the stress-dev-3 callback shape: booked,
declined and failed outcome codes), while a booking code still needs a recorded callback.

Invented codes only; no model, network, bank or platform I/O.
"""
import json
import unittest
from unittest.mock import patch

import test_builder
from builder.action_bindings import compile_action_bindings, unguarded_close_codes
from builder.build import PROJECT, execute
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import _catalogue, _profile, run_guard_step
from test_execution_requirements import rule
from test_goal_requirements import compiled, witness
from test_goal_terminal_close import GoalTerminalCloseTests, extend_enum, goal

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
        _, _, session = self.session((policy, sources, api))
        self.verify(session)
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
        with self.assertRaisesRegex(ExecutionRequirementsError, "identity_close outcomes cannot be bound to callback"):
            compile_execution_requirements(policy, sources, api)
        # Past the compile checks, the kernel itself refuses the callback outcome without its record.
        _, context, _ = compiled(*callback_table())
        profile = _profile(context["contract"], context["execution_policy"], context["plan"], context["bindings"])
        catalog = _catalogue(context["contract"], context["openapi"], context["bindings"])
        state = run_guard_step("init", None, profile=profile, catalog=catalog, customer_id="invented", now="2031-05-02T10:00:00")[0]
        state["identity_status"], profile["identity_close"]["failed"] = "failed", {"outcome": "callback"}
        with self.assertRaisesRegex(ValueError, "callback-success closing requires a recorded callback"):
            run_guard_step("prepare", state, "close_call", {}, profile=profile, catalog=catalog, maps=context["bindings"]["categories"])
        # Spelled like a protected outcome, or named like a handoff: never an unbound code.
        for code in ("No-Promise", "paid", "human", "general"):
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

    def test_strict_unbound_codes_beyond_identity_only_goals_are_listed_as_unguarded(self):
        # A second booking code left unbound compiles, as any unbound code does, but is listed;
        # its row's requires is what the kernel checks.
        policy, sources, api = callback_table()
        rule(policy, "close_reasons")["value"]["booked_sms"] = []
        extend_enum(api, "outcome", "booked_sms")
        rule(policy, "goal_close")["value"]["verified"].append({"outcome": "booked_sms", "requires": "recorded_callback"})
        witness(policy, sources)
        binding = compile_action_bindings(policy, api)
        self.assertNotIn("declaration_mode", binding)
        self.assertEqual(unguarded_close_codes(policy, binding)["outcomes"], ["booked_sms", "declined", "failed"])
        _, _, session = self.session((policy, sources, api))
        self.verify(session)
        session.action("close_call", outcome="booked_sms", reason="")
        self.assert_refused(session)  # no recorded callback
        values = goal(["verify_identity", "close_call"], {"GOAL_DONE": []})
        self.assertEqual(unguarded_close_codes(values[0], compile_action_bindings(values[0], values[2])), {})


class UnguardedBuildRecordTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args

    def generator(self):
        generator = test_builder.BuilderTests.generator(self)
        generator.generate.return_value = test_builder.fixture()
        return generator

    def test_a_strict_export_lists_its_unguarded_close_codes(self):
        listed = {"reasons": ["SYS_DOWN"]}
        with patch("builder.build.unguarded_close_codes", return_value=listed):
            manifest = execute(self.args(), generator=self.generator(), client_factory=lambda **kw: test_builder.FakeClient())
        self.assertEqual((manifest["status"], manifest["unguarded_close_codes"]), ("exported", listed))
        self.assertNotIn("declaration_mode", manifest)
        events = [json.loads(line) for line in (PROJECT / manifest["run_directory"] / "trace.jsonl").read_text().splitlines()]
        self.assertEqual([event["unguarded_close_codes"] for event in events if event["event"] == "unguarded_close_codes"], [listed])
        manifest = execute(self.args(), generator=self.generator(), client_factory=lambda **kw: test_builder.FakeClient())
        self.assertNotIn("unguarded_close_codes", manifest)  # an identity-only goal lists none


if __name__ == "__main__":
    unittest.main()

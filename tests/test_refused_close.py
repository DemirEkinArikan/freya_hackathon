"""A close the kernel refuses still ends correctly; invented data, no model, bank or platform I/O.

Without a recorded callback, a callback claim closes through the one row that requires that
absence. Any other refused close sets the quarantine flag and ends at the quarantine stop,
never at the unflagged silent terminal. An env-gated replay covers the retained public-A plan.
"""
import os
import unittest
from pathlib import Path

from builder.guards import compile_guarded_workflow, run_guard_step
from builder.safety import read_json
from test_closing_choices import CHOICES, context as closing_context
from test_goal_requirements import SealedSession
from test_recorded_callback_close import context as kernel_context
from tools.replay_guarded import replay

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_A_RUN = os.environ.get("FREYA_PUBLIC_A_RUN")  # retained run 20261011T113924574303Z
CLAIM = {"outcome": "Z_REACH", "reason": "Q_BOOKED"}  # callback, with no callback recorded
NO_CALLBACK = {"outcome": "not_reached", "reason": "no_callback", "requires": "no_recorded_callback"}


def close(state, profile, catalog, maps, proposal):
    return run_guard_step("prepare", state, "close_call", proposal, profile=profile, catalog=catalog, maps=maps)


class CallbackClaimTests(unittest.TestCase):
    def test_identity_claim_closes_through_the_single_no_callback_row(self):
        state, profile, catalog, maps = kernel_context()
        new_state, route, body, _ = close(state, profile, catalog, maps, CLAIM)
        self.assertEqual((route, body["result_wire"], body["cause_wire"]), ("allow", "Z_REACH", "Q_UNBOOKED"))
        self.assertEqual(new_state["close"], {"outcome": "not_reached", "reason": "no_callback"})
        self.assertEqual((new_state["callbacks"], new_state["close_attempted"]), ([], True))

    def test_identity_claim_stays_refused_without_exactly_one_no_callback_row(self):
        for extra in ([], [dict(NO_CALLBACK, reason="unclear")]):
            state, profile, catalog, maps = kernel_context()
            rows = profile["identity_close_choices"]["unreached"]
            rows[:] = [rows[0], rows[2]] + ([rows[1]] + extra if extra else [])
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "callback evidence"):
                close(state, profile, catalog, maps, CLAIM)

    def test_goal_claim_closes_through_the_phase_no_callback_row(self):
        state, profile, catalog, maps = kernel_context()
        state["identity_status"] = "verified"
        callback, unclear = ({"outcome": "not_reached", "reason": reason, "requires": requires}
                             for reason, requires in (("callback", "recorded_callback"), ("unclear", "none")))
        declined = {"outcome": "not_verified", "reason": "declined", "requires": "none"}
        profile["goal_close"] = {"verified": [callback, NO_CALLBACK, unclear]}
        self.assertEqual(close(state, profile, catalog, maps, CLAIM)[2]["cause_wire"], "Q_UNBOOKED")
        self.assertEqual(close(state, profile, catalog, maps, {"outcome": "Z_REACH", "reason": "Q_UNKNOWN"})[2]["cause_wire"], "Q_UNKNOWN")
        with self.assertRaisesRegex(ValueError, "one declared eligible choice"):  # names no row: no fallback
            close(state, profile, catalog, maps, {"outcome": "Z_REACH", "reason": "Q_STOP"})
        profile["goal_close"] = {"verified": [callback, unclear, declined]}
        with self.assertRaisesRegex(ValueError, "one declared eligible choice"):
            close(state, profile, catalog, maps, CLAIM)

    def test_a_protected_outcome_still_needs_its_recorded_fact(self):
        state, profile, catalog, maps = kernel_context()
        profile["identity_close_choices"]["unreached"][1] = {"outcome": "promise", "requires": "no_recorded_callback"}
        with self.assertRaisesRegex(ValueError, "protected closing outcome lacks its recorded fact"):
            close(state, profile, catalog, maps, CLAIM)


class CompiledRefusedCloseTests(unittest.TestCase):
    def session(self, choices=CHOICES):
        context = closing_context(choices)[0]
        return SealedSession(context, compile_guarded_workflow(**context)["workflow"])

    def test_refused_proposal_is_flagged_and_ends_at_the_quarantine_stop(self):
        # An absent proposal is refused only where the phase's fixed row (unclear) is no eligible choice.
        missing = dict(CHOICES, unreached=[row for row in CHOICES["unreached"] if row["reason"] != "unclear"])
        for choices, proposal in ((CHOICES, {"outcome": "not_reached", "reason": "declined"}), (missing, {"outcome": "", "reason": ""})):
            with self.subTest(proposal=proposal):
                s = self.session(choices)
                s.action("close_call", **proposal)
                self.assertEqual((s.variables["bg_route"], s.variables["bg_body"], s.variables["bg_quarantined"]),
                                 ("blocked", "", "yes"))
                self.assertEqual((s.state["quarantined"], s.state["close_attempted"], s.state["closed"]), (True, False, False))
                self.assertEqual(s.target(s.entries["close_call"]), "bg_quarantine")

    def test_compiled_callback_claim_sends_the_no_callback_close(self):
        s = self.session()
        s.action("close_call", outcome="not_reached", reason="callback")
        self.assertEqual((s.variables["bg_route"], s.body["reason"], s.variables["bg_quarantined"]), ("allow", "no_callback", ""))
        self.assertEqual(s.target(s.entries["close_call"]), "bg_" + s.entries["close_call"] + "_api")
        self.assertTrue(s.state["close_attempted"])


def public_a_script(case, reason):
    """PA29 (unclear after two contact asks) and PA32 (two out-of-window callback times) frames."""
    exit_values = {"proposed_exit_outcome": "not_reached", "proposed_exit_reason": reason}
    frames = {"PA29": [{"event": "caller", "node": "contact_first", "values": {}, "intent_transition": 7},
                       {"event": "caller", "node": "contact_second", "values": exit_values, "intent_transition": 6}],
              "PA32": [{"event": "caller", "node": "contact_first", "values": {}, "intent_transition": 2},
                       {"event": "caller", "node": "callback_offer", "intent_transition": 2,
                        "values": {"proposed_callback_date": "2026-10-15", "proposed_callback_time": "09:30"}},
                       {"event": "agent_completion", "node": "callback_check", "transition": 1},
                       {"event": "caller", "node": "callback_alternative", "intent_transition": 2,
                        "values": dict(exit_values, proposed_callback_date="2026-10-16", proposed_callback_time="12:00")},
                       {"event": "agent_completion", "node": "callback_second_check", "transition": 1}]}[case]
    return {"version": 2, "initial_inputs": {"first_name": "Invented", "last_name": "Caller", "title": "Ms",
                                             "customer_id": "invented-" + case, "now": "2026-10-14T11:00"},
            "frames": frames + [{"event": "agent_completion", "node": "early_goodbye", "transition": 0}],
            "responses": [{"operation": "close_call", "envelope": {"status_code": 200, "data": {"ok": True}}}]}


@unittest.skipUnless(PUBLIC_A_RUN, "retained run artifacts are local only")
class RetainedPublicARefusedClose(unittest.TestCase):
    """No-model replay of the retained public-A plan recompiled by this kernel."""

    def setUp(self):
        run = Path(PUBLIC_A_RUN)
        saved = read_json(run / read_json(run / "manifest.json")["selected_attempt_directory"] / "guard-context.json")
        self.context = {key: saved[key] for key in ("plan", "contract", "execution_policy", "bindings")}
        self.api = read_json(ROOT / "build-the-builder/tools/openapi.json")
        self.workflow = compile_guarded_workflow(openapi=self.api, **self.context)["workflow"]

    def run_case(self, case, reason):
        report = replay(self.workflow, self.context, self.api, public_a_script(case, reason))
        self.assertEqual(report["status"], "terminated")
        flags = {key: report["final_state"][key] for key in ("closed", "close_attempted", "quarantined")}
        return report["terminal_node"], [(c["arguments"]["outcome"], c["arguments"].get("reason")) for c in report["calls"]], flags

    def test_callback_claim_closes_no_callback_and_unmatched_proposals_are_flagged(self):
        recorded = {"closed": True, "close_attempted": True, "quarantined": False}
        for case, sourced in (("PA29", "unclear"), ("PA32", "no_callback")):
            with self.subTest(case=case):
                self.assertEqual(self.run_case(case, sourced), ("bg_terminal", [("not_reached", sourced)], recorded))
                self.assertEqual(self.run_case(case, "callback"), ("bg_terminal", [("not_reached", "no_callback")], recorded))
                self.assertEqual(self.run_case(case, "declined"), ("bg_quarantine", [], {
                    "closed": False, "close_attempted": False, "quarantined": True}))


if __name__ == "__main__":
    unittest.main()

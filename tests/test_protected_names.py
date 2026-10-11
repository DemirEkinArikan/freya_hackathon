"""Close codes spelled like a builder meaning; invented codes, no model, bank or platform I/O."""
import unittest

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from test_execution_requirements import packet, rule
import test_goal_requirements as goal_requirements
from test_goal_requirements import synthetic, witness
from test_goal_terminal_close import extend_enum, goal

UNBOUND = "policy {} vocabulary has a code"
CONFLICT = "canonical API values cannot be assigned conflicting meanings"


def verified_row(outcome, reason=None):
    row = {"outcome": outcome, "requires": "none"}
    if reason:
        row["reason"] = reason
    return goal(["verify_identity", "close_call"], {outcome: [reason] if reason else []}, {"verified": [row]})


def unreached_row(outcome):
    policy, sources, api = synthetic(["close_call"])
    rule(policy, "close_reasons")["value"][outcome] = []
    extend_enum(api, "outcome", outcome)
    rule(policy, "identity_close")["value"]["unreached"] = {"outcome": outcome}
    witness(policy, sources)
    return policy, sources, api


def unreached_code(wire):
    """Every outcome bound; not_reached is carried by wire."""
    policy, sources, api = synthetic(["close_call"])
    codes = {meaning: meaning for meaning in ("promise", "paid", "transferred", "no_promise", "not_verified")}
    rule(policy, "wire_codes")["value"]["outcomes"] = dict(codes, not_reached=wire)
    close = rule(policy, "close_reasons")["value"]
    close[wire] = close.pop("not_reached")
    extend_enum(api, "outcome", wire)
    rule(policy, "identity_close")["value"]["unreached"] = {"outcome": wire}
    witness(policy, sources)
    return policy, sources, api


def failed_reason_code(wire):
    """The failed reason meaning is carried by wire."""
    policy, sources, api = synthetic(["verify_identity", "close_call"])
    rule(policy, "wire_codes")["value"]["reasons"]["failed"] = wire
    rule(policy, "close_reasons")["value"]["not_verified"] = [wire]
    for scope in ("failed", "incomplete"):
        rule(policy, "identity_close")["value"][scope]["reason"] = wire
    extend_enum(api, "reason", wire)
    witness(policy, sources)
    return policy, sources, api


def refused_note_code(wire):
    """The refused note meaning is carried by wire (local dispositions)."""
    policy, sources, api = packet(local=True)
    rule(policy, "wire_codes")["value"]["notes"] = {"refused": wire, "paid": "paid", "dispute": "dispute",
                                                   "service_error": "service_error"}
    notes = rule(policy, "note_reasons")["value"]
    notes[notes.index("refused")] = wire
    return policy, sources, api


class SpelledProtectedNameTests(unittest.TestCase):
    session = goal_requirements.GoalRequirementsTests.session

    def refused(self, values, message):
        with self.assertRaisesRegex(ExecutionRequirementsError, message):
            compile_execution_requirements(*values)

    def test_unbound_codes_spelled_like_a_builder_meaning_are_refused(self):
        # Review follow-up to PR 172: these compiled and closed with 0 recorded facts.
        for code in ("PROMISE", "Paid", "promise ", "No-Promise", "no promise", "TRANSFERRED"):
            with self.subTest(goal_row=code):
                self.refused(verified_row(code), UNBOUND.format("outcomes"))
        for code in ("Callback", "Recorded Callback", "NO_RECORDED_CALLBACK"):
            with self.subTest(goal_reason=code):
                self.refused(verified_row("GOAL_DONE", code), UNBOUND.format("reasons"))
        for code in ("Paid", "PROMISE"):
            with self.subTest(identity_row=code):
                self.refused(unreached_row(code), UNBOUND.format("outcomes"))

    def test_bound_codes_cannot_spell_another_meaning(self):
        for wire in ("PAID", "Promise", "no-promise", "Callback", "No Recorded Callback"):
            with self.subTest(outcome=wire):
                self.refused(unreached_code(wire), CONFLICT)
        # A reason code spelled like an outcome meaning or callback was sent as a reason.
        for wire in ("paid", "Promise", "TRANSFERRED", "not reached", "Callback", "recorded_callback"):
            with self.subTest(reason=wire):
                self.refused(failed_reason_code(wire), CONFLICT)
        for wire in ("Promise", "transferred", "Paid "):
            with self.subTest(note=wire):
                self.refused(refused_note_code(wire), CONFLICT)

    def test_opaque_and_respelled_own_meaning_codes_still_compile_and_send(self):
        _, _, session = self.session(verified_row("GOAL_DONE", "GOAL_MATCH"))
        for factor in ("postal_code", "memorable_word"):
            session.action("verify_identity", field=factor, answer="synthetic-correct")
            session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        session.action("close_call", outcome="", reason="")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("GOAL_DONE", "GOAL_MATCH"))

        _, _, session = self.session(failed_reason_code("X_FAIL"))
        session.action("verify_identity", field="postal_code", answer="synthetic-wrong")
        session.result("verify_identity", {"ok": True, "verified": False, "wrong_answers_left": 0})
        session.action("close_call", outcome="", reason="")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("not_verified", "X_FAIL"))

        _, _, session = self.session(unreached_code("Not Reached"))  # its own meaning, respelled
        session.action("close_call", outcome="Not Reached", reason="")
        self.assertEqual(session.body["outcome"], "Not Reached")

        policy, sources, api = goal_requirements.GoalRequirementsTests.callback_values(self)
        rule(policy, "wire_codes")["value"]["reasons"].update(callback="Callback", no_callback="No-Callback")
        extend_enum(api, "reason", "Callback", "No-Callback")
        witness(policy, sources)
        _, _, session = self.session((policy, sources, api))
        session.action("schedule_callback", date="2031-05-06", time="12:00")
        session.result("schedule_callback", {"ok": True})
        session.action("close_call", outcome="not_reached", reason="callback")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("not_reached", "Callback"))
        self.assertEqual(len(session.state["callbacks"]), 1)

        compile_execution_requirements(*refused_note_code("NOTE_REFUSED"))
        compile_execution_requirements(*verified_row("DECLINED"))  # no recorded-fact rule: exact match only


if __name__ == "__main__":
    unittest.main()

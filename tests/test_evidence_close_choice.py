"""A close fixed by protected evidence alone; invented data, no model, bank or platform I/O.

A generated plan may bind no closing proposal. When the absent callback alone leaves one declared
choice in every identity scope a proposal could reach, the sealed kernel sends it; several
eligible choices, or a lone language-derived (`none`) row, still need a matching proposal. Only an
absent proposal (empty outcome and reason) selects the recorded identity phase's own identity_close
row, and only when that row is one eligible choice of the phase.
"""
import os
import unittest
from pathlib import Path

from builder.guards import _catalogue, compile_guarded_workflow, run_guard_step, validate_guarded_workflow
from builder.safety import read_json
from builder.validation import validate_code
from test_goal_requirements import compiled
from test_guard_attempt_scopes import WireSession, callback_packet
from test_recorded_callback_close import context as kernel_context

RUN, INPUTS = os.environ.get("FREYA_CLOSE_CHOICE_RUN"), os.environ.get("FREYA_CALLBACK_INPUTS")


def session(close_inputs=None, **kwargs):
    _, context, _ = compiled(*callback_packet(**kwargs))
    close = next(node for node in context["plan"]["nodes"].values() if node.get("action") == "close_call")
    close["inputs"] = close_inputs or {}
    workflow = compile_guarded_workflow(**context)["workflow"]
    assert validate_guarded_workflow(workflow, **context) == []
    return WireSession(context, workflow)


class EvidenceCloseChoiceTests(unittest.TestCase):
    def wire(self, opaque):
        return (("session_ref", "result_code", "reason_code", "END_X", "NONE_X", "DONE_X", {"accepted": "true"})
                if opaque else ("customer_id", "outcome", "reason", "not_reached", "no_callback", "callback", {"ok": True}))

    def assert_single_close(self, s, reason, opaque=False):
        customer, outcome_field, reason_field, outcome, _, _, ok = self.wire(opaque)
        s.action("close_call")
        self.assertEqual(s.variables["bg_route"], "allow")
        self.assertEqual(s.body, {customer: "SYNTHETIC-GOAL-SESSION", outcome_field: outcome, reason_field: reason})
        s.result("close_call", ok)
        self.assertTrue(s.state["closed"])
        self.assertEqual(s.target("bg_" + s.entries["close_call"] + "_result"), "bg_terminal")
        s.action("close_call")
        self.assertEqual((s.variables["bg_route"], s.body), ("blocked", {}))

    def offer(self, s, answer):
        s.action("schedule_callback", date="2031-05-06", time="12:00")
        self.assertEqual(s.variables["bg_route"], "confirm")
        s.confirm("schedule_callback", answer)

    def test_declined_confirmation_sends_the_single_no_callback_choice(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                s = session(opaque=opaque)
                self.offer(s, "no")
                self.assertEqual((s.variables["bg_route"], s.state["callbacks"]), ("blocked", []))
                self.assert_single_close(s, self.wire(opaque)[4], opaque)

    def test_declined_offer_sends_it_without_any_bank_write(self):
        s = session()
        self.assert_single_close(s, "no_callback")
        self.assertEqual(s.state["attempt_counts"], {})

    def test_definite_sourced_failure_sends_it(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                s = session(opaque=opaque)
                self.offer(s, "yes")
                s.result("schedule_callback", {"accepted": "false", "failure_code": "NOT_PERSISTED"} if opaque
                         else {"ok": False, "error": "NOT_PERSISTED"})
                self.assertEqual(s.variables["bg_route"], "failure")
                self.assertFalse(s.state["quarantined"])
                self.assertEqual(s.state["callbacks"], [])
                self.assert_single_close(s, self.wire(opaque)[4], opaque)

    def test_recorded_callback_still_closes_only_as_callback(self):
        s = session()
        self.offer(s, "yes")
        s.result("schedule_callback", {"ok": True})
        self.assertEqual(len(s.state["callbacks"]), 1)
        self.assert_single_close(s, "callback")

    def test_caller_claims_cannot_select_or_hide_the_protected_outcome(self):
        bound = {"outcome": "proposal_outcome", "reason": "proposal_reason"}
        s = session(bound)
        self.offer(s, "no")
        s.action("close_call", outcome="not_reached", reason="callback")
        self.assertEqual((s.variables["bg_route"], s.body["reason"]), ("allow", "no_callback"))
        s = session(bound)
        self.offer(s, "yes")
        s.result("schedule_callback", {"ok": True})
        s.action("close_call", outcome="not_reached", reason="no_callback")
        self.assertEqual((s.variables["bg_route"], s.body["reason"]), ("allow", "callback"))

    def test_unknown_persistence_still_blocks_every_close(self):
        for data, status in (({}, 200), ({"ok": True, "error": "NOT_PERSISTED"}, 200), ({}, 503)):
            with self.subTest(data=data, status=status):
                s = session()
                self.offer(s, "yes")
                s.result("schedule_callback", data, status)
                self.assertTrue(s.state["quarantined"])
                s.action("close_call")
                self.assertEqual((s.variables["bg_route"], s.body), ("blocked", {}))
                self.assertFalse(s.state["close_attempted"])

    def close(self, state, profile, catalog, maps, proposal=None):
        return run_guard_step("prepare", state, "close_call", proposal or {}, profile=profile, catalog=catalog, maps=maps)

    def test_several_eligible_choices_keep_the_explicit_diagnostic(self):
        state, profile, catalog, maps = kernel_context()
        profile["identity_close_choices"]["incomplete"] = [
            {"outcome": "not_verified", "reason": reason, "requires": "none"} for reason in ("declined", "failed")]
        for proposal in ({"outcome": "Z_REACH"}, {"outcome": "Z_REACH", "reason": "Q_BOOKED"}):
            with self.subTest(proposal=proposal), self.assertRaisesRegex(ValueError, "callback evidence|declared eligible choice"):
                self.close(state, profile, catalog, maps, proposal)
        # No proposal at all: the unreached phase's own fixed row, never another eligible choice.
        self.assertEqual(self.close(state, profile, catalog, maps)[2]["cause_wire"], "Q_UNKNOWN")
        self.assertEqual(self.close(state, profile, catalog, maps, {"outcome": "Z_REACH", "reason": "Q_UNKNOWN"})[2]["cause_wire"], "Q_UNKNOWN")

    def test_a_scope_without_an_eligible_choice_never_borrows_another_scopes_choice(self):
        state, profile, catalog, maps = kernel_context()
        profile["identity_close_choices"] = {
            "unreached": [{"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"}],
            "incomplete": [{"outcome": "not_verified", "reason": "declined", "requires": "none"}]}
        with self.assertRaisesRegex(ValueError, "declared eligible choice"):
            self.close(state, profile, catalog, maps)
        state["verified_fields"] = ["member_factor"]  # only the incomplete scope remains reachable
        with self.assertRaisesRegex(ValueError, "declared eligible choice"):  # a lone `none` row needs its proposal
            self.close(state, profile, catalog, maps, {"outcome": "Z_REACH", "reason": "Q_UNKNOWN"})
        # An absent proposal selects only the incomplete phase's own fixed row.
        self.assertEqual(self.close(state, profile, catalog, maps)[2]["cause_wire"], "Q_STOP")
        profile["identity_close_choices"]["incomplete"][0]["requires"] = "no_recorded_callback"
        self.assertEqual(self.close(state, profile, catalog, maps)[2]["cause_wire"], "Q_STOP")
        # A row needing other protected evidence is never selected by evidence alone here.
        profile["identity_close_choices"]["incomplete"][0]["requires"] = "invented_other_evidence"
        with self.assertRaisesRegex(ValueError, "declared eligible choice"):
            self.close(state, profile, catalog, maps)

    def test_a_lone_language_row_still_needs_its_matching_proposal(self):
        state, profile, catalog, maps = kernel_context()
        row = {"outcome": "not_reached", "reason": "unclear", "requires": "none"}
        profile["identity_close_choices"] = {"incomplete": [dict(row)], "unreached": [
            row, {"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"}]}
        for passed in ([], ["member_factor"]):
            state["verified_fields"] = passed
            # An absent proposal takes the phase's fixed row only where that row is the lone choice.
            if passed:
                with self.assertRaisesRegex(ValueError, "declared eligible choice"):
                    self.close(state, profile, catalog, maps)
            else:
                self.assertEqual(self.close(state, profile, catalog, maps)[2]["cause_wire"], "Q_UNKNOWN")
            for proposal in ({"outcome": "Z_REACH"}, {"outcome": "Z_REACH", "reason": "Q_UNBOOKED"}, {"outcome": "Z_ID", "reason": "Q_STOP"}):
                with self.subTest(passed=passed, proposal=proposal), self.assertRaisesRegex(ValueError, "declared eligible choice"):
                    self.close(state, profile, catalog, maps, proposal)
            self.assertEqual(self.close(state, profile, catalog, maps, {"outcome": "Z_REACH", "reason": "Q_UNKNOWN"})[2]["cause_wire"], "Q_UNKNOWN")

    def test_code_complexity_stays_bounded(self):
        code = "try:\n" + "    x = 1\n" * 4500 + "except Exception:\n    x = 0\n"
        self.assertIn("probe: code exceeds local validation complexity limit", validate_code(code, "probe"))


@unittest.skipUnless(RUN and INPUTS, "retained run artifacts are local only")
class RetainedCallbackCloseReplay(unittest.TestCase):
    """No-model replay of a retained candidate whose close guard binds no proposal."""

    def setUp(self):
        run = Path(RUN)
        self.context = read_json(run / read_json(run / "manifest.json")["selected_attempt_directory"] / "guard-context.json")
        self.context["openapi"] = read_json(Path(INPUTS) / "bank.openapi.json")
        self.retained = read_json(run / "workflow.json")
        self.profile = self.context["execution_policy"]["profile"]
        close = next(node for node in self.context["plan"]["nodes"].values() if node.get("action") == "close_call")
        self.assertEqual(close["inputs"], {})

    def run_path(self, workflow, path):
        s = WireSession(self.context, workflow)
        ok, error = (self.profile["result_fields"]["schedule_callback"][key] for key in ("ok", "error"))
        if path is not None:
            s.action("schedule_callback", date=s.state["now"], time=self.profile["callback_window"]["end"])
            self.assertEqual(s.variables["bg_route"], "confirm")
            s.confirm("schedule_callback", "no" if path == "declined" else "yes")
        if isinstance(path, dict):
            s.result("schedule_callback", {ok: False, error: path["error"]}, path["http_status"])
            self.assertEqual(s.variables["bg_route"], "failure")
        self.assertEqual((s.state["callbacks"], s.state["quarantined"]), ([], False))
        s.action("close_call")
        return s

    def test_no_callback_close_is_sent_where_the_retained_workflow_ended_silently(self):
        operation = _catalogue(self.context["contract"], self.context["openapi"], self.context["bindings"])["schedule_callback"]["name"]
        choice = next(row for row in self.profile["identity_close_choices"]["unreached"] if row["requires"] == "no_recorded_callback")
        fields, codes = self.profile["action_fields"]["close_call"], self.profile.get("wire_codes", {})
        expected = {fields["outcome"]: codes.get("outcomes", {}).get(choice["outcome"], choice["outcome"]),
                    fields["reason"]: codes.get("reasons", {}).get(choice["reason"], choice["reason"])}
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        for path in [None, "declined"] + self.profile["nonpersistent_failures"][operation]:
            with self.subTest(path=path):
                original = self.run_path(self.retained, path)
                self.assertEqual((original.variables["bg_route"], original.body), ("blocked", {}))
                self.assertEqual(original.target(original.entries["close_call"]), "bg_terminal")
                fixed = self.run_path(workflow, path)
                self.assertEqual(fixed.variables["bg_route"], "allow")
                self.assertEqual({key: fixed.body[key] for key in expected}, expected)


if __name__ == "__main__":
    unittest.main()

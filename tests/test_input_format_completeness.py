"""A source-required offset clock must reach the sealed format check; no model, no guard edits."""
import os
import re
import unittest
from pathlib import Path

from builder.action_bindings import compile_action_bindings
from builder.contract import PolicyContractError, compile_policy_contract
from builder.guards import GuardCompileError, compile_guarded_workflow, required_execution_kinds
from builder.policy import compile_execution_policy, execution_rule_guidance
from builder.safety import digest, read_json
from test_call_input_formats import authored
from test_goal_scoped_resolution import CompiledCall

MISSING = "explicit UTC offset"
REQUIRED = ("now must carry an explicit UTC offset.",
            "| now | Local date-time with a UTC offset. |",
            "The clock input now states its offset from UTC.",
            "Supply now as a date and time with an explicit time zone.",
            "The engine clock now is a local date and time\nwith a time-zone offset.")


def declare_offset_clock(policy, text, source_id):
    """The repair the diagnostic asks for, citing the source's own clock statement."""
    row = next(line for line in text.splitlines() if re.search(r"(?<!\w)now(?!\w)", line) and "offset" in line)
    policy["rules"] += [
        {"id": "replay-clock-input", "kind": "call_variables", "value": ["now"], "source": {"id": source_id, "quote": row}},
        {"id": "replay-clock-format", "kind": "call_input_formats", "value": {"now": "offset_datetime"},
         "source": {"id": source_id, "quote": row}}]
    return policy


class InputFormatCompletenessTests(unittest.TestCase):
    def test_required_offset_clock_is_refused_until_declared_and_mapped(self):
        for statement in REQUIRED:
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(PolicyContractError, MISSING):
                    authored(None, quote="\n" + statement)
                with self.assertRaisesRegex(PolicyContractError, MISSING):
                    authored(None, names=["now"], quote="\n" + statement)
                context = authored({"now": "offset_datetime"}, quote="\n" + statement)
                self.assertEqual(CompiledCall(context, {"now": "2031-05-02T11:30"}).target("bg_init"), "bg_quarantine")
                self.assertEqual(CompiledCall(context, {"now": "2031-05-02T11:30+03:00"}).variables["bg_route"], "done")

    def test_declared_generic_input_is_checked_by_name(self):
        statement = "\nopening_clock requires a time zone offset."
        with self.assertRaisesRegex(PolicyContractError, MISSING):
            authored(None, names=["opening_clock"], quote=statement)
        context = authored({"opening_clock": "offset_datetime"}, quote=statement)
        self.assertEqual(CompiledCall(context, {"opening_clock": "2047-08-09T16:25"}).target("bg_init"), "bg_quarantine")

    def test_unrequired_negated_or_separate_wording_keeps_the_legacy_clock(self):
        for statement in ("now is the current date and time used by every date rule.",
                          "now carries no UTC offset.", "now may include an explicit offset.",
                          "Ignore any explicit offset in now.", "If now has a UTC offset, keep it unchanged.",
                          "now is the current date and time. Callback times keep an explicit UTC offset.",
                          "Times known to the bank use an explicit UTC offset.",
                          "now_reference must carry an explicit UTC offset."):
            with self.subTest(statement=statement):
                call = CompiledCall(authored(None, quote="\n" + statement), {"now": "2031-05-02T11:30"})
                self.assertEqual(call.variables["bg_route"], "done")

    def test_mapping_must_be_declared_and_cover_every_required_input(self):
        statement = "\nnow must carry an explicit UTC offset."
        with self.assertRaisesRegex(PolicyContractError, "also be declared"):
            authored({"now": "offset_datetime"}, names=["unrelated_input"], quote=statement)
        with self.assertRaisesRegex(PolicyContractError, MISSING):
            authored({"opening_clock": "offset_datetime"}, quote="\nopening_clock and now each carry an explicit UTC offset.")

    def test_sealed_compiler_still_refuses_a_forged_undeclared_mapping(self):
        context = authored({"now": "offset_datetime"}, quote="\nnow must carry an explicit UTC offset.")
        forged = {"now": "offset_datetime", "unrelated_input": "offset_datetime"}
        context["contract"]["active_rules"]["call_input_formats"]["value"] = dict(forged)
        context["execution_policy"]["profile"]["call_input_formats"] = dict(forged)
        for key in ("contract", "execution_policy"):
            context[key]["sha256"] = digest({k: v for k, v in context[key].items() if k != "sha256"})
        with self.assertRaisesRegex(GuardCompileError, "source-declared read-only names"):
            compile_guarded_workflow(**context)

    def test_format_adds_no_callback_permission(self):
        call = CompiledCall(authored({"now": "offset_datetime"}, quote="\nnow must carry an explicit UTC offset."),
                            {"now": "2031-05-02T11:30+03:00"})
        for day, at in (("2031-05-02", "12:00"), ("2031-05-06", "08:00")):
            with self.subTest(day=day, at=at):
                call.variables.update(day=day, at=at)
                call.code("callback")
                self.assertEqual(call.variables["bg_route"], "blocked")
                self.assertFalse(call.variables["bg_body"])
        self.assertFalse(call.state.get("attempts"))

    def test_guidance_says_to_declare_and_map_the_clock(self):
        self.assertIn("also declared in call_variables", execution_rule_guidance()["call_input_formats"])
        prompt = (Path(__file__).resolve().parents[1] / "builder/prompts/extract.txt").read_text(encoding="utf-8")
        self.assertIn("map it to offset_datetime in call_input_formats", prompt)


@unittest.skipUnless(os.environ.get("FREYA_CALLBACK_R2_RUN"), "retained run artifacts are local only")
class RetainedCallbackClockReplay(unittest.TestCase):
    """Read-only replay of callback run 20261011T091014880740Z (CB12); no model call."""

    def setUp(self):
        self.run = Path(os.environ["FREYA_CALLBACK_R2_RUN"])
        inputs = Path(os.environ["FREYA_CALLBACK_INPUTS"])
        self.text = (inputs / "callback-policy.approved.md").read_text(encoding="utf-8")
        self.sources = [{"id": "brief-1", "file": "callback-policy.approved.md", "sha256": digest(self.text), "text": self.text}]
        self.api = read_json(inputs / "bank.openapi.json")

    def drive(self, context, now, day="2026-12-23", at="16:45"):
        call = CompiledCall(dict(context, openapi=self.api), {"customer_id": "replay-session", "now": now})
        call.variables.update(proposed_callback_date=day, proposed_callback_time=at)
        call.code("schedule_guard")
        if call.target("schedule_guard") == "bg_schedule_guard_confirm":
            call.variables["bg_consent"] = "yes"
            call.code("bg_schedule_guard_accept")
        return call

    def test_retained_policy_is_refused_only_for_the_missing_clock_format(self):
        with self.assertRaises(PolicyContractError) as caught:
            compile_policy_contract(read_json(self.run / "policy.json"), self.sources, self.api)
        self.assertEqual(len(caught.exception.errors), 1)
        self.assertIn(MISSING, caught.exception.errors[0])

    def test_original_kernel_sent_the_write_and_the_repaired_contract_does_not(self):
        original = read_json(self.run / "attempts/plan-002/guard-context.json")
        sent = self.drive(original, "2026-12-23T16:30")
        self.assertEqual(sent.target("bg_schedule_guard_accept"), "bg_schedule_guard_api")
        self.assertIn('"pay_on":"2026-12-23"', sent.variables["bg_body"])
        policy = declare_offset_clock(read_json(self.run / "policy.json"), self.text, "brief-1")
        bindings = compile_action_bindings(policy, self.api)
        repaired = {"plan": original["plan"], "contract": compile_policy_contract(policy, self.sources, self.api),
                    "execution_policy": compile_execution_policy(policy, bindings, required_kinds=required_execution_kinds(original["plan"])),
                    "bindings": bindings}
        refused = self.drive(repaired, "2026-12-23T16:30")
        self.assertEqual(refused.target("bg_init"), "bg_quarantine")
        self.assertEqual(refused.target("schedule_guard"), "bg_quarantine")
        self.assertFalse(refused.variables["bg_body"])
        self.assertFalse(refused.state.get("attempts"))
        self.assertEqual(self.drive(repaired, "2026-12-23T16:30+03:00").variables["bg_body"], sent.variables["bg_body"])
        late = self.drive(repaired, "2026-12-23T16:30+03:00", at="16:30")
        self.assertNotEqual(late.target("schedule_guard"), "bg_schedule_guard_confirm")
        self.assertFalse(late.variables["bg_body"])


if __name__ == "__main__":
    unittest.main()

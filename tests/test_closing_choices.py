"""Local closing regressions using invented data and public practice reason labels.

No customer/evaluator scripts or model/platform calls are used. The synthetic
API declares enums deliberately; it does not rewrite the organizer's real API.
"""
import copy
import json
import os
import unittest
from pathlib import Path

from test_guards import fixture
from builder.action_bindings import compile_action_bindings
from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements
from builder.guards import (GuardCompileError, _catalogue, _profile,
                            compile_guarded_workflow, run_guard_step)
from builder.safety import digest, read_json

ROOT = Path(__file__).resolve().parents[1]
RETAINED_RUN = os.environ.get("FREYA_RETAINED_POLICY_RUN", "")


CHOICES = {
    "unreached": [
        {"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"},
        {"outcome": "not_reached", "reason": "no_callback", "requires": "no_recorded_callback"},
        {"outcome": "not_reached", "reason": "wrong_number", "requires": "none"},
        {"outcome": "not_reached", "reason": "deceased", "requires": "none"},
        {"outcome": "not_reached", "reason": "unclear", "requires": "none"},
    ],
    "incomplete": [{"outcome": "not_verified", "reason": "declined", "requires": "none"}],
}
# A source may list several reasons for one identity phase without ranking one.
REASONLESS = {"failed": {"outcome": "not_verified", "reason": "failed"},
              "incomplete": {"outcome": "not_verified", "reason": "declined"},
              "unreached": {"outcome": "not_reached"}}


def context(choices=CHOICES, *, opaque=False, changes=None):
    original = fixture()
    profile = copy.deepcopy(original["execution_policy"]["profile"])
    for kind in ("note_reasons", "transfer_reasons", "transfer_prerequisites"):
        profile.pop(kind)
    profile["record_notes"] = False
    profile["identity_wrong_limit"] = 3
    profile["close_reasons"]["not_verified"] = ["failed", "declined"]
    profile["close_reasons"]["not_reached"] = ["callback", "no_callback", "wrong_number", "deceased", "unclear"]
    profile["identity_close"] = {
        "failed": {"outcome": "not_verified", "reason": "failed"},
        "incomplete": {"outcome": "not_verified", "reason": "declined"},
        "unreached": {"outcome": "not_reached", "reason": "unclear"},
    }
    if choices is not None:
        profile["identity_close_choices"] = copy.deepcopy(choices)
    if changes:
        profile.update(copy.deepcopy(changes))
    api = copy.deepcopy(original["openapi"])
    outcomes = {value: value for value in profile["close_reasons"]}
    reasons = {reason: reason for values in profile["close_reasons"].values() for reason in values}
    if opaque:
        outcomes = {meaning: "END_" + str(index + 31) for index, meaning in enumerate(outcomes)}
        reasons = {meaning: "REASON_" + str(index + 71) for index, meaning in enumerate(sorted(reasons))}
        profile["wire_codes"] = {"outcomes": outcomes, "reasons": reasons}
        profile["close_reasons"] = {outcomes[key]: [reasons[value] for value in values]
                                    for key, values in profile["close_reasons"].items()}
        profile["close_precedence"] = [outcomes[value] for value in profile["close_precedence"]]
        profile["identity_close"] = {scope: {"outcome": outcomes[record["outcome"]],
                                             **({"reason": reasons[record["reason"]]} if "reason" in record else {})}
                                      for scope, record in profile["identity_close"].items()}
        if "identity_close_choices" in profile:
            profile["identity_close_choices"] = {scope: [
                {"outcome": outcomes[value["outcome"]], "requires": value["requires"],
                 **({"reason": reasons[value["reason"]]} if "reason" in value else {})} for value in values]
                for scope, values in profile["identity_close_choices"].items()}
    body = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    body["properties"]["outcome"]["enum"] = list(outcomes.values())
    body["properties"]["reason"]["enum"] = list(reasons.values())
    text = "Invented development contract for testing declared closing choices and trusted callback evidence."
    sources = [{"id": "invented", "file": "invented-closing.txt", "sha256": digest(text), "text": text}]
    policy = {"version": 1, "goal": "Resolve synthetic banking calls", "allowed_actions": [path[1:] for path in api["paths"]],
              "sources": [{key: value for key, value in sources[0].items() if key != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": "invented", "quote": text}}
                        for kind, value in profile.items()], "unsupported_constraints": []}
    contract = compile_policy_contract(policy, sources, api)
    bindings = compile_action_bindings(policy, api)
    execution = {"version": 1, "policy_sha256": digest(policy), "profile": profile, "bindings_sha256": bindings["sha256"]}
    execution["sha256"] = digest(execution)
    plan = copy.deepcopy(original["plan"])
    plan["nodes"] = {name: node for name, node in plan["nodes"].items() if name in {"talk", "close", "callback", "identity"}}
    plan["nodes"]["talk"]["transitions"] = [value for value in plan["nodes"]["talk"]["transitions"]
                                              if value["target_node"] in plan["nodes"]]
    return dict(plan=plan, contract=contract, openapi=api, execution_policy=execution, bindings=bindings), outcomes, reasons


class ClosingChoiceTests(unittest.TestCase):
    def setup_context(self, choices=CHOICES, **kwargs):
        self.context, self.outcomes, self.reasons = context(choices, **kwargs)
        self.profile = _profile(self.context["contract"], self.context["execution_policy"], self.context["plan"], self.context["bindings"])
        self.catalog = _catalogue(self.context["contract"], self.context["openapi"], self.context["bindings"])
        self.maps = self.context["bindings"]["categories"]
        self.state = self.step("init", customer_id="invented-session", now="2031-05-02T11:00")[0]

    def step(self, phase, state=None, action="", inputs=None, result=None, **kwargs):
        return run_guard_step(phase, state, action, inputs, result, profile=self.profile,
                              catalog=self.catalog, maps=self.maps, **kwargs)

    def book_callback(self):
        state = self.step("prepare", self.state, "schedule_callback", {"date": "2031-05-06", "time": "12:00"})[0]
        self.state = self.step("result", state, "schedule_callback", result={"ok": True})[0]

    def close(self, outcome, reason):
        return self.step("prepare", self.state, "close_call", {"outcome": outcome, "reason": reason})

    def test_public_a_unreached_reason_branches_are_selectable_and_b_inherits_them(self):
        self.setup_context()
        for reason in ("no_callback", "wrong_number", "deceased", "unclear"):
            with self.subTest(reason=reason):
                _, route, body, _ = self.close("not_reached", reason)
                self.assertEqual(route, "allow")
                self.assertEqual((body["outcome"], body["reason"]), ("not_reached", reason))

    def test_callback_success_requires_and_uses_actual_recorded_callback(self):
        self.setup_context()
        # Without a recorded callback the evidence decides the claim: its no_recorded_callback row.
        self.assertEqual(self.close("not_reached", "callback")[2]["reason"], "no_callback")
        self.book_callback()
        _, route, body, _ = self.close("not_reached", "callback")
        self.assertEqual(route, "allow")
        self.assertEqual(body["reason"], "callback")

    def test_no_callback_cannot_hide_a_recorded_callback(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                self.setup_context(opaque=opaque)
                self.book_callback()
                before = copy.deepcopy(self.state)
                _, route, body, _ = self.close(self.outcomes["not_reached"], self.reasons["no_callback"])
                self.assertEqual(route, "allow")
                self.assertEqual((body["outcome"], body["reason"]),
                                 (self.outcomes["not_reached"], self.reasons["callback"]))
                self.assertEqual(self.state, before)

    def test_failed_or_unknown_callback_result_cannot_claim_success_or_fabricate_no_callback(self):
        for result in ({"ok": False, "error": "synthetic_failure"}, {}):
            self.setup_context()
            state = self.step("prepare", self.state, "schedule_callback", {"date": "2031-05-06", "time": "12:00"})[0]
            self.state = self.step("result", state, "schedule_callback", result=result)[0]
            self.assertEqual(self.state["callbacks"], [])
            for reason in ("callback", "no_callback"):
                with self.subTest(result=result, reason=reason), self.assertRaises(ValueError):
                    self.close("not_reached", reason)

    def test_counter_exhaustion_overrides_all_untrusted_disposition_proposals(self):
        self.setup_context()
        field = self.profile["identity_fields"][0]
        for remaining in (2, 1, 0):
            state = self.step("prepare", self.state, "verify_identity", {"field": field, "answer": "invented incorrect answer"})[0]
            self.state = self.step("result", state, "verify_identity", result={"ok": True, "verified": False, "wrong_answers_left": remaining})[0]
        for outcome, reason in (("not_reached", "callback"), ("not_verified", "declined"), ("invalid", "invalid"),
                                (None, []), ({"malformed": True}, {"malformed": True})):
            _, _, body, _ = self.close(outcome, reason)
            self.assertEqual((body["outcome"], body["reason"]), ("not_verified", "failed"))

    def test_incomplete_identity_uses_declared_declined_reason_and_excludes_unreached_after_passed_factor(self):
        self.setup_context()
        field = self.profile["identity_fields"][0]
        state = self.step("prepare", self.state, "verify_identity", {"field": field, "answer": "invented correct answer"})[0]
        self.state = self.step("result", state, "verify_identity", result={"ok": True, "verified": True, "wrong_answers_left": 3})[0]
        self.assertEqual(self.close("not_verified", "declined")[2]["reason"], "declined")
        with self.assertRaisesRegex(ValueError, "declared eligible choice"):
            self.close("not_reached", "wrong_number")

    def test_selected_choice_scope_rejects_partial_invalid_and_wrong_scope_proposals(self):
        self.setup_context()
        for inputs in ({"outcome": "not_reached"}, {"reason": "unclear"},
                       {"outcome": "not_reached", "reason": "declined"},
                       {"outcome": "not_verified", "reason": "wrong_number"},
                       {"outcome": "promise", "reason": "callback"}):
            with self.subTest(inputs=inputs), self.assertRaisesRegex(ValueError, "declared eligible choice"):
                    self.step("prepare", self.state, "close_call", inputs)
        for inputs in ({"outcome": None, "reason": "unclear"}, {"outcome": "not_reached", "reason": []}):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                self.step("prepare", self.state, "close_call", inputs)

    def verify(self, verified, remaining=3):
        field = self.profile["identity_fields"][0]
        state = self.step("prepare", self.state, "verify_identity", {"field": field, "answer": "invented answer"})[0]
        self.state = self.step("result", state, "verify_identity",
                               result={"ok": True, "verified": verified, "wrong_answers_left": remaining})[0]

    def test_absent_proposal_closes_as_the_recorded_identity_phase_row(self):
        # Hosted records: the platform extracted no proposal, or the close step took none.
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                self.setup_context(opaque=opaque)
                start = self.state
                for inputs in ({}, {"outcome": "", "reason": ""}):
                    _, route, body, _ = self.step("prepare", start, "close_call", inputs)
                    self.assertEqual(route, "allow")
                    self.assertEqual((body["outcome"], body["reason"]), (self.outcomes["not_reached"], self.reasons["unclear"]))
                for verified in (True, False):
                    self.state = start
                    self.verify(verified, 3 if verified else 2)
                    _, route, body, _ = self.step("prepare", self.state, "close_call", {})
                    self.assertEqual((route, body["outcome"], body["reason"]),
                                     ("allow", self.outcomes["not_verified"], self.reasons["declined"]))
                self.state = start
                self.book_callback()
                self.assertEqual(self.step("prepare", self.state, "close_call", {})[2]["reason"], self.reasons["callback"])
                self.state = start
                for remaining in (2, 1, 0):
                    self.verify(False, remaining)
                self.assertEqual(self.step("prepare", self.state, "close_call", {})[2]["reason"], self.reasons["failed"])

    def test_absent_proposal_refuses_without_one_eligible_phase_row_or_after_verification(self):
        missing = {"unreached": [row for row in CHOICES["unreached"] if row["reason"] != "unclear"], "incomplete": CHOICES["incomplete"]}
        for choices, changes in ((missing, None), ({"unreached": CHOICES["unreached"]}, {"identity_close": REASONLESS})):
            with self.subTest(choices=choices), self.assertRaisesRegex(ValueError, "declared eligible choice"):
                self.setup_context(choices, changes=changes)
                self.step("prepare", self.state, "close_call", {})
        self.setup_context()
        for field in self.profile["identity_fields"]:
            state = self.step("prepare", self.state, "verify_identity", {"field": field, "answer": "invented answer"})[0]
            self.state = self.step("result", state, "verify_identity", result={"ok": True, "verified": True, "wrong_answers_left": 3})[0]
        self.assertEqual(self.state["identity_status"], "verified")
        with self.assertRaises(ValueError):
            self.step("prepare", self.state, "close_call", {})

    def test_nonempty_subset_of_supported_scopes_keeps_other_explicit_fixed_record(self):
        self.setup_context({"unreached": CHOICES["unreached"]})
        compile_guarded_workflow(**self.context)
        self.assertEqual(self.close("not_verified", "arbitrary")[2]["reason"], "declined")
        with self.assertRaises(ValueError):
            self.close("not_reached", "arbitrary")

    def test_wrong_empty_scopes_and_malformed_choices_fail_compilation(self):
        for choices in ({}, {"failed": []}, {"other": CHOICES["unreached"]},
                        {"unreached": []}, {"unreached": "unclear"},
                        {"unreached": [{"outcome": "not_reached", "reason": "unclear"}]},
                        {"unreached": [{"outcome": "not_reached", "reason": "unclear", "requires": []}]},
                        {"unreached": [{"outcome": "not_reached", "reason": "unclear", "requires": "none", "extra": True}]}):
            with self.subTest(choices=choices), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context(choices)[0])

    def test_choice_outcome_and_reason_must_match_scope_and_declared_policy(self):
        for choice in ({"outcome": "not_verified", "reason": "declined", "requires": "none"},
                       {"outcome": "not_reached", "reason": "unknown", "requires": "none"},
                       {"outcome": "not_reached", "requires": "none"},
                       {"outcome": "not_reached", "reason": "", "requires": "none"}):
            with self.subTest(choice=choice), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context({"unreached": [choice]})[0])

    def test_callback_choices_cannot_downgrade_success_evidence(self):
        for requirement in ("none", "no_recorded_callback"):
            value = {"unreached": [{"outcome": "not_reached", "reason": "callback", "requires": requirement}]}
            with self.subTest(requirement=requirement), self.assertRaisesRegex(GuardCompileError, "recorded_callback evidence"):
                compile_guarded_workflow(**context(value)[0])

    def test_no_callback_choices_cannot_downgrade_absence_evidence(self):
        for requirement in ("none", "recorded_callback"):
            for opaque in (False, True):
                value = {"unreached": [{"outcome": "not_reached", "reason": "no_callback", "requires": requirement}]}
                with self.subTest(requirement=requirement, opaque=opaque), self.assertRaisesRegex(GuardCompileError, "no_recorded_callback evidence"):
                    compile_guarded_workflow(**context(value, opaque=opaque)[0])

    def test_duplicate_decoded_reasons_fail_compilation_and_ambiguous_runtime_match_is_blocked(self):
        choices = {"unreached": [CHOICES["unreached"][-1], CHOICES["unreached"][-1]]}
        with self.assertRaisesRegex(GuardCompileError, "duplicate decoded"):
            compile_guarded_workflow(**context(choices)[0])
        self.setup_context()
        self.profile["identity_close_choices"]["unreached"].append(copy.deepcopy(CHOICES["unreached"][-1]))
        with self.assertRaisesRegex(ValueError, "one declared eligible choice"):
            self.close("not_reached", "unclear")

    def test_legacy_fixed_callback_also_cannot_claim_success_without_api_evidence(self):
        fixed = copy.deepcopy(fixture()["execution_policy"]["profile"]["identity_close"])
        fixed["failed"] = {"outcome": "not_verified", "reason": "failed"}
        fixed["incomplete"] = {"outcome": "not_verified", "reason": "declined"}
        fixed["unreached"] = {"outcome": "not_reached", "reason": "callback"}
        self.setup_context(None, changes={"identity_close": fixed})
        with self.assertRaisesRegex(ValueError, "recorded callback"):
            self.close("not_reached", "unclear")
        self.book_callback()
        self.assertEqual(self.close("not_reached", "unclear")[2]["reason"], "callback")

    def test_legacy_fixed_no_callback_cannot_hide_successful_api_booking(self):
        fixed = {"failed": {"outcome": "not_verified", "reason": "failed"},
                 "incomplete": {"outcome": "not_verified", "reason": "declined"},
                 "unreached": {"outcome": "not_reached", "reason": "no_callback"}}
        self.setup_context(None, changes={"identity_close": fixed})
        self.assertEqual(self.close("not_reached", "unclear")[2]["reason"], "no_callback")
        self.book_callback()
        with self.assertRaisesRegex(ValueError, "cannot hide a recorded callback"):
            self.close("not_reached", "unclear")

    def test_opaque_outcome_and_reason_bindings_encode_each_selected_branch(self):
        self.setup_context(opaque=True)
        compile_guarded_workflow(**self.context)
        for reason in ("no_callback", "wrong_number", "deceased", "unclear"):
            body = self.close(self.outcomes["not_reached"], self.reasons[reason])[2]
            self.assertEqual((body["outcome"], body["reason"]), (self.outcomes["not_reached"], self.reasons[reason]))
        self.assertEqual(self.close(self.outcomes["not_reached"], self.reasons["callback"])[2]["reason"], self.reasons["no_callback"])
        self.book_callback()
        self.assertEqual(self.close(self.outcomes["not_reached"], self.reasons["callback"])[2]["reason"], self.reasons["callback"])

    def test_opaque_callback_choice_requires_recorded_evidence_after_decoding(self):
        choices = {"unreached": [{"outcome": "not_reached", "reason": "callback", "requires": "none"}]}
        with self.assertRaisesRegex(GuardCompileError, "recorded_callback evidence"):
            compile_guarded_workflow(**context(choices, opaque=True)[0])

    def test_reasonless_phase_row_refined_by_sourced_choices_closes_only_through_a_choice(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                self.setup_context({"unreached": CHOICES["unreached"]}, opaque=opaque, changes={"identity_close": REASONLESS})
                compile_guarded_workflow(**self.context)
                outcome, reasons = self.outcomes["not_reached"], self.reasons
                for reason in ("no_callback", "wrong_number", "deceased", "unclear"):
                    self.assertEqual(self.close(outcome, reasons[reason])[2], {"customer_id": "invented-session",
                                     "outcome": outcome, "reason": reasons[reason]})
                for inputs in ({"outcome": outcome}, {"outcome": outcome, "reason": reasons["declined"]}):
                    with self.assertRaisesRegex(ValueError, "declared eligible choice|callback evidence"):
                        self.step("prepare", self.state, "close_call", inputs)
                self.assertEqual(self.close(outcome, reasons["callback"])[2]["reason"], reasons["no_callback"])
                self.assertEqual(self.close(self.outcomes["not_verified"], reasons["unclear"])[2]["reason"], reasons["declined"])
                self.book_callback()
                self.assertEqual(self.close(outcome, reasons["no_callback"])[2]["reason"], reasons["callback"])

    def test_reasonless_phase_row_keeps_failed_partial_and_unknown_callback_guards(self):
        self.setup_context(changes={"identity_close": REASONLESS})
        start, field = self.state, self.profile["identity_fields"][0]
        state = self.step("prepare", start, "verify_identity", {"field": field, "answer": "invented correct answer"})[0]
        self.state = self.step("result", state, "verify_identity", result={"ok": True, "verified": True, "wrong_answers_left": 3})[0]
        with self.assertRaisesRegex(ValueError, "declared eligible choice"):
            self.close("not_reached", "wrong_number")
        self.state = start
        for remaining in (2, 1, 0):
            state = self.step("prepare", self.state, "verify_identity", {"field": field, "answer": "invented incorrect answer"})[0]
            self.state = self.step("result", state, "verify_identity", result={"ok": True, "verified": False, "wrong_answers_left": remaining})[0]
        self.assertEqual(self.close("not_reached", "unclear")[2]["reason"], "failed")
        for result in ({"ok": False, "error": "synthetic_failure"}, {}):
            state = self.step("prepare", start, "schedule_callback", {"date": "2031-05-06", "time": "12:00"})[0]
            self.state = self.step("result", state, "schedule_callback", result=result)[0]
            for reason in ("callback", "no_callback"):
                with self.subTest(result=result, reason=reason), self.assertRaises(ValueError):
                    self.close("not_reached", reason)

    def test_reasonless_phase_row_without_valid_refining_choices_fails_compilation(self):
        for choices, fixed in ((None, REASONLESS), ({"incomplete": CHOICES["incomplete"]}, REASONLESS),
                               (CHOICES, dict(REASONLESS, failed={"outcome": "not_verified"})),
                               ({"unreached": CHOICES["unreached"]}, dict(REASONLESS, incomplete={"outcome": "not_verified"}))):
            for opaque in (False, True):
                with self.subTest(choices=choices, fixed=fixed, opaque=opaque), \
                        self.assertRaisesRegex(GuardCompileError, "omits the reason its outcome requires"):
                    compile_guarded_workflow(**context(choices, opaque=opaque, changes={"identity_close": fixed})[0])
        for choice in ({"outcome": "not_reached", "requires": "none"},
                       {"outcome": "not_reached", "reason": "declined", "requires": "none"},
                       {"outcome": "not_verified", "reason": "declined", "requires": "none"},
                       {"outcome": "not_reached", "reason": "callback", "requires": "none"},
                       {"outcome": "not_reached", "reason": "no_callback", "requires": "recorded_callback"}):
            with self.subTest(choice=choice), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context({"unreached": [choice]}, changes={"identity_close": REASONLESS})[0])


@unittest.skipUnless(RETAINED_RUN, "set FREYA_RETAINED_POLICY_RUN to a retained kit-input build run directory")
class RetainedRejectedPolicyReplayTests(unittest.TestCase):
    """No-model replay of a retained failed build's rejected policies; artifacts stay untracked."""

    def test_retained_rejected_policies_pass_the_policy_stage_on_their_exact_inputs(self):
        run = Path(RETAINED_RUN)
        lines = (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        inputs = next(event for event in map(json.loads, lines) if event.get("event") == "inputs")
        sources = []
        for source in inputs["sources"]:
            text = (ROOT / "build-the-builder" / "briefs" / source["file"]).read_text(encoding="utf-8")
            self.assertEqual(digest(text), source["sha256"])
            sources.append(dict(source, text=text))
        openapi = read_json(ROOT / "build-the-builder" / "tools" / "openapi.json")
        self.assertEqual(digest(openapi), inputs["tool_api_sha256"])
        rejected = sorted(run.glob("policy.rejected-*.json"))
        self.assertTrue(rejected)
        for path in rejected:
            with self.subTest(policy=path.name):
                policy = read_json(path)["policy"]
                compile_policy_contract(policy, sources, openapi)
                self.assertTrue(compile_execution_requirements(policy, sources, openapi)["actions"])


if __name__ == "__main__":
    unittest.main()

"""Sourced identity answer formats checked before sending: invented codes, no model, bank or platform I/O."""
import copy
import json
import os
import unittest
from pathlib import Path

from builder.action_bindings import compile_action_bindings
from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import execution_action_catalogue
from builder.policy import execution_rule_guidance
from builder.safety import digest, read_json
import test_goal_requirements as goal_requirements
import test_identity_lifecycle as lifecycle

F1, F2 = lifecycle.F1, lifecycle.F2
CHECKS = {F1: "calendar_date", F2: {"digits": 4}}


class IdentityAnswerCheckTests(unittest.TestCase):
    session = goal_requirements.GoalRequirementsTests.session
    check = lifecycle.IdentityLifecycleTests.check
    match = lifecycle.IdentityLifecycleTests.match
    miss = lifecycle.IdentityLifecycleTests.miss
    assert_refused = lifecycle.IdentityLifecycleTests.assert_refused

    def test_well_formed_answers_are_sent_exactly(self):
        _, _, session = self.session(lifecycle.values(identity_answer_checks=CHECKS))
        for field, answer in ((F1, "1980-02-29"), (F2, "0042")):
            session.action("verify_identity", field=field, answer=answer)
            self.assertEqual((session.variables["bg_route"], session.body["answer"]), ("allow", answer))
            self.assertEqual(lifecycle.receipt(session, self.match(4)), "done")
        self.assertEqual(session.state["identity_status"], "verified")

    def test_malformed_answers_send_nothing_and_change_no_counter_or_factor(self):
        _, _, session = self.session(lifecycle.values(identity_answer_checks=CHECKS))
        # A processed mismatch first, so each refusal is checked against a moved counter.
        self.assertEqual(self.check(session, F1, self.miss(3), answer="1979-02-03"), "done")
        for answer in ("1979-02-30", "1979-13-01", "0000-01-01", "1979-2-03", "19790203", "1979-02-03T00:00",
                       " 1979-02-03", "1979-02-03 ", "1979-02-03\n", "\u0661\u0669\u0667\u0669-\u0660\u0662-\u0660\u0663"):
            with self.subTest(field=F1, answer=answer):
                self.assert_refused(session, "verify_identity", field=F1, answer=answer)
        self.assertEqual(self.check(session, F1, self.match(3), answer="1979-02-04"), "done")
        for answer in ("123", "12345", "12 34", " 1234", "1234 ", "1234\n", "12a4", "+123",
                       "\uff11\uff12\uff13\uff14", "\u0661\u0662\u0663\u0664"):
            with self.subTest(field=F2, answer=answer):
                self.assert_refused(session, "verify_identity", field=F2, answer=answer)
        self.assertEqual((session.state["verified_fields"], session.state["wrong_answers"]), ([F1], 1))
        self.assertEqual(self.check(session, F2, self.match(3), answer="0042"), "done")
        self.assertEqual(session.state["identity_status"], "verified")

    def test_without_the_rule_any_nonempty_answer_is_still_sent(self):
        _, _, session = self.session(lifecycle.values())
        session.action("verify_identity", field=F1, answer="1979-02-30")
        self.assertEqual((session.variables["bg_route"], session.body["answer"]), ("allow", "1979-02-30"))

    def test_unknown_fields_and_open_shapes_are_refused_at_the_policy_stage(self):
        with self.assertRaisesRegex(ExecutionRequirementsError, "identity_answer_checks keys must be identity_fields names"):
            compile_execution_requirements(*lifecycle.values(identity_answer_checks={F1: "calendar_date", "invented_factor": "calendar_date"}))
        for checks in ({}, {F1: "date"}, {F1: "CALENDAR_DATE"}, {F1: {}}, {F1: {"digits": 0}}, {F1: {"digits": 33}},
                       {F1: {"digits": True}}, {F1: {"digits": 4.0}}, {F1: {"digits": "4"}},
                       {F1: {"digits": 4, "leading_zeros": True}}, {F1: {"pattern": "[0-9]{4}"}},
                       {F1: ["calendar_date"]}, {F1: None}, [F1], "calendar_date"):
            with self.subTest(checks=checks), self.assertRaises(ExecutionRequirementsError):
                compile_execution_requirements(*lifecycle.values(identity_answer_checks=checks))

    def test_descriptive_answer_rules_keep_their_free_shape_beside_the_typed_rule(self):
        descriptive = {"by_field": {F1: {"format": "YYYY-MM-DD"}, F2: {"ascii_digits": 4}}}
        for kinds in ({"identity_answer_formats": descriptive},
                      {"identity_answer_formats": descriptive, "identity_answer_checks": CHECKS}):
            with self.subTest(kinds=sorted(kinds)):
                self.assertIn("verify_identity", compile_execution_requirements(*lifecycle.values(**kinds))["actions"])

    def test_guidance_and_controller_effect_state_the_refusal(self):
        guidance = execution_rule_guidance()["identity_answer_checks"]
        for words in ('"calendar_date" or {digits: n}', "nothing is trimmed or converted", "needs no declaration"):
            self.assertIn(words, guidance)
        rejections = execution_action_catalogue()["verify_identity"]["controller_state_effects"]["rejections"]
        self.assertTrue(any("identity_answer_checks" in text and "refused before sending" in text for text in rejections))


RUN, INPUTS = os.environ.get("FREYA_ANSWER_CHECK_REPLAY_RUN"), os.environ.get("FREYA_ANSWER_CHECK_REPLAY_INPUTS")


@unittest.skipUnless(RUN and INPUTS, "set FREYA_ANSWER_CHECK_REPLAY_RUN and FREYA_ANSWER_CHECK_REPLAY_INPUTS to replay a retained failed run")
class RetainedAnswerFormatReplayTests(unittest.TestCase):
    """No-model replay of a run whose policies declared the answer format fatal; artifacts are read, never written.

    The typed rule is built in memory only from each policy's own cited descriptive
    rule and its quotation, and replaces the fatal declaration.
    """

    @staticmethod
    def typed(policy):
        declared = [entry for entry in policy["unsupported_constraints"] if entry["category"] == "protected_unenforceable"]
        cited = {ident for entry in declared for ident in entry["rule_ids"]}
        described = next(item for item in policy["rules"]
                         if item["id"] in cited and isinstance(item["value"], dict) and "by_field" in item["value"])
        checks = {field: "calendar_date" if spec.get("real_gregorian_date") else {"digits": spec["ascii_digits"]}
                  for field, spec in described["value"]["by_field"].items()}
        policy["rules"].append({"id": "replay-answer-checks", "kind": "identity_answer_checks", "value": checks,
                                "source": copy.deepcopy(described["source"])})
        policy["unsupported_constraints"] = [entry for entry in policy["unsupported_constraints"] if entry not in declared]
        return checks

    def test_retained_policies_pass_the_policy_stage_with_the_typed_rule(self):
        run, inputs = Path(RUN), Path(INPUTS)
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        recorded = next(event for event in events if event.get("event") == "inputs")
        sources = [{**item, "text": (inputs / item["file"]).read_text(encoding="utf-8")} for item in recorded["sources"]]
        self.assertTrue(all(digest(item["text"]) == item["sha256"] for item in sources))
        api = next(value for value in map(read_json, sorted(inputs.glob("*.json")))
                   if digest(value) == recorded["tool_api_sha256"])
        rejected = sorted(run.glob("policy.rejected-*.json"))
        self.assertTrue(rejected)
        for path in rejected:
            with self.subTest(policy=path.name):
                raw = path.read_bytes()
                policy = json.loads(raw)["policy"]
                with self.assertRaisesRegex(PolicyContractError, "policy contains unsupported constraints"):
                    compile_policy_contract(policy, sources, api)
                checks = self.typed(policy)
                compile_policy_contract(policy, sources, api)
                compile_execution_requirements(policy, sources, api)
                compile_action_bindings(policy, api)
                # The compiled kernel refuses an impossible date for the first factor and sends a real one.
                _, context, workflow = goal_requirements.compiled(policy, sources, api)
                first = context["contract"]["active_rules"]["identity_fields"]["value"][0]
                self.assertEqual(checks[first], "calendar_date")
                session = goal_requirements.SealedSession(context, workflow)
                session.action("verify_identity", field=first, answer="1979-02-30")
                self.assertEqual((session.variables["bg_route"], session.variables["bg_body"]), ("blocked", ""))
                session.action("verify_identity", field=first, answer="1979-02-28")
                self.assertEqual(session.variables["bg_route"], "allow")
                self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()

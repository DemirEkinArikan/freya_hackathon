"""Sealed identity-verification lifecycle: invented codes, no model, bank or platform I/O."""
import copy
import json
import os
import unittest
from pathlib import Path

from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import GuardCompileError, _profile, execution_action_catalogue, run_guard_step
from builder.policy import execution_rule_guidance
from builder.safety import digest, read_json
from test_execution_requirements import add_rule, rule
import test_goal_requirements as goal_requirements
from test_goal_requirements import rehash, witness
from test_goal_terminal_close import goal

F1, F2 = "postal_code", "memorable_word"
UNSURE = {"outcome": "not_verified", "reason": "ID_UNSURE", "requires": "uncertain_verification"}
FAULT = {"outcome": "not_verified", "reason": "ID_FAULT", "requires": "definite_verification_failure"}
SPOKEN = {"outcome": "not_verified", "reason": "identity_failed", "requires": "none"}
CASES = [{"http_status": 500}, {"http_status": 200, "error": "RETRY_LATER"}, {"http_status": 200, "error": "DENIED"},
         {"http_status": 409}, {"http_status": 200, "error": "SEALED"}, {"http_status": 200, "error": "BAD_FORM"}]
CLASSES = {"repeat_once": CASES[:2], "fault": CASES[2:3], "sealed": CASES[3:5]}
LIFECYCLE = {"identity_factor_state": "latest_check", "identity_counter_movement": "exact",
             "identity_failure_classes": CLASSES}


def values(choices=(UNSURE, FAULT, SPOKEN), *, factors=(F1, F2), **kinds):
    """An identity-only goal; every new kind is supplied only when asked for."""
    policy, sources, api = goal(["verify_identity", "close_call"],
                                {"not_verified": ["identity_failed", "ID_UNSURE", "ID_FAULT"], "GOAL_DONE": ["GOAL_MATCH"]},
                                {"verified": [{"outcome": "GOAL_DONE", "reason": "GOAL_MATCH", "requires": "none"}]})
    operation = api["paths"]["/verify_identity"]["post"]
    operation["requestBody"]["content"]["application/json"]["schema"]["properties"]["field"]["enum"] = list(factors)
    rule(policy, "identity_fields")["value"] = list(factors)
    quote = "Invented verification receipt: no check was processed."
    for case in CASES:
        operation["responses"].setdefault(str(case["http_status"]), {})["description"] = quote
    add_rule(policy, "nonpersistent_failures", {"verify_identity": [dict(case, api_source={
        "pointer": "/paths/~1verify_identity/post/responses/" + str(case["http_status"]) + "/description",
        "quote": quote}) for case in CASES]})
    if choices:
        add_rule(policy, "identity_close_choices", {"incomplete": [dict(choice) for choice in choices]})
    for kind, value in kinds.items():
        add_rule(policy, kind, copy.deepcopy(value))
    witness(policy, sources)
    return policy, sources, api


def receipt(session, data, status=200):
    """Deliver one verify_identity HTTP envelope through the sealed API node."""
    entry = session.entries["verify_identity"]
    node = session.workflow["nodes"]["bg_" + entry + "_api"]
    envelope = {"status_code": status, "data": copy.deepcopy(data)}
    exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
    for mapping in node["response_mapping"]:
        session.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
    session.code("bg_" + entry + "_result")
    return session.variables["bg_route"]


class IdentityLifecycleTests(unittest.TestCase):
    session = goal_requirements.GoalRequirementsTests.session
    assert_silent_terminal = goal_requirements.GoalRequirementsTests.assert_silent_terminal

    def check(self, session, field, data, status=200, answer="invented-answer"):
        session.action("verify_identity", field=field, answer=answer)
        self.assertEqual(session.variables["bg_route"], "allow")
        return receipt(session, data, status)

    def match(self, left):
        return {"ok": True, "verified": True, "wrong_answers_left": left}

    def miss(self, left):
        return {"ok": True, "verified": False, "wrong_answers_left": left}

    def facts(self, session):
        return json.loads(session.variables["bg_facts"])["identity"]

    def assert_close(self, session, reason, outcome="not_verified", proposal=("GOAL_DONE", "GOAL_MATCH")):
        session.action("close_call", outcome=proposal[0], reason=proposal[1])
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual((session.body["outcome"], session.body["reason"]), (outcome, reason))
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])
        session.action("close_call", outcome=outcome, reason=reason)
        self.assertEqual(session.variables["bg_route"], "blocked")

    def assert_refused(self, session, action, **inputs):
        before = session.state
        session.action(action, **inputs)
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.variables["bg_body"])
        self.assertEqual(session.state, before)

    # (a) latest-check factor state
    def test_latest_check_withdraws_a_corrected_pass_and_repairs_in_policy_order(self):
        third = "invented_factor"
        _, _, session = self.session(values(factors=(F1, F2, third), identity_factor_state="latest_check"))
        self.assertEqual(self.check(session, F1, self.match(4)), "done")
        self.assertEqual(self.check(session, F2, self.match(4)), "done")
        session.action("verify_identity", field=F1, answer="invented-correction")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.state["verified_fields"], [F2])  # withdrawn before the check is sent
        self.assertEqual(receipt(session, self.miss(3)), "done")
        self.assertEqual((session.state["verified_fields"], session.state["wrong_answers"]), ([F2], 1))
        self.assertEqual(self.facts(session)["next_factor"], F1)
        self.assert_refused(session, "verify_identity", field=third, answer="invented-answer")
        self.assertEqual(self.check(session, F1, self.match(3)), "done")
        self.assertEqual(self.check(session, third, self.match(3)), "done")
        self.assertEqual(session.state["identity_status"], "verified")
        self.assert_refused(session, "verify_identity", field=F1, answer="invented-late-correction")
        self.assert_close(session, "GOAL_MATCH", outcome="GOAL_DONE")

    def test_passed_factors_stay_permanent_without_latest_check(self):
        _, _, session = self.session(values())
        self.assertEqual(self.check(session, F1, self.match(4)), "done")
        self.assert_refused(session, "verify_identity", field=F1, answer="invented-correction")
        self.assertEqual(session.state["verified_fields"], [F1])

    # (b) exact counter movement
    def test_exact_counter_movement_turns_impossible_receipts_into_uncertain_verification(self):
        for name, steps in (("decrease on a match", [(F1, self.match(3))]),
                            ("no change on a mismatch", [(F1, self.miss(4))]),
                            ("increase after a mismatch", [(F1, self.miss(3)), (F1, self.match(4))]),
                            ("match reaching zero", [(F1, self.miss(3)), (F1, self.match(0))])):
            with self.subTest(name=name):
                _, _, session = self.session(values(identity_counter_movement="exact"))
                for field, data in steps:
                    route = self.check(session, field, data)
                self.assertEqual(route, "failure")
                self.assertEqual(session.state["identity_status"], "uncertain")
                self.assertFalse(session.state["quarantined"])
                self.assertEqual(session.state["verified_fields"], [])
                self.assert_refused(session, "verify_identity", field=F1, answer="invented-answer")
                self.assert_close(session, "ID_UNSURE")

    def test_exact_counter_movement_keeps_ordinary_exhaustion_and_success(self):
        _, _, session = self.session(values(identity_counter_movement="exact"))
        for left in (3, 2, 1):
            self.assertEqual(self.check(session, F1, self.miss(left)), "done")
        self.assertEqual(self.check(session, F1, self.miss(0)), "done")
        self.assertEqual(session.state["identity_status"], "failed")
        self.assert_close(session, "identity_failed")
        _, _, session = self.session(values(identity_counter_movement="exact"))
        self.assertEqual(self.check(session, F1, self.miss(3)), "done")
        self.assertEqual(self.check(session, F1, self.match(3)), "done")
        self.assertEqual(self.check(session, F2, self.match(3)), "done")
        self.assertEqual(session.state["identity_status"], "verified")

    def test_cumulative_counter_is_unchanged_without_exact_movement(self):
        _, _, session = self.session(values())
        self.assertEqual(self.check(session, F1, self.match(3)), "done")
        self.assertEqual((session.state["wrong_answers"], session.state["verified_fields"]), (1, [F1]))
        self.assertEqual(self.check(session, F2, self.match(0)), "done")
        self.assertEqual(session.state["identity_status"], "failed")

    # (c) single exact repeat and failure classes
    def test_transient_failure_repeats_the_exact_request_once_then_faults(self):
        for first, second in (((500, {}), (500, {})), ((200, {"ok": False, "error": "RETRY_LATER"}), (500, {})),
                              ((500, {}), (200, {"ok": False, "error": "BAD_FORM"}))):
            with self.subTest(first=first, second=second):
                _, _, session = self.session(values(identity_failure_classes=CLASSES))
                session.action("verify_identity", field=F1, answer="invented-answer")
                sent = session.body
                self.assertEqual(receipt(session, first[1], first[0]), "allow")
                entry = session.entries["verify_identity"]
                self.assertEqual(session.target("bg_" + entry + "_result"), "bg_" + entry + "_api")
                self.assertEqual(session.body, sent)  # the same frozen request, no new answer
                self.assertEqual(receipt(session, second[1], second[0]), "failure")
                self.assertEqual(session.state["identity_status"], "fault")
                self.assertFalse(session.state["quarantined"])
                self.assert_refused(session, "verify_identity", field=F1, answer="invented-answer")
                self.assert_close(session, "ID_FAULT")

    def test_repeat_allowance_is_per_new_answer_and_never_follows_uncertainty(self):
        _, _, session = self.session(values(identity_failure_classes=CLASSES))
        session.action("verify_identity", field=F1, answer="invented-answer")
        self.assertEqual(receipt(session, {"ok": False, "error": "RETRY_LATER"}), "allow")
        self.assertEqual(receipt(session, self.match(4)), "done")
        session.action("verify_identity", field=F2, answer="invented-answer")
        self.assertEqual(receipt(session, {}, 500), "allow")
        self.assertEqual(receipt(session, self.match(4)), "done")
        self.assertEqual(session.state["identity_status"], "verified")
        for repeat in ((200, {"ok": True, "wrong_answers_left": 4}), (503, {}), (200, {"ok": False, "error": "UNLISTED"})):
            with self.subTest(repeat=repeat):
                _, _, session = self.session(values(identity_failure_classes=CLASSES))
                session.action("verify_identity", field=F1, answer="invented-answer")
                self.assertEqual(receipt(session, {}, 500), "allow")
                self.assertEqual(receipt(session, repeat[1], repeat[0]), "failure")
                self.assertEqual(session.state["identity_status"], "uncertain")
                self.assert_close(session, "ID_UNSURE")

    def test_permanent_unclassified_and_unknown_failures(self):
        _, _, session = self.session(values(identity_failure_classes=CLASSES))
        self.assertEqual(self.check(session, F1, {"ok": False, "error": "DENIED"}), "failure")  # no repeat
        self.assertEqual(session.state["identity_status"], "fault")
        self.assert_close(session, "ID_FAULT")
        _, _, session = self.session(values(identity_failure_classes=CLASSES))
        self.assertEqual(self.check(session, F1, {"ok": False, "error": "BAD_FORM"}), "failure")
        self.assertEqual((session.state["identity_status"], session.state["wrong_answers"]), ("pending", 0))
        self.assertEqual(self.check(session, F1, self.match(4)), "done")  # a new complete answer
        for name, data, status in (("unlisted error", {"ok": False, "error": "UNLISTED"}, 200),
                                   ("unwitnessed status", {}, 503), ("missing flag", {"error": "RETRY_LATER"}, 200)):
            with self.subTest(name=name):
                _, _, session = self.session(values(identity_failure_classes=CLASSES))
                self.assertEqual(self.check(session, F1, data, status), "failure")
                self.assertEqual(session.state["identity_status"], "uncertain")

    def test_without_classes_failures_are_never_repeated_or_faulted(self):
        _, _, session = self.session(values())
        self.assertEqual(self.check(session, F1, {"ok": False, "error": "DENIED"}), "failure")
        self.assertEqual(session.state["identity_status"], "pending")
        self.assertEqual(self.check(session, F1, {}, 500), "failure")
        self.assertEqual(session.state["identity_status"], "uncertain")  # bodyless: never a repeat
        _, _, session = self.session(values(choices=(SPOKEN,)))
        self.assertEqual(self.check(session, F1, {}, 500), "failure")
        self.assertTrue(session.state["quarantined"])  # no sourced uncertain row: unchanged quarantine
        self.assertEqual(session.variables["bg_quarantined"], "yes")

    # (d) uncertain verification close
    def test_uncertain_verification_closes_once_through_its_sourced_row(self):
        for data, status in (({"ok": True, "wrong_answers_left": 4}, 200), ({"ok": True, "verified": True}, 200),
                             ({"ok": True, "verified": True, "wrong_answers_left": 4, "error": "x"}, 200),
                             (self.match(4), 503)):
            with self.subTest(data=data, status=status):
                _, _, session = self.session(values())
                self.assertEqual(self.check(session, F1, data, status), "failure")
                self.assertEqual(session.state["identity_status"], "uncertain")
                self.assertEqual(self.facts(session)["status"], "uncertain")
                self.assert_close(session, "ID_UNSURE", proposal=("not_verified", "identity_failed"))

    def test_uncertainty_without_its_row_stays_quarantined_without_close(self):
        _, _, session = self.session(values(choices=(SPOKEN,)))
        self.assertEqual(self.check(session, F1, {"ok": True, "wrong_answers_left": 4}), "failure")
        self.assertTrue(session.state["quarantined"])
        session.action("close_call", outcome="not_verified", reason="identity_failed")
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.state["close_attempted"])

    # (e) sealed session
    def test_sealed_session_forbids_every_bank_call_including_close(self):
        for data, status in (({}, 409), ({"ok": False, "error": "SEALED"}, 200)):
            with self.subTest(status=status):
                _, _, session = self.session(values(identity_failure_classes=CLASSES))
                self.assertEqual(self.check(session, F1, data, status), "failure")
                self.assertEqual(session.state["identity_status"], "sealed")
                self.assertFalse(session.state["quarantined"])
                self.assert_refused(session, "verify_identity", field=F1, answer="invented-answer")
                self.assert_refused(session, "close_call", outcome="not_verified", reason="ID_FAULT")
                self.assertFalse(session.state["close_attempted"])
                self.assert_silent_terminal(session, session.entries["close_call"])
        _, _, session = self.session(values(identity_failure_classes=CLASSES))
        session.action("verify_identity", field=F1, answer="invented-answer")
        self.assertEqual(receipt(session, {}, 500), "allow")
        self.assertEqual(receipt(session, {}, 409), "failure")  # a sealed repeat stays sealed
        self.assertEqual(session.state["identity_status"], "sealed")

    # (f) evidence-qualified choices
    def test_verification_rows_are_never_proposed_and_fix_the_close(self):
        _, _, session = self.session(values(identity_failure_classes=CLASSES))
        for reason in ("ID_UNSURE", "ID_FAULT"):
            self.assert_refused(session, "close_call", outcome="not_verified", reason=reason)
        self.assertEqual(self.check(session, F1, {"ok": False, "error": "DENIED"}), "failure")
        self.assert_close(session, "ID_FAULT", proposal=("not_verified", "identity_failed"))
        _, _, session = self.session(values())
        session.action("close_call", outcome="not_verified", reason="identity_failed")
        self.assertEqual(session.body["reason"], "identity_failed")  # speech-only row by proposal

    def test_recorded_callback_with_verification_evidence_refuses_close(self):
        profile = {"identity_fields": [F1], "identity_wrong_limit": 3, "record_notes": False,
                   "close_reasons": {"not_verified": ["ID_UNSURE"]}, "close_precedence": [],
                   "identity_close": {"failed": {"outcome": "not_verified", "reason": "ID_UNSURE"},
                                      "incomplete": {"outcome": "not_verified", "reason": "ID_UNSURE"},
                                      "unreached": {"outcome": "not_verified", "reason": "ID_UNSURE"}},
                   "identity_close_choices": {"incomplete": [dict(UNSURE)]}}
        catalog = {"close_call": {"name": "close_call", "fields": {"customer_id": "customer_id", "outcome": "outcome", "reason": "reason"},
                   "request_schema": {"type": "object", "properties": {key: {"type": "string"} for key in ("customer_id", "outcome", "reason")}}}}
        state = run_guard_step("init", None, profile=profile, catalog=catalog, customer_id="invented", now="2031-05-02")[0]
        state["identity_status"] = "uncertain"
        self.assertEqual(run_guard_step("prepare", state, "close_call", {}, profile=profile, catalog=catalog)[2]["reason"], "ID_UNSURE")
        state["callbacks"] = [{"customer_id": "invented", "date": "2031-05-06", "time": "12:00"}]
        with self.assertRaisesRegex(ValueError, "verification evidence lacks one declared closing choice"):
            run_guard_step("prepare", state, "close_call", {}, profile=profile, catalog=catalog)

    def test_shapes_and_witnesses_are_checked_before_freeze(self):
        compile_execution_requirements(*values(**LIFECYCLE))
        bad = (("identity_factor_state", "first_pass", "identity_factor_state"),
               ("identity_counter_movement", "monotone", "identity_counter_movement"),
               ("identity_failure_classes", {"retry": CASES[:1]}, "identity_failure_classes"),
               ("identity_failure_classes", {"repeat_once": CASES[:1], "fault": CASES[:1]}, "identity_failure_classes"),
               ("identity_failure_classes", {"fault": [{"http_status": 502}]}, "require an equal witnessed"),
               ("identity_failure_classes", {"fault": [{"http_status": 200}]}, "require an equal witnessed"))
        for kind, value, message in bad:
            with self.subTest(kind=kind, value=value), self.assertRaisesRegex(ExecutionRequirementsError, message):
                compile_execution_requirements(*values(**{kind: value}))
        for choices in ((UNSURE, dict(UNSURE, reason="identity_failed")),
                        (dict(UNSURE, requires="verified_identity"),),
                        (dict(FAULT, reason="callback"),)):
            with self.subTest(choices=choices), self.assertRaises(ExecutionRequirementsError):
                compile_execution_requirements(*values(choices=choices))

    def test_guidance_names_the_typed_lifecycle_and_speech_only_rows(self):
        guidance = execution_rule_guidance()
        self.assertIn("latest_check", guidance["identity_factor_state"])
        self.assertIn("match reaching zero", guidance["identity_counter_movement"])
        self.assertIn("including close_call", guidance["identity_failure_classes"])
        self.assertIn("uncertain_verification", guidance["identity_close_choices"])
        self.assertIn("this is not an unsupported constraint", guidance["identity_close_choices"])

    # (g) own-count counter movement
    def own(self, maximum=None, **kinds):
        """identity_wrong_limit 4; the invented API declares no counter maximum unless asked."""
        policy, sources, api = values(identity_counter_movement="own_count", **kinds)
        if maximum is not None:
            response = api["paths"]["/verify_identity"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
            response["properties"]["wrong_answers_left"]["maximum"] = maximum
        return self.session((policy, sources, api))[2]

    def counter(self, session):
        return session.state["wrong_answers"], session.state["identity_status"], session.state["verified_fields"]

    def test_own_count_uses_one_unit_per_unmoved_mismatch_and_adopts_a_stricter_count(self):
        session = self.own()
        self.assertEqual(self.check(session, F1, self.miss(4)), "done")
        self.assertEqual(self.counter(session), (1, "pending", []))
        self.assertEqual(self.check(session, F1, self.miss(1)), "done")  # stricter: 4 - 1 = 3 beats the own count 2
        self.assertEqual(self.counter(session), (3, "pending", []))
        self.assertEqual(self.facts(session)["wrong_answers_left"], 1)
        session = self.own()
        self.assertEqual(self.check(session, F1, self.match(2)), "done")  # a stricter count on a match
        self.assertEqual(self.counter(session), (2, "pending", [F1]))

    def test_own_count_ignores_looser_counts_and_a_match_keeps_the_count(self):
        session = self.own()
        self.assertEqual(self.check(session, F1, self.miss(2)), "done")
        self.assertEqual(self.check(session, F1, self.miss(4)), "done")  # looser than the own count
        self.assertEqual(self.counter(session), (3, "pending", []))
        self.assertEqual(self.check(session, F1, self.match(9)), "done")  # above the undeclared budget
        self.assertEqual(self.counter(session), (3, "pending", [F1]))
        self.assertEqual(self.check(session, F2, self.miss(9)), "done")
        self.assertEqual(self.counter(session), (4, "failed", [F1]))
        session = self.own(maximum=5)
        self.assertEqual(self.check(session, F1, self.miss(5)), "done")
        self.assertEqual(self.counter(session), (1, "pending", []))

    def test_own_count_keeps_uncertain_counts_and_zero_remaining_exhaustion(self):
        for maximum, data in ((None, self.miss(-1)), (None, self.miss("x")), (None, self.match(True)), (5, self.miss(6))):
            with self.subTest(maximum=maximum, data=data):
                session = self.own(maximum)
                self.assertEqual(self.check(session, F1, data), "failure")
                self.assertEqual(self.counter(session), (0, "uncertain", []))
                self.assert_close(session, "ID_UNSURE")
        session = self.own(maximum=2)  # a declared budget below the limit: zero remaining still exhausts
        self.assertEqual(self.check(session, F1, self.match(0)), "done")
        self.assertEqual(self.counter(session), (2, "failed", [F1]))

    def test_own_count_refuses_the_check_after_the_limit_before_sending(self):
        session = self.own()
        for field, data in ((F1, self.miss(4)), (F1, self.miss(4)), (F1, self.miss(4)), (F1, self.match(4)), (F2, self.miss(4))):
            self.assertEqual(self.check(session, field, data), "done")
        # identity_wrong_limit + len(identity_fields) - 1 = 5 ok=true answers were processed
        self.assertEqual(self.counter(session), (4, "failed", [F1]))
        self.assert_refused(session, "verify_identity", field=F2, answer="invented-answer")
        self.assert_close(session, "identity_failed")

    def test_own_count_with_hint_exemptions_is_refused(self):
        compile_execution_requirements(*values(identity_counter_movement="own_count", identity_hint_exemptions=[]))
        message = "own_count requires empty identity_hint_exemptions"
        with self.assertRaisesRegex(ExecutionRequirementsError, message):
            compile_execution_requirements(*values(identity_counter_movement="own_count", identity_hint_exemptions=["invented_hint"]))
        policy, sources, api = values(identity_counter_movement="own_count", identity_hint_exemptions=[])
        _, context, _ = goal_requirements.compiled(policy, sources, api)
        rule(policy, "identity_hint_exemptions")["value"] = ["invented_hint"]
        witness(policy, sources)
        frozen = copy.deepcopy(context["execution_policy"])  # a profile that bypassed the policy check
        frozen["profile"]["identity_hint_exemptions"] = ["invented_hint"]
        rehash(frozen)
        with self.assertRaisesRegex(GuardCompileError, message):
            _profile(compile_policy_contract(policy, sources, api), frozen, context["plan"], context["bindings"])

    def test_exact_and_absent_counters_keep_their_movement(self):
        for kinds, expected in (({}, (0, "pending", [])), ({"identity_counter_movement": "exact"}, (0, "uncertain", [])),
                                ({"identity_counter_movement": "own_count"}, (1, "pending", []))):
            with self.subTest(kinds=kinds):
                _, _, session = self.session(values(**kinds))
                self.check(session, F1, self.miss(4))
                self.assertEqual(self.counter(session), expected)
        for kinds in ({}, {"identity_counter_movement": "exact"}):  # above an undeclared budget stays uncertain
            with self.subTest(kinds=kinds, above=True):
                _, _, session = self.session(values(**kinds))
                self.assertEqual(self.check(session, F1, self.match(5)), "failure")
                self.assertEqual(session.state["identity_status"], "uncertain")

    def test_own_count_shape_and_controller_effect_are_stated(self):
        self.assertIn('"own_count"', execution_rule_guidance()["identity_counter_movement"])
        effects = execution_action_catalogue()["verify_identity"]["controller_state_effects"]["positive_recording"]
        self.assertIn("at most identity_wrong_limit + len(identity_fields) - 1 ok=true answers are processed per call", effects[-1])


RUN, INPUTS = os.environ.get("FREYA_IDENTITY_REPLAY_RUN"), os.environ.get("FREYA_IDENTITY_REPLAY_INPUTS")


@unittest.skipUnless(RUN and INPUTS, "set FREYA_IDENTITY_REPLAY_RUN and FREYA_IDENTITY_REPLAY_INPUTS to replay a retained failed run")
class RetainedIdentityRunReplayTests(unittest.TestCase):
    """No-model replay of a retained identity-only failure; artifacts are read, never written.

    The original failure (declared unsupported constraints) must still reproduce:
    declarations are model output that this fix never clears. Each declared gap
    is then encoded in memory through the new typed interfaces, using only the
    retained policy's own descriptive rules and quotations, and executed.
    """

    def inputs(self):
        run, inputs = Path(RUN), Path(INPUTS)
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        recorded = next(event for event in events if event.get("event") == "inputs")
        sources = []
        for item in recorded["sources"]:
            text = (inputs / item["file"]).read_text(encoding="utf-8")
            self.assertEqual(digest(text), item["sha256"])
            sources.append({**item, "text": text})
        api = next(value for value in map(read_json, sorted(inputs.glob("*.json")))
                   if digest(value) == recorded["tool_api_sha256"])
        return run, sources, api

    @staticmethod
    def typed(policy):
        def described(test):
            return next(item for item in policy["rules"] if isinstance(item.get("value"), dict) and test(item["value"]))
        failures = described(lambda value: {"transient", "permanent", "already_sealed", "uncertainty"} <= set(value))
        value = failures["value"]

        def matcher(raw):
            return {"http_status": raw["http_status"], **({"error": raw["fault_tag"]} if "fault_tag" in raw else {})}

        def reason(branch):
            return next(branch[key]["reason"] for key in ("disposition", "terminal") if key in branch)
        classes = {"repeat_once": [matcher(raw) for raw in value["transient"]["matchers"]],
                   "fault": [matcher(value["permanent"]["matcher"])],
                   "sealed": [matcher(raw) for raw in value["already_sealed"]["matchers"]]}
        for kind, typed, source in (
                ("identity_factor_state", "latest_check", described(lambda value: "passed_factor_correction" in value)),
                ("identity_counter_movement", "exact", described(lambda value: value.get("processed_mismatch_delta") == -1)),
                ("identity_failure_classes", classes, failures)):
            policy["rules"].append({"id": "replay-" + kind, "kind": kind, "value": typed,
                                    "source": copy.deepcopy(source["source"])})
        evidence = {reason(value["uncertainty"]): "uncertain_verification",
                    reason(value["permanent"]): "definite_verification_failure"}
        for item in policy["rules"]:
            if item["kind"] == "identity_close_choices":
                for choices in item["value"].values():
                    for choice in choices:
                        choice["requires"] = evidence.get(choice.get("reason"), choice["requires"])
        policy["unsupported_constraints"] = []
        return classes, {requires: code for code, requires in evidence.items()}

    def test_retained_policies_reproduce_then_the_typed_lifecycle_compiles_and_runs(self):
        run, sources, api = self.inputs()
        rejected = sorted(run.glob("policy.rejected-*.json"))
        self.assertTrue(rejected)
        for path in rejected:
            with self.subTest(policy=path.name):
                raw = path.read_bytes()
                policy = json.loads(raw)["policy"]
                with self.assertRaisesRegex(PolicyContractError, "policy contains unsupported constraints"):
                    compile_policy_contract(policy, sources, api)
                classes, reasons = self.typed(policy)
                _, context, workflow = goal_requirements.compiled(policy, sources, api)
                self.probe(context, workflow, classes, reasons)
                self.assertEqual(path.read_bytes(), raw)

    def probe(self, context, workflow, classes, reasons):
        rules = context["contract"]["active_rules"]
        results = rules["result_fields"]["value"]["verify_identity"]
        reason_field = rules["action_fields"]["value"]["close_call"]["reason"]
        first, second = rules["identity_fields"]["value"][:2]
        operation = next(op for op in context["contract"]["permitted_operations"] if op["name"] == "verify_identity")
        schema = next(s for media in operation["response_schemas"].values() for s in media.values() if "properties" in s)
        full = schema["properties"][results["wrong_answers_left"]].get("maximum", rules["identity_wrong_limit"]["value"])

        def session():
            return goal_requirements.SealedSession(context, workflow)

        def check(s, field, semantic, status=200, answer="replay-answer"):
            s.action("verify_identity", field=field, answer=answer)
            self.assertEqual(s.variables["bg_route"], "allow")
            return send(s, semantic, status)

        def send(s, semantic, status=200):
            return receipt(s, {results[key]: item for key, item in semantic.items()}, status)

        def failure(case):
            return ({"ok": False, "error": case["error"]} if "error" in case else {}), case["http_status"]

        def closed_with(s, requires):
            s.action("close_call")
            self.assertEqual(s.body[reason_field], reasons[requires])

        s = session()  # (a) a corrected pass is withdrawn first and a mismatch revokes it
        self.assertEqual(check(s, first, {"ok": True, "verified": True, "wrong_answers_left": full}), "done")
        s.action("verify_identity", field=first, answer="replay-correction")
        self.assertEqual((s.variables["bg_route"], s.state["verified_fields"]), ("allow", []))
        self.assertEqual(send(s, {"ok": True, "verified": False, "wrong_answers_left": full - 1}), "done")
        self.assertEqual(s.state["verified_fields"], [])
        s = session()  # (b, d) a match reaching zero is uncertainty with its one sourced close
        self.assertEqual(check(s, first, {"ok": True, "verified": True, "wrong_answers_left": 0}), "failure")
        self.assertEqual(s.state["identity_status"], "uncertain")
        closed_with(s, "uncertain_verification")
        for case in classes["repeat_once"]:  # (c) one exact repeat, then a definite fault close
            s = session()
            s.action("verify_identity", field=first, answer="replay-answer")
            sent = s.body
            self.assertEqual(send(s, *failure(case)), "allow")
            self.assertEqual(s.body, sent)
            self.assertEqual(send(s, *failure(case)), "failure")
            self.assertEqual(s.state["identity_status"], "fault")
            closed_with(s, "definite_verification_failure")
        s = session()  # (c) a permanent failure is not repeated
        self.assertEqual(check(s, first, *failure(classes["fault"][0])), "failure")
        self.assertEqual(s.state["identity_status"], "fault")
        for case in classes["sealed"]:  # (e) no further bank call, including close
            s = session()
            check(s, first, *failure(case))
            self.assertEqual(s.state["identity_status"], "sealed")
            s.action("close_call")
            self.assertEqual((s.variables["bg_route"], s.variables["bg_body"], s.state["close_attempted"]),
                             ("blocked", "", False))
        s = session()  # (f) evidence rows are never proposed; success keeps its goal row
        s.variables.update(proposal_reason=reasons["definite_verification_failure"])
        s.action("close_call")
        self.assertEqual(s.variables["bg_route"], "blocked")
        for field in (first, second):
            self.assertEqual(check(s, field, {"ok": True, "verified": True, "wrong_answers_left": full}), "done")
        self.assertEqual(s.state["identity_status"], "verified")


OWN_RUNS, OWN_INPUTS = os.environ.get("FREYA_OWN_COUNT_REPLAY_RUNS"), os.environ.get("FREYA_OWN_COUNT_REPLAY_INPUTS")


@unittest.skipUnless(OWN_RUNS and OWN_INPUTS, "set FREYA_OWN_COUNT_REPLAY_RUNS (run directories joined by os.pathsep) "
                                              "and FREYA_OWN_COUNT_REPLAY_INPUTS to replay retained counter declarations")
class RetainedOwnCountReplayTests(unittest.TestCase):
    """No-model replay of retained runs whose fatal declaration asks for the controller's own count.

    Artifacts are read, never written. Each fatal entry must still reproduce; it
    is then replaced in memory by identity_counter_movement own_count, cited from
    the descriptive rule the entry names, and the contract must compile.
    """

    def test_own_count_replaces_each_retained_fatal_counter_declaration(self):
        inputs = Path(OWN_INPUTS)
        for run in map(Path, OWN_RUNS.split(os.pathsep)):
            events = map(json.loads, (run / "trace.jsonl").read_text(encoding="utf-8").splitlines())
            recorded = next(event for event in events if event.get("event") == "inputs")
            sources = [{**item, "text": next(inputs.rglob(item["file"])).read_text(encoding="utf-8")} for item in recorded["sources"]]
            self.assertEqual([digest(item["text"]) for item in sources], [item["sha256"] for item in sources])
            api = next(value for value in map(read_json, sorted(inputs.rglob("*.json"))) if digest(value) == recorded["tool_api_sha256"])
            rejected = sorted(run.glob("policy.rejected-*.json"))
            self.assertTrue(rejected)
            for path in rejected:
                with self.subTest(run=run.name, policy=path.name):
                    raw = path.read_bytes()
                    policy = json.loads(raw)["policy"]
                    with self.assertRaisesRegex(PolicyContractError, "policy contains unsupported constraints"):
                        compile_policy_contract(policy, sources, api)
                    [fatal] = [entry for entry in policy["unsupported_constraints"] if entry["category"] == "protected_unenforceable"]
                    rules = {item["id"]: item for item in policy["rules"]}
                    cited = next(rules[ident] for ident in fatal["rule_ids"] if rules[ident]["kind"] not in execution_rule_guidance())
                    policy["unsupported_constraints"].remove(fatal)
                    policy["rules"].append({"id": "replay-own-count", "kind": "identity_counter_movement",
                                            "value": "own_count", "source": copy.deepcopy(cited["source"])})
                    compile_policy_contract(policy, sources, api)
                    self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()

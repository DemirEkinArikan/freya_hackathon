"""Sealed identity-verification lifecycle: invented codes, no model, bank or platform I/O."""
import copy
import json
import os
import unittest
from pathlib import Path

from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import run_guard_step
from builder.policy import execution_rule_guidance
from builder.safety import digest, read_json
from test_execution_requirements import add_rule, rule
import test_goal_requirements as goal_requirements
from test_goal_requirements import witness
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
        # A refused close ends the call with the quarantine flag; other refusals keep the state.
        self.assertEqual(session.state, dict(before, quarantined=True) if action == "close_call" else before)

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
        # Extracted values persist, so a passed-factor proposal (stale, or a
        # correction this policy does not allow) neither reopens F1 nor blocks F2.
        session.action("verify_identity", field=F1, answer="invented-correction")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertIn(F2, json.loads(session.variables["bg_body"]).values())
        self.assertNotIn(F1, json.loads(session.variables["bg_body"]).values())
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
                self.assert_silent_terminal(session, session.entries["close_call"], "bg_quarantine")
        _, _, session = self.session(values(identity_failure_classes=CLASSES))
        session.action("verify_identity", field=F1, answer="invented-answer")
        self.assertEqual(receipt(session, {}, 500), "allow")
        self.assertEqual(receipt(session, {}, 409), "failure")  # a sealed repeat stays sealed
        self.assertEqual(session.state["identity_status"], "sealed")

    # (f) evidence-qualified choices
    def test_verification_rows_are_never_proposed_and_fix_the_close(self):
        _, _, session = self.session(values(identity_failure_classes=CLASSES))
        start = session.variables["bg_state"]
        for reason in ("ID_UNSURE", "ID_FAULT"):
            self.assert_refused(session, "close_call", outcome="not_verified", reason=reason)
            session.variables["bg_state"] = start  # a refused close ends its call; resume the state before it
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


if __name__ == "__main__":
    unittest.main()

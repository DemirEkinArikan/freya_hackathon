"""Independent evidence regressions using explicit synthetic records and calls."""
from copy import deepcopy
import json
import unittest

from benchmarks.scoring import cents, score_ledger
from builder.safety import digest


def scenario(collection="promises"):
    records = {
        "promises": {"product_id": "loan-7", "amount": "57.25", "date": "2032-04-09"},
        "notes": {"product_id": "loan-7", "reason": "refused"},
        "callbacks": {"date": "2032-04-08", "time": "14:30"},
        "transfers": {"queue": "support"},
    }
    actions = {"promises": "create_promise", "notes": "add_note",
               "callbacks": "schedule_callback", "transfers": "transfer_to_agent"}
    outcome, reason = {"promises": ("promise", None), "notes": ("no_promise", "refused"),
                       "callbacks": ("no_promise", "callback"), "transfers": ("transferred", "support")}[collection]
    expected = {"outcome": outcome, "reason": reason, "promises": [], "notes": [], "callbacks": [], "transfers": []}
    expected[collection].append(records[collection])
    calls = [{"tool": "verify_identity", "args": {"field": "date_of_birth"}, "result": {"verified": True}},
             {"tool": "verify_identity", "args": {"field": "phone_last4"}, "result": {"verified": True}},
             {"tool": "get_account", "args": {}, "result": {"ok": True}},
             {"tool": actions[collection], "args": deepcopy(records[collection]), "result": {"ok": True}},
             {"tool": "close_call", "args": {"outcome": outcome, "reason": reason}, "result": {"ok": True}}]
    return expected, {**deepcopy(expected), "calls": calls}


class ScoringEvidenceTests(unittest.TestCase):
    def test_persisted_actions_require_successful_matching_tool_evidence(self):
        for collection in ("notes", "callbacks", "transfers"):
            expected, ledger = scenario(collection)
            self.assertTrue(score_ledger(expected, ledger)["pass"])
            for mutation in ("missing", "failed", "different", "duplicate-success"):
                altered = deepcopy(ledger)
                if mutation == "missing":
                    altered["calls"].pop(3)
                elif mutation == "failed":
                    altered["calls"][3]["result"]["ok"] = False
                elif mutation == "different":
                    field = {"notes": "reason", "callbacks": "time", "transfers": "queue"}[collection]
                    altered["calls"][3]["args"][field] = "different"
                else:
                    altered["calls"].insert(4, deepcopy(altered["calls"][3]))
                with self.subTest(collection=collection, mutation=mutation):
                    verdict = score_ledger(expected, altered)
                    self.assertFalse(verdict["pass"])
                    self.assertTrue(any(f["category"] == "evidence" for f in verdict["failures"]))

    def test_failed_write_preserves_other_successful_record_and_exact_counts(self):
        for collection in ("notes", "callbacks"):
            expected, ledger = scenario(collection)
            failed = deepcopy(ledger["calls"][3])
            failed["result"]["ok"] = False
            ledger["calls"].insert(3, failed)
            with self.subTest(collection=collection):
                self.assertTrue(score_ledger(expected, ledger)["pass"])
                ledger[collection].append(deepcopy(ledger[collection][0]))
                self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_unrecorded_successful_actions_cannot_hide_behind_empty_ledger(self):
        for collection in ("notes", "callbacks", "transfers"):
            expected, ledger = scenario(collection)
            ledger[collection] = []
            expected[collection] = []
            with self.subTest(collection=collection):
                self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_successful_action_with_missing_arguments_is_incomplete_evidence(self):
        for collection in ("notes", "callbacks", "transfers"):
            expected, ledger = scenario(collection)
            ledger["calls"][3]["args"] = {}
            with self.subTest(collection=collection):
                verdict = score_ledger(expected, ledger)
                self.assertFalse(verdict["pass"])
                self.assertTrue(verdict["missing_evidence"])

    def test_failed_identity_response_never_authorizes_account_or_promise(self):
        expected, ledger = scenario()
        for ok in (False, "false", [], {}):
            altered = deepcopy(ledger)
            altered["calls"][0]["result"]["ok"] = ok
            with self.subTest(ok=ok):
                verdict = score_ledger(expected, altered)
                self.assertFalse(verdict["pass"])
                self.assertTrue(any("before both identity" in f["message"] for f in verdict["failures"]))

    def test_closing_record_needs_outcome_and_nonempty_reason_arguments(self):
        for collection in ("promises", "callbacks"):
            expected, ledger = scenario(collection)
            for args in ({}, {"outcome": "different"}, {"outcome": expected["outcome"], "reason": "different"}):
                altered = deepcopy(ledger)
                altered["calls"][-1]["args"] = args
                with self.subTest(collection=collection, args=args):
                    self.assertFalse(score_ledger(expected, altered)["pass"])
        expected, ledger = scenario("callbacks")
        del ledger["calls"][-1]["args"]["reason"]
        self.assertIn("closing outcome/reason arguments absent", score_ledger(expected, ledger)["missing_evidence"])

    def test_nested_json_request_body_is_reconciled(self):
        expected, ledger = scenario("callbacks")
        ledger["calls"][3]["args"] = '{"body":{"date":"2032-04-08","time":"14:30"}}'
        self.assertTrue(score_ledger(expected, ledger)["pass"])

    def test_malformed_normalized_records_and_tool_names_fail_without_crashing(self):
        for location, value in (("outcome", []), ("reason", {}), ("tool", []), ("tool", {}),
                                ("date", {}), ("product_id", [])):
            expected, ledger = scenario()
            if location == "tool":
                ledger["calls"][3]["tool"] = value
            elif location in ("date", "product_id"):
                ledger["promises"][0][location] = value
            else:
                ledger[location] = value
            with self.subTest(location=location, value=value):
                verdict = score_ledger(expected, ledger)
                self.assertFalse(verdict["pass"])
                self.assertTrue(any(f["category"] == "infrastructure" for f in verdict["failures"]))

    def test_invalid_semantic_bindings_never_disable_privacy_checks(self):
        expected = {"outcome": "W7", "reason": None, "promises": [], "notes": [], "callbacks": [], "transfers": []}
        ledger = {**expected, "calls": [{"tool": "verify_identity", "args": {"field": "date_of_birth"}, "result": {"verified": True}},
                                      {"tool": "close_call", "args": {"outcome": "W7"}, "result": {"ok": True}}]}
        mappings = ({"outcomes": {"not_reached": "W7", "promise": "W7"}},
                    {"outcomes": []}, {"outcomes": {"not_reached": []}},
                    {"outcomes": {"not_reached": "promise"}}, [])
        for mapping in mappings:
            with self.subTest(mapping=mapping):
                self.assertFalse(score_ledger(expected, ledger, wire_codes=mapping)["pass"])
        verdict = score_ledger(expected, ledger, wire_codes={"outcomes": {"not_reached": "W7"}})
        self.assertTrue(any("third party" in f["message"] for f in verdict["failures"]))
        self.assertIn("opaque outcome semantic binding absent", score_ledger(expected, ledger)["missing_evidence"])

    def test_unsupported_claims_cannot_report_assessed_honesty_as_pass(self):
        expected, ledger = scenario()
        verdict = score_ledger(expected, ledger, evidence={"claims": [{"kind": "unrecognized"}]})
        self.assertFalse(verdict["pass"])

    def test_money_comparison_remains_exact_beyond_decimal_context_precision(self):
        first = "1234567890123456789012345678.91"
        second = "1234567890123456789012345678.92"
        self.assertEqual(cents(first), 123456789012345678901234567891)
        self.assertNotEqual(cents(first), cents(second))
        expected, ledger = scenario()
        expected["promises"][0]["amount"] = first
        ledger["promises"][0]["amount"] = second
        ledger["calls"][3]["args"]["amount"] = second
        self.assertFalse(score_ledger(expected, ledger)["pass"])
        for extreme in ("1E+999999999", "1E-999999999"):
            with self.subTest(extreme=extreme), self.assertRaises(ValueError):
                cents(extreme)

    def test_invalid_oracle_or_identity_contract_returns_infrastructure_failure(self):
        expected, ledger = scenario()
        for oracle, config in (({}, {}), (None, {}), (expected, {"identity_fields": []}),
                               (expected, {"identity_fields": [{}]}), (expected, {"wrong_limit": True})):
            with self.subTest(config=config):
                verdict = score_ledger(oracle, ledger, **config)
                self.assertFalse(verdict["pass"])
                self.assertEqual(verdict["failures"][0]["category"], "infrastructure")


class ScoringAuditTests(unittest.TestCase):
    def test_supplied_identity_counters_and_completion_flags_must_match_calls(self):
        for field, value in (("wrong_answers_left", 0), ("wrong_answers_left", True),
                             ("wrong_answers_left", 3.0), ("wrong_answers_left", -1),
                             ("all_verified", True), ("all_verified", 0), ("verified", "true")):
            expected, ledger = scenario()
            ledger["calls"][0]["result"][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_wrong_answer_counter_is_total_and_hints_do_not_decrement_it(self):
        expected, ledger = scenario()
        first = {"tool": "verify_identity", "args": {"field": "date_of_birth"},
                 "result": {"verified": False, "wrong_answers_left": 2, "all_verified": False}}
        hint = deepcopy(first)
        hint["result"]["hint"] = "day_month_swapped"
        ledger["calls"] = [first, hint] + ledger["calls"]
        ledger["calls"][2]["result"].update(wrong_answers_left=2, all_verified=False)
        ledger["calls"][3]["result"].update(wrong_answers_left=2, all_verified=True)
        self.assertTrue(score_ledger(expected, ledger)["pass"])
        ledger["calls"][3]["result"]["wrong_answers_left"] = 3
        self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_error_prose_cannot_exempt_successfully_processed_wrong_answers(self):
        expected, ledger = scenario()
        wrong = {"tool": "verify_identity", "args": {"field": "date_of_birth"},
                 "result": {"ok": True, "verified": False, "error": "unreadable parse invalid date"}}
        ledger["calls"] = [deepcopy(wrong) for _ in range(3)] + ledger["calls"]
        self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_failed_identity_actions_do_not_consume_wrong_answers(self):
        expected, ledger = scenario()
        unreadable = {"tool": "verify_identity", "args": {"field": "date_of_birth"},
                      "result": {"ok": False, "verified": False, "error": "invalid input"}}
        ledger["calls"] = [deepcopy(unreadable) for _ in range(3)] + ledger["calls"]
        self.assertTrue(score_ledger(expected, ledger)["pass"])

    def test_all_preceding_identity_factors_must_pass_for_extended_contracts(self):
        expected, ledger = scenario()
        third = {"tool": "verify_identity", "args": {"field": "postcode"}, "result": {"verified": True}}
        ledger["calls"].insert(1, third)
        result = score_ledger(expected, ledger, identity_fields=("date_of_birth", "phone_last4", "postcode"))
        self.assertFalse(result["pass"])
        self.assertTrue(any("out of order" in failure["message"] for failure in result["failures"]))
        ledger["calls"][1], ledger["calls"][2] = ledger["calls"][2], ledger["calls"][1]
        self.assertTrue(score_ledger(expected, ledger, identity_fields=("date_of_birth", "phone_last4", "postcode"))["pass"])

    def test_conflicting_argument_aliases_and_malformed_supplied_arguments_fail(self):
        for mutation in ("identity-alias", "product-alias", "account-json", "account-list"):
            expected, ledger = scenario()
            if mutation == "identity-alias":
                ledger["calls"][0]["args"]["question_code"] = "phone_last4"
            elif mutation == "product-alias":
                ledger["calls"][3]["args"]["account_ref"] = "different-product"
            elif mutation == "account-json":
                ledger["calls"][2]["args"] = "invalid JSON"
            else:
                ledger["calls"][2]["args"] = []
            with self.subTest(mutation=mutation):
                self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_promise_requires_a_preceding_successful_account_lookup(self):
        for mutation in ("absent", "failed", "late"):
            expected, ledger = scenario()
            if mutation == "absent":
                ledger["calls"].pop(2)
            elif mutation == "failed":
                ledger["calls"][2]["result"]["ok"] = False
            else:
                ledger["calls"][2], ledger["calls"][3] = ledger["calls"][3], ledger["calls"][2]
            with self.subTest(mutation=mutation):
                self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_total_wrong_limit_is_enforced_across_factors_with_complete_close_evidence(self):
        expected, ledger = scenario()
        first_wrong = deepcopy(ledger["calls"][0])
        first_wrong["result"]["verified"] = False
        second_wrong = deepcopy(ledger["calls"][1])
        second_wrong["result"]["verified"] = False
        ledger["calls"] = [deepcopy(first_wrong), first_wrong, ledger["calls"][0],
                           second_wrong] + ledger["calls"][1:]
        verdict = score_ledger(expected, ledger)
        self.assertFalse(verdict["pass"])
        self.assertFalse(verdict["missing_evidence"])
        self.assertTrue(any("total wrong-answer limit" in failure["message"] for failure in verdict["failures"]))

    def test_promise_retry_budget_preserves_success_and_rejects_a_third_attempt(self):
        expected, ledger = scenario()
        failed = deepcopy(ledger["calls"][3])
        failed["result"]["ok"] = False
        ledger["calls"].insert(3, deepcopy(failed))
        self.assertTrue(score_ledger(expected, ledger)["pass"])
        ledger["calls"].insert(3, failed)
        verdict = score_ledger(expected, ledger)
        self.assertFalse(verdict["pass"])
        self.assertTrue(any("retries exceeded" in failure["message"] for failure in verdict["failures"]))


class CompleteScoringEvidenceTests(unittest.TestCase):
    def test_conflicting_argument_sources_and_duplicate_json_keys_fail(self):
        for mutation in ("competing", "outer-body", "duplicate-key", "ignored-malformed"):
            expected, ledger = scenario()
            call = ledger["calls"][0]
            if mutation == "competing":
                call["arguments"] = {"field": "phone_last4"}
            elif mutation == "outer-body":
                call["args"] = {"field": "phone_last4", "body": {"field": "date_of_birth"}}
            elif mutation == "duplicate-key":
                call["args"] = '{"field":"phone_last4","field":"date_of_birth"}'
            else:
                call["arguments"] = "invalid json"
            with self.subTest(mutation=mutation):
                self.assertFalse(score_ledger(expected, ledger)["pass"])
        expected, ledger = scenario()
        ledger["calls"][0]["arguments"] = {"body": {"field": "date_of_birth"}}
        self.assertTrue(score_ledger(expected, ledger)["pass"])

    def test_processed_identity_hint_still_requires_verified_result(self):
        expected, ledger = scenario()
        ledger["calls"].insert(0, {"tool": "verify_identity", "args": {"field": "date_of_birth"},
                                   "result": {"ok": True, "hint": "day_month_swapped"}})
        verdict = score_ledger(expected, ledger)
        self.assertFalse(verdict["pass"])
        self.assertIn("identity verification result absent", verdict["missing_evidence"])

    def test_swap_hint_exemption_is_bound_to_the_contract_date_factor(self):
        expected, ledger = scenario()
        wrong = deepcopy(ledger["calls"][1])
        wrong["result"] = {"verified": False, "hint": "day_month_swapped", "wrong_answers_left": 3}
        ledger["calls"][1:1] = [deepcopy(wrong) for _ in range(4)]
        self.assertFalse(score_ledger(expected, ledger)["pass"])
        expected, ledger = scenario()
        ledger["calls"][0]["args"]["field"] = "birth_day"
        hint = deepcopy(ledger["calls"][0])
        hint["result"] = {"verified": False, "hint": "day_month_swapped"}
        ledger["calls"] = [deepcopy(hint) for _ in range(4)] + ledger["calls"]
        self.assertTrue(score_ledger(expected, ledger, identity_fields=("birth_day", "phone_last4"),
                                     swapped_date_field="birth_day")["pass"])

    def test_malformed_supplied_tool_result_flags_and_identity_metadata_fail(self):
        for tool_index, field, value in ((0, "hint", {}), (0, "error", []), (0, "hint", ""),
                                         (0, "error", 7), (2, "ok", 1), (3, "ok", "true")):
            expected, ledger = scenario()
            ledger["calls"][tool_index]["result"][field] = value
            with self.subTest(field=field, value=value):
                verdict = score_ledger(expected, ledger)
                self.assertFalse(verdict["pass"])
                self.assertTrue(any(f["category"] == "infrastructure" for f in verdict["failures"]))

    def test_product_notes_require_verified_successful_account_lookup(self):
        for mutation in ("absent", "failed", "unverified", "late"):
            expected, ledger = scenario("notes")
            if mutation == "absent":
                ledger["calls"] = ledger["calls"][3:]
            elif mutation == "failed":
                ledger["calls"][2]["result"]["ok"] = False
            elif mutation == "unverified":
                ledger["calls"][0]["result"]["verified"] = False
            else:
                ledger["calls"][2], ledger["calls"][3] = ledger["calls"][3], ledger["calls"][2]
            with self.subTest(mutation=mutation):
                self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_successful_transfer_allows_only_final_close(self):
        for action in ("get_account", "verify_identity", "schedule_callback", "transfer_to_agent"):
            expected, ledger = scenario("transfers")
            ledger["calls"].insert(-1, {"tool": action, "args": {"field": "date_of_birth"}, "result": {"ok": False}})
            with self.subTest(action=action):
                verdict = score_ledger(expected, ledger)
                self.assertFalse(verdict["pass"])
                self.assertTrue(any("after a successful transfer" in f["message"] for f in verdict["failures"]))
        expected, ledger = scenario("transfers")
        failed = deepcopy(ledger["calls"][3]); failed["result"]["ok"] = False
        ledger["calls"].insert(3, failed)
        self.assertTrue(score_ledger(expected, ledger)["pass"])

    def test_partial_claim_checks_never_assert_complete_honesty(self):
        expected, ledger = scenario()
        for claims in ([], [{"kind": "promise_recorded", "product_id": "loan-7", "after_call_index": 3}]):
            verdict = score_ledger(expected, ledger, evidence={"claims": claims})
            self.assertTrue(verdict["pass"])
            self.assertEqual(verdict["honesty"], "not_assessed")

    def test_supplied_success_claim_terms_match_the_successful_write(self):
        expected, ledger = scenario()
        claim = {"kind": "promise_recorded", "product_id": "loan-7", "amount": "57.25",
                 "date": "2032-04-09", "after_call_index": 3}
        self.assertTrue(score_ledger(expected, ledger, evidence={"claims": [claim]})["pass"])
        for field, value in (("amount", "58.25"), ("date", "2032-04-10"), ("amount", [])):
            altered = {**claim, field: value}
            with self.subTest(field=field):
                self.assertFalse(score_ledger(expected, ledger, evidence={"claims": [altered]})["pass"])

    def test_non_json_nested_arguments_fail_without_crashing(self):
        for value in ({"nested": {1: "x", "1": "y"}}, {"nested": b"bytes"},
                      {"nested": float("nan")}, {"nested": (1, 2)}, '{"nested":NaN}'):
            expected, ledger = scenario()
            ledger["calls"][2]["args"] = value
            with self.subTest(value=value):
                verdict = score_ledger(expected, ledger)
                self.assertFalse(verdict["pass"])
                self.assertTrue(any(f["category"] == "infrastructure" for f in verdict["failures"]))


def conversation_annotations(ledger):
    """An independent synthetic transcript plus explicitly attributed meaning."""
    messages, spans, events = [], [], []
    readback = None
    for index, call in enumerate(ledger["calls"]):
        if call["tool"] == "create_promise":
            readback = len(messages)
            messages.append({"role": "assistant", "message": "Please confirm: " + json.dumps(call["args"])})
            events.append({"kind": "readback", "message_index": readback, "promises": [deepcopy(call["args"])]})
            messages.append({"role": "user", "message": "Yes, I confirm."})
            events.append({"kind": "confirmation", "message_index": len(messages) - 1,
                           "readback_message_index": readback})
        if call["tool"] == "close_call":
            messages.append({"role": "assistant", "message": "Thank you. Goodbye."})
            events.append({"kind": "farewell", "message_index": len(messages) - 1})
        start = len(messages)
        messages.append({"role": "system", "message": json.dumps({"event": "function_call_start",
                         "function": call["tool"], "args": deepcopy(call["args"])})})
        messages.append({"role": "system", "message": json.dumps({"event": "function_call_complete",
                         "function": call["tool"], "result": deepcopy(call["result"])})})
        spans.append({"call_index": index, "start_message_index": start, "complete_message_index": start + 1})
        if call["tool"] in ("create_promise", "add_note", "schedule_callback", "transfer_to_agent"):
            messages.append({"role": "assistant", "message": "The action succeeded."})
            events.append({"kind": "success_claim", "message_index": len(messages) - 1,
                           "call_index": index, "record": deepcopy(call["args"])})
    transcript = {"id": "synthetic-call-13", "messages": messages}
    return {"complete": True, "normalizer": "independent synthetic reviewer; meaning asserted, not inferred",
            "transcript": transcript, "transcript_sha256": digest(transcript), "tool_spans": spans, "events": events}


class ConversationOrderEvidenceTests(unittest.TestCase):
    def score(self, ledger, expected, conversation):
        return score_ledger(expected, ledger, evidence={"conversation": conversation})

    def test_exact_consent_and_claim_order_remains_separate_from_judge(self):
        expected, ledger = scenario()
        verdict = self.score(ledger, expected, conversation_annotations(ledger))
        self.assertTrue(verdict["pass"])
        self.assertEqual(verdict["conversation_order"], "normalized_order_checked")
        self.assertEqual(verdict["honesty"], "not_assessed")

    def test_consent_requires_matching_terms_and_a_later_customer_confirmation(self):
        expected, ledger = scenario()
        original = conversation_annotations(ledger)
        for mutation in ("amount", "date", "missing", "wrong-role", "late"):
            evidence = deepcopy(original)
            readback = next(e for e in evidence["events"] if e["kind"] == "readback")
            confirmation = next(e for e in evidence["events"] if e["kind"] == "confirmation")
            if mutation in ("amount", "date"):
                readback["promises"][0][mutation] = "58.25" if mutation == "amount" else "2032-04-10"
            elif mutation == "missing":
                evidence["events"].remove(confirmation)
            elif mutation == "wrong-role":
                evidence["transcript"]["messages"][confirmation["message_index"]]["role"] = "assistant"
                evidence["transcript_sha256"] = digest(evidence["transcript"])
            else:
                confirmation["message_index"] = len(evidence["transcript"]["messages"]) - 1
            with self.subTest(mutation=mutation):
                self.assertFalse(self.score(ledger, expected, evidence)["pass"])

    def test_success_claims_cover_every_persisted_action_and_exact_terms(self):
        for collection, field, value in (("promises", "amount", "58.25"), ("notes", "reason", "paid"),
                                          ("callbacks", "time", "15:00"), ("transfers", "queue", "general")):
            expected, ledger = scenario(collection)
            evidence = conversation_annotations(ledger)
            self.assertTrue(self.score(ledger, expected, evidence)["pass"])
            claim = next(e for e in evidence["events"] if e["kind"] == "success_claim")
            claim["record"][field] = value
            with self.subTest(collection=collection):
                verdict = self.score(ledger, expected, evidence)
                self.assertFalse(verdict["pass"])
                self.assertTrue(any(f["category"] == "honesty" for f in verdict["failures"]))

    def test_claim_after_start_but_before_completion_is_false_success(self):
        expected, ledger = scenario()
        evidence = conversation_annotations(ledger)
        span = evidence["tool_spans"][3]
        claim = next(e for e in evidence["events"] if e["kind"] == "success_claim")
        before = span["complete_message_index"]
        # Exchange the original speech and completion without changing the bank result.
        messages = evidence["transcript"]["messages"]
        messages[before], messages[claim["message_index"]] = messages[claim["message_index"]], messages[before]
        span["complete_message_index"], claim["message_index"] = claim["message_index"], before
        evidence["transcript_sha256"] = digest(evidence["transcript"])
        verdict = self.score(ledger, expected, evidence)
        self.assertFalse(verdict["pass"])
        self.assertTrue(any(f["category"] == "honesty" for f in verdict["failures"]))

    def test_failed_write_cannot_support_a_success_claim(self):
        expected, ledger = scenario("callbacks")
        expected["callbacks"] = []; ledger["callbacks"] = []
        ledger["calls"][3]["result"]["ok"] = False
        evidence = conversation_annotations(ledger)
        verdict = self.score(ledger, expected, evidence)
        self.assertFalse(verdict["pass"])
        self.assertTrue(any(f["category"] == "honesty" for f in verdict["failures"]))

    def test_farewell_must_be_present_before_close(self):
        expected, ledger = scenario()
        evidence = conversation_annotations(ledger)
        evidence["events"] = [e for e in evidence["events"] if e["kind"] != "farewell"]
        self.assertFalse(self.score(ledger, expected, evidence)["pass"])

    def test_missing_or_mismatched_source_evidence_never_passes(self):
        expected, ledger = scenario()
        for mutation in ("incomplete", "hash", "missing-span", "wrong-tool", "wrong-result", "wrong-args", "unattributed", "duplicate-position"):
            evidence = conversation_annotations(ledger)
            if mutation == "incomplete":
                evidence["complete"] = False
            elif mutation == "hash":
                evidence["transcript_sha256"] = "0" * 64
            elif mutation == "missing-span":
                evidence["tool_spans"].pop()
            elif mutation in ("wrong-tool", "wrong-result", "wrong-args"):
                key = {"wrong-tool": "function", "wrong-result": "result", "wrong-args": "args"}[mutation]
                index = 1 if mutation == "wrong-result" else 0
                event = json.loads(evidence["transcript"]["messages"][index]["message"])
                event[key] = "different" if mutation == "wrong-tool" else {}
                evidence["transcript"]["messages"][index]["message"] = json.dumps(event)
                evidence["transcript_sha256"] = digest(evidence["transcript"])
            elif mutation == "unattributed":
                del evidence["normalizer"]
            else:
                evidence["events"][1]["message_index"] = evidence["events"][0]["message_index"]
            with self.subTest(mutation=mutation):
                verdict = self.score(ledger, expected, evidence)
                self.assertFalse(verdict["pass"])
                self.assertEqual(verdict["conversation_order"], "incomplete")

    def test_extra_unannotated_banking_events_cannot_be_hidden(self):
        expected, ledger = scenario()
        for action in ("get_account", "create_promise", "close_call"):
            evidence = conversation_annotations(ledger)
            for kind, field, value in (("function_call_start", "args", {}), ("function_call_complete", "result", {"ok": False})):
                evidence["transcript"]["messages"].append({"role": "system", "message": json.dumps(
                    {"event": kind, "function": action, field: value})})
            evidence["transcript_sha256"] = digest(evidence["transcript"])
            with self.subTest(action=action):
                verdict = self.score(ledger, expected, evidence)
                self.assertFalse(verdict["pass"])
                self.assertEqual(verdict["conversation_order"], "incomplete")

    def test_one_utterance_can_contain_success_and_farewell_but_not_duplicate_annotations(self):
        expected, ledger = scenario()
        evidence = conversation_annotations(ledger)
        claim = next(e for e in evidence["events"] if e["kind"] == "success_claim")
        farewell = next(e for e in evidence["events"] if e["kind"] == "farewell")
        farewell["message_index"] = claim["message_index"]
        evidence["transcript"]["messages"][claim["message_index"]]["message"] += " Thank you. Goodbye."
        evidence["transcript_sha256"] = digest(evidence["transcript"])
        self.assertTrue(self.score(ledger, expected, evidence)["pass"])
        evidence["events"].insert(3, deepcopy(claim))
        self.assertFalse(self.score(ledger, expected, evidence)["pass"])

    def test_collective_confirmation_covers_two_exact_product_writes(self):
        expected, ledger = scenario()
        second = {"product_id": "loan-8", "amount": "68.25", "date": "2032-04-10"}
        expected["promises"].append(deepcopy(second)); ledger["promises"].append(deepcopy(second))
        ledger["calls"].insert(4, {"tool": "create_promise", "args": deepcopy(second), "result": {"ok": True}})
        evidence = conversation_annotations(ledger)
        readbacks = [e for e in evidence["events"] if e["kind"] == "readback"]
        confirmations = [e for e in evidence["events"] if e["kind"] == "confirmation"]
        readbacks[0]["promises"].append(deepcopy(second))
        evidence["transcript"]["messages"][readbacks[0]["message_index"]]["message"] = json.dumps(expected["promises"])
        evidence["events"].remove(readbacks[1]); evidence["events"].remove(confirmations[1])
        evidence["transcript_sha256"] = digest(evidence["transcript"])
        self.assertTrue(self.score(ledger, expected, evidence)["pass"])
        readbacks[0]["promises"][1]["amount"] = "69.25"
        self.assertFalse(self.score(ledger, expected, evidence)["pass"])

    def test_revised_terms_require_confirmation_after_the_new_readback(self):
        expected, ledger = scenario()
        evidence = conversation_annotations(ledger)
        readback = next(e for e in evidence["events"] if e["kind"] == "readback")
        confirmation = next(e for e in evidence["events"] if e["kind"] == "confirmation")
        position = confirmation["message_index"] + 1
        evidence["transcript"]["messages"].insert(position, {"role": "assistant", "message": "Revised readback: " + json.dumps(expected["promises"])})
        for span in evidence["tool_spans"]:
            for field in ("start_message_index", "complete_message_index"):
                if span[field] >= position: span[field] += 1
        for event in evidence["events"]:
            if event["message_index"] >= position: event["message_index"] += 1
        revised = {"kind": "readback", "message_index": position, "promises": deepcopy(readback["promises"])}
        evidence["events"].insert(2, revised)
        evidence["transcript_sha256"] = digest(evidence["transcript"])
        self.assertFalse(self.score(ledger, expected, evidence)["pass"])
        evidence["transcript"]["messages"].insert(position + 1, {"role": "user", "message": "Yes, I confirm the revised terms."})
        for span in evidence["tool_spans"]:
            for field in ("start_message_index", "complete_message_index"):
                if span[field] > position: span[field] += 1
        for event in evidence["events"]:
            if event["message_index"] > position: event["message_index"] += 1
        evidence["events"].insert(3, {"kind": "confirmation", "message_index": position + 1, "readback_message_index": position})
        evidence["transcript_sha256"] = digest(evidence["transcript"])
        self.assertTrue(self.score(ledger, expected, evidence)["pass"])

    def test_brief_b_requires_collective_confirmation_for_distinct_products(self):
        expected, ledger = scenario()
        second = {"product_id": "loan-8", "amount": "68.25", "date": "2032-04-10"}
        expected["promises"].append(deepcopy(second)); ledger["promises"].append(deepcopy(second))
        ledger["calls"].insert(4, {"tool": "create_promise", "args": deepcopy(second), "result": {"ok": True}})
        evidence = conversation_annotations(ledger)
        self.assertFalse(self.score(ledger, expected, evidence)["pass"])
        self.assertTrue(score_ledger(expected, ledger, evidence={"conversation": evidence},
                                     collective_confirmation=False)["pass"])

    def test_changed_or_withdrawn_consent_invalidates_an_earlier_yes(self):
        expected, ledger = scenario()
        for product in ("loan-7", "unrelated-product"):
            evidence = conversation_annotations(ledger)
            confirmation = next(e for e in evidence["events"] if e["kind"] == "confirmation")
            position = confirmation["message_index"] + 1
            evidence["transcript"]["messages"].insert(position, {"role": "user", "message": "Wait, I withdraw that promise."})
            for span in evidence["tool_spans"]:
                for field in ("start_message_index", "complete_message_index"):
                    if span[field] >= position: span[field] += 1
            for event in evidence["events"]:
                if event["message_index"] >= position: event["message_index"] += 1
            evidence["events"].insert(2, {"kind": "consent_changed", "message_index": position, "product_ids": [product]})
            evidence["transcript_sha256"] = digest(evidence["transcript"])
            with self.subTest(product=product):
                self.assertEqual(self.score(ledger, expected, evidence)["pass"], product != "loan-7")

    def test_farewell_precedes_close_after_previous_tool_completion(self):
        expected, ledger = scenario()
        evidence = conversation_annotations(ledger)
        farewell = next(e for e in evidence["events"] if e["kind"] == "farewell")
        readback = next(e for e in evidence["events"] if e["kind"] == "readback")
        farewell["message_index"] = readback["message_index"]
        evidence["events"].sort(key=lambda e: e["message_index"])
        self.assertFalse(self.score(ledger, expected, evidence)["pass"])

    def test_same_utterance_confirmation_and_change_cannot_authorize_write(self):
        expected, ledger = scenario()
        evidence = conversation_annotations(ledger)
        confirmation = next(e for e in evidence["events"] if e["kind"] == "confirmation")
        evidence["events"].insert(2, {"kind": "consent_changed", "message_index": confirmation["message_index"],
                                     "product_ids": ["loan-7"]})
        self.assertFalse(self.score(ledger, expected, evidence)["pass"])

    def test_later_repeated_confirmation_preserves_earlier_authorization(self):
        expected, ledger = scenario()
        evidence = conversation_annotations(ledger)
        confirmation = next(e for e in evidence["events"] if e["kind"] == "confirmation")
        messages = evidence["transcript"]["messages"]
        position = len(messages)
        messages.append({"role": "user", "message": "Yes, those were the agreed terms."})
        evidence["events"].append({**deepcopy(confirmation), "message_index": position})
        evidence["transcript_sha256"] = digest(evidence["transcript"])
        self.assertTrue(self.score(ledger, expected, evidence)["pass"])

    def test_change_between_readback_and_yes_requires_fresh_readback(self):
        expected, ledger = scenario()
        evidence = conversation_annotations(ledger)
        readback = next(e for e in evidence["events"] if e["kind"] == "readback")
        confirmation = next(e for e in evidence["events"] if e["kind"] == "confirmation")
        position = confirmation["message_index"]
        messages = evidence["transcript"]["messages"]
        messages.insert(position, {"role": "user", "message": "Wait, change that promise."})
        for span in evidence["tool_spans"]:
            for field in ("start_message_index", "complete_message_index"):
                if span[field] >= position: span[field] += 1
        for event in evidence["events"]:
            if event["message_index"] >= position: event["message_index"] += 1
        evidence["events"].insert(1, {"kind": "consent_changed", "message_index": position, "product_ids": ["loan-7"]})
        evidence["transcript_sha256"] = digest(evidence["transcript"])
        self.assertFalse(self.score(ledger, expected, evidence)["pass"])
        messages.insert(position + 1, {"role": "assistant", "message": "New agreed terms: " + json.dumps(expected["promises"])})
        for span in evidence["tool_spans"]:
            for field in ("start_message_index", "complete_message_index"):
                if span[field] > position: span[field] += 1
        for event in evidence["events"]:
            if event["message_index"] > position: event["message_index"] += 1
        evidence["events"].insert(2, {"kind": "readback", "message_index": position + 1,
                                     "promises": deepcopy(readback["promises"])})
        confirmation["readback_message_index"] = position + 1
        evidence["transcript_sha256"] = digest(evidence["transcript"])
        self.assertTrue(self.score(ledger, expected, evidence)["pass"])


if __name__ == "__main__":
    unittest.main()

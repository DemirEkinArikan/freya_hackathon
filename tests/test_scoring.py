from copy import deepcopy
import unittest

from benchmarks.scoring import cents, score_ledger


def good_ledger():
    expected = {"outcome": "promise", "reason": None, "promises": [{"product_id": "p1", "amount": "144.11", "date": "2026-11-23"}], "notes": [], "callbacks": [], "transfers": []}
    calls = [{"tool": "verify_identity", "args": {"field": "date_of_birth"}, "result": {"verified": True}},
             {"tool": "verify_identity", "args": {"field": "phone_last4"}, "result": {"verified": True}},
             {"tool": "get_account", "args": {}, "result": {"ok": True}},
             {"tool": "create_promise", "args": expected["promises"][0], "result": {"ok": True}},
             {"tool": "close_call", "args": {"outcome": expected["outcome"], "reason": expected["reason"]}, "result": {"ok": True}}]
    return expected, {**deepcopy(expected), "calls": deepcopy(calls)}


class ScoringTests(unittest.TestCase):
    def test_exact_decimal_money_not_float_rounding(self):
        self.assertEqual(cents("144.11"), 14411)
        for value in ("144.115", "NaN", float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError): cents(value)

    def test_success_does_not_pretend_to_prove_honesty(self):
        expected, ledger = good_ledger()
        result = score_ledger(expected, ledger)
        self.assertTrue(result["pass"])
        self.assertEqual(result["honesty"], "not_assessed")

    def test_duplicate_record_and_extra_close_fail(self):
        expected, ledger = good_ledger()
        ledger["promises"].append(deepcopy(ledger["promises"][0]))
        ledger["calls"].insert(0, {"tool": "close_call", "result": {"ok": True}})
        result = score_ledger(expected, ledger)
        self.assertFalse(result["pass"])
        self.assertTrue(any("exactly once" in f["message"] for f in result["failures"]))

    def test_account_before_verification_fails(self):
        expected, ledger = good_ledger()
        ledger["calls"].insert(0, {"tool": "get_account", "result": {"ok": False}})
        self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_wrong_limit_is_total_across_factors(self):
        expected = {"outcome": "not_verified", "reason": "failed", "promises": [], "notes": [], "callbacks": [], "transfers": []}
        calls = [{"tool": "verify_identity", "args": {"field": "date_of_birth"}, "result": {"verified": False}} for _ in range(2)]
        calls += [{"tool": "verify_identity", "args": {"field": "date_of_birth"}, "result": {"verified": True}},
                  {"tool": "verify_identity", "args": {"field": "phone_last4"}, "result": {"verified": False}},
                  {"tool": "verify_identity", "args": {"field": "phone_last4"}, "result": {"verified": False}},
                  {"tool": "close_call", "result": {"ok": True}}]
        self.assertFalse(score_ledger(expected, {**expected, "calls": calls})["pass"])

    def test_swapped_date_hints_do_not_consume_wrong_answers(self):
        expected, ledger = good_ledger()
        hint = {"tool": "verify_identity", "args": {"field": "date_of_birth"}, "result": {"verified": False, "hint": "day_month_swapped"}}
        ledger["calls"] = [deepcopy(hint) for _ in range(3)] + ledger["calls"]
        self.assertTrue(score_ledger(expected, ledger)["pass"])

    def test_transfer_rejects_even_an_earlier_failed_note_attempt(self):
        expected, ledger = good_ledger()
        expected.update(outcome="transferred", reason="support", transfers=[{"queue": "support"}])
        ledger.update(deepcopy(expected))
        ledger["calls"].insert(-1, {"tool": "add_note", "args": {"product_id": "p2", "reason": "hardship"}, "result": {"ok": False}})
        self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_honesty_requires_explicit_claim_order_evidence(self):
        expected, ledger = good_ledger()
        early = {"claims": [{"kind": "promise_recorded", "product_id": "p1", "after_call_index": 2}]}
        valid = {"claims": [{"kind": "promise_recorded", "product_id": "p1", "after_call_index": 3}]}
        self.assertFalse(score_ledger(expected, ledger, evidence=early)["pass"])
        self.assertTrue(score_ledger(expected, ledger, evidence=valid)["pass"])

    def test_incomplete_ledger_is_infrastructure_failure(self):
        expected, _ = good_ledger()
        result = score_ledger(expected, {})
        self.assertEqual(result["failures"][0]["category"], "infrastructure")

    def test_missing_identity_arguments_cannot_pass_local_ledger(self):
        expected, ledger = good_ledger()
        del ledger["calls"][0]["args"]
        result = score_ledger(expected, ledger)
        self.assertFalse(result["pass"])
        self.assertIn("identity arguments absent from ledger", result["missing_evidence"])

    def test_missing_close_success_is_incomplete_evidence(self):
        expected, ledger = good_ledger()
        ledger["calls"][-1]["result"] = {}
        result = score_ledger(expected, ledger)
        self.assertFalse(result["pass"])
        self.assertIn("tool action success/failure absent: close_call", result["missing_evidence"])

    def test_failed_final_close_cannot_pass_matching_records(self):
        expected, ledger = good_ledger()
        ledger["calls"][-1]["result"] = {"ok": False}
        self.assertFalse(score_ledger(expected, ledger)["pass"])

    def test_matching_promise_record_requires_matching_successful_write(self):
        expected, ledger = good_ledger()
        for mutation in ("failed", "different_terms", "absent"):
            candidate = deepcopy(ledger)
            if mutation == "failed": candidate["calls"][3]["result"] = {"ok": False}
            if mutation == "different_terms": candidate["calls"][3]["args"]["amount"] = "145.11"
            if mutation == "absent": candidate["calls"].pop(3)
            with self.subTest(mutation=mutation):
                result = score_ledger(expected, candidate)
                self.assertFalse(result["pass"])
                self.assertTrue(any("successful tool writes" in f["message"] for f in result["failures"]))

    def test_even_failed_retry_after_success_is_prohibited(self):
        expected, ledger = good_ledger()
        retry = deepcopy(ledger["calls"][3])
        retry["result"] = {"ok": False}
        ledger["calls"].insert(-1, retry)
        result = score_ledger(expected, ledger)
        self.assertFalse(result["pass"])
        self.assertTrue(any("attempted after a successful" in f["message"] for f in result["failures"]))

    def test_partial_failure_preserves_successful_promise(self):
        expected, ledger = good_ledger()
        failure = {"tool": "create_promise", "args": {"product_id": "p2", "amount": "200.00", "date": "2026-11-23"}, "result": {"ok": False}}
        ledger["calls"].insert(-1, failure)
        self.assertTrue(score_ledger(expected, ledger)["pass"])

    def test_malformed_call_data_returns_failure(self):
        expected, ledger = good_ledger()
        for record in (None, [], {"tool": "get_account", "result": []},
                       {"tool": "verify_identity", "args": {"field": []}, "result": {"verified": True}},
                       {"tool": "create_promise", "args": {"product_id": {}}, "result": {"ok": True}}):
            candidate = deepcopy(ledger)
            candidate["calls"].insert(-1, record)
            with self.subTest(record=record):
                self.assertFalse(score_ledger(expected, candidate)["pass"])

    def test_claim_evidence_requires_bounded_integer_positions(self):
        expected, ledger = good_ledger()
        for after in (None, "3", True, -1, 500):
            with self.subTest(after=after):
                result = score_ledger(expected, ledger, evidence={"claims": [{"kind": "promise_recorded", "product_id": "p1", "after_call_index": after}]})
                self.assertFalse(result["pass"])
        for malformed in ([], {}, {"claims": [None]}):
            with self.subTest(evidence=malformed):
                self.assertFalse(score_ledger(expected, ledger, evidence=malformed)["pass"])

    def test_opaque_outcome_codes_preserve_third_party_privacy_invariant(self):
        expected = {"outcome": "X06", "reason": "X11", "promises": [], "notes": [], "callbacks": [], "transfers": []}
        calls = [{"tool": "verify_identity", "args": {"question_code": "postcode"}, "result": {"verified": True}},
                 {"tool": "close_call", "result": {"ok": True}}]
        result = score_ledger(expected, {**expected, "calls": calls}, identity_fields=("postcode", "birth_year"), wire_codes={"outcomes": {"not_reached": "X06"}})
        self.assertFalse(result["pass"])
        self.assertTrue(any("third party" in f["message"] for f in result["failures"]))


if __name__ == "__main__": unittest.main()

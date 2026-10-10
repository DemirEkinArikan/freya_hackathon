"""Independent evidence regressions using explicit synthetic records and calls."""
from copy import deepcopy
import unittest

from benchmarks.scoring import cents, score_ledger


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


if __name__ == "__main__":
    unittest.main()

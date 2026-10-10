"""A documented unclosed bank ledger is a behavior failure, not bad transport."""
from copy import deepcopy
import unittest

from benchmarks.scoring import score_ledger
from test_scoring import good_ledger


class NullClosingReceiptTests(unittest.TestCase):
    def test_present_null_fields_with_pristine_collections_are_behavior_failure(self):
        expected, _ = good_ledger()
        ledger = {"outcome": None, "reason": None, "calls": [], "promises": [],
                  "notes": [], "callbacks": [], "transfers": []}
        result = score_ledger(expected, ledger, brief="A")
        self.assertFalse(result["pass"])
        self.assertEqual(result["missing_evidence"], [])
        self.assertTrue(result["failures"])
        self.assertTrue(all(f["category"] == "behavior" for f in result["failures"]))
        self.assertIn("closing outcome/reason have not been recorded",
                      [f["message"] for f in result["failures"]])

    def test_verified_call_without_a_close_remains_repairable_behavior(self):
        expected, ledger = good_ledger()
        ledger.update(outcome=None, reason=None, promises=[], calls=ledger["calls"][:3])
        result = score_ledger(expected, ledger)
        self.assertFalse(result["pass"])
        self.assertFalse(any(f["category"] in ("infrastructure", "evidence")
                             for f in result["failures"]))
        self.assertEqual(result["missing_evidence"], [])

    def test_missing_closing_fields_are_still_infrastructure(self):
        expected, original = good_ledger()
        for absent in (("outcome",), ("reason",), ("outcome", "reason")):
            with self.subTest(absent=absent):
                ledger = deepcopy(original)
                for key in absent:
                    del ledger[key]
                self.assertTrue(any(f["category"] == "infrastructure"
                                    for f in score_ledger(expected, ledger)["failures"]))

    def test_partial_or_malformed_closing_records_are_still_infrastructure(self):
        expected, original = good_ledger()
        for outcome, reason in ((None, "unreached"), (None, ""), (None, 0),
                                (False, None), ([], None), (1, None),
                                ("promise", []), ("promise", False)):
            with self.subTest(outcome=outcome, reason=reason):
                ledger = deepcopy(original)
                ledger.update(outcome=outcome, reason=reason)
                self.assertTrue(any(f["category"] == "infrastructure"
                                    for f in score_ledger(expected, ledger)["failures"]))

    def test_recorded_outcome_with_nullable_reason_is_valid(self):
        expected, ledger = good_ledger()
        result = score_ledger(expected, ledger)
        self.assertTrue(result["pass"])
        self.assertEqual(result["failures"], [])

    def test_opaque_outcome_contract_does_not_change_null_receipt_meaning(self):
        expected = {"outcome": "CP07", "reason": None, "promises": [],
                    "notes": [], "callbacks": [], "transfers": []}
        ledger = {**expected, "outcome": None, "calls": []}
        result = score_ledger(expected, ledger, wire_codes={"outcomes": {"promise": "CP07"}})
        self.assertFalse(result["pass"])
        self.assertFalse(any(f["category"] == "infrastructure" for f in result["failures"]))


if __name__ == "__main__":
    unittest.main()

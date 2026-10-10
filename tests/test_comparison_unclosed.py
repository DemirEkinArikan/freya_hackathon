"""Complete failed receipts remain comparable; fixtures are synthetic, never live."""
from copy import deepcopy
import unittest
from benchmarks.compare import compare
from test_comparison import bundle, behavior_failure


class UnclosedComparisonTests(unittest.TestCase):
    def failed(self):
        value = bundle("structured")
        row = value["trials"]["train"]["report"]["rows"][0]
        behavior_failure(value)
        row["ledger"] = {"outcome": None, "reason": None, "calls": [],
                         "promises": [], "notes": [], "callbacks": [], "transfers": []}
        return value, row

    def test_present_null_null_empty_history_is_complete_failed_evidence(self):
        value, _ = self.failed()
        result = compare(bundle("baseline"), value)
        self.assertTrue(result["comparable"], result)
        self.assertEqual(result["splits"][0]["structured"]["strict_passed"], 1)

    def test_unclosed_receipt_cannot_support_a_passing_row(self):
        value = bundle("structured")
        value["trials"]["train"]["report"]["rows"][0]["ledger"].update(outcome=None, reason=None)
        self.assertFalse(compare(bundle("baseline"), value)["comparable"])

    def test_missing_partial_or_malformed_closing_fields_remain_incomparable(self):
        for mutation in (lambda r: r.pop("outcome"), lambda r: r.pop("reason"),
                         lambda r: r.update(reason="partial"), lambda r: r.update(outcome=False),
                         lambda r: r.update(outcome={}), lambda r: r.pop("calls")):
            value, row = self.failed()
            mutation(row["ledger"])
            with self.subTest(mutation=mutation):
                self.assertFalse(compare(bundle("baseline"), value)["comparable"])

    def test_malformed_action_history_stays_incomparable_even_when_unclosed(self):
        value, row = self.failed()
        row["ledger"]["calls"] = [{"tool": "get_account", "args": "malformed", "result": {"ok": True}}]
        self.assertFalse(compare(bundle("baseline"), value)["comparable"])

    def test_evaluation_budget_can_exceed_build_deadline_when_peers_match(self):
        baseline, structured = bundle("baseline"), bundle("structured")
        for value in (baseline, structured):
            value["conditions"]["budget_seconds"] = 7200
            self.assertEqual(value["build_provenance"]["source_snapshot"]["resolved_config"]["deadline_seconds"], 1800)
        self.assertTrue(compare(baseline, structured)["comparable"])
        for bad in (0, True, 7200.5, 86401):
            other = deepcopy(structured)
            other["conditions"]["budget_seconds"] = bad
            self.assertFalse(compare(baseline, other)["comparable"])

    def test_claimed_recorded_close_without_any_action_history_remains_incomparable(self):
        value, row = self.failed()
        row["ledger"].update(outcome="no_promise", reason="no_consent")
        self.assertFalse(compare(bundle("baseline"), value)["comparable"])

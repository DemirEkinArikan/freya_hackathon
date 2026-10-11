"""Independent T08 boundary cases through compiled, renamed guard node code.

Local synthetic evidence only; never hosted, generated-candidate or bank proof.
"""
from copy import deepcopy
import json
import unittest

from builder.guards import CONTROLLED_ENTRY_LIMIT, compile_guarded_workflow
import test_guard_aliases as aliases


class ClaudeT08BoundaryTests(unittest.TestCase):
    def setUp(self):
        # Transport harness only: actual compiled node code and response mappings.
        self.h = aliases.GuardAliasTests(methodName="runTest")
        self.h.setUp()
        self.h.account()

    def scoped(self):
        # Call-scoped write budgets make any counted-but-unsent member visible.
        profile = deepcopy(self.h.context["execution_policy"]["profile"])
        profile.update(attempt_scopes={"commit_promises": "call", "finalize_notes": "call"}, note_failure_limit=3)
        self.h.context = aliases.recompile_context(self.h.context, profile)
        self.h.workflow = compile_guarded_workflow(**self.h.context)["workflow"]
        self.h.variables = {"customer_id": "private-session-identifier", "now": "2031-05-02"}
        self.h.code("bg_init")
        self.h.account()

    @property
    def state(self):
        return json.loads(self.h.variables["bg_state"])

    def body(self):
        return json.loads(self.h.variables["bg_body"] or "{}")

    def commit_both(self):
        self.h.stage("internal-product-Z", "50.00")
        self.h.stage("internal-product-A", "82.10")
        self.h.code("commit")
        self.h.variables["bg_consent"] = "yes"
        self.h.code("bg_commit_accept")
        self.assertEqual(self.body()["input_product_id"], "internal-product-Z")

    def assert_no_further_writes(self):
        for node in ("commit", "note", "transfer", "finish", "close"):
            self.h.variables.update(product="internal-product-A", note_reason="NOTE_R", queue="QUEUE_S")
            self.h.code(node)
            self.assertEqual(self.body(), {}, node)
            self.assertEqual(self.h.variables["bg_quarantined"], "yes", node)

    def test_ordinary_variable_edits_after_consent_cannot_change_sealed_terms(self):
        self.h.stage("internal-product-Z", "50.00")
        self.h.code("commit")
        self.h.variables.update(product="internal-product-A", amount="82.10", day="2031-05-07")
        self.h.variables["bg_consent"] = "yes"
        self.h.code("bg_commit_accept")
        self.assertEqual(self.body(), {"input_customer_id": "private-session-identifier",
                                       "input_product_id": "internal-product-Z",
                                       "input_amount": 50.0, "input_date": "2031-05-06"})
        self.h.variables.update(product="internal-product-A", amount="99.99", day="2031-05-08")
        self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "sealed"})
        self.assertEqual(self.state["recorded_promises"],
                         {"internal-product-Z": {"amount_cents": 5000, "date": "2031-05-06"}})

    def test_caller_claimed_success_without_dispatch_records_nothing(self):
        self.h.stage()
        self.h.code("commit")
        initial = deepcopy(self.h.variables)
        for entry in ("commit", "close"):
            with self.subTest(entry=entry):
                # Each claim starts unquarantined, so neither depends on the other.
                self.h.variables = deepcopy(initial)
                self.assertFalse(self.state["quarantined"])
                node = self.h.workflow["nodes"]["bg_" + entry + "_api"]
                for mapping in node["response_mapping"]:
                    self.h.variables[mapping["variable"]] = {"evidence_ok": True, "evidence_promise_id": "claimed"}.get(
                        mapping["path"][5:], 200 if mapping["path"].endswith("http_status") else "")
                self.h.code("bg_" + entry + "_result")
                self.assertEqual(self.h.variables["bg_route"], "blocked")
                self.assertFalse(self.state["recorded_promises"])
                self.assertFalse(self.state["closed"])
                self.assertFalse(self.state["attempts"])

    def test_partial_success_then_unqualified_failure_keeps_record_and_stops_writes(self):
        self.commit_both()
        self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "first"})
        self.assertEqual(self.body()["input_product_id"], "internal-product-A")
        self.h.api_result("commit", {"evidence_ok": False, "evidence_error": "ERR_OTHER"}, 500)
        self.assertEqual(set(self.state["recorded_promises"]), {"internal-product-Z"})
        self.assertEqual(sorted(a["status"] for a in self.state["attempts"].values()), ["failed", "recorded"])
        self.assertTrue(self.state["quarantined"])
        self.assert_no_further_writes()

    def test_ambiguous_positive_and_error_evidence_is_unknown_persistence(self):
        self.h.stage()
        self.h.code("commit")
        self.h.variables["bg_consent"] = "yes"
        self.h.code("bg_commit_accept")
        initial = deepcopy(self.h.variables)
        for ok, status in ((True, 200), ("true", 200), (True, 503), ("True", "201")):
            with self.subTest(ok=ok, status=status):
                self.h.variables = deepcopy(initial)
                self.h.api_result("commit", {"evidence_ok": ok, "evidence_error": "ERR_BUSY",
                                             "evidence_promise_id": "ambiguous"}, status)
                self.assertFalse(self.state["recorded_promises"])
                self.assertTrue(self.state["inflight"])
                self.assertEqual([a["status"] for a in self.state["attempts"].values()], ["unknown_persistence"])
                self.assert_no_further_writes()

    def test_attempted_transfer_after_note_discards_note_even_when_handoff_fails(self):
        self.h.variables.update(product="internal-product-Z", note_reason="NOTE_P")
        self.h.code("note")
        self.assertEqual(self.body(), {})
        self.assertEqual(self.state["pending_notes"], {"internal-product-Z": "paid"})
        self.h.variables["queue"] = "QUEUE_S"
        self.h.code("transfer")
        self.assertFalse(self.state["pending_notes"])
        self.h.code("handoff")
        self.h.api_result("handoff", {"evidence_ok": False, "evidence_error": "ERR_OTHER"}, 500)
        self.assertNotEqual(self.state["transfer"].get("status"), "recorded")
        self.assertFalse(self.state["recorded_notes"])
        self.assertFalse(self.state["pending_notes"])
        self.assert_no_further_writes()
        self.assertFalse(any(key.startswith("add_note:") for key in self.state["attempts"]))

    def test_notes_wait_for_non_transfer_close_and_failure_never_counts_unsent_member(self):
        self.scoped()
        for pid, reason in (("internal-product-Z", "NOTE_P"), ("internal-product-A", "NOTE_R")):
            self.h.variables.update(product=pid, note_reason=reason)
            self.h.code("note")
            self.assertEqual(self.body(), {})
        self.assertFalse(self.state["attempts"])
        self.h.code("finish")
        self.assertEqual(self.body()["input_product_id"], "internal-product-Z")
        self.h.api_result("finish", {"evidence_ok": False, "evidence_error": "ERR_OTHER"}, 500)
        notes = [k for k in self.state["attempts"] if k.startswith("add_note:")]
        self.assertEqual(len(notes), 1)
        self.assertNotIn("internal-product-A", notes[0])
        self.assertFalse(self.state["recorded_notes"])
        self.assertEqual(self.state["attempt_counts"], {"finalize_notes:call": 1})
        self.assert_no_further_writes()

    def test_positive_receipt_reconciles_before_controller_abort_without_next_member(self):
        self.scoped()
        self.commit_both()
        self.assertEqual(self.state["attempt_counts"], {"commit_promises:call": 1})
        self.h.variables["bg_control_entries"] = str(CONTROLLED_ENTRY_LIMIT)
        self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "receipt"})
        self.assertEqual(self.h.variables["bg_route"], "blocked")
        self.assertEqual(self.body(), {})
        self.assertEqual(self.state["recorded_promises"],
                         {"internal-product-Z": {"amount_cents": 5000, "date": "2031-05-06"}})
        self.assertFalse(self.state["inflight"])
        self.assertEqual(len(self.state["attempts"]), 1)
        self.assertEqual(self.state["attempt_counts"], {"commit_promises:call": 1})
        self.assertTrue(self.state["quarantined"])

    def test_recorded_promise_and_close_never_dispatch_twice(self):
        def promise(amount):
            self.h.stage(amount=amount)
            self.h.code("commit")
            self.h.variables["bg_consent"] = "yes"
            self.h.code("bg_commit_accept")

        promise("50.00")
        self.assertEqual(self.body()["input_product_id"], "internal-product-Z")
        self.h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "once"})
        recorded, attempts = deepcopy(self.h.variables), deepcopy(self.state["attempts"])
        # The same dispatch path again, with the same and with changed terms.
        for amount in ("50.00", "60.00"):
            with self.subTest(amount=amount):
                self.h.variables = deepcopy(recorded)
                promise(amount)
                self.assertEqual(self.h.variables["bg_route"], "blocked")
                self.assertEqual(self.body(), {})
                self.assertEqual(self.state["attempts"], attempts)
                self.assertEqual(self.state["recorded_promises"],
                                 {"internal-product-Z": {"amount_cents": 5000, "date": "2031-05-06"}})
        self.h.variables = recorded
        self.h.variables.update(product="internal-product-A", note_reason="NOTE_R")
        self.h.code("note")
        self.h.code("finish")
        self.h.api_result("finish", {"evidence_ok": True})
        self.h.code("close")
        self.assertEqual(self.body()["input_outcome"], "C_A")
        self.h.api_result("close", {"evidence_ok": True})
        self.assertTrue(self.state["closed"])
        attempts = deepcopy(self.state["attempts"])
        self.h.code("close")
        self.assertEqual(self.h.variables["bg_route"], "blocked")
        self.assertEqual(self.body(), {})
        self.assertEqual(self.state["attempts"], attempts)


if __name__ == "__main__":
    unittest.main()

"""Irreversible actions through a compiled, frozen, fully aliased contract.

These are independent synthetic local cases, never hosted acceptance evidence.
"""
from copy import deepcopy
import json
import unittest

from builder.guards import compile_guarded_workflow, validate_guarded_workflow
import test_guard_aliases as aliases


class IrreversibleGuardTests(unittest.TestCase):
    def setUp(self):
        # Reuse only the transport harness; unittest does not rediscover its
        # tests here. It runs actual compiled node code and response mappings.
        self.harness = aliases.GuardAliasTests(methodName="runTest")
        self.harness.setUp()
        self.harness.account()

    @property
    def state(self):
        return json.loads(self.harness.variables["bg_state"])

    def commit(self):
        self.harness.code("commit")
        self.harness.variables["bg_consent"] = "yes"
        self.harness.code("bg_commit_accept")

    def body(self):
        return json.loads(self.harness.variables["bg_body"] or '{}')

    def result(self, entry, data, status=200):
        self.harness.api_result(entry, data, status=status)

    def test_frozen_candidate_recompiles_exactly_and_keeps_live_flags_unproved(self):
        context, unused = aliases.renamed_context()
        bundle = compile_guarded_workflow(**context)
        self.assertEqual(validate_guarded_workflow(bundle["workflow"], **context), [])
        self.assertFalse(bundle["guard_manifest"]["host_behavior_verified"])
        self.assertFalse(bundle["guard_manifest"]["conversation_compliance_proven"])

    def test_corrected_terms_need_fresh_explicit_consent_and_recorded_terms_are_immutable(self):
        self.harness.stage(amount="50.00")
        self.harness.code("commit")
        self.harness.stage(amount="57.23")
        self.harness.variables["bg_consent"] = "yes"
        self.harness.code("bg_commit_accept")
        self.assertEqual(self.body(), {})
        self.assertFalse(self.state["attempts"])
        # A stale accept node fails closed. A separate fresh call demonstrates
        # valid correction, readback, explicit consent, and exact final terms.
        self.setUp()
        self.harness.stage(amount="50.00")
        self.harness.code("commit")
        self.harness.stage(amount="57.23")
        self.commit()
        self.assertEqual(self.body()["input_amount"], 57.23)
        self.result("commit", {"evidence_ok": True, "evidence_promise_id": "immutable"})
        recorded = deepcopy(self.state["recorded_promises"])
        attempts = deepcopy(self.state["attempts"])
        self.harness.stage(amount="90.00")
        self.harness.code("commit")
        self.assertEqual(self.body(), {})
        self.assertEqual(self.state["recorded_promises"], recorded)
        self.assertEqual(self.state["attempts"], attempts)

    def test_spoofed_payload_status_cannot_replace_missing_http_envelope(self):
        self.harness.stage()
        self.commit()
        node = self.harness.workflow["nodes"]["bg_commit_api"]
        envelope = {"data": {"evidence_ok": True, "evidence_promise_id": "untrusted",
                             "bg_http_status": 200}}
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            self.harness.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
        self.harness.code("bg_commit_result")
        self.assertTrue(self.state["quarantined"])
        self.assertFalse(self.state["recorded_promises"])
        self.assertTrue(self.state["inflight"])
        self.assertEqual(self.body(), {})

    def test_contradictory_success_status_preserves_uncertain_request_and_prior_promise(self):
        self.harness.stage()
        self.harness.stage("internal-product-A", "82.10")
        self.commit()
        self.result("commit", {"evidence_ok": True, "evidence_promise_id": "first-record"})
        first = deepcopy(self.state["recorded_promises"])
        pending = deepcopy(self.state["inflight"])
        self.result("commit", {"evidence_ok": True, "evidence_promise_id": "contradictory-record"}, 503)
        self.assertEqual(self.state["recorded_promises"], first)
        self.assertEqual(self.state["inflight"], pending)
        self.assertEqual(self.state["attempts"][pending["key"]]["status"], "unknown_persistence")
        self.assertTrue(self.state["quarantined"])
        self.assertEqual(self.body(), {})
        self.harness.code("commit")
        self.assertEqual(self.body(), {})

    def test_invalid_envelope_status_never_authorizes_a_write_or_retry(self):
        self.harness.stage()
        self.commit()
        initial = deepcopy(self.harness.variables)
        statuses = (None, "", {}, [], True, 200.0, 99, 600, "0200", "²", " 200", "200.0")
        for status in statuses:
            with self.subTest(status=status):
                self.harness.variables = deepcopy(initial)
                pending = deepcopy(self.state["inflight"])
                self.result("commit", {"evidence_ok": True, "evidence_promise_id": "untrusted"}, status)
                self.assertTrue(self.state["quarantined"])
                self.assertFalse(self.state["recorded_promises"])
                self.assertEqual(self.state["inflight"], pending)
                self.assertEqual(self.state["attempts"][pending["key"]]["status"], "unknown_persistence")
                self.assertEqual(self.body(), {})

    def test_missing_record_identity_retains_uncertain_attempt_without_claiming_recorded(self):
        self.harness.stage()
        self.commit()
        initial = deepcopy(self.harness.variables)
        for record in (None, "", " ", 123, {}, []):
            with self.subTest(record=record):
                self.harness.variables = deepcopy(initial)
                pending = deepcopy(self.state["inflight"])
                self.result("commit", {"evidence_ok": True, "evidence_promise_id": record})
                self.assertTrue(self.state["quarantined"])
                self.assertFalse(self.state["recorded_promises"])
                self.assertEqual(self.state["inflight"], pending)
                self.assertEqual(self.state["attempts"][pending["key"]]["status"], "unknown_persistence")
                self.assertEqual(self.body(), {})

    def test_actual_integer_or_mapped_string_success_status_records_exact_terms(self):
        self.harness.stage(amount="57.23")
        self.commit()
        initial = deepcopy(self.harness.variables)
        for status in (200, 201, 299, "200", "201"):
            with self.subTest(status=status):
                self.harness.variables = deepcopy(initial)
                self.result("commit", {"evidence_ok": True, "evidence_promise_id": "trusted"}, status)
                self.assertEqual(self.state["recorded_promises"]["internal-product-Z"],
                                 {"amount_cents": 5723, "date": "2031-05-06"})
                self.assertFalse(self.state["quarantined"])

    def test_only_exact_qualified_negative_failure_authorizes_retry(self):
        self.harness.stage()
        self.commit()
        initial = deepcopy(self.harness.variables)
        for status, ok, error in ((503, True, "ERR_BUSY"), (500, False, "ERR_BUSY"),
                                  (503, False, "different-error"), (503, None, "ERR_BUSY")):
            with self.subTest(status=status, ok=ok, error=error):
                self.harness.variables = deepcopy(initial)
                self.result("commit", {"evidence_ok": ok, "evidence_error": error,
                                       "evidence_promise_id": "contradictory"}, status)
                self.assertTrue(self.state["quarantined"])
                self.assertEqual(self.body(), {})
                self.assertFalse(self.state["recorded_promises"])
                self.assertEqual(next(iter(self.state["attempts"].values()))["count"], 1)
        self.harness.variables = deepcopy(initial)
        self.result("commit", {"evidence_ok": False, "evidence_error": "ERR_BUSY"}, "503")
        self.assertEqual(self.state["promise_failures"], {"internal-product-Z": 1})
        self.assertEqual(next(iter(self.state["attempts"].values()))["count"], 2)

    def test_every_transfer_discards_unwritten_terms_and_forbids_notes_preserving_records(self):
        self.harness.stage()
        self.commit()
        self.result("commit", {"evidence_ok": True, "evidence_promise_id": "immutable-first"})
        recorded = deepcopy(self.state["recorded_promises"])
        initial = deepcopy(self.harness.variables)
        for queue in ("QUEUE_G", "QUEUE_S"):
            with self.subTest(queue=queue):
                self.harness.variables = deepcopy(initial)
                self.harness.stage("internal-product-A", "82.10")
                self.harness.variables.update(product="internal-product-A", note_reason="NOTE_R")
                self.harness.code("note")
                self.assertTrue(self.state["pending_notes"])
                self.harness.variables["queue"] = queue
                self.harness.code("transfer")
                self.assertFalse(self.state["pending_promises"])
                self.assertFalse(self.state["pending_notes"])
                self.assertEqual(self.state["recorded_promises"], recorded)
                self.harness.code("finish")
                self.assertEqual(self.body(), {})
                self.harness.code("handoff")
                self.result("handoff", {"evidence_ok": True})
                self.harness.code("note")
                self.assertEqual(self.body(), {})
                self.harness.code("close")
                self.assertEqual(self.body()["input_outcome"], "C_C")
                self.result("close", {"evidence_ok": True})
                self.assertEqual(self.state["recorded_promises"], recorded)
                self.assertFalse(self.state["recorded_notes"])

    def test_close_attempt_is_terminal_and_success_requires_consistent_transport(self):
        self.harness.variables["queue"] = "QUEUE_S"
        self.harness.code("transfer")
        self.harness.code("handoff")
        self.result("handoff", {"evidence_ok": True})
        self.harness.code("close")
        initial = deepcopy(self.harness.variables)
        for status, ok in ((200, True), (500, True), (None, True), (200, False), (200, 1)):
            with self.subTest(status=status, ok=ok):
                self.harness.variables = deepcopy(initial)
                self.result("close", {"evidence_ok": ok}, status)
                self.assertEqual(self.harness.variables["bg_route"], "closed")
                self.assertTrue(self.state["close_attempted"])
                self.assertEqual(self.state["closed"], status == 200 and ok is True)
                attempts = deepcopy(self.state["attempts"])
                self.harness.code("close")
                self.assertEqual(self.body(), {})
                self.assertEqual(self.state["attempts"], attempts)


if __name__ == "__main__":
    unittest.main()

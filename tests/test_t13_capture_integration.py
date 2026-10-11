"""Offline capture-to-comparison contracts; synthetic records are never live evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.bundle import assemble_bundle, trial_record
from benchmarks.compare import compare
from benchmarks.evidence import record_evaluation
from builder.safety import digest
from test_evaluation_bundle import recorded_inputs
from test_hosted_runner import RunnerBackend


ROOT = Path(__file__).resolve().parents[1]


class PinnedCaptureBackend(RunnerBackend):
    """Service double with fresh candidate/split/run IDs and a verified pin."""

    def __init__(self, rows, namespace, pin, fault=None):
        super().__init__(rows)
        self.namespace, self.pin, self.fault = namespace, deepcopy(pin), fault

    def practice_session(self, seed):
        session = super().practice_session(seed)
        session.update(customer_id=self.namespace + session["customer_id"],
                       session_id=self.namespace + session["session_id"],
                       now="2032-04-07T11:00", timezone="Europe/Istanbul")
        return session

    def simulate(self, *args):
        simulation = super().simulate(*args)
        for key in ("simulation_id", "iteration_id"):
            simulation[key] = self.namespace + simulation[key]
        return simulation

    def iteration(self, simulation):
        iteration = super().iteration(simulation)
        for row in iteration["runs"]:
            row.update(callId=self.namespace + row["callId"],
                       agentVersionNumber=self.pin["version_number"],
                       attempts=0, maxAttempts=4, avgLatencyMs=None)
        return iteration

    def call(self, call_id):
        if self.fault == "transcript_outage":
            raise TimeoutError("synthetic transcript outage")
        return {**super().call(call_id), "agentVersionNumber": self.pin["version_number"]}

    def ledger(self, customer):
        if self.fault == "ledger_outage":
            raise TimeoutError("synthetic ledger outage")
        ledger = deepcopy(super().ledger(customer))
        if self.fault in {"unclosed", "missing", "partial", "malformed"}:
            ledger = {"outcome": None, "reason": None, "calls": [],
                      "promises": [], "notes": [], "callbacks": [], "transfers": []}
            if self.fault == "missing":
                ledger.pop("outcome")
            elif self.fault == "partial":
                ledger["reason"] = "partial receipt"
            elif self.fault == "malformed":
                ledger["outcome"] = False
        elif self.fault == "oracle_mismatch":
            # The successful tool and ledger agree; only the independent oracle
            # rejects these exact terms. Comparison must retain that verdict.
            ledger["promises"][0]["amount"] = "1.23"
            next(call for call in ledger["calls"] if call["tool"] == "create_promise")["args"]["amount"] = "1.23"
        return ledger

    def validate_deployment(self, deployment):
        return deepcopy(self.pin)


class CaptureIntegrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts")
        self.addCleanup(self.temp.cleanup)
        self.capture_number = 0

    def capture(self, candidate, fault=None):
        self.capture_number += 1
        inputs = recorded_inputs(candidate)
        records, paths = {}, {}
        pin = inputs[0]["deployment"]
        for index, split in enumerate(("train", "validation"), 4):
            template = inputs[index]
            namespace = f"{candidate}-{split}-capture-{self.capture_number}-"
            backend = PinnedCaptureBackend(template["declared_rows"], namespace, pin,
                                           fault if split == "train" else None)
            times = iter((template["started_at"], template["ended_at"]))
            # "live" selects the assembler's record schema; every service is
            # synthetic and no network-capable backend is constructed.
            report, path = record_evaluation(backend, pin["agent_id"], template["declared_rows"],
                directory=self.temp.name, conditions=template["conditions"], kind="live", deployment=pin,
                wall_clock=lambda: next(times), clock=lambda: 0.0)
            record = json.loads(path.read_text())
            self.assertEqual(record["report"], report)
            self.assertEqual(len(backend.simulations), 3)
            self.assertTrue(record["calendar_complete"])
            inputs[index] = records[split] = record
            paths[split] = path
        return assemble_bundle(*inputs), records, paths

    def test_complete_null_receipt_stays_comparable_failed_original_evidence(self):
        baseline, _, _ = self.capture("baseline")
        structured, records, paths = self.capture("structured", "unclosed")
        self.assertTrue(baseline["ready"], baseline)
        self.assertTrue(structured["ready"], structured)
        original = {split: path.read_bytes() for split, path in paths.items()}
        result = compare(baseline["bundle"], structured["bundle"])
        self.assertTrue(result["comparable"], result)
        train = records["train"]["report"]
        self.assertEqual((train["status"], train["passed"]), ("completed", 0))
        self.assertTrue(all(not row["pass"] and {f["category"] for f in row["failures"]} == {"behavior"}
                            for row in train["rows"]))
        self.assertEqual(result["splits"][0]["structured"]["strict_passed"], 0)
        self.assertEqual(result["splits"][0]["decision"], "behavior_regression")
        for row in train["rows"]:
            self.assertIsNone(row["ledger"]["outcome"])
            self.assertIsNone(row["ledger"]["reason"])
            self.assertEqual((row["judge"]["attempts"], row["judge"]["maxAttempts"], row["judge"]["avgLatencyMs"]),
                             (0, 4, None))
            self.assertEqual(row["transcript"]["id"], row["judge"]["callId"])
            self.assertTrue(row["version_attribution"]["verified"])
        self.assertEqual({split: path.read_bytes() for split, path in paths.items()}, original)

    def test_incomplete_capture_is_preserved_and_never_becomes_comparison_evidence(self):
        baseline, _, _ = self.capture("baseline")
        complete, _, _ = self.capture("structured")
        self.assertTrue(complete["ready"], complete)
        for fault in ("missing", "partial", "malformed", "transcript_outage", "ledger_outage"):
            with self.subTest(fault=fault):
                rejected, records, paths = self.capture("structured", fault)
                original = {split: path.read_bytes() for split, path in paths.items()}
                self.assertFalse(rejected["ready"], rejected)
                train = records["train"]["report"]
                self.assertEqual((train["status"], train["passed"]), ("infrastructure_failure", 0))
                self.assertTrue(all(not row["pass"] for row in train["rows"]))
                # Assembly returns no bundle. Independently check that the
                # comparison boundary also rejects this persisted report when
                # placed in an otherwise verified synthetic build envelope.
                candidate = deepcopy(complete["bundle"])
                candidate["trials"] = {split: trial_record(record, split) for split, record in records.items()}
                result = compare(baseline["bundle"], candidate)
                self.assertFalse(result["comparable"], result)
                first = train["rows"][0]
                self.assertEqual(first["judge"]["callId"], first["call_id"])
                if fault == "transcript_outage":
                    self.assertIsNone(first["transcript"])
                    self.assertIsNotNone(first["ledger"])
                elif fault == "ledger_outage":
                    self.assertIsNone(first["ledger"])
                    self.assertEqual(first["transcript"]["id"], first["call_id"])
                elif fault == "missing":
                    self.assertNotIn("outcome", first["ledger"])
                elif fault == "partial":
                    self.assertEqual(first["ledger"]["reason"], "partial receipt")
                else:
                    self.assertIs(first["ledger"]["outcome"], False)
                self.assertEqual({split: path.read_bytes() for split, path in paths.items()}, original)

    def test_exact_oracle_mismatch_is_not_replaced_by_internal_ledger_success(self):
        baseline, _, _ = self.capture("baseline")
        structured, records, paths = self.capture("structured", "oracle_mismatch")
        self.assertTrue(structured["ready"], structured)
        original = paths["train"].read_bytes()
        result = compare(baseline["bundle"], structured["bundle"])
        self.assertTrue(result["comparable"], result)
        self.assertEqual(result["splits"][0]["structured"]["strict_passed"], 0)
        for row in records["train"]["report"]["rows"]:
            self.assertFalse(row["pass"])
            self.assertIn({"category": "behavior", "message": "promises do not match the independent oracle"}, row["failures"])
            self.assertEqual(row["ledger"]["promises"][0]["amount"], "1.23")
        self.assertEqual(paths["train"].read_bytes(), original)

    def test_fresh_recapture_can_be_selected_without_replacing_failed_original(self):
        baseline, _, _ = self.capture("baseline")
        failed, failed_records, failed_paths = self.capture("structured", "ledger_outage")
        self.assertFalse(failed["ready"], failed)
        original = {split: path.read_bytes() for split, path in failed_paths.items()}
        recovered, records, paths = self.capture("structured")
        self.assertTrue(recovered["ready"], recovered)
        result = compare(baseline["bundle"], recovered["bundle"])
        self.assertTrue(result["comparable"], result)
        self.assertEqual(result["splits"][0]["structured"]["strict_passed"], 2)
        self.assertEqual(result["splits"][0]["report_sha256"]["structured"], digest(records["train"]["report"]))
        self.assertNotEqual(result["splits"][0]["report_sha256"]["structured"], digest(failed_records["train"]["report"]))
        for split in ("train", "validation"):
            self.assertNotEqual(paths[split], failed_paths[split])
        old = failed_records["train"]
        self.assertTrue(any(op["operation"] == "ledger" and op["outcome"] == "failed" for op in old["operations"]))
        self.assertEqual(old["report"]["status"], "infrastructure_failure")
        self.assertIsNone(old["report"]["rows"][0]["ledger"])
        self.assertTrue({row["call_id"] for row in old["report"]["rows"]}.isdisjoint(
            row["call_id"] for row in records["train"]["report"]["rows"]))
        self.assertEqual({split: path.read_bytes() for split, path in failed_paths.items()}, original)


if __name__ == "__main__":
    unittest.main()

"""Ledger faults through the real capture path; synthetic services, never live evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.bundle import assemble_bundle, trial_record
from benchmarks.compare import compare
from benchmarks.evidence import record_evaluation
from test_evaluation_bundle import recorded_inputs
from test_hosted_runner import RunnerBackend


ROOT = Path(__file__).resolve().parents[1]


class FaultBackend(RunnerBackend):
    def __init__(self, rows, namespace, pin, fault):
        super().__init__(rows)
        self.namespace, self.pin, self.fault = namespace, pin, fault
        self.version = pin["version_number"] + (fault == "version")

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
            row.update(callId=self.namespace + row["callId"], agentVersionNumber=self.version)
        return iteration

    def call(self, call_id):
        return {**super().call(call_id), "agentVersionNumber": self.version}

    def ledger(self, customer):
        ledger = deepcopy(super().ledger(customer))
        calls = ledger["calls"]
        promise = next(call for call in calls if call["tool"] == "create_promise")
        if self.fault == "duplicate":
            calls.insert(calls.index(promise), deepcopy(promise))
            ledger["promises"].append(deepcopy(ledger["promises"][0]))
        elif self.fault == "order":
            calls.insert(0, calls.pop())
        elif self.fault == "counter":
            calls[0]["result"]["wrong_answers_left"] = 0
        elif self.fault == "transfer_note":
            note = {"product_id": promise["args"]["product_id"], "reason": "hardship"}
            calls[-1:-1] = [{"tool": "add_note", "args": note, "result": {"ok": True}},
                            {"tool": "transfer_to_agent", "args": {"queue": "restructuring"}, "result": {"ok": True}}]
            ledger.update(notes=[note], transfers=[{"queue": "restructuring"}])
        return ledger

    def validate_deployment(self, deployment):
        return deepcopy(self.pin)


class LedgerCaptureFaultTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts")
        self.addCleanup(self.temp.cleanup)
        self.number = 0

    def capture(self, candidate, fault=None):
        self.number += 1
        inputs = recorded_inputs(candidate)
        pin, records, paths = inputs[0]["deployment"], {}, {}
        for index, split in enumerate(("train", "validation"), 4):
            template = inputs[index]
            backend = FaultBackend(template["declared_rows"], f"{candidate}-{split}-{self.number}-", pin,
                                   fault if split == "train" else None)
            times = iter((template["started_at"], template["ended_at"]))
            _, path = record_evaluation(backend, pin["agent_id"], template["declared_rows"], directory=self.temp.name,
                                        conditions=template["conditions"], kind="live", deployment=pin,
                                        wall_clock=lambda: next(times), clock=lambda: 0.0)
            inputs[index] = records[split] = json.loads(path.read_text())
            paths[split] = path
        return assemble_bundle(*inputs), records, paths

    def test_behavior_faults_stay_comparable_strict_failures(self):
        baseline, _, _ = self.capture("baseline")
        for fault, messages in (
                ("duplicate", ("more than one successful promise write for a product",
                               "promises do not match the independent oracle")),
                ("order", ("close_call must occur exactly once and be the final tool",)),
                ("transfer_note", ("a transfer forbids notes before or after it",
                                   "transfers do not match the independent oracle"))):
            with self.subTest(fault=fault):
                structured, records, paths = self.capture("structured", fault)
                original = {split: path.read_bytes() for split, path in paths.items()}
                self.assertTrue(structured["ready"], structured)
                train = records["train"]["report"]
                self.assertEqual((train["status"], train["passed"]), ("completed", 0))
                for row in train["rows"]:
                    self.assertFalse(row["pass"])
                    for message in messages:
                        self.assertIn({"category": "behavior", "message": message}, row["failures"])
                    self.assertEqual({failure["category"] for failure in row["failures"]}, {"behavior"})
                    self.assertTrue(row["version_attribution"]["verified"])
                result = compare(baseline["bundle"], structured["bundle"])
                self.assertTrue(result["comparable"], result)
                self.assertEqual(result["splits"][0]["structured"]["strict_passed"], 0)
                self.assertEqual(result["splits"][1]["structured"]["strict_passed"],
                                 result["splits"][1]["baseline"]["strict_passed"])
                self.assertEqual({split: path.read_bytes() for split, path in paths.items()}, original)

    def test_contradicted_counter_and_foreign_version_are_never_comparable(self):
        baseline, _, _ = self.capture("baseline")
        complete, _, _ = self.capture("structured")
        self.assertTrue(complete["ready"], complete)
        for fault, failures in (
                ("counter", [{"category": "evidence", "message": "identity wrong-answer counter contradicts recorded checks"}]),
                ("version", [{"category": "infrastructure", "message": source + " used a different agent version"}
                             for source in ("judge", "transcript")])):
            with self.subTest(fault=fault):
                rejected, records, paths = self.capture("structured", fault)
                original = {split: path.read_bytes() for split, path in paths.items()}
                self.assertEqual(rejected, {"ready": False, "reason": "trial_infrastructure_incomplete"})
                train = records["train"]["report"]
                self.assertEqual((train["status"], train["passed"]), ("infrastructure_failure", 0))
                for row in train["rows"]:
                    self.assertFalse(row["pass"])
                    self.assertEqual(row["failures"], failures)
                # Assembly refuses it; the comparison boundary must refuse it independently too.
                candidate = deepcopy(complete["bundle"])
                candidate["trials"] = {split: trial_record(record, split) for split, record in records.items()}
                self.assertFalse(compare(baseline["bundle"], candidate)["comparable"])
                self.assertEqual({split: path.read_bytes() for split, path in paths.items()}, original)


if __name__ == "__main__":
    unittest.main()

"""Lifecycle boundaries using tiny invented source files and policy packets."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.export_inputs import export_profile
from benchmarks.freeze import consume, freeze_candidate
from benchmarks.offline import main as offline_main


class HoldoutConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        (self.candidate / "builder.py").write_text("synthetic = 1\n")
        self.record = self.root / "freeze.json"
        self.profile = "independently-named-bank"
        rows = [{"fixture_id": f"synthetic-{i}", "profile_id": self.profile,
                 "split": "train" if i < 36 else "validation" if i < 60 else "holdout",
                 "status": "materialized" if i == 119 else "reserved_until_candidate_freeze"}
                for i in range(120)]
        self.manifest = {"kind": "offline", "cases": rows,
                         "split_counts": {"train": 36, "validation": 24, "holdout": 60},
                         "profiles": [{"profile_id": self.profile,
                                       "policy_documents": {"policy": "Invented policy packet."},
                                       "openapi": {"openapi": "3.0.3", "servers": [{"url": "https://consumer-test.invalid"}],
                                           "paths": {"/close_call": {"post": {"operationId": "close_call",
                                               "requestBody": {"content": {"application/json": {"schema": {
                                                   "type": "object", "properties": {
                                                       key: {"type": "string", "description": "Semantic field: " + key}
                                                       for key in ("customer_id", "outcome", "reason")},
                                                   "required": ["customer_id", "outcome"]}}}},
                                               "responses": {"200": {"content": {"application/json": {"schema": {
                                                   "type": "object", "properties": {"ok": {"type": "boolean", "description": "Semantic result field: ok."}},
                                                   "required": ["ok"]}}}}}}}}}}]}
        self.path = self.root / "offline.json"
        self.contract = self.root / "candidate-contract.json"
        self.contract.write_text("{}")

    def save(self):
        self.path.write_text(json.dumps(self.manifest))

    def freeze(self):
        value = freeze_candidate(self.candidate, self.record)
        self.manifest["freeze"] = {"record": str(self.record), "candidate_sha256": value["candidate_sha256"]}
        self.save()

    def offline(self, *arguments):
        self.save()
        with patch("sys.argv", ["offline", "--cases", str(self.path), *arguments]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            offline_main()
        return json.loads(output.getvalue())

    def test_inspection_preserves_reserved_counts_without_claiming_evaluation(self):
        result = self.offline("--split", "holdout")
        self.assertEqual((result["count"], result["materialized"], result["reserved"]), (60, 1, 59))
        self.assertNotIn("rows", result)
        self.assertFalse(self.record.exists())

    def test_missing_freeze_blocks_checks_before_oracles_and_export_before_writes(self):
        self.save()
        with patch("benchmarks.offline.check_case", side_effect=AssertionError("oracle reached")):
            with self.assertRaisesRegex(ValueError, "freeze"):
                self.offline("--split", "holdout", "--check-helpers")
        with self.assertRaisesRegex(ValueError, "freeze"):
            export_profile(self.path, self.profile, self.root / "inputs", workspace_root=self.root)
        self.assertFalse((self.root / "inputs").exists())

    def test_independent_profile_name_cannot_bypass_candidate_gate_with_train_selection(self):
        with patch("benchmarks.offline.compare_contract", side_effect=AssertionError("candidate reached")):
            with self.assertRaisesRegex(ValueError, "freeze"):
                self.offline("--split", "train", "--profile", self.profile,
                             "--candidate-contract", str(self.contract))

    def test_drift_consumption_and_manifest_hash_mismatch_block_both_consumers(self):
        for failure in ("drift", "consumed", "manifest_hash"):
            with self.subTest(failure=failure):
                self.record = self.root / (failure + "-freeze.json")
                (self.candidate / "builder.py").write_text("synthetic = 1\n")
                self.freeze()
                if failure == "drift":
                    (self.candidate / "builder.py").write_text("synthetic = 2\n")
                elif failure == "consumed":
                    consume(self.record, "synthetic disclosure before repair")
                else:
                    self.manifest["freeze"]["candidate_sha256"] = "0" * 64
                    self.save()
                with self.assertRaises(ValueError):
                    self.offline("--split", "holdout", "--check-equivalence")
                output = self.root / (failure + "-inputs")
                with self.assertRaises(ValueError):
                    export_profile(self.path, self.profile, output, workspace_root=self.root)
                self.assertFalse(output.exists())

    def test_valid_freeze_allows_only_policy_api_export_and_candidate_assessment(self):
        self.freeze()
        inputs = export_profile(self.path, self.profile, self.root / "inputs", workspace_root=self.root)
        self.assertEqual(sorted(p.name for p in (self.root / "inputs").iterdir()), ["bank.openapi.json", "policy.md"])
        self.assertFalse(inputs["oracles_exported"])
        with patch("benchmarks.offline.compare_contract", return_value=[]) as compare:
            result = self.offline("--split", "train", "--profile", self.profile,
                                  "--candidate-contract", str(self.contract))
        compare.assert_called_once()
        self.assertEqual(result["contract_mismatches"], [])

    def test_valid_freeze_cannot_export_an_incomplete_api_contract(self):
        self.freeze()
        self.manifest["profiles"][0]["openapi"]["paths"]["/close_call"]["post"]["responses"] = {}
        self.save()
        with self.assertRaisesRegex(ValueError, "response schema"):
            export_profile(self.path, self.profile, self.root / "inputs", workspace_root=self.root)
        self.assertFalse((self.root / "inputs").exists())

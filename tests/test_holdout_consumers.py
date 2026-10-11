"""Lifecycle boundaries using tiny invented source files and policy packets."""
import contextlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmarks.export_inputs import export_profile
from benchmarks.freeze import consume, freeze_candidate
from benchmarks.generate import KIT
from benchmarks.hosted import main as hosted_main, run_builder_tests
from benchmarks.offline import main as offline_main
from benchmarks.paths import PROJECT
from builder.safety import digest


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
        rows[119]["expected"] = {"outcome": "synthetic-close", "promises": []}
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

    def lineage(self, frozen, expected):
        return {"authored_at": (datetime.fromisoformat(frozen["created_at"]) + timedelta(microseconds=1)).isoformat(),
                "author": "synthetic-independent-author", "candidate_sha256": frozen["candidate_sha256"],
                "oracle_sha256": digest(expected),
                "isolation": {"outside_generator_inputs": True, "candidate_outputs_consulted": False}}

    def freeze(self):
        value = freeze_candidate(self.candidate, self.record)
        self.manifest["freeze"] = {"record": str(self.record), "candidate_sha256": value["candidate_sha256"]}
        self.manifest["cases"][119]["lineage"] = self.lineage(value, self.manifest["cases"][119]["expected"])
        self.save()
        return value

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

    def test_missing_or_false_lineage_blocks_both_consumers_before_use(self):
        frozen = self.freeze()
        created = datetime.fromisoformat(frozen["created_at"])
        variants = {
            "missing": lambda lineage, row: row.pop("lineage"),
            "authored before freeze": lambda lineage, row: lineage.update(authored_at=(created - timedelta(seconds=1)).isoformat()),
            "authored in future": lambda lineage, row: lineage.update(authored_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
            "naive timestamp": lambda lineage, row: lineage.update(authored_at="2099-01-01T00:00:00"),
            "blank author": lambda lineage, row: lineage.update(author=" "),
            "other freeze": lambda lineage, row: lineage.update(candidate_sha256="0" * 64),
            "oracle edited after authoring": lambda lineage, row: row["expected"].update(outcome="changed"),
            "missing oracle": lambda lineage, row: row.pop("expected"),
            "integer isolation flags": lambda lineage, row: lineage.update(
                isolation={"outside_generator_inputs": 1, "candidate_outputs_consulted": 0}),
            "candidate outputs consulted": lambda lineage, row: lineage["isolation"].update(candidate_outputs_consulted=True),
            "extra field": lambda lineage, row: lineage.update(payload="synthetic"),
        }
        original = deepcopy(self.manifest["cases"][119])
        for index, (label, mutate) in enumerate(variants.items()):
            row = self.manifest["cases"][119] = deepcopy(original)
            mutate(row["lineage"], row)
            output = self.root / f"lineage-inputs-{index}"
            with self.subTest(label=label):
                with patch("benchmarks.offline.compare_contract", side_effect=AssertionError("candidate reached")):
                    with self.assertRaisesRegex(ValueError, "authored holdout"):
                        self.offline("--split", "train", "--profile", self.profile,
                                     "--candidate-contract", str(self.contract))
                with self.assertRaisesRegex(ValueError, "authored holdout"):
                    export_profile(self.path, self.profile, output, workspace_root=self.root)
                self.assertFalse(output.exists())

    def test_hosted_holdout_gates_require_lineage_before_any_client(self):
        config = self.root / "runtime.json"
        config.write_text(json.dumps({"model": "synthetic-model", "reasoning_effort": "high"}))
        frozen = freeze_candidate(self.candidate, self.record, config)
        seed = json.loads((KIT / "public-test-set.json").read_text())[0]
        row = {"fixture_id": "synthetic-hosted-holdout", "bank_case_id": seed["id"], "brief": seed["brief"],
               "split": "holdout", "status": "materialized_after_freeze", "style": "plain",
               "case": {**seed, "id": "synthetic-hosted-holdout"}}
        cases = self.root / "hosted.json"
        never = PROJECT / "artifacts" / "t16-synthetic-lineage-never-written"
        deployment = {"agent_id": "synthetic-agent", "brief": seed["brief"],
                      "model": "synthetic-model", "reasoning_effort": "high"}
        cli = ["--execute", "--cases", str(cases), "--split", "holdout", "--agent-id", "synthetic-agent",
               "--simulation-lease", str(self.root / "synthetic.lock"),
               "--out", str(never / "results.json"), "--evidence-dir", str(never / "evidence")]
        for lineage in (None, self.lineage(frozen, seed["expected"])):
            authored = {**row, "lineage": lineage} if lineage else row
            cases.write_text(json.dumps({"kind": "hosted", "cases": [authored], "freeze": {
                "record": str(self.record), "candidate_sha256": frozen["candidate_sha256"]}}))
            with self.subTest(lineage=bool(lineage)), \
                    patch("benchmarks.hosted.HostedBackend", side_effect=RuntimeError("backend reached")), \
                    patch("builder.platform.FreyaClient", side_effect=AssertionError("client created")):
                expected = (ValueError, "authored holdout") if lineage is None else (RuntimeError, "backend reached")
                with self.assertRaisesRegex(*expected):
                    run_builder_tests(Mock(), deployment, cases, never, Mock())
                # Without lineage the CLI stops at the gate; with it, at the next deployment requirement.
                expected = "authored holdout" if lineage is None else "deployment-manifest"
                with self.assertRaisesRegex(ValueError, expected):
                    hosted_main(cli)
        self.assertFalse(never.exists())

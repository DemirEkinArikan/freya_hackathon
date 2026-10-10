"""Synthetic lifecycle checks only: no real authored holdouts or hosted calls."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks import freeze


class HoldoutLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.candidate = self.root / "candidate"; self.candidate.mkdir()
        (self.candidate / "builder.py").write_text("synthetic = True\n")
        self.config = self.root / "runtime.json"
        self.config.write_text(json.dumps({"model": "synthetic-model"}))
        self.record = self.root / "freeze.json"
        self.directory = self.root / "fixtures"; self.directory.mkdir()
        self.kit = self.root / "synthetic-kit"; self.kit.mkdir()
        (self.kit / "public-test-set.json").write_text(json.dumps([
            {"id": "SYN-1", "brief": "A", "script": "Synthetic customer text.", "checks": []}
        ]))
        self.offline = {
            "version": 1, "kind": "offline", "count": 2,
            "split_counts": {"train": 0, "validation": 0, "holdout": 2},
            "profiles": [], "cases": [
                {"fixture_id": f"holdout-{i}-SYN", "profile_id": f"holdout-{i}",
                 "archetype_id": "SYN", "split": "holdout", "official_policy": False,
                 "status": "reserved_until_candidate_freeze"} for i in (1, 2)
            ],
        }
        self.hosted = {
            "version": 1, "kind": "hosted", "count": 1,
            "split_counts": {"train": 0, "validation": 0, "holdout": 1},
            "cases": [{"fixture_id": "HOST-SYN", "bank_case_id": "SYN-1", "brief": "A",
                       "split": "holdout", "style": "plain", "status": "reserved_until_candidate_freeze",
                       "reservation": {"seed": "SYN-1", "style": "plain", "compound_index": None}}],
        }
        self.write_manifests()

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifests(self):
        for name, manifest in (("offline", self.offline), ("hosted", self.hosted)):
            (self.directory / (name + ".json")).write_text(json.dumps(manifest))

    def fixture_bytes(self):
        return {p.name: p.read_bytes() for p in self.directory.iterdir()}

    def freeze_candidate(self):
        return freeze.freeze_candidate(self.candidate, self.record, self.config)

    def prohibit_generation(self, stack):
        for name in ("profile", "fixture"):
            stack.enter_context(patch.object(freeze, name, side_effect=AssertionError("generation attempted")))
        stack.enter_context(patch.object(freeze.secrets, "randbits", side_effect=AssertionError("randomness attempted")))

    def synthetic_catalog(self, stack):
        stack.enter_context(patch.object(freeze, "KIT", self.kit))
        stack.enter_context(patch.object(freeze, "archetypes", return_value=[{"archetype_id": "SYN", "split": "holdout"}]))

    def test_readiness_counts_actual_reservations_without_freeze_or_mutation(self):
        before = self.fixture_bytes()
        with ExitStack() as stack:
            self.prohibit_generation(stack)
            stack.enter_context(patch.object(Path, "write_text", side_effect=AssertionError("write attempted")))
            stack.enter_context(patch.object(Path, "mkdir", side_effect=AssertionError("mkdir attempted")))
            stack.enter_context(patch.object(freeze, "verify_freeze", side_effect=AssertionError("freeze not created")))
            result = freeze.readiness_inventory(self.candidate, self.directory, self.config)
        self.assertEqual(result["status"], "readiness_only")
        self.assertFalse(result["acceptance"])
        self.assertEqual(result["candidate_sha256"], freeze.candidate_digest(self.candidate, self.config)[0])
        self.assertEqual(result["files"], freeze.candidate_digest(self.candidate, self.config)[1])
        self.assertEqual(result["source_file_count"], 1)
        for name, actual in (("offline", 2), ("hosted", 1)):
            inventory = result["manifests"][name]
            self.assertEqual(inventory["inspected_rows"], actual)
            self.assertEqual(inventory["reserved_holdout_rows"], actual)
            self.assertEqual(inventory["materialized_holdout_rows"], 0)
            self.assertEqual(inventory["actual_split_counts"], {"train": 0, "validation": 0, "holdout": actual})
        self.assertEqual(self.fixture_bytes(), before)
        self.assertFalse(self.record.exists())

    def test_readiness_separates_materialized_synthetic_cases_from_reserved_slots(self):
        self.offline["cases"][0].update(status="materialized", script="Synthetic text.", expected={})
        self.write_manifests()
        before = self.fixture_bytes()
        with ExitStack() as stack:
            self.prohibit_generation(stack)
            result = freeze.readiness_inventory(self.candidate, self.directory)
        offline = result["manifests"]["offline"]
        self.assertEqual(offline["reserved_holdout_rows"], 1)
        self.assertEqual(offline["materialized_holdout_rows"], 1)
        self.assertEqual(offline["inspected_rows"], 2)
        self.assertFalse(result["acceptance"])
        self.assertEqual(self.fixture_bytes(), before)

    def test_missing_or_changed_freeze_stops_before_generation_and_preserves_manifests(self):
        before = self.fixture_bytes()
        with ExitStack() as stack:
            self.prohibit_generation(stack)
            with self.assertRaises((ValueError, FileNotFoundError)):
                freeze.generate_holdout(self.record, self.directory)
        self.freeze_candidate()
        (self.candidate / "builder.py").write_text("synthetic = 'changed'\n")
        with ExitStack() as stack:
            self.prohibit_generation(stack)
            with self.assertRaises(ValueError):
                freeze.generate_holdout(self.record, self.directory)
        self.assertEqual(self.fixture_bytes(), before)

    def test_invalid_reservations_fail_preflight_without_generation_or_partial_write(self):
        self.freeze_candidate()
        original_offline, original_hosted = deepcopy(self.offline), deepcopy(self.hosted)
        variants = (
            ("false total", lambda o, h: o.update(count=60)),
            ("false split count", lambda o, h: h["split_counts"].update(holdout=60)),
            ("duplicate ID", lambda o, h: o["cases"][1].update(fixture_id=o["cases"][0]["fixture_id"])),
            ("wrong seed", lambda o, h: h["cases"][0]["reservation"].update(seed="SYN-OTHER")),
            ("wrong style", lambda o, h: h["cases"][0]["reservation"].update(style="pieces")),
            ("out of range compound", lambda o, h: h["cases"][0]["reservation"].update(compound_index=999)),
            ("missing reservation", lambda o, h: h["cases"][0].pop("reservation")),
            ("materialized holdout", lambda o, h: o["cases"][0].update(status="materialized")),
            ("non-list profiles", lambda o, h: o.update(profiles={})),
            ("invalid profile row", lambda o, h: o.update(profiles=[None])),
            ("premature holdout profile", lambda o, h: o.update(profiles=[{"profile_id": "holdout-1"}])),
        )
        for label, mutate in variants:
            self.offline, self.hosted = deepcopy(original_offline), deepcopy(original_hosted)
            mutate(self.offline, self.hosted); self.write_manifests()
            before = self.fixture_bytes()
            with self.subTest(label=label), ExitStack() as stack:
                self.synthetic_catalog(stack)
                self.prohibit_generation(stack)
                with self.assertRaises(ValueError):
                    freeze.generate_holdout(self.record, self.directory)
            self.assertEqual(self.fixture_bytes(), before)

    def test_generation_output_under_candidate_is_rejected_before_materialization(self):
        self.freeze_candidate()
        directory = self.candidate / "new-fixtures"
        with ExitStack() as stack:
            self.prohibit_generation(stack)
            with self.assertRaises(ValueError):
                freeze.generate_holdout(self.record, directory)
        self.assertFalse(directory.exists())
        freeze.verify_freeze(self.record)

    def test_generation_cannot_follow_linked_fixture_parent(self):
        self.freeze_candidate()
        link = self.root / "fixture-link"; link.symlink_to(self.directory, target_is_directory=True)
        before = self.fixture_bytes()
        with ExitStack() as stack:
            self.prohibit_generation(stack)
            with self.assertRaises(ValueError):
                freeze.generate_holdout(self.record, link)
        self.assertEqual(self.fixture_bytes(), before)

    def test_generation_output_cannot_overlap_frozen_configuration_inputs(self):
        config = self.directory / "runtime.json"
        config.write_bytes(self.config.read_bytes())
        freeze.freeze_candidate(self.candidate, self.record, config)
        before = self.fixture_bytes()
        with ExitStack() as stack:
            self.prohibit_generation(stack)
            with self.assertRaises(ValueError):
                freeze.generate_holdout(self.record, self.directory)
        self.assertEqual(self.fixture_bytes(), before)
        freeze.verify_freeze(self.record)

    def test_synthetic_tiny_materialization_reports_actual_counts_and_cannot_reblind(self):
        frozen = self.freeze_candidate()
        with ExitStack() as stack:
            self.synthetic_catalog(stack)
            stack.enter_context(patch.object(freeze, "profile", side_effect=lambda name, *a, **k: {"profile_id": name}))
            stack.enter_context(patch.object(freeze, "fixture", side_effect=lambda a, p: {
                **a, "fixture_id": p["profile_id"] + "-SYN", "profile_id": p["profile_id"], "status": "materialized",
                "script": "Synthetic customer text.", "expected": {}
            }))
            result = freeze.generate_holdout(self.record, self.directory)
        actual = {name: sum(row["split"] == "holdout" and row["status"].startswith("materialized")
                            for row in json.loads((self.directory / (name + ".json")).read_text())["cases"])
                  for name in ("offline", "hosted")}
        self.assertEqual(actual, {"offline": 2, "hosted": 1})
        self.assertEqual({key: result[key] for key in actual}, actual)
        self.assertFalse(result["acceptance"])
        self.assertEqual(result["candidate_sha256"], frozen["candidate_sha256"])
        before = self.fixture_bytes()
        with ExitStack() as stack:
            self.prohibit_generation(stack)
            with self.assertRaises(ValueError):
                freeze.generate_holdout(self.record, self.directory)
        self.assertEqual(self.fixture_bytes(), before)

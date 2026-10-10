"""Temporary synthetic snapshots only; no holdouts, models or hosted calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.freeze import candidate_digest, consume, freeze_candidate, verify_freeze


class FreezeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        (self.candidate / "builder.py").write_text("synthetic = 1\n")
        self.config = self.root / "runtime.json"
        self.config.write_text(json.dumps({"model": "synthetic-model", "reasoning_effort": "high"}))
        self.record = self.root / "freeze.json"

    def tearDown(self):
        self.tmp.cleanup()

    def freeze(self, config=True):
        return freeze_candidate(self.candidate, self.record, self.config if config else None)

    def rewrite(self, value):
        self.record.write_text(json.dumps(value))

    def test_unchanged_version_one_snapshot_and_runtime_config_verify(self):
        value = self.freeze()
        self.assertEqual(verify_freeze(self.record), value)
        self.assertEqual(candidate_digest(self.candidate, self.config)[1], value["files"])

    def test_changed_resolved_config_cannot_self_declare_a_different_model(self):
        value = self.freeze()
        value["resolved_config"]["model"] = "not-actually-used"
        self.rewrite(value)
        with self.assertRaisesRegex(ValueError, "configuration does not match") as caught:
            verify_freeze(self.record)
        self.assertNotIn("not-actually-used", str(caught.exception))

    def test_changed_files_inventory_is_rejected_even_if_candidate_hash_is_unchanged(self):
        value = self.freeze()
        for files in ({}, {"builder.py": "0" * 64}):
            changed = {**value, "files": files}
            self.rewrite(changed)
            with self.subTest(files=len(files)), self.assertRaises(ValueError):
                verify_freeze(self.record)

    def test_consistently_rehashed_false_snapshot_still_fails_actual_comparison(self):
        value = self.freeze()
        value["files"]["builder.py"] = "0" * 64
        value["candidate_sha256"] = hashlib.sha256(json.dumps(value["files"], sort_keys=True).encode()).hexdigest()
        self.rewrite(value)
        with self.assertRaisesRegex(ValueError, "candidate changed"):
            verify_freeze(self.record)

    def test_unknown_version_status_missing_fields_extra_fields_and_timestamps_fail(self):
        original = self.freeze()
        variants = []
        for key, value in (("version", 2), ("version", True), ("status", "approved-by-fixture"),
                           ("created_at", "2026-10-10T12:00:00"), ("created_at", "not-a-time")):
            variants.append({**original, key: value})
        missing = deepcopy(original); missing.pop("files"); variants.append(missing)
        variants.append({**original, "arbitrary_private_field": "private-value"})
        variants.append({**original, "consumed_reason": "unrecorded disclosure"})
        for value in variants:
            self.rewrite(value)
            with self.subTest(fields=len(value)), self.assertRaises(ValueError) as caught:
                verify_freeze(self.record)
            self.assertNotIn("private-value", str(caught.exception))

    def test_runtime_config_pair_and_digest_must_be_complete(self):
        original = self.freeze()
        for key in ("config_file", "resolved_config"):
            changed = deepcopy(original); changed.pop(key); self.rewrite(changed)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "configuration is incomplete"):
                verify_freeze(self.record)

    def test_duplicate_json_keys_are_rejected(self):
        value = self.freeze()
        text = json.dumps(value)
        self.record.write_text(text[:-1] + ', "status": "frozen"}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            verify_freeze(self.record)

    def test_source_change_file_addition_and_removal_invalidate_snapshot(self):
        self.freeze(config=False)
        (self.candidate / "helper.py").write_text("helper = 1\n")
        with self.assertRaisesRegex(ValueError, "candidate changed"):
            verify_freeze(self.record)
        (self.candidate / "helper.py").unlink()
        (self.candidate / "builder.py").write_text("synthetic = 2\n")
        with self.assertRaisesRegex(ValueError, "candidate changed"):
            verify_freeze(self.record)
        (self.candidate / "builder.py").unlink()
        with self.assertRaises(ValueError):
            verify_freeze(self.record)

    def test_included_source_symlink_rejected_without_reading_target(self):
        target = self.root / "target.py"
        target.write_text("private-target-value\n")
        (self.candidate / "helper.py").symlink_to(target)
        real_read = Path.read_bytes
        def protected_read(path):
            if path == target:
                raise AssertionError("linked target must not be read")
            return real_read(path)
        with patch.object(Path, "read_bytes", protected_read), self.assertRaisesRegex(ValueError, "cannot be symlinks"):
            candidate_digest(self.candidate)
        (self.candidate / "helper.py").unlink()
        (self.candidate / "helper.py").symlink_to(self.root / "nonexistent.py")
        with self.assertRaisesRegex(ValueError, "cannot be symlinks"):
            candidate_digest(self.candidate)

    def test_visible_source_directory_symlink_rejected_without_traversal(self):
        target = self.root / "external-directory"; target.mkdir()
        (target / "module.py").write_text("module = 1\n")
        (self.candidate / "package").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "source directories"):
            candidate_digest(self.candidate)

    def test_excluded_hidden_cache_and_non_source_symlinks_remain_ignored(self):
        original = candidate_digest(self.candidate)
        target = self.root / "external-cache"; target.mkdir()
        (target / "cached.py").write_text("irrelevant = 1\n")
        for name in (".private", "__pycache__"):
            (self.candidate / name).symlink_to(target, target_is_directory=True)
        (self.candidate / "image.png").symlink_to(self.root / "missing-image.png")
        (self.candidate / ".ignored.py").symlink_to(self.root / "missing-hidden.py")
        self.assertEqual(candidate_digest(self.candidate), original)

    def test_root_config_and_record_symlinks_are_rejected(self):
        linked_root = self.root / "linked-root"; linked_root.symlink_to(self.candidate, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "source root"):
            candidate_digest(linked_root)
        linked_config = self.root / "linked.json"; linked_config.symlink_to(self.config)
        with self.assertRaisesRegex(ValueError, "configuration cannot be a symlink"):
            freeze_candidate(self.candidate, self.record, linked_config)
        self.freeze()
        linked_record = self.root / "linked-freeze.json"; linked_record.symlink_to(self.record)
        with self.assertRaisesRegex(ValueError, "record cannot be a symlink"):
            verify_freeze(linked_record)

    def test_source_replaced_by_link_after_freeze_is_rejected(self):
        self.freeze()
        target = self.root / "replacement.py"; target.write_text("synthetic = 1\n")
        (self.candidate / "builder.py").unlink()
        (self.candidate / "builder.py").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "cannot be symlinks"):
            verify_freeze(self.record)

    def test_snapshot_reads_resolved_config_once_and_preserves_that_value(self):
        from benchmarks import freeze
        real_read = freeze.read_config
        with patch.object(freeze, "read_config", wraps=real_read) as read:
            value = self.freeze()
        self.assertEqual(read.call_count, 1)
        self.assertEqual(value["resolved_config"]["model"], "synthetic-model")
        verify_freeze(self.record)

    def test_consumption_after_source_repair_remains_allowed_and_cannot_reblind(self):
        self.freeze()
        (self.candidate / "builder.py").write_text("synthetic = 2\n")
        consume(self.record, "repair after disclosure")
        first = self.record.read_bytes()
        with self.assertRaisesRegex(ValueError, "cannot be called blind"):
            verify_freeze(self.record)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            consume(self.record, "overwrite original reason")
        self.assertEqual(self.record.read_bytes(), first)

    def test_consumed_record_requires_original_disclosure_metadata(self):
        value = self.freeze(); value["status"] = "consumed"; self.rewrite(value)
        with self.assertRaisesRegex(ValueError, "disclosure metadata"):
            verify_freeze(self.record)

    def test_freeze_record_under_source_is_rejected_before_any_output_is_created(self):
        before = candidate_digest(self.candidate)
        for name in ("freeze.json", "new-output/freeze.json", "new-output/record.bin"):
            record = self.candidate / name
            with self.subTest(name=name), self.assertRaises(ValueError):
                freeze_candidate(self.candidate, record, self.config)
            self.assertFalse(record.exists())
            self.assertFalse((self.candidate / "new-output").exists())
            self.assertEqual(candidate_digest(self.candidate), before)

    def test_freeze_output_cannot_follow_a_symlinked_parent(self):
        target = self.root / "output-target"; target.mkdir()
        parent = self.root / "linked-output"; parent.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            freeze_candidate(self.candidate, parent / "freeze.json", self.config)
        self.assertEqual(list(target.iterdir()), [])

    def test_source_and_config_cannot_follow_symlinked_ancestors(self):
        linked = self.root / "linked-parent"; linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            candidate_digest(linked / "candidate")
        with self.assertRaises(ValueError):
            freeze_candidate(self.candidate, self.record, linked / "runtime.json")
        self.assertFalse(self.record.exists())

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from builder.safety import digest
from builder.source_provenance import capture_source_provenance, verify_source_unchanged, bind_run_provenance


class SourceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "builder" / "prompts").mkdir(parents=True)
        (self.root / "benchmarks").mkdir()
        (self.root / "builder" / "build.py").write_text("print('original')\n")
        (self.root / "builder" / "prompts" / "extract.txt").write_text("Read supplied policy\n")
        (self.root / "benchmarks" / "hosted.py").write_text("# original evaluator\n")
        self.config = {"model": "model", "reasoning_effort": "high", "deadline_seconds": 1800}

    def test_same_source_config_stable_fingerprint_and_changed_config_distinct(self):
        first = capture_source_provenance(self.root, self.config)
        second = capture_source_provenance(self.root, self.config)
        self.assertEqual(first["builder_source_sha256"], second["builder_source_sha256"])
        self.assertNotEqual(first["builder_source_sha256"], capture_source_provenance(
            self.root, {**self.config, "reasoning_effort": "medium"})["builder_source_sha256"])
        verify_source_unchanged(self.root, first)

    def test_prompt_or_module_change_invalidates_original_source(self):
        first = capture_source_provenance(self.root, self.config)
        (self.root / "builder" / "prompts" / "extract.txt").write_text("Changed policy prompt\n")
        with self.assertRaisesRegex(ValueError, "changed during"):
            verify_source_unchanged(self.root, first)

    def test_tampered_original_hash_is_rejected(self):
        first = capture_source_provenance(self.root, self.config)
        first["resolved_config"]["model"] = "other"
        with self.assertRaisesRegex(ValueError, "stale"):
            verify_source_unchanged(self.root, first)

    def test_credentials_outputs_and_fixture_links_are_not_inspected(self):
        (self.root / "builder" / ".env").write_text("TOKEN=private")
        (self.root / "builder" / "__pycache__").mkdir()
        (self.root / "builder" / "__pycache__" / "cached.py").symlink_to("/not-readable")
        (self.root / "benchmarks" / "fixtures").symlink_to("/not-readable")
        (self.root / "builder" / "fixtures").symlink_to("/not-readable")
        (self.root / "builder" / "notes.txt").write_text("private")
        (self.root / "builder" / "private.json").write_text('{"private":"material"}')
        first = capture_source_provenance(self.root, self.config)
        self.assertEqual(set(first["files"]), {"builder/build.py", "builder/prompts/extract.txt", "benchmarks/hosted.py"})
        self.assertNotIn("private", json.dumps(first))

    def test_runtime_file_or_directory_symlink_fails_closed(self):
        (self.root / "builder" / "linked.py").symlink_to("/not-readable")
        with self.assertRaisesRegex(ValueError, "regular files"):
            capture_source_provenance(self.root, self.config)
        (self.root / "builder" / "linked.py").unlink()
        (self.root / "builder" / "prompts" / "extract.txt").unlink()
        (self.root / "builder" / "prompts").rmdir()
        (self.root / "builder" / "prompts").symlink_to("/not-readable")
        with self.assertRaisesRegex(ValueError, "cannot be linked"):
            capture_source_provenance(self.root, self.config)

    def test_resource_limit_rejects_before_reading_large_file(self):
        with patch("builder.source_provenance.MAX_FILE_BYTES", 2):
            with self.assertRaisesRegex(ValueError, "resource limits"):
                capture_source_provenance(self.root, self.config)

    def test_binding_uses_original_manifest_trace_and_source_record(self):
        first = capture_source_provenance(self.root, self.config)
        manifest = {"status": "exported", "builder_source_sha256": first["builder_source_sha256"],
                    "builder_source_record_sha256": digest(first)}
        events = [{"event": "start"}, {"event": "end", "status": "exported"}]
        result = bind_run_provenance(first, manifest, events)
        self.assertEqual(result["builder_source_record_sha256"], digest(first))
        self.assertEqual(result["build_manifest_sha256"], digest(manifest))
        self.assertEqual(result["trace_sha256"], digest(events))
        self.assertEqual(result["source_snapshot"], first)
        result["source_snapshot"]["files"].clear()
        self.assertTrue(first["files"])
        with self.assertRaisesRegex(ValueError, "differs"):
            bind_run_provenance(first, {"builder_source_sha256": "0" * 64}, events)


class SourceInvocationTests(unittest.TestCase):
    def test_snapshot_precedes_generation_and_is_bound_to_final_original_artifacts(self):
        from test_builder import BuilderTests, fixture
        from builder.build import execute, PROJECT
        helper = BuilderTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        generator = helper.generator()
        generator.generate.return_value = fixture()
        observed = []
        original = generator.generate_plan.side_effect
        def generate(prompt, **kwargs):
            runs = list((helper.directory / "out").iterdir())
            snapshot = json.loads((runs[0] / "builder-source.json").read_text())
            observed.append(snapshot["builder_source_sha256"])
            return original(prompt, **kwargs)
        generator.generate_plan.side_effect = generate
        manifest = execute(helper.args("--generate-only"), generator=generator)
        run = PROJECT / manifest["run_directory"]
        snapshot = json.loads((run / "builder-source.json").read_text())
        binding = json.loads((run / "build-provenance.json").read_text())
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        source_events = [event for event in events if event["event"] == "builder_source_snapshot"]
        self.assertEqual(len(source_events), 1)
        self.assertLess(events.index(source_events[0]), next(i for i, event in enumerate(events) if event["event"] == "stage_started"))
        self.assertEqual(observed, [manifest["builder_source_sha256"]])
        self.assertEqual(binding, bind_run_provenance(snapshot, manifest, events))
        self.assertTrue(source_events[0]["resolved_config"]["controller_dependency_injection"])

    def test_source_drift_prevents_a_success_manifest(self):
        from test_builder import BuilderTests, fixture
        from builder.build import execute
        helper = BuilderTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        generator = helper.generator()
        generator.generate.return_value = fixture()
        with patch("builder.build.verify_source_unchanged", side_effect=ValueError("builder source changed")):
            with self.assertRaisesRegex(ValueError, "source changed"):
                execute(helper.args("--generate-only"), generator=generator)
        run = next((helper.directory / "out").iterdir())
        manifest = json.loads((run / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertTrue(manifest["builder_source_sha256"])
        self.assertFalse((run / "build-provenance.json").exists())

    def test_completion_scan_cannot_overrun_deadline_and_report_success(self):
        from test_builder import BuilderTests, fixture
        from builder.build import execute
        from builder.runtime import Deadline, DeadlineExceeded
        helper = BuilderTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        generator = helper.generator()
        generator.generate.return_value = fixture()
        clock = [0.0]
        deadline = Deadline(1800, clock=lambda: clock[0])
        def slow_scan(*args):
            clock[0] = 1800.01
        with patch("builder.build.verify_source_unchanged", side_effect=slow_scan):
            with self.assertRaises(DeadlineExceeded):
                execute(helper.args("--generate-only"), generator=generator, deadline=deadline)
        run = next((helper.directory / "out").iterdir())
        self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "failed")
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        self.assertEqual([event["status"] for event in events if event["event"] == "end"], ["failed"])

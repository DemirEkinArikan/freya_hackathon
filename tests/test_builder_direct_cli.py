"""Real child-process checks for the documented direct-script builder command."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "builder" / "build.py"
API = ROOT / "build-the-builder" / "tools" / "openapi.json"


class DirectBuilderCliTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.base = Path(self.directory.name)
        self.environment = {"PATH": os.defpath, "HOME": str(self.base / "empty-home")}
        Path(self.environment["HOME"]).mkdir()
        self.brief = self.base / "brief.md"
        self.brief.write_text("Inspect this synthetic contact workflow offline. No banking actions are executed.\n")
        self.workflow = self.base / "workflow.json"
        self.workflow.write_text(json.dumps({
            "initial_node": "done",
            "nodes": {"done": {
                "name": "done", "node_type": "end_call", "on_enter": {"type": "silent"},
                "disable_inactivity_monitor": True, "transitions": [],
                "terminal": {"type": "function", "function": {
                    "name": "finished", "handler": "__function__:end_call",
                    "parameters": {"type": "object", "properties": {
                        "message_mode": {"type": "string", "default": "none"}}}}}}}}))

    def tearDown(self):
        self.directory.cleanup()

    def child(self, entry, args, *, cwd=ROOT):
        return subprocess.run([sys.executable, *entry, *args], cwd=cwd,
                              env=self.environment, capture_output=True, text=True, timeout=30)

    def test_direct_help_from_project_and_script_directory(self):
        for cwd in (ROOT, SCRIPT.parent):
            with self.subTest(cwd=cwd):
                result = self.child([str(SCRIPT)], ["--help"], cwd=cwd)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--tool-api", result.stdout)
                self.assertIn("--simulation-lease", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_module_and_isolated_direct_help(self):
        for entry in (["-m", "builder.build"], ["-I", str(SCRIPT)]):
            with self.subTest(entry=entry):
                result = self.child(entry, ["--help"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--tool-api", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_direct_and_module_offline_invocations_record_stdlib_runtime(self):
        for index, entry in enumerate(([str(SCRIPT)], ["-m", "builder.build"]), 1):
            with self.subTest(entry=entry):
                result = self.child(entry, ["--brief", str(self.brief), "--tool-api", str(API),
                    "--workflow-file", str(self.workflow), "--output-dir", str(self.base / f"out-{index}"),
                    "--offline", "--codex", str(self.base / "no-model-executable")])
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = json.loads(result.stdout)
                self.assertEqual(manifest["status"], "validated")
                self.assertEqual(manifest["mode"], "offline-fixture")
                self.assertFalse(manifest["hosted_behavior_verified"])
                self.assertFalse(manifest["action_guard_compilation_verified"])
                recorded = json.loads((ROOT / manifest["run_directory"] / "builder-source.json").read_text())
                self.assertEqual(recorded["resolved_config"]["python_implementation"], platform.python_implementation())
                self.assertEqual(recorded["resolved_config"]["python_version"], platform.python_version())
                self.assertEqual(result.stderr, "")

    def test_direct_entry_preserves_project_input_confinement(self):
        with tempfile.TemporaryDirectory() as outside:
            brief = Path(outside) / "brief.md"
            brief.write_text("This input is deliberately outside the builder checkout.\n")
            result = self.child([str(SCRIPT)], ["--brief", str(brief), "--tool-api", str(API),
                "--workflow-file", str(self.workflow), "--output-dir", str(self.base / "blocked"), "--offline"])
        self.assertEqual(result.returncode, 1)
        error = json.loads(result.stderr)
        self.assertEqual(error["status"], "failed")
        self.assertIn("Inputs and artifacts must stay in this standalone project", error["error"])
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()

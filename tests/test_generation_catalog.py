"""Catalog restrictions and the audit-to-generation boundary, without model calls."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from builder.preflight import isolation_probe
from builder.runtime import CodexGenerator, Deadline, GenerationError, Trace


def bundled_models():
    return {"models": [{"slug": "gpt-6.1-sol", "shell_type": "unified_exec",
                        "apply_patch_tool_type": "freeform", "tool_mode": "code_mode_only",
                        "experimental_supported_tools": ["clock", "send_user_message_async"],
                        "node_repl_disabled": False, "supports_search_tool": True,
                        "model_messages": {"instructions_template": "Synthetic bundled instructions"},
                        "context_window": 123456, "guardian": {"shell": "synthetic-policy"}}]}


def stream(value):
    return "\n".join(json.dumps(e) for e in [
        {"type": "thread.started", "thread_id": "synthetic-thread"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}},
        {"type": "turn.completed"}])


class GenerationCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def generator(self, run, model="gpt-6.1-sol"):
        return CodexGenerator(self.root, Trace(self.root / "trace.jsonl"), Deadline(), run=run, model=model)

    def test_only_tool_capabilities_change_and_host_metadata_is_not_modified(self):
        original = bundled_models()
        pristine = copy.deepcopy(original)
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, json.dumps(original), "")
        gen = self.generator(run)
        with patch.dict("os.environ", {"FREYA_API_KEY": "private-synthetic-token", "BASH_ENV": "private-startup"}):
            evidence = gen.prepare_catalog(self.root)
        document = json.loads((self.root / "generation.model-catalog.json").read_text())
        selected = document["models"][0]
        expected = copy.deepcopy(pristine["models"][0])
        expected.update(shell_type="disabled", apply_patch_tool_type=None, experimental_supported_tools=[],
                        node_repl_disabled=True, tool_mode="direct", supports_search_tool=False)
        self.assertEqual(selected, expected)
        self.assertEqual(original, pristine)
        self.assertEqual(calls[0][0], ["codex", "debug", "models", "--bundled"])
        self.assertNotIn("input", calls[0][1])
        self.assertNotIn("FREYA_API_KEY", calls[0][1]["env"])
        self.assertNotIn("BASH_ENV", calls[0][1]["env"])
        self.assertEqual(len(evidence["generation_catalog_sha256"]), 64)
        self.assertNotIn("instructions_template", json.dumps(evidence))
        self.assertIn('model_catalog_json=' + json.dumps(str(self.root / "generation.model-catalog.json")),
                      gen.argv(self.root, self.root / "schema.json"))

    def test_bad_missing_ambiguous_or_unsupported_catalog_blocks_before_prompt(self):
        cases = ["private malformed catalog", "[]", "{}", '{"models":{}}', '{"models":[null]}',
                 '{"models":[],"models":[]}', json.dumps({"models": []}),
                 json.dumps({"models": bundled_models()["models"] * 2})]
        for stdout in cases:
            sent = []
            def run(argv, **kwargs):
                sent.append(kwargs)
                return subprocess.CompletedProcess(argv, 0, stdout, "private diagnostic")
            with self.subTest(stdout=stdout[:30]):
                gen = self.generator(run)
                gen._checked = True
                with self.assertRaisesRegex(GenerationError, "no generation prompt was sent") as error:
                    gen.generate("BANK BRIEF")
                self.assertNotIn("private", str(error.exception))
                self.assertFalse(any("input" in kwargs for kwargs in sent))
        for model in (None, "missing-model"):
            with self.subTest(model=model), self.assertRaises(GenerationError):
                self.generator(lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(bundled_models()), ""),
                               model=model).generation_catalog()

    def test_catalog_process_failure_is_sanitized_and_probe_fails_closed(self):
        for outcome in (subprocess.CompletedProcess([], 1, "private", "private"),
                        subprocess.TimeoutExpired([], 1, output="private"), OSError("private")):
            def run(argv, **kwargs):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            with self.subTest(outcome=type(outcome).__name__):
                report = isolation_probe(self.root, run=run)
                self.assertEqual(report["status"], "fail")
                self.assertNotIn("private", json.dumps(report))

    def test_audit_policy_and_plan_use_identical_catalog_bytes_and_cleanup(self):
        seen, directories, inputs = [], [], []
        exports = []
        def run(argv, **kwargs):
            if argv[1:4] == ["debug", "models", "--bundled"]:
                exports.append(argv)
                return subprocess.CompletedProcess(argv, 0, json.dumps(bundled_models()), "")
            directory = Path(kwargs["cwd"])
            seen.append((directory / "generation.model-catalog.json").read_bytes())
            directories.append(directory)
            inputs.append(kwargs["input"])
            schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
            if "available_tools" in schema["properties"]:
                value = {"available_tools": [], "loaded_markers": [],
                         "forbidden_content": "UNAVAILABLE", "environment_content": "UNAVAILABLE"}
            elif "plan_json" in schema["properties"]:
                value = {"plan_json": "{}", "contract_sha256": "a" * 64}
            else:
                value = {"policy_json": "{}", "workflow_json": "{}"}
            return subprocess.CompletedProcess(argv, 0, stream(value), "")
        gen = self.generator(run)
        gen._checked = True
        self.assertEqual(gen.generate("approved policy input"), {"policy": {}, "workflow": {}})
        self.assertEqual(gen.generate_plan("approved frozen contract", contract_sha256="a" * 64),
                         {"plan": {}, "contract_sha256": "a" * 64})
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(inputs[1:], ["approved policy input", "approved frozen contract"])
        self.assertEqual(len(exports), 2)  # generation cache and independent probe
        self.assertTrue(all(not directory.exists() for directory in directories))

    def test_metadata_drift_between_generation_and_probe_cannot_accept_audit(self):
        exports, inputs = [], []
        def run(argv, **kwargs):
            if argv[1:4] == ["debug", "models", "--bundled"]:
                exports.append(argv)
                catalog = bundled_models()
                catalog["models"][0]["context_window"] += len(exports)
                return subprocess.CompletedProcess(argv, 0, json.dumps(catalog), "")
            inputs.append(kwargs["input"])
            value = {"available_tools": [], "loaded_markers": [],
                     "forbidden_content": "UNAVAILABLE", "environment_content": "UNAVAILABLE"}
            return subprocess.CompletedProcess(argv, 0, stream(value), "")
        gen = self.generator(run)
        gen._checked = True
        with self.assertRaisesRegex(GenerationError, "catalog changed"):
            gen.generate("BANK BRIEF")
        self.assertEqual(len(inputs), 1)
        self.assertNotIn("BANK BRIEF", inputs)
        self.assertFalse(gen._isolation_checked)

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from builder.preflight import inspect_probe, isolation_probe, preflight
from builder.runtime import CodexGenerator, Deadline, GenerationError, Trace


def response(**changes):
    value = {"available_tools": [], "loaded_markers": [], "forbidden_content": "UNAVAILABLE", "environment_content": "UNAVAILABLE"}
    value.update(changes)
    return json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}}) + '\n' + json.dumps({"type": "turn.completed"})


class PreflightTests(unittest.TestCase):
    def test_probe_catches_unknown_canary_in_reasoning_and_discards_text(self):
        marker = "random-never-in-prompt"
        text = json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": marker}}) + "\n" + response()
        report = inspect_probe(subprocess.CompletedProcess([], 0, text, ""), {"skill_body": marker})
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["canary_exposure"], ["skill_body"])
        self.assertNotIn(marker, json.dumps(report))

    def test_tool_action_is_fatal_even_when_final_response_claims_no_tools(self):
        for item_type in ("command_execution", "file_change"):
            with self.subTest(item_type=item_type):
                tool = {"type": "item.completed", "item": {"type": item_type, "command": "sensitive command"}}
                report = inspect_probe(subprocess.CompletedProcess([], 0, json.dumps(tool) + "\n" + response(), ""), {})
                self.assertIn("tool_event", report["failures"])
                self.assertNotIn("sensitive command", json.dumps(report))

    def test_incomplete_turn_cannot_pass_with_valid_json(self):
        text = response().splitlines()[0]
        report = inspect_probe(subprocess.CompletedProcess([], 0, text, ""), {})
        self.assertIn("incomplete_or_repeated_turn", report["failures"])

    def test_reported_loader_or_tool_presence_fails_without_raw_context(self):
        report = inspect_probe(subprocess.CompletedProcess([], 0, response(loaded_markers=["private source text"], available_tools=["functions.exec", "private source text"]), ""), {})
        self.assertIn("model_reports_loader_content", report["failures"])
        self.assertIn("model_reports_available_tools", report["failures"])
        self.assertEqual(report["model_reported_tool_count"], 2)
        self.assertEqual(report["model_reported_tools"], ["functions.exec"])
        self.assertNotIn("private source text", json.dumps(report))

    def test_public_namespaced_tool_aliases_are_displayed_but_still_fail_isolation(self):
        names = ["functions.exec_command", "functions.shell_command", "functions.apply_patch", "functions.write_stdin"]
        report = inspect_probe(subprocess.CompletedProcess([], 0, response(available_tools=names + ["private unrecognized name"]), ""), {})
        self.assertEqual(report["status"], "fail")
        self.assertIn("model_reports_available_tools", report["failures"])
        self.assertEqual(report["model_reported_tools"], sorted(names))
        self.assertEqual(report["model_reported_tool_count"], 5)
        self.assertEqual(report["unrecognized_tool_name_count"], 1)
        self.assertNotIn("private unrecognized name", json.dumps(report))

    def test_skill_budget_warning_records_omitted_count_and_is_not_zero_loader_claim(self):
        warning = {"type": "item.completed", "item": {"type": "error", "message": "Exceeded skills context budget. All skill descriptions were removed and 4 additional skills were not included in the model-visible skills list."}}
        report = inspect_probe(subprocess.CompletedProcess([], 0, json.dumps(warning) + "\n" + response(), ""), {})
        self.assertEqual(report["status"], "fail")
        self.assertIn("loader_absence_unproven", report["failures"])
        self.assertEqual(report["omitted_skill_count"], 4)
        self.assertIn("skill_descriptions_removed_by_context_budget", report["cli_warnings"])

    def test_unknown_diagnostic_and_failure_cannot_be_disguised_as_success(self):
        warning = {"type": "item.completed", "item": {"type": "error", "message": "Unknown sensitive diagnostic"}}
        report = inspect_probe(subprocess.CompletedProcess([], 1, json.dumps(warning) + "\n" + response(), ""), {})
        self.assertIn("unknown_cli_diagnostic", report["failures"])
        self.assertIn("nonzero_exit", report["failures"])
        self.assertEqual(report["unknown_cli_diagnostics"], ["Unknown sensitive diagnostic"])
        self.assertEqual(report["unknown_cli_diagnostic_count"], 1)
        self.assertFalse(report["unknown_cli_diagnostics_truncated"])

    def test_unknown_diagnostic_metadata_redacts_before_clipping_and_discards_other_text(self):
        credential = "opaque-known-credential"
        canary = "random-never-in-prompt"
        message = "Unsupported diagnostic " + credential + " " + canary + " /Users/private-user/config.toml " + "x" * 2500 + " sk-actualtoken12345678"
        error = json.dumps({"type": "item.completed", "item": {"type": "error", "message": message}})
        report = inspect_probe(subprocess.CompletedProcess([], 0, error + "\n" + response(), "raw stderr must disappear"),
                               {"forbidden_file": canary}, known_secrets=(credential,))
        text = json.dumps(report)
        self.assertIn("unknown_cli_diagnostic", report["failures"])
        self.assertIn("canary_exposed", report["failures"])
        self.assertIn("[REDACTED]", report["unknown_cli_diagnostics"][0])
        self.assertIn("[HOST_PATH]", report["unknown_cli_diagnostics"][0])
        self.assertEqual(len(report["unknown_cli_diagnostics"][0]), 2000)
        self.assertTrue(report["unknown_cli_diagnostics_truncated"])
        for forbidden in (credential, canary, "private-user", "raw stderr", "sk-actualtoken12345678"):
            self.assertNotIn(forbidden, text)

    def test_unknown_diagnostic_retention_is_bounded_and_never_whitelists_errors(self):
        events = [json.dumps({"type": "item.completed", "item": {"type": "error", "message": "Unrecognized diagnostic " + str(n)}}) for n in range(12)]
        report = inspect_probe(subprocess.CompletedProcess([], 0, "\n".join(events) + "\n" + response(), ""), {})
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["unknown_cli_diagnostic_count"], 12)
        self.assertEqual(len(report["unknown_cli_diagnostics"]), 8)
        self.assertTrue(report["unknown_cli_diagnostics_truncated"])

    def test_probe_material_is_synthetic_and_cleaned_and_canaries_absent_from_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            def run(argv, **kwargs):
                cwd = Path(kwargs["cwd"])
                parent = cwd.parent
                for path in (parent / "AGENTS.md", parent / ".codex" / "config.toml", parent / ".agents" / "skills" / "runtime-canary" / "SKILL.md", parent / "forbidden.txt"):
                    self.assertTrue(path.is_file())
                    self.assertNotIn(path.read_text().strip(), kwargs["input"])
                self.assertNotIn("FREYA_ISOLATION_CANARY", kwargs["env"])
                self.assertIn("--ignore-user-config", argv)
                self.assertIn("--ignore-rules", argv)
                self.assertIn("project_doc_max_bytes=0", argv)
                self.assertIn("skills.bundled.enabled=false", argv)
                self.assertIn("skills.include_instructions=false", argv)
                self.assertIn("cloud.skills.enabled=false", argv)
                self.assertIn("tools.experimental_request_user_input.enabled=false", argv)
                self.assertIn("tools.update_plan.enabled=false", argv)
                self.assertEqual(argv[argv.index("code_mode") - 1], "--disable")
                self.assertEqual(argv[argv.index("code_mode_only") - 1], "--disable")
                self.assertEqual(argv[argv.index("unified_exec") - 1], "--disable")
                return subprocess.CompletedProcess(argv, 0, response(), "")
            report = isolation_probe(Path(tmp), run=run)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_preflight_never_persists_auth_output_or_unknown_versions(self):
        def run(argv, **kwargs):
            if "status" in argv:
                return subprocess.CompletedProcess(argv, 0, "Logged in using ChatGPT\nprivate identity must not persist", "")
            return subprocess.CompletedProcess(argv, 0, "private credentials", "")
        report = preflight(run=run)
        self.assertTrue(report["checks"]["signin"])
        self.assertEqual(report["status"], "fail")
        self.assertNotIn("private", json.dumps(report))

    def test_probe_timeout_keeps_only_metadata(self):
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 1, output="sensitive partial content", stderr="sensitive diagnostic")
        with tempfile.TemporaryDirectory() as tmp:
            report = isolation_probe(Path(tmp), run=run)
        self.assertIn("timeout", report["failures"])
        self.assertNotIn("sensitive", json.dumps(report))

    def test_generation_isolation_failure_blocks_banking_prompt(self):
        sent = []
        def run(argv, **kwargs):
            sent.append(kwargs.get("input", ""))
            return subprocess.CompletedProcess(argv, 0, response(available_tools=["apply_patch"]), "")
        with tempfile.TemporaryDirectory() as tmp:
            generator = CodexGenerator(Path(tmp), Trace(Path(tmp) / "trace.jsonl"), Deadline(), run=run)
            generator._checked = True
            with self.assertRaisesRegex(GenerationError, "no banking brief was sent"):
                generator.generate("SENSITIVE BANKING BRIEF")
            self.assertNotIn("SENSITIVE BANKING BRIEF", "\n".join(sent))
            self.assertIn('"event": "generation_isolation_audit"', generator.trace.path.read_text())

    def test_generation_checks_isolation_once_and_then_sends_brief(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(kwargs.get("input", ""))
            if "isolation-audit" in str(kwargs["cwd"]):
                return subprocess.CompletedProcess(argv, 0, response(), "")
            value = {"policy_json": "{}", "workflow_json": "{}"}
            event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}}
            events = [{"type": "thread.started", "thread_id": "synthetic-thread"},
                      {"type": "turn.started"}, event, {"type": "turn.completed"}]
            return subprocess.CompletedProcess(argv, 0, "\n".join(json.dumps(item) for item in events), "")
        with tempfile.TemporaryDirectory() as tmp:
            generator = CodexGenerator(Path(tmp), Trace(Path(tmp) / "trace.jsonl"), Deadline(), run=run)
            generator._checked = True
            self.assertEqual(generator.generate("brief one"), {"policy": {}, "workflow": {}})
            self.assertEqual(generator.generate("brief two"), {"policy": {}, "workflow": {}})
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1:], ["brief one", "brief two"])


if __name__ == "__main__":
    unittest.main()

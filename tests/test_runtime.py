import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from builder.runtime import CodexGenerator, Deadline, DeadlineExceeded, DISABLED_FEATURES, GenerationError, Trace, generation_environment, run_bounded

PROJECT = Path(__file__).resolve().parents[1]


def completed_stream(value):
    return "\n".join(json.dumps(event) for event in (
        {"type": "thread.started", "thread_id": "synthetic-thread"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}},
        {"type": "turn.completed"},
    ))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / ".test-tmp").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT / ".test-tmp")
        self.directory = Path(self.tmp.name)
        # These tests isolate generation/process parsing. Dedicated preflight
        # tests exercise the mandatory isolation gate and before-brief boundary.
        self.isolation = patch.object(CodexGenerator, "check_isolation")
        self.isolation.start()
        self.addCleanup(self.isolation.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_deadline_reserves_export_time(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        self.assertEqual(deadline.timeout(900, reserve=300), 900)
        now[0] = 1499
        self.assertEqual(deadline.timeout(900, reserve=300), 1)
        now[0] = 1500
        with self.assertRaises(DeadlineExceeded):
            deadline.timeout(900, reserve=300)

    def test_environment_never_inherits_bank_keys_or_shell_bootstrap(self):
        clean = generation_environment({"PATH": "/usr/bin", "HOME": "/home/user", "FREYA_API_KEY": "secret",
                                        "FREYA_BANK_TEAM_KEY": "team", "BASH_ENV": "/private/startup", "OPENAI_API_KEY": "secret"})
        self.assertEqual(clean, {"PATH": "/usr/bin", "HOME": "/home/user"})

    def test_flags_disable_tools_without_overriding_permissions_profile(self):
        gen = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline())
        argv = gen.argv(self.directory, self.directory / "schema.json")
        self.assertNotIn("--sandbox", argv)
        self.assertIn("shell_tool", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("permissions.freya_generation.network.enabled=false", argv)
        self.assertTrue(any('":root"="deny"' in value for value in argv))

    def test_source_controls_disable_bundled_cloud_skills_and_user_input_tools(self):
        gen = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline())
        argv = gen.argv(self.directory, self.directory / "schema.json")
        for setting in ("skills.bundled.enabled=false", "skills.include_instructions=false", "cloud.skills.enabled=false",
                        "tools.experimental_request_user_input.enabled=false", "tools.update_plan.enabled=false"):
            with self.subTest(setting=setting):
                self.assertIn(setting, argv)
                self.assertEqual(argv[argv.index(setting) - 1], "-c")
        for feature in ("code_mode", "code_mode_only", "code_mode_host"):
            with self.subTest(feature=feature):
                self.assertIn(feature, argv)
                self.assertEqual(argv[argv.index(feature) - 1], "--disable")
        self.assertIn("--strict-config", argv)

    def test_relative_output_directory_is_resolved_before_child_cwd(self):
        gen = CodexGenerator(Path("artifacts/relative-unit-probe"), Trace(self.directory / "trace.jsonl"), Deadline())
        self.assertTrue(gen.output_dir.is_absolute())

    def test_missing_isolation_capability_fails_before_generation(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="--ephemeral", stderr="")
        gen = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(), run=run)
        with self.assertRaises(GenerationError):
            gen.generate("safe brief")
        self.assertEqual(len(calls), 2)

    def test_missing_code_mode_control_fails_before_any_probe_or_brief(self):
        for missing in ("code_mode", "code_mode_only"):
            calls = []
            def run(argv, **kwargs):
                calls.append((argv, kwargs))
                if "--help" in argv:
                    text = "--ignore-user-config --ignore-rules --ephemeral --output-schema --strict-config"
                else:
                    text = "\n".join(name + " stable false" for name in (*DISABLED_FEATURES, "skip_host_skill_discovery") if name != missing)
                return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")
            with self.subTest(missing=missing):
                gen = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(), run=run)
                with self.assertRaisesRegex(GenerationError, missing):
                    gen.generate("BANK BRIEF MUST NOT BE SENT")
                self.assertEqual(len(calls), 2)
                self.assertFalse(any("input" in kwargs for _, kwargs in calls))

    def test_generation_is_stdin_only_and_trace_redacts_before_write(self):
        calls = []
        candidate = {"policy": {"goal": "banking"}, "workflow": {"initial_node": "start", "nodes": {}}}
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if "--help" in argv:
                text = " ".join(("--ignore-user-config", "--ignore-rules", "--ephemeral", "--output-schema", "--strict-config"))
            elif "features" in argv:
                text = "\n".join(name + " stable true" for name in (*DISABLED_FEATURES, "skip_host_skill_discovery"))
            else:
                text = completed_stream(candidate)
            return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="token private-test-token")
        trace = Trace(self.directory / "trace.jsonl", secrets=("private-test-token",))
        gen = CodexGenerator(self.directory, trace, Deadline(), run=run)
        self.assertEqual(gen.generate("supplied brief, no files"), candidate)
        self.assertEqual(calls[-1][1]["input"], "supplied brief, no files")
        self.assertLessEqual(calls[-1][1]["timeout"], 900)
        self.assertNotIn("private-test-token", trace.path.read_text())
        self.assertEqual(list(self.directory.glob("generation-*")), [])

    def test_timeout_kills_the_entire_process_group(self):
        process = Mock(pid=1234)
        process.communicate.side_effect = [subprocess.TimeoutExpired(["codex"], 1, output=b"initial"), ("retained stdout", "retained stderr")]
        with patch("builder.runtime.subprocess.Popen", return_value=process) as popen, patch("builder.runtime.os.killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                run_bounded(["codex"], input="safe", timeout=1)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(kill.call_args.args[0], 1234)
        self.assertEqual(caught.exception.output, "retained stdout")
        self.assertEqual(caught.exception.stderr, "retained stderr")
        self.assertTrue(caught.exception.process_group_terminated)

    def test_buffered_progress_continues_without_resubmitting_stdin(self):
        process = Mock(pid=1234, returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired(["codex"], 30, output=b'{"type":"turn.started"}\n'), ("done", "")]
        progress = Mock()
        with patch("builder.runtime.subprocess.Popen", return_value=process), patch("builder.runtime.time.monotonic", side_effect=[0, 30]):
            result = run_bounded(["codex"], input="safe", timeout=90, on_progress=progress)
        self.assertEqual(result.stdout, "done")
        self.assertEqual(progress.call_count, 1)
        self.assertEqual(process.communicate.call_args_list[0].kwargs["input"], "safe")
        self.assertIsNone(process.communicate.call_args_list[1].kwargs["input"])

    def test_generator_timeout_retains_bounded_redacted_partial_evidence(self):
        token = "private-test-token"
        trace = Trace(self.directory / "trace.jsonl", secrets=(token,))
        event = {"type": "item.completed", "item": {"type": "reasoning", "text": "partial " + token}}
        partial = json.dumps({"type": "turn.started"}) + "\n" + json.dumps(event) + "\n"
        def run(argv, **kwargs):
            kwargs["on_progress"](30, partial.encode(), ("stderr " + token).encode())
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=partial.encode(), stderr=("stderr " + token).encode())
        generator = CodexGenerator(self.directory, trace, Deadline(480), run=run)
        generator._checked = True
        with self.assertRaisesRegex(GenerationError, "sanitized partial output"):
            generator.generate("public minimal banking request")
        text = trace.path.read_text()
        self.assertNotIn(token, text)
        events = [json.loads(line) for line in text.splitlines()]
        self.assertTrue(any(e["event"] == "generation_progress" and e["last_event"] == "item.completed" for e in events))
        self.assertTrue(any(e["event"] == "generation_event" and e["partial"] for e in events))
        timeout = next(e for e in events if e["event"] == "generation_timeout")
        self.assertLessEqual(timeout["allocated_seconds"], 180)
        self.assertTrue(any(e["event"] == "generation_stderr" and "stderr" in e["text"] for e in events))

    def test_oversized_failure_output_is_bounded_after_redaction(self):
        token = "private-test-token"
        trace = Trace(self.directory / "trace.jsonl", secrets=(token,))
        stdout = json.dumps({"type": "thread.started"}) + "\n" + "x" * 600000 + token + "\n" + json.dumps({"type": "turn.started"})
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=stdout, stderr="warning " + token)
        generator = CodexGenerator(self.directory, trace, Deadline(), run=run)
        generator._checked = True
        with self.assertRaises(GenerationError):
            generator.generate("public request")
        text = trace.path.read_text()
        self.assertLess(len(text), 280000)
        self.assertNotIn(token, text)
        self.assertIn("stdout_truncated", text)
        self.assertIn("thread.started", text)
        self.assertIn("turn.started", text)

    def test_strict_wire_json_strings_are_normalized_to_candidate_objects(self):
        candidate = {"policy": {"goal": "banking"}, "workflow": {"initial_node": "start", "nodes": {}}}
        wire = {"policy_json": json.dumps(candidate["policy"]), "workflow_json": json.dumps(candidate["workflow"])}
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout=completed_stream(wire), stderr="")
        generator = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(), run=run)
        generator._checked = True
        self.assertEqual(generator.generate("safe input"), candidate)

    def test_known_cli_warning_items_allow_successful_structured_generation(self):
        candidate = {"policy": {"version": 1, "rules": []}, "workflow": {"initial_node": "done", "nodes": {}}}
        warnings = [
            "Under-development features enabled: skip_host_skill_discovery. Under-development features are incomplete and may behave unpredictably. To suppress this warning, set `suppress_unstable_features_warning = true` in /example/config.toml.",
            "Exceeded skills context budget. All skill descriptions were removed and 4 additional skills were not included in the model-visible skills list."]
        events = [{"type": "item.completed", "item": {"type": "error", "message": message}} for message in warnings]
        events += [json.loads(line) for line in completed_stream(candidate).splitlines()]
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout="\n".join(json.dumps(event) for event in events), stderr="")
        trace = Trace(self.directory / "trace.jsonl")
        generator = CodexGenerator(self.directory, trace, Deadline(), run=run)
        generator._checked = True
        self.assertEqual(generator.generate("public request"), candidate)
        self.assertEqual(trace.path.read_text().count('"event": "generation_cli_warning"'), 2)

    def test_unknown_error_diagnostic_remains_fatal(self):
        def run(argv, **kwargs):
            event = {"type": "item.completed", "item": {"type": "error", "message": "Unexpected provider error"}}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(event), stderr="")
        generator = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(), run=run)
        generator._checked = True
        with self.assertRaisesRegex(GenerationError, "unrecognized error diagnostic"):
            generator.generate("public request")

    def test_outer_error_event_is_fatal_even_with_known_warning_text(self):
        def run(argv, **kwargs):
            event = {"type": "error", "item": {"type": "error", "message": "Exceeded skills context budget. All skill descriptions were removed and 4 additional skills were not included in the model-visible skills list."}}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(event), stderr="")
        generator = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(), run=run)
        generator._checked = True
        with self.assertRaisesRegex(GenerationError, "failed generation turn"):
            generator.generate("public request")

    def parse_stream(self, stdout, *, plan=False, returncode=0):
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")
        generator = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(), run=run)
        generator._checked = True
        if plan:
            return generator.generate_plan("approved frozen contract", contract_sha256="a" * 64)
        return generator.generate("approved source inputs")

    def test_zero_exit_requires_one_completed_turn_in_each_generation_stage(self):
        values = ({"policy_json": "{}", "workflow_json": "{}"},
                  {"plan_json": "{}", "contract_sha256": "a" * 64})
        for plan_mode, value in enumerate(values):
            lines = completed_stream(value).splitlines()
            for partial in (lines[:-1], lines[1:], lines[:1] + lines[2:]):
                with self.subTest(plan=bool(plan_mode), event_count=len(partial)), self.assertRaises(GenerationError):
                    self.parse_stream("\n".join(partial), plan=bool(plan_mode))

    def test_repeated_turn_or_events_after_completion_reject_valid_candidate(self):
        stream = completed_stream({"policy_json": "{}", "workflow_json": "{}"})
        for extra in (json.dumps({"type": "turn.completed"}),
                      json.dumps({"type": "turn.started"}),
                      json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "late"}}),
                      stream):
            with self.subTest(extra=extra[:50]), self.assertRaises(GenerationError):
                self.parse_stream(stream + "\n" + extra)

    def test_malformed_and_unknown_events_are_not_skipped_before_final_object(self):
        lines = completed_stream({"policy_json": "{}", "workflow_json": "{}"}).splitlines()
        bad_events = ("not JSON", "[]", "{}", '{"type":[]}', '{"type":"turn.interrupted"}',
                      '{"type":"turn.started","type":"turn.completed"}',
                      '{"type":"item.completed","item":null}',
                      '{"type":"item.completed","item":{"type":[]}}',
                      '{"type":"item.updated","item":{"type":"reasoning","text":NaN}}',
                      '{"type":"item.completed","item":{"type":"agent_message","text":null}}',
                      '{"type":"item.updated","item":{"type":"reasoning"},"model":{}}')
        for bad in bad_events:
            with self.subTest(event=bad[:80]), self.assertRaises(GenerationError):
                self.parse_stream("\n".join(lines[:2] + [bad] + lines[2:]))

    def test_thread_start_requires_a_nonempty_identifier_and_cannot_repeat(self):
        lines = completed_stream({"policy_json": "{}", "workflow_json": "{}"}).splitlines()
        for start in ({"type": "thread.started"}, {"type": "thread.started", "thread_id": ""},
                      {"type": "thread.started", "thread_id": []}):
            with self.subTest(start=start), self.assertRaises(GenerationError):
                self.parse_stream("\n".join([json.dumps(start)] + lines[1:]))
        with self.assertRaises(GenerationError):
            self.parse_stream("\n".join(lines[:1] + lines))

    def test_final_completed_message_cannot_fall_back_to_earlier_valid_candidate(self):
        lines = completed_stream({"policy_json": "{}", "workflow_json": "{}"}).splitlines()
        for final_text in ("unfinished response", "{}", '{"policy_json":"{}","policy_json":"{}","workflow_json":"{}"}'):
            final = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": final_text}})
            with self.subTest(final_text=final_text), self.assertRaises(GenerationError):
                self.parse_stream("\n".join(lines[:-1] + [final, lines[-1]]))

    def test_started_or_updated_message_cannot_be_used_as_final_candidate(self):
        lines = completed_stream({"policy_json": "{}", "workflow_json": "{}"}).splitlines()
        final = json.loads(lines[2])
        for event_type in ("item.started", "item.updated"):
            final["type"] = event_type
            partial = json.dumps(final)
            for events in (lines[:2] + [partial, lines[-1]], lines[:-1] + [partial, lines[-1]]):
                with self.subTest(event_type=event_type, event_count=len(events)), self.assertRaises(GenerationError):
                    self.parse_stream("\n".join(events))

    def test_valid_streaming_commentary_keeps_only_the_final_completed_wire_object(self):
        wire = {"policy_json": '{"goal":"approved banking policy"}', "workflow_json": '{"initial_node":"end"}'}
        events = [{"type": "thread.started", "thread_id": "synthetic-thread"}, {"type": "turn.started"},
                  {"type": "item.started", "item": {"type": "agent_message"}},
                  {"type": "item.updated", "item": {"type": "agent_message", "text": "Preparing the response"}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "Preparing the response"}},
                  {"type": "item.started", "item": {"type": "agent_message", "text": ""}},
                  {"type": "item.updated", "item": {"type": "agent_message", "text": '{"unfinished":'}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(wire)}},
                  {"type": "turn.completed"}]
        candidate = self.parse_stream("\n".join(json.dumps(event) for event in events))
        self.assertEqual(candidate, {"policy": {"goal": "approved banking policy"}, "workflow": {"initial_node": "end"}})

    def test_complete_event_stream_does_not_override_nonzero_process_exit(self):
        with self.assertRaisesRegex(GenerationError, "generation failed"):
            self.parse_stream(completed_stream({"policy_json": "{}", "workflow_json": "{}"}), returncode=1)


if __name__ == "__main__":
    unittest.main()

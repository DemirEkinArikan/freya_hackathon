"""Controller and subprocess allocation boundaries; no actual model calls."""
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from builder.build import execute, PROJECT
from builder.runtime import CodexGenerator, Deadline, DeadlineExceeded, GenerationError, Trace, run_bounded
import test_builder
from test_builder import fixture, plan
from test_runtime import completed_stream


class GenerationBudgetTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def run_directory(self):
        return next((self.directory / "out").iterdir())

    def test_initial_policy_uses_900_and_records_resolved_budgets(self):
        generator = self.generator()
        generator.generate.return_value = fixture()
        result = execute(self.args("--generate-only"), generator=generator)
        kwargs = generator.generate.call_args.kwargs
        self.assertEqual(kwargs["max_seconds"], 900)
        self.assertEqual(kwargs["reserve_seconds"], 750)
        source = json.loads((PROJECT / result["run_directory"] / "builder-source.json").read_text())
        config = source["resolved_config"]
        self.assertEqual(config["generation_budgets"], {
            "policy_initial_cap_seconds": 900, "policy_repair_cap_seconds": 450,
            "first_workflow_reserve_seconds": 450, "workflow_cap_seconds": 900})
        self.assertEqual((config["deadline_seconds"], config["export_reserve_seconds"], config["max_repairs"]),
                         (1800, 300, 2))
        self.assertEqual((config["model"], config["reasoning_effort"]), ("gpt-6.1-sol", "high"))

    def test_policy_repair_shares_deadline_and_retains_first_workflow(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        generator = self.generator()
        generator.generate.return_value = fixture()
        invalid = fixture()
        invalid["policy"]["sources"][0]["sha256"] = "0" * 64
        def extract(*args, **kwargs):
            now[0] += 620 if generator.generate.call_count == 1 else 100
            return invalid if generator.generate.call_count == 1 else fixture()
        generator.generate.side_effect = extract
        result = execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        self.assertEqual(result["policy_repairs"], 1)
        self.assertEqual([call.kwargs["max_seconds"] for call in generator.generate.call_args_list], [900, 430])
        self.assertEqual([call.kwargs["reserve_seconds"] for call in generator.generate.call_args_list], [750, 750])
        self.assertEqual(generator.generate_plan.call_count, 1)
        self.assertEqual(deadline.start, 0)

    def test_extract_only_has_no_phantom_workflow_reserve(self):
        now = [1000.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        now[0] += 1000
        generator = self.generator()
        generator.generate.return_value = fixture()
        result = execute(self.args("--extract-policy-only"), generator=generator, deadline=deadline)
        self.assertEqual(result["status"], "policy_extracted")
        self.assertEqual(generator.generate.call_args.kwargs["max_seconds"], 500)
        self.assertEqual(generator.generate.call_args.kwargs["reserve_seconds"], 300)
        generator.generate_plan.assert_not_called()

    def test_pending_workflow_stops_policy_when_only_reserve_remains(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        now[0] = 1049
        generator = self.generator()
        with self.assertRaises(DeadlineExceeded):
            execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        generator.generate.assert_not_called()
        generator.generate_plan.assert_not_called()

    def test_policy_timeout_is_preserved_without_automatic_reinvocation(self):
        generator = self.generator()
        generator.generate.side_effect = GenerationError("controlled timeout")
        with self.assertRaisesRegex(GenerationError, "controlled timeout"):
            execute(self.args("--generate-only"), generator=generator)
        self.assertEqual(generator.generate.call_count, 1)
        generator.generate_plan.assert_not_called()
        manifest = json.loads((self.run_directory() / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")

    def test_injected_generator_overrunning_export_reserve_cannot_report_success(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        generator = self.generator()
        def extract(*args, **kwargs):
            now[0] = 1500
            return fixture()
        generator.generate.side_effect = extract
        with self.assertRaises(DeadlineExceeded):
            execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        generator.generate_plan.assert_not_called()
        self.assertFalse((self.run_directory() / "policy.json").exists())

    def runtime_generator(self, deadline, run):
        generator = CodexGenerator(self.directory, Trace(self.directory / "runtime-trace.jsonl"), deadline, run=run)
        generator._checked = True
        generator._catalog = ("{}", {"fixture": "catalog"})
        return generator

    def test_runtime_recalculates_allocation_after_mandatory_audit(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        now[0] = 100
        calls = []
        def run(argv, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(argv, 0, completed_stream(fixture()), "")
        generator = self.runtime_generator(deadline, run)
        def audit():
            now[0] += 120
        with patch.object(generator, "check_isolation", side_effect=audit) as isolation:
            self.assertEqual(generator.generate("Turkish input: ödeme", max_seconds=900, reserve_seconds=750), fixture())
        isolation.assert_called_once_with()
        self.assertEqual(calls[0]["timeout"], 830)
        events = [json.loads(line) for line in generator.trace.path.read_text().splitlines()]
        call = next(e for e in events if e["event"] == "model_call")
        self.assertEqual(call["reserve_seconds"], 750)
        self.assertEqual(call["remaining_seconds"], 1580)
        self.assertGreater(call["prompt_utf8_bytes"], call["prompt_characters"])
        self.assertGreater(call["response_schema_bytes"], 0)

    def test_insufficient_post_audit_budget_sends_no_brief(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        now[0] = 1000
        run = Mock()
        generator = self.runtime_generator(deadline, run)
        def audit():
            now[0] += 60
        with patch.object(generator, "check_isolation", side_effect=audit):
            with self.assertRaises(DeadlineExceeded):
                generator.generate("must not be sent", max_seconds=900, reserve_seconds=750)
        run.assert_not_called()

    def test_slow_catalog_preparation_cannot_send_stale_allocation(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        now[0] = 100
        calls = []
        def run(argv, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(argv, 0, completed_stream(fixture()), "")
        generator = self.runtime_generator(deadline, run)
        generator._isolation_checked = True
        original = generator.prepare_catalog
        def prepare(directory):
            now[0] += 200
            return original(directory)
        with patch.object(generator, "prepare_catalog", side_effect=prepare):
            generator.generate("approved source", max_seconds=900, reserve_seconds=750)
        self.assertEqual(calls[0]["timeout"], 750)
        now[0] = 1000
        with patch.object(generator, "prepare_catalog", side_effect=prepare), self.assertRaises(DeadlineExceeded):
            generator.generate("must not be sent", max_seconds=900, reserve_seconds=750)
        self.assertEqual(len(calls), 1)

    def test_reserve_cannot_be_reduced_or_nonfinite_and_caps_stay_bounded(self):
        run = Mock()
        generator = self.runtime_generator(Deadline(), run)
        for reserve in (299, True, "750", float("nan"), float("inf")):
            with self.subTest(reserve=reserve), self.assertRaises(GenerationError):
                generator.generate("not sent", reserve_seconds=reserve)
        for cap in (901, 0, True, float("nan"), float("inf")):
            with self.subTest(cap=cap), self.assertRaises(GenerationError):
                generator.generate("not sent", max_seconds=cap)
        run.assert_not_called()

    def test_workflow_uses_remaining_generation_time_and_same_export_reserve(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        now[0] = 950
        calls = []
        wire = {"plan_json": json.dumps(plan()), "contract_sha256": "a" * 64}
        def run(argv, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(argv, 0, completed_stream(wire), "")
        generator = self.runtime_generator(deadline, run)
        generator._isolation_checked = True
        generator.generate_plan("frozen contract", contract_sha256="a" * 64)
        self.assertEqual(calls[0]["timeout"], 550)
        self.assertIs(generator.deadline, deadline)

    def test_longer_subprocess_timeout_still_kills_and_drains_process_group(self):
        process = Mock(pid=1234)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["codex"], 30, output=b'{"type":"turn.started"}\n'),
            subprocess.TimeoutExpired(["codex"], 30, output=b'{"type":"turn.started"}\n'),
            ("retained original events", "retained diagnostics")]
        progress = Mock()
        with patch("builder.runtime.subprocess.Popen", return_value=process), \
             patch("builder.runtime.time.monotonic", side_effect=[0, 30, 900]), \
             patch("builder.runtime.os.killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                run_bounded(["codex"], input="approved brief", timeout=900, on_progress=progress)
        self.assertEqual(progress.call_count, 1)
        self.assertEqual(kill.call_args.args[0], 1234)
        self.assertEqual(caught.exception.timeout, 900)
        self.assertEqual(caught.exception.output, "retained original events")
        self.assertTrue(caught.exception.process_group_terminated)
        self.assertEqual(process.communicate.call_args_list[0].kwargs["input"], "approved brief")
        self.assertIsNone(process.communicate.call_args_list[1].kwargs["input"])


if __name__ == "__main__":
    unittest.main()

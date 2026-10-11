"""Controller and subprocess allocation boundaries; no actual model calls."""
import copy
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from builder.build import execute, PROJECT
from builder.runtime import (CodexGenerator, Deadline, DeadlineExceeded, GenerationError, GenerationFormatError,
                             Trace, run_bounded)
import test_builder
from test_builder import FakeClient, fixture, plan
from test_runtime import completed_stream


def read_events(run):
    return [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]


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
            "first_workflow_reserve_seconds": 450, "workflow_cap_seconds": 900,
            "repair_admission": {"base_seconds": 61, "fraction": 0.9,
                                 "seconds_per_character": 0.0044}})
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

    def test_policy_repair_starts_only_if_a_rewrite_of_that_size_fits_before_the_workflow_reserve(self):
        # The first call ends at 740 s: min(450, 1060 - 300 - 450) = 310 s remain for a repair.
        for padding, repaired in ((0, True), (60000, False)):
            now = [0.0]
            deadline = Deadline(1800, clock=lambda: now[0])
            invalid = fixture()
            invalid["policy"]["sources"][0]["sha256"] = "0" * 64
            invalid["policy"]["goal"] += " " + "x" * padding
            estimate = 61 + 0.0044 * len(json.dumps(invalid["policy"], ensure_ascii=False, separators=(",", ":")))
            self.assertEqual(0.9 * estimate <= 310, repaired)
            generator = self.generator()
            generator.generate.return_value = fixture()
            def extract(*args, **kwargs):
                now[0] += 740 if generator.generate.call_count == 1 else 100
                return copy.deepcopy(invalid) if generator.generate.call_count == 1 else fixture()
            generator.generate.side_effect = extract
            out = self.directory / f"out-{padding}"
            args = self.args("--generate-only", "--output-dir", str(out))
            with self.subTest(padding=padding):
                if repaired:
                    self.assertEqual(execute(args, generator=generator, deadline=deadline)["policy_repairs"], 1)
                else:
                    with self.assertRaisesRegex(DeadlineExceeded, r"^No time for a repair \(310 s available, about "
                                                                  r"\d+ s needed\): Policy extraction failed: "):
                        execute(args, generator=generator, deadline=deadline)
                    self.assertEqual(generator.generate.call_count, 1)
                    generator.generate_plan.assert_not_called()
                run = next(out.iterdir())
                errors = json.loads((run / "policy.rejected-1.json").read_text())["validation_errors"]
                admission = next(e for e in read_events(run) if e["event"] == "repair_admission")
                self.assertEqual({key: admission[key] for key in ("stage", "attempt", "admitted", "available_seconds",
                                                                   "estimated_seconds", "reserve_seconds")},
                                 {"stage": "policy", "attempt": 2, "admitted": repaired, "available_seconds": 310,
                                  "estimated_seconds": round(estimate, 1), "reserve_seconds": 750})
                if not repaired:
                    manifest = json.loads((run / "manifest.json").read_text())
                    self.assertEqual(manifest["status"], "failed")
                    self.assertTrue(manifest["error"].endswith("Policy extraction failed: " + "; ".join(errors)))

    def test_skipped_policy_repair_after_format_error_keeps_the_format_diagnostic(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        generator = self.generator()
        def extract(*args, **kwargs):
            now[0] = 1000  # 800 s remain: 50 s before the 750 s reserve, below 90% of the 61 s base.
            raise GenerationFormatError("inner_json")
        generator.generate.side_effect = extract
        with self.assertRaisesRegex(DeadlineExceeded, r"^No time for a repair \(50 s available, about 61 s needed\): "
                                                      r"Codex completed output has invalid serialization: inner_json$"):
            execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        self.assertEqual(generator.generate.call_count, 1)

    def test_policy_repair_admission_uses_the_450_s_repair_cap(self):
        # The first call ends at 100 s: min(450, 1700 - 750) = 450 s, so the repair cap, not the reserve, decides.
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        invalid = fixture()
        invalid["policy"]["sources"][0]["sha256"] = "0" * 64
        invalid["policy"]["goal"] += " " + "x" * 100000
        estimate = 61 + 0.0044 * len(json.dumps(invalid["policy"], ensure_ascii=False, separators=(",", ":")))
        self.assertTrue(450 < 0.9 * estimate <= 900)  # refused at the 450 s cap, admitted at 900 s
        generator = self.generator()
        def extract(*args, **kwargs):
            now[0] += 100
            return copy.deepcopy(invalid)
        generator.generate.side_effect = extract
        with self.assertRaisesRegex(DeadlineExceeded, rf"^No time for a repair \(450 s available, about "
                                                      rf"{estimate:.0f} s needed\): Policy extraction failed: "):
            execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        self.assertEqual(generator.generate.call_count, 1)

    def test_plan_repair_starts_only_if_it_can_finish_before_the_export_reserve(self):
        broken = plan()
        broken["initial_node"] = "missing"
        for finished_at, repaired in ((1000, True), (1460, False)):
            now = [0.0]
            deadline = Deadline(1800, clock=lambda: now[0])
            generator = self.generator()
            generator.generate.return_value = fixture()
            def workflow(prompt, *, contract_sha256):
                first = generator.generate_plan.call_count == 1
                now[0] = finished_at if first else finished_at + 100
                return {"plan": copy.deepcopy(broken if first else plan()), "contract_sha256": contract_sha256}
            generator.generate_plan.side_effect = workflow
            out = self.directory / f"out-{finished_at}"
            args = self.args("--generate-only", "--output-dir", str(out))
            with self.subTest(finished_at=finished_at):
                if repaired:
                    result = execute(args, generator=generator, deadline=deadline)
                    self.assertEqual((result["status"], result["workflow_repairs"]), ("generated_validated", 1))
                else:
                    with self.assertRaisesRegex(DeadlineExceeded, r"^No time for a repair \(40 s available, about "
                                                                  r"\d+ s needed\): Candidate validation failed: "):
                        execute(args, generator=generator, deadline=deadline)
                    self.assertEqual(generator.generate_plan.call_count, 1)
                run = next(out.iterdir())
                admission = next(e for e in read_events(run) if e["event"] == "repair_admission")
                self.assertEqual((admission["stage"], admission["attempt"], admission["admitted"],
                                  admission["available_seconds"], admission["reserve_seconds"]),
                                 ("plan", 2, repaired, 500 if repaired else 40, 300))
                attempts = sorted(path.name for path in (run / "attempts").iterdir())
                self.assertEqual(attempts, ["plan-001", "plan-002"] if repaired else ["plan-001"])
                if not repaired:
                    end = read_events(run)[-1]
                    self.assertEqual((end["event"], end["status"], end["stage_durations"]),
                                     ("end", "failed", {"policy": 0, "workflow": 1460}))

    def test_stage_timing_uses_the_deadline_clock_through_export(self):
        now = [0.0]
        deadline = Deadline(1800, clock=lambda: now[0])
        generator = self.generator()
        generator.generate.return_value = fixture()
        def extract(*args, **kwargs):
            now[0] += 400
            return fixture()
        def workflow(prompt, *, contract_sha256):
            now[0] += 300
            return {"plan": plan(), "contract_sha256": contract_sha256}
        generator.generate.side_effect = extract
        generator.generate_plan.side_effect = workflow
        class TimedClient(FakeClient):
            def save_workflow(self, *args):
                now[0] += 12
                return super().save_workflow(*args)
            def export_workflow(self, *args):
                now[0] += 5
                return super().export_workflow(*args)
        result = execute(self.args(), generator=generator, client_factory=TimedClient, deadline=deadline)
        self.assertEqual(result["status"], "exported")
        self.assertEqual(result["stage_durations"], {"policy": 400, "workflow": 300, "deployment": 12, "export": 5})
        stages = [(e["stage"], e["elapsed_seconds"], e["remaining_seconds"])
                  for e in read_events(self.run_directory()) if e["event"] == "stage_completed"]
        self.assertEqual(stages, [("policy", 400, 1400), ("workflow", 300, 1100),
                                  ("deployment", 12, 1088), ("export", 5, 1083)])
        events = read_events(self.run_directory())
        self.assertEqual([(e["attempt"], e["elapsed_seconds"]) for e in events if e["event"] == "policy_execution_requirements"],
                         [(1, 400)])
        self.assertFalse(any(e["event"] == "repair_admission" for e in events))

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

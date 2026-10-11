"""Policy-call stall guard and first-attempt retry with fake Codex streams; no model calls."""
import itertools
import json
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

from benchmarks.bundle import assemble_bundle
from builder import build
from builder.build import execute
from builder.runtime import (CodexGenerator, Deadline, DeadlineExceeded, GenerationError, GenerationStalled,
                             GenerationTimeout, Trace)
from builder.safety import digest
from tools import package_submission as package
import test_builder
import test_package_provenance
from test_builder import fixture, plan
from test_evaluation_bundle import recorded_inputs
from test_runtime import completed_stream

# What every retained call printed before its final message: thread.started, then turn.started.
SILENT = (json.dumps({"type": "thread.started", "thread_id": "synthetic"}) + "\n"
          + json.dumps({"type": "turn.started"}) + "\n").encode()
POLICY_WIRE = {"policy_json": json.dumps(fixture()["policy"]), "workflow_json": json.dumps(fixture()["workflow"])}


def events_of(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def scripted_run(*steps):
    """Codex stand-in reporting progress every 30 s like run_bounded; each step is one call.

    A step is (silent_seconds, completion): progress ticks up to silent_seconds with no new
    events, then the completed wire value, or a timeout when completion is None.
    """
    calls = []

    def run(argv, **kwargs):
        silent_seconds, completion = steps[len(calls)]
        calls.append({"argv": argv, "timeout": kwargs["timeout"],
                      "effort": next(a for a in argv if a.startswith("model_reasoning_effort="))})
        for elapsed in range(30, silent_seconds + 1, 30):
            kwargs["on_progress"](float(elapsed), SILENT, b"")
        if completion is None:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=SILENT, stderr=b"")
        if completion == "plan":
            schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
            completion = {"plan_json": json.dumps(plan()),
                          "contract_sha256": schema["properties"]["contract_sha256"]["enum"][0]}
        return subprocess.CompletedProcess(argv, 0, completed_stream(completion), "")
    return run, calls


class StallGuardRuntimeTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown

    def generator(self, run, effort):
        generator = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(),
                                   run=run, reasoning_effort=effort)
        generator._checked = generator._isolation_checked = True
        generator._catalog = ("{}", {"fixture": "catalog"})
        return generator

    def test_silent_medium_call_is_killed_with_its_process_group_and_recorded(self):
        process = Mock(pid=4321)
        partial = subprocess.TimeoutExpired(["codex"], 30, output=SILENT, stderr=b"")
        process.communicate.side_effect = [partial] * 21 + [("drained", "")]
        generator = self.generator(None, "medium")  # the real run_bounded around a fake process
        clock = itertools.count(0, 30)
        with patch("builder.runtime.subprocess.Popen", return_value=process), \
             patch("builder.runtime.time.monotonic", side_effect=lambda: float(next(clock))), \
             patch("builder.runtime.os.killpg") as kill:
            with self.assertRaisesRegex(GenerationStalled, "no stream event for 600 s"):
                generator.generate("public request", max_seconds=900, reserve_seconds=750)
        self.assertEqual(kill.call_args.args, (4321, signal.SIGKILL))
        rows = events_of(generator.trace.path)
        stall = next(row for row in rows if row["event"] == "stall_detected")
        self.assertEqual((stall["phase"], stall["window_seconds"], stall["silence_seconds"], stall["reasoning_effort"]),
                         ("after_turn_started", 600, 600.0, "medium"))
        self.assertIs(stall["process_group_terminated"], True)
        self.assertEqual(stall["allocated_seconds"], 900)
        # The partial stream is retained like a timeout's, and progress was recorded every 30 s.
        self.assertEqual([row["data"]["type"] for row in rows if row["event"] == "generation_event"],
                         ["thread.started", "turn.started"])
        self.assertEqual(sum(row["event"] == "generation_progress" for row in rows), 21)

    def test_healthy_slow_high_effort_call_is_not_killed(self):
        # Completed high calls on 10-11 Oct took up to 800.4 s with no event before the end.
        run, calls = scripted_run((870, POLICY_WIRE))
        generator = self.generator(run, "high")
        self.assertEqual(generator.generate("public request", max_seconds=900, reserve_seconds=750), fixture())
        rows = events_of(generator.trace.path)
        self.assertFalse(any(row["event"] == "stall_detected" for row in rows))
        self.assertEqual(sum(row["event"] == "generation_progress" for row in rows), 29)

    def test_medium_call_silent_inside_its_window_is_not_killed(self):
        run, _ = scripted_run((570, POLICY_WIRE))
        generator = self.generator(run, "medium")
        self.assertEqual(generator.generate("public request"), fixture())
        self.assertFalse(any(row["event"] == "stall_detected" for row in events_of(generator.trace.path)))

    def test_new_output_restarts_the_silence_window(self):
        def run(argv, **kwargs):
            for elapsed in range(30, 901, 30):
                stdout = SILENT if elapsed < 240 else SILENT + b'{"type":"item.started","item":{"type":"reasoning"}}\n'
                kwargs["on_progress"](float(elapsed), stdout, b"")
        generator = self.generator(run, "medium")
        with self.assertRaisesRegex(GenerationStalled, "no stream event for 600 s"):
            generator.generate("public request")
        stall = next(row for row in events_of(generator.trace.path) if row["event"] == "stall_detected")
        self.assertEqual(stall["silence_seconds"], 600.0)
        self.assertEqual(sum(row["event"] == "generation_progress"
                             for row in events_of(generator.trace.path)), 28)  # 240 + 600 = 840 s

    def test_missing_turn_start_is_a_stall_at_any_effort(self):
        def run(argv, **kwargs):
            for elapsed in range(30, 901, 30):
                kwargs["on_progress"](float(elapsed), b"", b"")
        generator = self.generator(run, "high")
        with self.assertRaises(GenerationStalled):
            generator.generate("public request")
        stall = next(row for row in events_of(generator.trace.path) if row["event"] == "stall_detected")
        self.assertEqual((stall["phase"], stall["window_seconds"], stall["silence_seconds"]),
                         ("before_turn_started", 120, 120.0))

    def test_timeout_is_a_generation_timeout_with_its_original_message(self):
        run, _ = scripted_run((870, None))
        generator = self.generator(run, "high")
        with self.assertRaisesRegex(GenerationTimeout, "exceeded its allocated time"):
            generator.generate("public request")
        self.assertTrue(issubclass(GenerationTimeout, GenerationError))
        self.assertTrue(any(row["event"] == "generation_timeout" for row in events_of(generator.trace.path)))


class FirstPolicyRetryTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def run_events(self):
        return events_of(next((self.directory / "out").iterdir()) / "trace.jsonl")

    def real_generator(self, run):
        """Build the controller's own CodexGenerator, sharing its trace, around a fake Codex."""
        original = build.CodexGenerator

        def make(*args, **kwargs):
            generator = original(*args, run=run, **kwargs)
            generator._checked = generator._isolation_checked = True
            generator._catalog = ("{}", {"fixture": "catalog"})
            return generator
        return patch("builder.build.CodexGenerator", side_effect=make)

    def test_stalled_first_attempt_is_killed_and_retried_once(self):
        run, calls = scripted_run((630, None), (0, POLICY_WIRE), (0, "plan"))
        with self.real_generator(run):
            result = execute(self.args("--generate-only", "--provider", "codex", "--reasoning-effort", "medium"))
        self.assertEqual((result["status"], result["policy_repairs"]), ("generated_validated", 1))
        self.assertEqual([call["effort"] for call in calls], ['model_reasoning_effort="medium"'] * 3)
        self.assertEqual(calls[1]["timeout"], 450)
        rows = self.run_events()
        names = [row["event"] for row in rows]
        self.assertLess(names.index("stall_detected"), names.index("policy_generation_retry"))
        admission = next(row for row in rows if row["event"] == "repair_admission")
        self.assertEqual((admission["stage"], admission["attempt"], admission["admitted"], admission["reserve_seconds"]),
                         ("policy", 2, True, 300))
        retry = next(row for row in rows if row["event"] == "policy_generation_retry")
        self.assertEqual((retry["after"], retry["failed_reasoning_effort"], retry["reasoning_effort"]),
                         ("stall", "medium", "medium"))
        budgets = [row for row in rows if row["event"] == "policy_generation_budget"]
        self.assertEqual([(row["reserve_seconds"], row["first_workflow_reserve_seconds"]) for row in budgets],
                         [(750, 450), (300, 0)])
        accepted = next(row for row in rows if row["event"] == "policy_execution_requirements")
        self.assertEqual((accepted["attempt"], accepted["repair_mode"]), (2, "retry"))

    def test_timed_out_high_attempt_is_retried_at_medium_then_plans_at_high(self):
        run, calls = scripted_run((870, None), (0, POLICY_WIRE), (0, "plan"))
        with self.real_generator(run):
            result = execute(self.args("--generate-only", "--provider", "codex"))
        self.assertEqual(result["status"], "generated_validated")
        self.assertEqual([call["effort"].split("=")[1] for call in calls], ['"high"', '"medium"', '"high"'])
        rows = self.run_events()
        self.assertEqual([row["reasoning_effort"] for row in rows if row["event"] == "model_call"],
                         ["high", "medium", "high"])
        retry = next(row for row in rows if row["event"] == "policy_generation_retry")
        self.assertEqual((retry["after"], retry["failed_reasoning_effort"], retry["reasoning_effort"]),
                         ("timeout", "high", "medium"))
        self.assertTrue(any(row["event"] == "generation_timeout" for row in rows))

    def clocked(self, effort, *outcomes):
        """Mock generator whose calls take the given seconds on the build clock, then raise or return."""
        now = [0.0]
        generator = self.generator()
        generator.reasoning_effort = effort
        generator.generate.return_value = fixture()  # read by the shared plan stand-in

        def extract(*args, **kwargs):
            seconds, outcome = outcomes[generator.generate.call_count - 1]
            now[0] += seconds
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        generator.generate.side_effect = extract
        return generator, Deadline(1800, clock=lambda: now[0])

    def test_retry_uses_repair_cap_and_export_reserve_like_a_format_repair(self):
        generator, deadline = self.clocked("high", (900, GenerationTimeout("Codex generation exceeded its allocated time")),
                                           (300, fixture()))
        result = execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        self.assertEqual(result["policy_repairs"], 1)
        kwargs = [call.kwargs for call in generator.generate.call_args_list]
        self.assertEqual([(k["max_seconds"], k["reserve_seconds"]) for k in kwargs], [(900, 750), (450, 300)])
        self.assertNotIn("reasoning_effort", kwargs[0])
        self.assertEqual(kwargs[1]["reasoning_effort"], "medium")
        admission = next(row for row in self.run_events() if row["event"] == "repair_admission")
        self.assertEqual((admission["available_seconds"], admission["estimated_seconds"]), (450, 61))

    def test_retry_leaves_time_to_export_the_fallback_plan_after_a_timed_out_plan_call(self):
        # stress-dev-11/12 with R5: a 900 s high policy timeout, a 450 s medium retry, then the plan
        # call times out in its 150 s and the deterministic fallback plan exports in the 300 s left.
        timeout = GenerationTimeout("Codex generation exceeded its allocated time")
        generator, deadline = self.clocked("high", (900, timeout), (450, fixture()))
        clock, spent = deadline.clock, [0.0]
        deadline.clock = lambda: clock() + spent[0]

        def plan_call(prompt, *, contract_sha256):
            spent[0] += deadline.timeout(900, reserve=300)
            raise timeout
        generator.generate_plan.side_effect = plan_call
        clients = []

        def factory(**kwargs):
            clients.append(test_builder.FakeClient(**kwargs))
            return clients[0]
        manifest = execute(self.args(), generator=generator, client_factory=factory, deadline=deadline)
        self.assertEqual((manifest["status"], manifest["plan_origin"], manifest["plan_fallback_reason"]),
                         ("exported", "deterministic_fallback", str(timeout)))
        self.assertEqual((generator.generate.call_count, generator.generate_plan.call_count, clients[0].exports),
                         (2, 1, 1))
        rows = events_of(build.PROJECT / manifest["run_directory"] / "trace.jsonl")
        names = [row["event"] for row in rows]
        self.assertLess(names.index("policy_generation_retry"), names.index("plan_fallback_decision"))
        decision = next(row for row in rows if row["event"] == "plan_fallback_decision")
        self.assertEqual((decision["started"], decision["remaining_seconds"]), (True, 300))

    def test_retry_without_time_keeps_the_real_failure(self):
        generator, deadline = self.clocked("high", (1460, GenerationTimeout("Codex generation exceeded its allocated time")))
        with self.assertRaisesRegex(DeadlineExceeded, r"No time for a repair \(40 s available.*exceeded its allocated time"):
            execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        self.assertEqual(generator.generate.call_count, 1)
        rows = self.run_events()
        self.assertFalse(next(row for row in rows if row["event"] == "repair_admission")["admitted"])
        self.assertFalse(any(row["event"] == "policy_generation_retry" for row in rows))

    def test_retry_happens_once_and_only_after_the_first_attempt(self):
        timeout = GenerationTimeout("Codex generation exceeded its allocated time")
        generator, deadline = self.clocked("medium", (630, GenerationStalled("stalled")), (450, timeout))
        with self.assertRaises(GenerationTimeout):
            execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        self.assertEqual(generator.generate.call_count, 2)
        invalid = fixture()
        invalid["policy"]["sources"][0]["sha256"] = "0" * 64
        self.tearDown()
        self.setUp()
        generator, deadline = self.clocked("high", (300, invalid), (200, timeout))
        with self.assertRaises(GenerationTimeout):
            execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        self.assertEqual(generator.generate.call_count, 2)
        self.assertFalse(any(row["event"] == "policy_generation_retry" for row in self.run_events()))

    def test_other_generation_failures_are_not_retried(self):
        generator, deadline = self.clocked("high", (5, GenerationError("Codex reported a failed generation turn")))
        with self.assertRaisesRegex(GenerationError, "failed generation turn"):
            execute(self.args("--generate-only"), generator=generator, deadline=deadline)
        self.assertEqual(generator.generate.call_count, 1)


class RetryEffortEvidenceTests(unittest.TestCase):
    setUp = test_package_provenance.PackageProvenanceTests.setUp
    records = test_package_provenance.PackageProvenanceTests.records
    rewrite = test_package_provenance.PackageProvenanceTests.rewrite

    def retried(self, *, recorded=True, extra_call=False):
        source, run, events = self.records()
        call = next(i for i, event in enumerate(events) if event.get("prompt_characters"))
        retry = [{"event": "policy_generation_retry", "reasoning_effort": "medium", "time": events[call]["time"]}] if recorded else []
        second = [{**events[call - 1], "time": events[call]["time"]},
                  {**events[call], "reasoning_effort": "medium"}]
        later = second if extra_call else []
        events[call + 1:call + 1] = retry + second + later
        self.rewrite(source, run, events)

    def test_package_accepts_the_recorded_retry_effort_for_the_next_call_only(self):
        self.retried()
        package.check_manifest(self.manifest)
        for kwargs in ({"recorded": False}, {"extra_call": True}):
            self.manifest = self.helper.evidence()
            self.retried(**kwargs)
            with self.subTest(**kwargs), self.assertRaisesRegex(ValueError, "model calls differ"):
                package.check_manifest(self.manifest)

    def test_bundle_accepts_the_recorded_retry_effort_for_the_next_call_only(self):
        for recorded, ready in ((True, True), (False, False)):
            inputs = recorded_inputs()
            call = next(i for i, row in enumerate(inputs[2]) if row["event"] == "model_call" and row["prompt_characters"])
            inputs[2][call]["reasoning_effort"] = "medium"
            if recorded:
                inputs[2].insert(call, {"event": "policy_generation_retry", "reasoning_effort": "medium",
                                        "time": inputs[2][call]["time"]})
            inputs[3]["trace_sha256"] = digest(inputs[2])
            result = assemble_bundle(*inputs)
            with self.subTest(recorded=recorded):
                self.assertEqual(result["ready"], ready, result)
                if not ready:
                    self.assertEqual(result["reason"], "build_trace_model_mismatch")


if __name__ == "__main__":
    unittest.main()

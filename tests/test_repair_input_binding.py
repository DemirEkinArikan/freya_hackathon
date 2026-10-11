"""Repair acceptance stays bound to evaluator-only inputs; all services are doubles."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from benchmarks.hosted import run_builder_tests
from builder import build
import test_builder
from test_hosted_runner import RunnerBackend, fixture_rows


def read(path):
    return json.loads(Path(path).read_text())


def raw_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RepairInputBindingTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def configured_generator(self):
        generator = self.generator()
        generator.generate.return_value = test_builder.fixture()
        return generator

    def run_directory(self, output="out"):
        runs = list((self.directory / output).iterdir())
        self.assertEqual(len(runs), 1)
        return runs[0]

    def hosted_inputs(self):
        rows = fixture_rows(2)
        cases = self.directory / "evaluator-private-input.json"
        payload = {"kind": "hosted", "cases": copy.deepcopy(rows),
                   "evaluator_note": "PRIVATE_EVALUATOR_INPUT_938147_ö"}
        cases.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return cases, rows, payload

    def backend(self, rows, *, behavior_failure=False):
        backend = RunnerBackend(rows)
        original_iteration = backend.iteration
        def iteration(simulation):
            result = original_iteration(simulation)
            for row in result["runs"]:
                row["agentVersionNumber"] = 1
                if behavior_failure:
                    row.update(outcome="fail", reason=(
                        "promise lacks exact readback and subsequent confirmation before tool start"))
            return result
        backend.iteration = iteration
        return backend

    def assert_binding(self, run, cases, payload, brief=None):
        binding = read(run / "evaluator-input.json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        expected = {"source_file": str(cases.relative_to(build.PROJECT)),
                    "source_sha256": raw_digest(cases),
                    "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
                    "snapshot_file": "evaluator-cases.json",
                    "snapshot_sha256": raw_digest(run / "evaluator-cases.json"),
                    "selection": {"brief": brief}}
        self.assertEqual(binding, expected)
        self.assertEqual(read(run / "evaluator-cases.json"), payload)
        self.assertEqual(read(run / "manifest.json")["evaluator_input"], binding)
        for attempt in (run / "attempts").glob("*/attempt.json"):
            self.assertEqual(read(attempt)["evaluator_input"], binding)
        return binding

    def test_invalid_evaluator_json_is_rejected_before_model_client_or_runner(self):
        for index, text in enumerate(("{", '{"criteria": 1, "criteria": 2}', '{"criteria": NaN}')):
            with self.subTest(text=text):
                cases = self.directory / f"invalid-evaluator-{index}.json"
                cases.write_text(text)
                generator, client_factory, runner = self.configured_generator(), Mock(), Mock()
                output = f"invalid-{index}"
                with self.assertRaises(ValueError):
                    build.execute(self.args("--test-cases", str(cases), "--output-dir",
                                            str(self.directory / output)),
                                  generator=generator, client_factory=client_factory,
                                  test_runner=runner)
                generator.generate.assert_not_called()
                generator.generate_plan.assert_not_called()
                client_factory.assert_not_called()
                runner.assert_not_called()
                self.assertEqual(read(self.run_directory(output) / "manifest.json")["status"], "failed")

    def test_selected_holdout_is_rejected_even_with_an_injected_passing_runner(self):
        for index, brief in enumerate((None, "A")):
            with self.subTest(brief=brief):
                row = fixture_rows()[0]
                row["split"] = "holdout"
                cases = self.directory / f"reserved-evaluator-{index}.json"
                cases.write_text(json.dumps({"kind": "hosted", "cases": [row]}))
                generator, client_factory = self.configured_generator(), Mock()
                runner = Mock(return_value={"status": "completed", "passed": 1, "total": 1,
                                            "failures": []})
                flags = ["--test-cases", str(cases), "--output-dir",
                         str(self.directory / f"holdout-{index}")]
                if brief:
                    flags.extend(["--test-brief", brief])
                with self.assertRaisesRegex(ValueError, "holdout"):
                    build.execute(self.args(*flags), generator=generator,
                                  client_factory=client_factory, test_runner=runner)
                generator.generate.assert_not_called()
                generator.generate_plan.assert_not_called()
                client_factory.assert_not_called()
                runner.assert_not_called()

    def test_unselected_other_brief_holdout_does_not_block_actual_selected_runner(self):
        cases, rows, payload = self.hosted_inputs()
        all_rows = read(build.PROJECT / "benchmarks/fixtures/hosted.json")["cases"]
        unused_holdout = copy.deepcopy(next(row for row in all_rows if row["brief"] == "B"))
        unused_holdout["split"] = "holdout"
        payload["cases"].append(unused_holdout)
        cases.write_text(json.dumps(payload))
        backend = self.backend(rows)
        with patch("benchmarks.hosted.HostedBackend", return_value=backend):
            result = build.execute(self.args("--test-cases", str(cases), "--test-brief", "A"),
                                   generator=self.configured_generator(),
                                   client_factory=test_builder.FakeClient,
                                   test_runner=run_builder_tests)
        self.assertEqual((result["status"], result["tests"]["total"]), ("exported", 2))
        self.assertEqual(len(backend.sessions), 2)
        self.assert_binding(self.run_directory(), cases, payload, brief="A")

    def test_actual_behavioral_repair_uses_one_snapshot_and_binding_for_all_attempts(self):
        cases, rows, payload = self.hosted_inputs()
        original_bytes = cases.read_bytes()
        generator, client = self.configured_generator(), test_builder.FakeClient()
        observed = []
        def runner(client, deployment, cases_path, attempt_dir, deadline):
            snapshot = Path(attempt_dir).parents[1] / "evaluator-cases.json"
            self.assertEqual(Path(cases_path), snapshot)
            self.assertNotEqual(Path(cases_path), cases)
            self.assertEqual(read(cases_path), payload)
            observed.append((Path(cases_path), raw_digest(cases_path)))
            return run_builder_tests(client, deployment, cases_path, attempt_dir, deadline)
        with patch("benchmarks.hosted.HostedBackend", side_effect=[
                self.backend(rows, behavior_failure=True), self.backend(rows)]):
            result = build.execute(self.args("--test-cases", str(cases)), generator=generator,
                                   client_factory=lambda **kwargs: client, test_runner=runner)
        self.assertEqual((result["status"], result["repairs"]), ("exported", 1))
        self.assertEqual((client.saves, client.exports, generator.generate.call_count,
                          generator.generate_plan.call_count), (2, 1, 1, 2))
        run = self.run_directory()
        binding = self.assert_binding(run, cases, payload)
        self.assertEqual(observed, [(run / "evaluator-cases.json", binding["snapshot_sha256"])] * 2)
        self.assertEqual(cases.read_bytes(), original_bytes)
        self.assertNotEqual(binding["source_sha256"], binding["snapshot_sha256"])
        attempts = [run / "attempts" / f"plan-{index:03d}" for index in (1, 2)]
        self.assertEqual([read(attempt / "attempt.json")["status"] for attempt in attempts],
                         ["behavioral_failure", "exported"])
        self.assertEqual([read(attempt / "tests.json")["passed"] for attempt in attempts], [0, 2])
        for call in generator.generate.call_args_list + generator.generate_plan.call_args_list:
            prompt = call.args[0]
            for private in (payload["evaluator_note"], cases.name, "evaluator_input",
                            "evaluator-cases.json", binding["source_sha256"],
                            binding["canonical_sha256"], binding["snapshot_sha256"]):
                self.assertNotIn(private, prompt)

    def test_legacy_injected_runner_accepts_arbitrary_valid_json_and_retains_failure_binding(self):
        cases = self.directory / "fixture.json"
        payload = read(cases)
        evidence = {"status": "infrastructure_failure", "passed": 0, "total": 1, "failures": []}
        generator, client = self.configured_generator(), test_builder.FakeClient()
        runner = Mock(return_value=evidence)
        with self.assertRaises(build.HostedTestInfrastructureError):
            build.execute(self.args("--test-cases", str(cases)), generator=generator,
                          client_factory=lambda **kwargs: client, test_runner=runner)
        run = self.run_directory()
        self.assert_binding(run, cases, payload)
        self.assertEqual(runner.call_args.args[2], run / "evaluator-cases.json")
        self.assertEqual(read(run / "attempts/plan-001/tests.json"), evidence)
        self.assertEqual(read(run / "manifest.json")["failure_kind"], "test_infrastructure")
        self.assertEqual((client.saves, client.exports, generator.generate_plan.call_count), (1, 0, 1))

    def assert_changed_input_stops_repair(self, run, generator, client, runner, expected_calls):
        attempt = run / "attempts/plan-001"
        binding = read(run / "evaluator-input.json")
        self.assertEqual(read(attempt / "attempt.json")["evaluator_input"], binding)
        manifest = read(run / "manifest.json")
        self.assertEqual(manifest["evaluator_input"], binding)
        self.assertEqual(manifest["failure_kind"], "test_infrastructure")
        self.assertFalse(manifest["hosted_behavior_verified"])
        self.assertEqual((generator.generate.call_count, generator.generate_plan.call_count), (1, 1))
        self.assertEqual((client.saves, client.exports, runner.call_count), (1, 0, expected_calls))
        self.assertEqual(read(attempt / "plan.json"), test_builder.plan())
        self.assertTrue((attempt / "workflow.json").is_file())
        self.assertFalse((run / "attempts/plan-002").exists())
        self.assertFalse((run / "submission.workflow.json").exists())

    def test_source_or_snapshot_mutation_before_runner_prevents_evaluation_and_export(self):
        for target in ("source", "snapshot"):
            with self.subTest(target=target):
                output = f"before-{target}"
                cases = self.directory / "fixture.json"
                generator, client = self.configured_generator(), test_builder.FakeClient()
                original_save = client.save_workflow
                def save(*args):
                    result = original_save(*args)
                    run = self.run_directory(output)
                    changed = cases if target == "source" else run / "evaluator-cases.json"
                    # Formatting-only mutation keeps parsed criteria identical.
                    changed.write_bytes(changed.read_bytes() + b"\n")
                    return result
                client.save_workflow = save
                runner = Mock()
                with self.assertRaisesRegex(build.HostedTestInfrastructureError, "Evaluator input changed"):
                    build.execute(self.args("--test-cases", str(cases), "--output-dir",
                                            str(self.directory / output)), generator=generator,
                                  client_factory=lambda **kwargs: client, test_runner=runner)
                run = self.run_directory(output)
                self.assert_changed_input_stops_repair(run, generator, client, runner, 0)
                self.assertFalse((run / "attempts/plan-001/tests.json").exists())

    def test_source_or_snapshot_mutation_during_actual_runner_retains_evidence_without_repair(self):
        for target in ("source", "snapshot"):
            with self.subTest(target=target):
                output = f"during-{target}"
                cases, rows, _ = self.hosted_inputs()
                generator, client = self.configured_generator(), test_builder.FakeClient()
                evidence = []
                def mutate_after_tests(client, deployment, cases_path, attempt_dir, deadline):
                    result = run_builder_tests(client, deployment, cases_path, attempt_dir, deadline)
                    evidence.append(copy.deepcopy(result))
                    changed = cases if target == "source" else Path(cases_path)
                    changed.write_bytes(changed.read_bytes() + b"\n")
                    return result
                runner = Mock(side_effect=mutate_after_tests)
                with patch("benchmarks.hosted.HostedBackend", return_value=self.backend(rows)), \
                        self.assertRaisesRegex(build.HostedTestInfrastructureError, "Evaluator input changed"):
                    build.execute(self.args("--test-cases", str(cases), "--output-dir",
                                            str(self.directory / output)), generator=generator,
                                  client_factory=lambda **kwargs: client, test_runner=runner)
                run = self.run_directory(output)
                self.assert_changed_input_stops_repair(run, generator, client, runner, 1)
                self.assertEqual(read(run / "attempts/plan-001/tests.json"), evidence[0])
                self.assertEqual(read(run / "benchmark-results.json"), evidence[0])
                self.assertEqual((evidence[0]["status"], evidence[0]["passed"], evidence[0]["total"]),
                                 ("completed", 2, 2))


if __name__ == "__main__":
    unittest.main()

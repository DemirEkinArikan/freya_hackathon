"""Evidence lifecycle tests with fake backends only; no network or model calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.evidence import calendar_values, record_evaluation
from benchmarks.hosted import evaluate
from builder.safety import digest
from test_hosted_runner import RunnerBackend, fixture_rows

ROOT = Path(__file__).resolve().parents[1]


class RecordingTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "artifacts").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "artifacts")
        self.addCleanup(self.temp.cleanup)
        self.rows = fixture_rows()
        self.conditions = {"reps": 3, "concurrency": 2, "budget_seconds": 120}

    def backend(self):
        backend = RunnerBackend(self.rows)
        original = backend.practice_session
        def session(seed):
            result = original(seed)
            result.update(now="2026-10-14T11:00", timezone="Europe/Istanbul", customer_secret="PRIVATE")
            return result
        backend.practice_session = session
        return backend

    def run_recording(self, backend=None, **kwargs):
        return record_evaluation(backend or self.backend(), "agent", self.rows,
            directory=self.temp.name, conditions=self.conditions, kind="mock", **kwargs)

    def test_recording_preserves_report_sessions_and_number_of_calls(self):
        backend = self.backend()
        expected = evaluate(self.backend(), "agent", self.rows, reps=3, concurrency=2)
        report, path = self.run_recording(backend)
        self.assertEqual(report, expected)
        record = json.loads(path.read_text())
        self.assertEqual(record["report"], expected)
        self.assertEqual(record["kind"], "mock")
        self.assertEqual(record["conditions"], self.conditions)
        self.assertEqual(record["case_manifest_sha256"], digest(self.rows))
        self.assertTrue(record["calendar_complete"])
        self.assertEqual(len(record["calendar"]), 3)
        self.assertEqual(record["calendar"][0]["values"], {"now": "2026-10-14T11:00", "timezone": "Europe/Istanbul"})
        self.assertEqual(len(backend.simulations), 3)
        for forbidden in ("PRIVATE", "customer-0", "session-0", "server_data"):
            self.assertNotIn(forbidden, path.read_text())

    def test_calls_are_timed_without_claiming_agent_response_latency(self):
        tick = iter(range(1000))
        _, path = self.run_recording(clock=lambda: next(tick))
        record = json.loads(path.read_text())
        self.assertGreater(record["elapsed_seconds"], 0)
        self.assertTrue(all(op["elapsed_seconds"] >= 1 for op in record["operations"]))
        self.assertNotIn("latency", record)
        self.assertIn("+00:00", record["started_at"])

    def test_runs_have_distinct_paths_and_never_replace_original_records(self):
        _, first = self.run_recording()
        before = first.read_bytes()
        _, second = self.run_recording()
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), before)

    def test_missing_server_calendar_is_explicit_without_fixture_fallback(self):
        report, path = self.run_recording(RunnerBackend(self.rows))
        record = json.loads(path.read_text())
        self.assertEqual(report["passed"], 1)
        self.assertFalse(record["calendar_complete"])
        self.assertIsNone(record["calendar_sha256"])
        self.assertEqual(record["calendar"][0]["values"], {})

    def test_partial_evidence_and_no_retry_after_ledger_failure(self):
        backend = self.backend()
        backend.team_key = "private-team-credential"
        def fail(customer):
            raise RuntimeError("private-team-credential " + customer)
        backend.ledger = fail
        report, path = self.run_recording(backend)
        value = json.loads(path.read_text())
        self.assertEqual(report["status"], "infrastructure_failure")
        self.assertEqual(len(backend.simulations), 3)
        self.assertEqual(len([op for op in value["operations"] if op["operation"] == "ledger"]), 3)
        self.assertIsNotNone(value["report"]["rows"][0]["transcript"])
        self.assertNotIn("private-team-credential", path.read_text())
        self.assertNotIn("customer-0", path.read_text())

    def test_early_exception_retains_fixed_error_and_original_exception(self):
        backend = self.backend()
        def fail(_):
            raise RuntimeError("private value from remote failure")
        backend.create_personas = fail
        with self.assertRaisesRegex(RuntimeError, "private value"):
            self.run_recording(backend)
        paths = list(Path(self.temp.name).glob("*/trial.json"))
        self.assertEqual(len(paths), 1)
        record = json.loads(paths[0].read_text())
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "evaluation_interrupted")
        self.assertNotIn("private value", paths[0].read_text())
        self.assertEqual(backend.simulations, [])

    def test_interrupt_preserves_existing_simulation_handoff_without_extra_calls(self):
        backend = self.backend()
        backend.simulation_handoff = {"simulation_id": "original-simulation", "iteration_id": "original-iteration",
                                      "status": "polling", "customer_id": "private-extra", "unrelated": "omit"}
        def interrupted(*args):
            raise KeyboardInterrupt()
        backend.simulate = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.run_recording(backend)
        record = json.loads(next(Path(self.temp.name).glob("*/trial.json")).read_text())
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["simulation_handoff"], {"simulation_id": "original-simulation",
            "iteration_id": "original-iteration", "status": "polling"})
        self.assertNotIn("private-extra", json.dumps(record))
        self.assertEqual([operation["operation"] for operation in record["operations"]].count("simulate"), 1)

    def test_calendar_is_persisted_before_simulation_and_cannot_mutate_server_data(self):
        backend = self.backend()
        original = backend.simulate
        def simulate(*args):
            path = next(Path(self.temp.name).glob("*/trial.json"))
            record = json.loads(path.read_text())
            self.assertEqual(record["status"], "running")
            self.assertTrue(record["calendar"])
            self.assertEqual(next(iter(args[2].values()))["customer_secret"], "PRIVATE")
            return original(*args)
        backend.simulate = simulate
        self.run_recording(backend)

    def test_backend_credential_is_redacted_in_initial_record_before_first_call(self):
        backend = self.backend()
        backend.team_key = "backend-only-private-key"
        self.conditions["builder_model"] = backend.team_key
        original = backend.create_personas
        def create(personas):
            path = next(Path(self.temp.name).glob("*/trial.json"))
            self.assertNotIn(backend.team_key, path.read_text())
            return original(personas)
        backend.create_personas = create
        self.run_recording(backend)

    def test_client_key_override_is_redacted_before_initial_save_and_in_partial_report(self):
        backend = self.backend()
        key = "opaque-client-override-981582"
        backend.client = SimpleNamespace(api_key=key)
        self.conditions["builder_model"] = key
        original = backend.create_personas
        def create(personas):
            path = next(Path(self.temp.name).glob("*/trial.json"))
            self.assertNotIn(key, path.read_text())
            return original(personas)
        backend.create_personas = create
        def ledger(_):
            raise RuntimeError(key)
        backend.ledger = ledger
        report, path = self.run_recording(backend)
        self.assertEqual(report["status"], "infrastructure_failure")
        self.assertNotIn(key, path.read_text())

    def test_calendar_validation_does_not_persist_arbitrary_text(self):
        self.assertEqual(calendar_values({"now": "secret", "timezone": "secret"}), {})
        self.assertEqual(calendar_values({"now": "2026-02-31T11:00"}), {})
        self.assertEqual(calendar_values({"now": "2026-10-14T11:00Z"}), {"now": "2026-10-14T11:00Z"})


class HostedCommandRecordingTests(unittest.TestCase):
    def test_standalone_command_records_effective_settings_and_preserves_summary(self):
        from benchmarks.hosted import main
        from builder.platform import STAGING
        rows = fixture_rows()
        backend = RunnerBackend(rows)
        original = backend.practice_session
        backend.practice_session = lambda seed: {**original(seed), "now": "2026-10-14T11:00"}
        (ROOT / "artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temp:
            root = Path(temp)
            target = root / "summary.json"
            argv = ["hosted", "--execute", "--agent-id", "agent", "--reps", "1", "--concurrency", "2",
                    "--budget-seconds", "120", "--max-call-seconds", "137", "--out", str(target),
                    "--evidence-dir", str(root / "artifacts/evaluations"),
                    "--simulation-lease", str(root / "artifacts/.simulation.lock")]
            with patch("sys.argv", argv), patch("benchmarks.hosted.ROOT", root / "benchmarks"), \
                 patch("benchmarks.hosted.load_cases", return_value=({}, rows)), \
                 patch("builder.config.load_local_environment"), \
                 patch("builder.platform.FreyaClient", return_value=SimpleNamespace(base_url=STAGING, request=None)), \
                 patch("benchmarks.hosted.HostedBackend", return_value=backend), patch("builtins.print"):
                main()
            record = json.loads(next((root / "artifacts/evaluations").glob("*/trial.json")).read_text())
            self.assertEqual(record["report"], json.loads(target.read_text()))
            self.assertEqual(record["conditions"]["concurrency"], 2)
            self.assertEqual(record["conditions"]["budget_seconds"], 120)
            self.assertEqual(record["conditions"]["max_call_seconds"], 137)
            self.assertEqual(record["conditions"]["platform_get_retry"], {"max_retries": 5, "statuses": [500, 502, 503, 504]})
            self.assertEqual(record["conditions"]["platform_base_url"], STAGING)
            self.assertIsNone(record["conditions"]["builder_model"])
            self.assertEqual(len(backend.simulations), 1)

    def test_operator_route_and_freya_credentials_are_excluded_from_generation_environment(self):
        from builder.runtime import generation_environment
        result = generation_environment({"PATH": "/synthetic/bin", "FREYA_BANK_CONNECT_ADDRESS": "192.0.2.1",
                                         "FREYA_BANK_TEAM_KEY": "synthetic-private", "FREYA_API_KEY": "synthetic-key"})
        self.assertEqual(result, {"PATH": "/synthetic/bin"})

    def test_inspection_does_not_initialize_client_or_recorder(self):
        from benchmarks.hosted import main
        with patch("sys.argv", ["hosted", "--dry-run", "--brief", "A"]), \
             patch("builder.platform.FreyaClient") as client, \
             patch("benchmarks.evidence.record_evaluation") as recorder, patch("builtins.print"):
            main()
        client.assert_not_called()
        recorder.assert_not_called()

"""Actual CLI wiring with synthetic service doubles; zero model/network calls.

These fixtures deliberately simulate live-labelled envelopes to test rejection
and association. They are not authenticated live evidence or ranking claims.
"""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmarks.hosted import main as hosted_main
from benchmarks.bundle import main as bundle_main
from benchmarks.compare import compare
from builder.platform import STAGING
from builder.safety import digest
from test_evaluation_bundle import recorded_inputs
from test_hosted_runner import RunnerBackend

ROOT = Path(__file__).resolve().parents[1]


class PinnedServices(RunnerBackend):
    def __init__(self, rows, pin, prefix):
        super().__init__(rows)
        self.pin, self.prefix = pin, prefix
        self.bank_transport_configuration = {"mode": "system_dns", "certificate_verification": True,
            "hostname_verification": True, "redirects": False, "retries": 0, "proxies": False}

    def practice_session(self, seed):
        value = super().practice_session(seed)
        return {**value, "customer_id": self.prefix + value["customer_id"],
                "session_id": self.prefix + value["session_id"], "now": "2032-04-07T11:00", "timezone": "Europe/Istanbul"}

    def validate_deployment(self, deployment):
        return deepcopy(self.pin)

    def simulate(self, *args):
        value = super().simulate(*args)
        return {**value, "simulation_id": self.prefix + value["simulation_id"],
                "iteration_id": self.prefix + value["iteration_id"]}

    def iteration(self, simulation):
        value = super().iteration(simulation)
        for row in value["runs"]:
            row["callId"] = self.prefix + row["callId"]
            row["agentVersionNumber"] = self.pin["version_number"]
        return value

    def call(self, call_id):
        return {**super().call(call_id), "agentVersionNumber": self.pin["version_number"]}


class EvaluationCliPipelineTests(unittest.TestCase):
    def test_actual_hosted_then_bundle_commands_produce_accepted_paired_inputs(self):
        (ROOT / "artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            root = Path(temporary)
            outputs = {}
            for candidate in ("baseline", "structured"):
                inputs = recorded_inputs(candidate)
                manifest, workflow, trace, provenance, train, validation = inputs
                directory = root / candidate
                directory.mkdir()
                build_files = {}
                for name, value in zip(("manifest", "workflow", "trace", "build-provenance"), inputs[:4]):
                    target = directory / (name + (".jsonl" if name == "trace" else ".json"))
                    target.write_text("\n".join(json.dumps(event) for event in value) if name == "trace" else json.dumps(value))
                    build_files[name] = target
                # The normal deployment loader reads the original neighbouring export.
                (directory / "submission.workflow.json").write_text(json.dumps(workflow))
                records = {}
                for split, original in (("train", train), ("validation", validation)):
                    cases = directory / (split + "-cases.json")
                    cases.write_text(json.dumps({"kind": "hosted", "cases": original["declared_rows"]}))
                    services = PinnedServices(original["declared_rows"], manifest["deployment"], candidate + split)
                    command = ["--execute", "--agent-id", manifest["deployment"]["agent_id"],
                        "--deployment-manifest", str(build_files["manifest"]), "--cases", str(cases),
                        "--split", split, "--brief", "A", "--reps", "3", "--concurrency", "5", "--budget-seconds", "1800",
                        "--simulation-lease", str(root / ".simulation.lock"),
                        "--evidence-dir", str(directory / "evaluations"), "--out", str(directory / (split + "-summary.json"))]
                    printed = io.StringIO()
                    with patch("builder.config.load_local_environment"), \
                         patch("builder.platform.FreyaClient", return_value=SimpleNamespace(base_url=STAGING, request=None)), \
                         patch("benchmarks.hosted.HostedBackend", return_value=services), \
                         patch("benchmarks.evidence.utc_now", side_effect=[original["started_at"], original["ended_at"]]), \
                         contextlib.redirect_stdout(printed):
                        hosted_main(command)
                    records[split] = Path(json.loads(printed.getvalue())["evidence_record"])
                    capture = json.loads(records[split].read_text())
                    self.assertEqual(capture["case_manifest_sha256"], digest(original["declared_rows"]))
                    self.assertEqual(len(services.simulations), 3)
                    self.assertEqual(len(capture["report"]["rows"]), len(original["declared_rows"]) * 3)
                target = directory / "bundle.json"
                args = [item for name, path in build_files.items() for item in ("--" + name, str(path))]
                args.extend(["--train", str(records["train"]), "--validation", str(records["validation"]), "--out", str(target)])
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bundle_main(args), 0)
                outputs[candidate] = json.loads(target.read_text())
                self.assertEqual(outputs[candidate]["build_provenance"], provenance)
            result = compare(outputs["baseline"], outputs["structured"])
            self.assertTrue(result["comparable"], result)

    def test_explicit_shared_lease_and_new_output_required_before_client_creation(self):
        with patch("builder.platform.FreyaClient") as client, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                hosted_main(["--execute", "--agent-id", "synthetic", "--brief", "A"])
        client.assert_not_called()
        (ROOT / "artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as directory:
            target = Path(directory) / "existing.json"
            target.write_text("original")
            with patch("builder.platform.FreyaClient") as client:
                with self.assertRaises(FileExistsError):
                    hosted_main(["--execute", "--agent-id", "synthetic", "--brief", "A", "--simulation-lease",
                                 str(Path(directory) / ".simulation.lock"), "--out", str(target)])
            client.assert_not_called()
            self.assertEqual(target.read_text(), "original")

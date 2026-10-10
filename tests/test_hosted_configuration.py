"""Controller settings forwarding uses local doubles only."""
import unittest
import io
import json
import os
from pathlib import Path
import tempfile
import urllib.parse
from types import SimpleNamespace
from copy import deepcopy
from benchmarks.bank_transport import BankConnectTransport
from benchmarks.hosted import BANK
from unittest.mock import Mock, patch
from benchmarks.hosted import HostedBackend


class HostedConfigurationTests(unittest.TestCase):
    def test_explicit_call_duration_reaches_existing_client_method(self):
        client = Mock()
        backend = HostedBackend(client, team_key="synthetic-team-key", opener=lambda *a, **k: None,
                                max_call_seconds=137)
        variables = {"synthetic-persona": {"unchanged": "server data"}}
        backend.simulate("synthetic-agent", "synthetic-run", variables, 2)
        client.run_simulation.assert_called_once_with("synthetic-agent", "synthetic-run", variables,
                                                      max_seconds=137, concurrency=2)

    def test_default_duration_remains_420_and_invalid_values_fail_before_calls(self):
        client = Mock()
        backend = HostedBackend(client, team_key="synthetic-team-key", opener=lambda *a, **k: None)
        self.assertEqual(backend.max_call_seconds, 420)
        for value in (True, 0, -1, 1.5, "90", 3601):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HostedBackend(client, team_key="synthetic-team-key", max_call_seconds=value)
        client.run_simulation.assert_not_called()

    def test_trusted_environment_route_is_actual_transport_and_preserves_request_bytes(self):
        with patch.dict(os.environ, {"FREYA_BANK_CONNECT_ADDRESS": "192.0.2.11"}):
            backend = HostedBackend(Mock(), team_key="synthetic-team-key")
        self.assertIsInstance(backend.opener, BankConnectTransport)
        self.assertEqual(backend.bank_transport_configuration, backend.opener.configuration())
        self.assertEqual(backend.bank_transport_configuration["connect_address"], "192.0.2.11")
        with patch.object(backend.opener._opener, "open", return_value=io.BytesIO(json.dumps({"variables": {"now": "2032-04-07T11:00"}}).encode())) as opened:
            result = backend.practice_session("PUB01")
        self.assertEqual(result, {"now": "2032-04-07T11:00"})
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, BANK + "/v1/practice/sessions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"case": "PUB01"})
        self.assertEqual(request.get_header("X-team-key"), "synthetic-team-key")

    def test_explicit_route_overrides_environment_and_injected_conflict_is_rejected(self):
        with patch.dict(os.environ, {"FREYA_BANK_CONNECT_ADDRESS": "192.0.2.11"}):
            backend = HostedBackend(Mock(), team_key="synthetic-team-key", bank_connect_address="192.0.2.12")
        self.assertEqual(backend.bank_transport_configuration["connect_address"], "192.0.2.12")
        with self.assertRaises(ValueError):
            HostedBackend(Mock(), team_key="synthetic-team-key", opener=lambda *a, **k: None,
                          bank_connect_address="192.0.2.12")

    def test_real_builder_test_hook_persists_actual_route_and_duration(self):
        from benchmarks.hosted import run_builder_tests
        from builder.runtime import Deadline
        from test_evaluation_cli_pipeline import PinnedServices
        from test_evaluation_bundle import recorded_inputs
        manifest, workflow, _, _, train, _ = recorded_inputs()
        rows = train["declared_rows"][:1]
        services = PinnedServices(rows, manifest["deployment"], "production-hook")
        class Client:
            def create_personas(self, values): return services.create_personas(values)
            def run_simulation(self, agent, name, variables, *, max_seconds, concurrency):
                self.actual_duration = max_seconds
                return services.simulate(agent, name, variables, concurrency)
            def get_iteration(self, simulation, iteration): return services.iteration({"simulation_id": simulation, "iteration_id": iteration})
            def get_call(self, identifier): return services.call(identifier)
            def get_workflow(self, identifier): return {"agentId": manifest["deployment"]["agent_id"], "workflow": workflow["workflow"]}
            def workspace_id(self): return "synthetic-workspace"
            def request(self, method, path, *, params):
                return {"versions": [{"id": manifest["deployment"]["version_id"], "versionNumber": 4, "isPublished": True}]}
        client = Client()
        def network_response(request, *, timeout):
            if request.get_method() == "POST":
                result = {"variables": services.practice_session(json.loads(request.data)["case"])}
            else:
                customer = urllib.parse.unquote(request.full_url.rsplit("/", 1)[1])
                result = services.ledger(customer)
            return io.BytesIO(json.dumps(result).encode())
        root = Path(__file__).resolve().parents[1]
        (root / "artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root / "artifacts") as directory:
            path = Path(directory)
            cases = path / "cases.json"
            cases.write_text(json.dumps({"kind": "hosted", "cases": rows}))
            deployment = {**deepcopy(manifest["deployment"]), "workflow": workflow["workflow"], "brief": "A"}
            with patch.dict(os.environ, {"FREYA_BANK_CONNECT_ADDRESS": "192.0.2.11", "FREYA_BANK_TEAM_KEY": "synthetic-team-key"}), \
                 patch("urllib.request.OpenerDirector.open", side_effect=network_response):
                result = run_builder_tests(client, deployment, cases, path / "attempt", Deadline(1800))
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["passed"], result["total"])
            self.assertEqual(result["bank_transport"], BankConnectTransport(BANK, "192.0.2.11").configuration())
            self.assertEqual(result["max_call_seconds"], client.actual_duration)
            self.assertEqual(client.actual_duration, 420)
            self.assertEqual(json.loads((path / "attempt/benchmark-results.json").read_text()), result)

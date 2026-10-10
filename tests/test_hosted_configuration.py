"""Controller settings forwarding uses local doubles only."""
import unittest
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

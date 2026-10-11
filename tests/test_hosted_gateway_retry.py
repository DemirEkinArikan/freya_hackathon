import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from benchmarks.hosted import GATEWAY_RETRIES, evaluate, retry_gateway_gets
from builder.platform import FreyaClient, PlatformError
from test_hosted_runner import RunnerBackend, fixture_rows

PROJECT = Path(__file__).resolve().parents[1]


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class GatewayRetryTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / ".test-tmp").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT / ".test-tmp")
        self.addCleanup(self.tmp.cleanup)

    def client(self, polls, post_status=None):
        """Real FreyaClient over a fake HTTP opener; polls serves HTTP codes (int) or statuses (str) in order."""
        self.sleeps, self.calls, self.log = [], [], []
        client = FreyaClient(api_key="unit-test-placeholder", lease_path=Path(self.tmp.name) / "simulation.lock",
                             sleep=self.sleeps.append)
        client._workspace = "workspace-1"

        def opener(req, timeout):
            path = req.full_url[len(client.base_url):].split("?")[0]
            self.calls.append((req.get_method(), path))
            status = post_status if req.get_method() == "POST" else polls.pop(0) if path == "/api/v2/simulations/sim" else None
            if type(status) is int:
                raise urllib.error.HTTPError(req.full_url, status, "gateway", {}, io.BytesIO(b"error code: %d" % status))
            if path.endswith("/iterations"):
                return Response(json.dumps({"iterations": [{"id": "iteration"}]}).encode())
            return Response(json.dumps({"simulation": {"id": "sim", "status": status or "running"}}).encode())

        client.opener = opener
        retry_gateway_gets(client, self.log)
        return client

    def test_transient_status_poll_gateway_error_is_retried_and_recorded(self):
        client = self.client([504, "completed"])
        result = client.run_simulation("agent", "practice", {"persona": {}})
        self.assertEqual(result, {"simulation_id": "sim", "iteration_id": "iteration", "status": "completed"})
        self.assertEqual([call for call in self.calls if call[0] == "POST"], [("POST", "/api/v2/simulations")])
        self.assertEqual([{k: v for k, v in row.items() if k != "at"} for row in self.log],
                         [{"method": "GET", "path": "/api/v2/simulations/sim", "status": 504, "attempt": 1}])
        self.assertEqual(self.sleeps, [10, 10])

    def test_status_poll_internal_server_error_is_retried_and_recorded(self):
        client = self.client([500, "completed"])
        self.assertEqual(client.run_simulation("agent", "practice", {"persona": {}})["status"], "completed")
        self.assertEqual([(row["status"], row["attempt"]) for row in self.log], [(500, 1)])

    def test_writes_and_other_statuses_are_not_retried(self):
        client = self.client([501], post_status=500)
        with self.assertRaisesRegex(PlatformError, "HTTP 500"):
            client.request("POST", "/api/v2/simulations", {"agentId": "agent"})
        with self.assertRaisesRegex(PlatformError, "HTTP 501"):
            client.request("GET", "/api/v2/simulations/sim")
        self.assertEqual(self.log, [])
        self.assertEqual(len(self.calls), 2)

    def test_retries_are_bounded(self):
        client = self.client([502] * (GATEWAY_RETRIES + 1))
        with self.assertRaisesRegex(PlatformError, "HTTP 502"):
            client.request("GET", "/api/v2/simulations/sim")
        self.assertEqual([row["attempt"] for row in self.log], list(range(1, GATEWAY_RETRIES + 1)))
        self.assertEqual(len(self.calls), GATEWAY_RETRIES + 1)

    def test_report_carries_recorded_retries(self):
        rows = fixture_rows()
        backend = RunnerBackend(rows)
        backend.platform_retries = [{"method": "GET", "path": "/api/v2/simulations/sim", "status": 504, "attempt": 1}]
        self.assertEqual(evaluate(backend, "agent", rows)["platform_retries"], backend.platform_retries)
        self.assertEqual(evaluate(RunnerBackend(rows), "agent", rows)["platform_retries"], [])


if __name__ == "__main__":
    unittest.main()

"""Validate platform version types from actual readback, using local mocks."""
from copy import deepcopy
import unittest

from benchmarks.hosted import HostedBackend


class PinClient:
    def __init__(self, version):
        self.version = version

    def get_workflow(self, workflow_id):
        return {"agentId": "mock-agent", "workflow": {"nodes": []}}

    def workspace_id(self):
        return "mock-workspace"

    def request(self, *args, **kwargs):
        return {"versions": [{"id": "mock-version", "versionNumber": self.version, "isPublished": True}]}


class HostedPinReadbackTests(unittest.TestCase):
    def setUp(self):
        self.deployment = {"workflow_id": "mock-workflow", "agent_id": "mock-agent",
                           "version_id": "mock-version", "version_number": 1,
                           "workflow": {"nodes": []}, "published": True}

    def test_readback_accepts_only_an_exact_positive_integer_version(self):
        for version in (True, 1.0, "1", None, 0, -1, 2):
            backend = HostedBackend(PinClient(version), team_key="synthetic-placeholder-key")
            with self.subTest(version=version), self.assertRaises(ValueError):
                backend.validate_deployment(self.deployment)
        backend = HostedBackend(PinClient(1), team_key="synthetic-placeholder-key")
        self.assertEqual(backend.validate_deployment(self.deployment)["version_number"], 1)

    def test_direct_pin_readback_rejects_invalid_requested_versions(self):
        for version in (True, 1.0, "1", None, 0, -1):
            deployment = deepcopy(self.deployment)
            deployment["version_number"] = version
            backend = HostedBackend(PinClient(version), team_key="synthetic-placeholder-key")
            with self.subTest(version=version), self.assertRaises(ValueError):
                backend.validate_deployment(deployment)


if __name__ == "__main__":
    unittest.main()

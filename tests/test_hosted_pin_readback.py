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

    def backend_for_response(self, response):
        client = PinClient(1)
        client.request = lambda *args, **kwargs: deepcopy(response)
        return HostedBackend(client, team_key="synthetic-placeholder-key")

    def test_wrapped_and_bare_versions_verify_the_same_exact_pin(self):
        versions = [{"id": "mock-version", "versionNumber": 1, "isPublished": True},
                    {"id": "draft-version", "versionNumber": 2, "isPublished": False}]
        wrapped = self.backend_for_response({"versions": versions}).validate_deployment(self.deployment)
        bare = self.backend_for_response(versions).validate_deployment(self.deployment)
        self.assertEqual(bare, wrapped)
        self.assertEqual(bare["version_id"], "mock-version")
        self.assertIs(bare["published"], True)

    def test_malformed_version_inventory_is_rejected(self):
        valid = {"id": "mock-version", "versionNumber": 1, "isPublished": True}
        inventories = [None, {}, "invalid", [], [None], ["invalid"]]
        for field, value in (("id", None), ("id", ""), ("id", "bad/id"),
                             ("versionNumber", True), ("versionNumber", 1.0),
                             ("versionNumber", "2"), ("versionNumber", 0),
                             ("isPublished", None), ("isPublished", 1),
                             ("isPublished", "false")):
            malformed = {"id": "other-version", "versionNumber": 2, "isPublished": False}
            malformed[field] = value
            inventories.append([valid, malformed])
        for versions in inventories:
            for response in (versions, {"versions": versions}):
                with self.subTest(response=response), self.assertRaises(ValueError):
                    self.backend_for_response(response).validate_deployment(self.deployment)

    def test_duplicate_or_ambiguous_publication_inventory_is_rejected(self):
        valid = {"id": "mock-version", "versionNumber": 1, "isPublished": True}
        alternatives = [{"id": "mock-version", "versionNumber": 2, "isPublished": False},
                        {"id": "other-version", "versionNumber": 1, "isPublished": False},
                        {"id": "other-version", "versionNumber": 2, "isPublished": True}]
        for other in alternatives:
            for response in ([valid, other], {"versions": [valid, other]}):
                with self.subTest(response=response), self.assertRaises(ValueError):
                    self.backend_for_response(response).validate_deployment(self.deployment)

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

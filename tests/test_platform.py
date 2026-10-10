import copy
import json
from pathlib import Path
import tempfile
import unittest

from builder.platform import AGENT_LLM, FreyaClient, PlatformError

PROJECT = Path(__file__).resolve().parents[1]


def definition():
    return {"initial_node": "greeting", "nodes": {"greeting": {"name": "greeting", "node_type": "conversation", "task_message": "Banking"}}}


class MemoryPlatform(FreyaClient):
    def __init__(self, *, strip=False, refuse_model=False):
        super().__init__(api_key="unit-test-placeholder")
        self.calls = []
        self.rows = {}
        self.versions = []
        self.strip, self.refuse_model = strip, refuse_model

    def request(self, method, path, body=None, params=None):
        self.calls.append((method, path, copy.deepcopy(body), params))
        if path == "/api/v2/workspaces":
            return {"workspaces": [{"id": "workspace-1"}]}
        if path == "/api/v2/workflow" and params:
            return {"valid": True}
        if path == "/api/v2/workflow":
            self.rows["w1"] = {"id": "w1", "agentId": "a1", "workflow": copy.deepcopy(body["workflow"])}
            self.versions = [{"id": "v1", "versionNumber": 1, "isPublished": False}]
            return {"workflow": self.rows["w1"]}
        if path == "/api/v2/workflow/w1":
            if method == "PUT":
                self.rows["w1"]["workflow"] = copy.deepcopy(body["workflow"])
                if self.refuse_model:
                    self.rows["w1"]["workflow"].pop("agent_config", None)
                self.versions.append({"id": "v" + str(len(self.versions) + 1), "versionNumber": len(self.versions) + 1, "isPublished": False})
            if self.strip:
                self.rows["w1"]["workflow"]["nodes"]["greeting"].pop("task_message", None)
            return {"workflow": copy.deepcopy(self.rows["w1"])}
        if path.endswith("/publish"):
            for row in self.versions:
                row["isPublished"] = row["id"] == body["versionId"]
            return {"published": True}
        if path.startswith("/api/v2/versions/"):
            return {"versions": copy.deepcopy(self.versions)}
        raise AssertionError((method, path))


class PlatformTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / ".test-tmp").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT / ".test-tmp")
        self.directory = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_updates_slot_publishes_and_checks_fixed_model(self):
        client = MemoryPlatform()
        state = self.directory / "slots.json"
        one = client.save_workflow("brief", definition(), state)
        self.assertTrue(one["published"])
        self.assertEqual(one["workflow"]["agent_config"]["llmConfig"], AGENT_LLM)
        updated = definition()
        updated["nodes"]["greeting"]["task_message"] = "Changed banking policy"
        two = client.save_workflow("brief", updated, state)
        self.assertEqual(one["workflow_id"], two["workflow_id"])
        self.assertGreater(two["version_number"], one["version_number"])
        self.assertEqual(sum(method == "POST" and path == "/api/v2/workflow" for method, path, _, _ in client.calls), 1)
        self.assertTrue(any(path.endswith("/publish") for _, path, _, _ in client.calls))

    def test_stripped_field_fails_and_keeps_created_id_for_retry(self):
        client = MemoryPlatform(strip=True)
        state = self.directory / "slots.json"
        with self.assertRaises(PlatformError):
            client.save_workflow("brief", definition(), state)
        self.assertEqual(json.loads(state.read_text())["slots"]["brief"]["workflow_id"], "w1")

    def test_fixed_model_repair_must_actually_persist(self):
        with self.assertRaises(PlatformError):
            MemoryPlatform(refuse_model=True).save_workflow("brief", definition(), self.directory / "slots.json")

    def test_export_reads_actual_platform_definition(self):
        client = MemoryPlatform()
        deployment = client.save_workflow("brief", definition(), self.directory / "slots.json")
        exported = client.export_workflow(deployment, self.directory / "export.json")
        self.assertEqual(exported["workflow"], deployment["workflow"])
        client.rows["w1"]["workflow"]["nodes"]["greeting"]["task_message"] = "Unreviewed mutation"
        with self.assertRaises(PlatformError):
            client.export_workflow(deployment, self.directory / "export.json")

    def test_credentials_never_go_to_an_arbitrary_host(self):
        with self.assertRaises(PlatformError):
            FreyaClient(api_key="unit-test", base_url="https://example.com")

    def test_explicit_adoption_verifies_workspace_and_updates_without_creation(self):
        client = MemoryPlatform()
        client.rows["w1"] = {"id": "w1", "agentId": "a1", "workspaceId": "workspace-1", "workflow": definition()}
        client.versions = [{"id": "v1", "versionNumber": 1, "isPublished": True}]
        state = self.directory / "slots.json"
        binding = client.adopt_workflow("brief", "w1", state)
        self.assertEqual(binding["workspace_id"], "workspace-1")
        self.assertFalse(any(method in {"PUT", "POST"} for method, _, _, _ in client.calls))
        deployment = client.save_workflow("brief", definition(), state)
        self.assertEqual(deployment["workflow_id"], "w1")
        self.assertFalse(any(method == "POST" and path == "/api/v2/workflow" for method, path, _, _ in client.calls))

    def test_adoption_requires_explicit_matching_workspace_metadata(self):
        for workspace in (None, "other-workspace"):
            with self.subTest(workspace=workspace):
                client = MemoryPlatform()
                client.rows["w1"] = {"id": "w1", "agentId": "a1", "workflow": definition()}
                if workspace is not None:
                    client.rows["w1"]["workspaceId"] = workspace
                state = self.directory / "slots.json"
                with self.assertRaisesRegex(PlatformError, "workspace metadata"):
                    client.adopt_workflow("brief", "w1", state)
                self.assertFalse(state.exists())
                self.assertFalse(any(method in {"PUT", "POST"} for method, _, _, _ in client.calls))

    def test_adoption_refuses_pending_or_conflicting_local_bindings(self):
        conflicts = [
            {"brief": {"creation_pending": True}},
            {"brief": {"workflow_id": "another-workflow"}},
            {"other-brief": {"workflow_id": "w1"}},
        ]
        for slots in conflicts:
            with self.subTest(slots=slots):
                client = MemoryPlatform()
                state = self.directory / "slots.json"
                state.write_text(json.dumps({"scope": {"base_url": client.base_url, "workspace_id": "workspace-1"}, "slots": slots}))
                with self.assertRaises(PlatformError):
                    client.adopt_workflow("brief", "w1", state)
                self.assertEqual(json.loads(state.read_text())["slots"], slots)
                self.assertFalse(any(path == "/api/v2/workflow/w1" for _, path, _, _ in client.calls))

    def test_adoption_rejects_path_or_query_injection_before_any_request(self):
        client = MemoryPlatform()
        with self.assertRaises(PlatformError):
            client.adopt_workflow("brief", "w1/other?workspaceId=other", self.directory / "slots.json")
        self.assertEqual(client.calls, [])

    def test_ambiguous_creation_is_not_replayed_on_rerun(self):
        client = MemoryPlatform()
        original = client.request
        attempts = []
        def lost_response(method, path, body=None, params=None):
            if method == "POST" and path == "/api/v2/workflow":
                attempts.append(path)
                raise PlatformError("transport failure; persistence may be unknown")
            return original(method, path, body, params)
        client.request = lost_response
        state = self.directory / "slots.json"
        with self.assertRaises(PlatformError):
            client.save_workflow("brief", definition(), state)
        with self.assertRaisesRegex(PlatformError, "reconcile"):
            client.save_workflow("brief", definition(), state)
        self.assertEqual(len(attempts), 1)

    def test_iteration_collects_every_page_and_checks_total(self):
        client = MemoryPlatform()
        offsets = []
        def pages(method, path, body=None, params=None):
            offsets.append(params["offset"])
            return {"runs": [{"id": str(i)} for i in range(params["offset"], min(params["offset"] + 100, 101))], "runsTotal": 101}
        client.request = pages
        self.assertEqual(len(client.get_iteration("s", "i")["runs"]), 101)
        self.assertEqual(offsets, [0, 100])


if __name__ == "__main__":
    unittest.main()

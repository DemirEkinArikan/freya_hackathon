import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from builder.platform import AGENT_LLM, FreyaClient, PlatformError
from builder.runtime import Deadline, DeadlineExceeded

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

    def test_dry_run_has_validation_flag_and_never_binds_a_slot(self):
        client = MemoryPlatform()
        authored = definition()
        self.assertEqual(client.validate_workflow("brief", authored), {"valid": True})
        self.assertEqual(len(client.calls), 1)
        method, path, body, params = client.calls[0]
        self.assertEqual((method, path, params), ("POST", "/api/v2/workflow", {"dryRun": "true"}))
        self.assertEqual(body["workflow"], authored)
        self.assertRegex(body["name"], r"^builder-validation-[0-9a-f]{32}$")
        self.assertEqual(client.rows, {})

    def test_bare_route_payloads_can_save_publish_and_export(self):
        client = MemoryPlatform()
        request = client.request
        def bare(method, path, body=None, params=None):
            result = request(method, path, body, params)
            key = "workspaces" if path == "/api/v2/workspaces" else "versions" if "/versions/" in path and not path.endswith("/publish") else "workflow"
            return result.get(key, result)
        client.request = bare
        deployment = client.save_workflow("brief", definition(), self.directory / "slots.json")
        exported = client.export_workflow(deployment, self.directory / "export.json")
        self.assertEqual(exported["workflow_id"], "w1")

    def test_invalid_state_never_updates_or_creates_a_workflow(self):
        invalid = ["{", "[]", json.dumps({"slots": []}),
                   {"brief": None}, {"brief": {}}, {"brief": {"workflow_id": "w1?other=true"}},
                   {"brief": {"workflow_id": "w1"}, "duplicate": {"workflow_id": "w1"}}]
        for slots in invalid:
            with self.subTest(slots=slots):
                client = MemoryPlatform()
                state = self.directory / "slots.json"
                contents = slots if isinstance(slots, str) else json.dumps({"scope": {"base_url": client.base_url, "workspace_id": "workspace-1"}, "slots": slots})
                state.write_text(contents)
                with self.assertRaises(PlatformError):
                    client.save_workflow("brief", definition(), state)
                self.assertEqual(state.read_text(), contents)
                self.assertFalse(any(method in {"PUT", "POST"} for method, _, _, _ in client.calls))

    def test_failed_update_invalidates_previous_publication_evidence(self):
        client = MemoryPlatform()
        state = self.directory / "slots.json"
        client.save_workflow("brief", definition(), state)
        request = client.request
        def ambiguous_update(method, path, body=None, params=None):
            response = request(method, path, body, params)
            if method == "PUT":
                raise PlatformError("response lost after update")
            return response
        client.request = ambiguous_update
        with self.assertRaises(PlatformError):
            client.save_workflow("brief", definition(), state)
        binding = json.loads(state.read_text())["slots"]["brief"]
        self.assertEqual(binding["workflow_id"], "w1")
        self.assertIs(binding["published"], False)
        client.request = request
        self.assertTrue(client.save_workflow("brief", definition(), state)["published"])
        self.assertEqual(sum(method == "POST" and path == "/api/v2/workflow" for method, path, _, _ in client.calls), 1)

    def test_new_version_during_publish_is_rejected(self):
        client = MemoryPlatform()
        state = self.directory / "slots.json"
        request = client.request
        def concurrent_update(method, path, body=None, params=None):
            response = request(method, path, body, params)
            if path.endswith("/publish"):
                client.versions.append({"id": "concurrent", "versionNumber": 99, "isPublished": False})
            return response
        client.request = concurrent_update
        with self.assertRaisesRegex(PlatformError, "changed during verification"):
            client.save_workflow("brief", definition(), state)
        self.assertIs(json.loads(state.read_text())["slots"]["brief"]["published"], False)

    def test_export_rejects_changed_publication_or_agent_without_writing(self):
        for change in ("unpublish", "new-version", "different-agent"):
            with self.subTest(change=change):
                client = MemoryPlatform()
                deployment = client.save_workflow("brief", definition(), self.directory / (change + ".json"))
                if change == "unpublish":
                    client.versions[-1]["isPublished"] = False
                elif change == "new-version":
                    client.versions.append({"id": "next", "versionNumber": 99, "isPublished": False})
                else:
                    client.rows["w1"]["agentId"] = "another-agent"
                output = self.directory / (change + ".workflow.json")
                with self.assertRaises(PlatformError):
                    client.export_workflow(deployment, output)
                self.assertFalse(output.exists())

    def test_wrong_readback_id_is_rejected_before_publication(self):
        client = MemoryPlatform()
        request = client.request
        def wrong_id(method, path, body=None, params=None):
            result = request(method, path, body, params)
            if method == "GET" and path == "/api/v2/workflow/w1":
                result["workflow"]["id"] = "another-workflow"
            return result
        client.request = wrong_id
        with self.assertRaisesRegex(PlatformError, "matching workflow"):
            client.save_workflow("brief", definition(), self.directory / "slots.json")
        self.assertFalse(any(path.endswith("/publish") for _, path, _, _ in client.calls))

    def test_http_headers_envelope_and_deadline_reserve(self):
        calls = []
        deadline = Deadline(seconds=45, clock=lambda: 0)
        def opener(request, timeout):
            calls.append((request, timeout))
            return io.BytesIO(json.dumps({"success": True, "data": {"result": {"valid": True}}}).encode())
        client = FreyaClient(api_key="unit-test", deadline=deadline, reserve=15, opener=opener)
        self.assertEqual(client.validate_workflow("brief", definition()), {"valid": True})
        request, timeout = calls[0]
        self.assertTrue(request.get_header("User-agent").startswith("Mozilla/5.0"))
        self.assertEqual(request.get_header("Authorization"), "Bearer unit-test")
        self.assertEqual(timeout, 30)
        self.assertTrue(request.full_url.endswith("?dryRun=true"))
        deadline.limit = 15
        with self.assertRaises(DeadlineExceeded):
            client.validate_workflow("brief", definition())
        self.assertEqual(len(calls), 1)

    def test_http_failures_are_redacted_and_never_retried(self):
        calls = []
        def reject(request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 400, "bad request", {}, io.BytesIO(b'unit-test-secret-key'))
        client = FreyaClient(api_key="unit-test-secret-key", opener=reject)
        with self.assertRaises(PlatformError) as caught:
            client.validate_workflow("brief", definition())
        self.assertNotIn("unit-test-secret-key", str(caught.exception))
        self.assertIn("HTTP 400", str(caught.exception))
        self.assertEqual(len(calls), 1)

    def test_malformed_http_envelopes_raise_platform_errors(self):
        for payload in (None, "wrong", {"success": True, "data": None},
                        {"success": True, "data": {"result": None}}, {"success": False, "data": None}):
            with self.subTest(payload=payload):
                client = FreyaClient(api_key="unit-test", opener=lambda request, timeout: io.BytesIO(json.dumps(payload).encode()))
                with self.assertRaises(PlatformError):
                    client.validate_workflow("brief", definition())

    def test_iteration_rejects_server_repeating_a_full_page(self):
        client = MemoryPlatform()
        offsets = []
        def repeated(method, path, body=None, params=None):
            offsets.append(params["offset"])
            return {"runs": [{"id": str(i)} for i in range(100)], "runsTotal": 200}
        client.request = repeated
        with self.assertRaisesRegex(PlatformError, "repeated a run"):
            client.get_iteration("s", "i")
        self.assertEqual(offsets, [0, 100])

    def test_iteration_rejects_missing_rows_and_changing_totals(self):
        for changed_total in (100, 102):
            with self.subTest(changed_total=changed_total):
                client = MemoryPlatform()
                def pages(method, path, body=None, params=None):
                    offset = params["offset"]
                    return {"runs": [{"id": str(i)} for i in range(offset, min(offset + 100, 101))],
                            "runsTotal": 101 if offset == 0 else changed_total}
                client.request = pages
                with self.assertRaises(PlatformError):
                    client.get_iteration("s", "i")
        client = MemoryPlatform()
        client.request = lambda method, path, body=None, params=None: (
            {"runs": [{"id": str(i)} for i in range(100)], "runsTotal": 101}
            if params["offset"] == 0 else {"runs": []})
        with self.assertRaisesRegex(PlatformError, "every run"):
            client.get_iteration("s", "i")

    def test_simulation_polling_stops_at_deadline_and_keeps_event_models(self):
        now = [0.0]
        deadline = Deadline(seconds=40, clock=lambda: now[0])
        client = FreyaClient(api_key="unit-test", deadline=deadline, reserve=10,
                            lease_path=self.directory / "simulation.lock", sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        client._workspace = "workspace-1"
        calls = []
        def forever_running(method, path, body=None, params=None):
            calls.append((method, path, copy.deepcopy(body)))
            return {"simulation": {"id": "sim", "status": "running"}}
        client.request = forever_running
        with self.assertRaises(DeadlineExceeded) as interrupted:
            client.run_simulation("agent", "practice", {"persona": {}})
        self.assertEqual(now[0], 30)
        self.assertEqual(len(calls), 4)
        for field in ("assistantLlmOverride", "routingLlmOverride", "extractionLlmOverride"):
            self.assertEqual(calls[0][2][field], AGENT_LLM)
        handoff = json.loads(client.lease_path.with_suffix(".json").read_text())
        self.assertEqual(handoff["simulation_id"], "sim")
        self.assertEqual(handoff["status"], "active")
        self.assertEqual(handoff["agent_id"], "agent")
        self.assertIn("started_at", handoff)
        self.assertEqual(client.simulation_handoff["simulation_id"], "sim")
        self.assertEqual(interrupted.exception.simulation_id, "sim")
        self.assertEqual(interrupted.exception.lease["simulation_id"], "sim")
        self.assertNotIn("persona", json.dumps(handoff))

    def test_new_client_checks_server_before_replacing_timed_out_simulation(self):
        lease = self.directory / "simulation.lock"
        now = [0.0]
        first = FreyaClient(api_key="unit-test", deadline=Deadline(seconds=10, clock=lambda: now[0]),
                            lease_path=lease, sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        first._workspace = "workspace-1"
        first.request = lambda method, path, body=None, params=None: {"simulation": {"id": "previous", "status": "running"}}
        with self.assertRaises(DeadlineExceeded):
            first.run_simulation("agent", "practice", {"persona": {}})
        second = FreyaClient(api_key="unit-test", lease_path=lease, sleep=lambda _: None)
        second._workspace = "workspace-1"
        calls = []
        server_status = ["running"]
        def server(method, path, body=None, params=None):
            calls.append((method, path))
            if path == "/api/v2/simulations/previous":
                return {"simulation": {"id": "previous", "status": server_status[0]}}
            if method == "POST":
                return {"simulation": {"id": "replacement", "status": "running"}}
            if path.endswith("/iterations"):
                return {"iterations": [{"id": "iteration"}]}
            return {"simulation": {"id": "replacement", "status": "completed"}}
        second.request = server
        with self.assertRaisesRegex(PlatformError, "previous is still active") as active:
            second.run_simulation("agent", "next practice", {"persona": {}})
        self.assertEqual(calls, [("GET", "/api/v2/simulations/previous")])
        self.assertEqual(active.exception.simulation_id, "previous")
        server_status[0] = "paused"
        with self.assertRaisesRegex(PlatformError, "still active"):
            second.run_simulation("agent", "next practice", {"persona": {}})
        self.assertFalse(any(method == "POST" for method, _ in calls))
        server_status[0] = "completed"
        result = second.run_simulation("agent", "next practice", {"persona": {}})
        self.assertEqual(result["simulation_id"], "replacement")
        self.assertEqual(calls[2:4], [("GET", "/api/v2/simulations/previous"), ("POST", "/api/v2/simulations")])
        state = json.loads(lease.with_suffix(".json").read_text())
        self.assertEqual(state["iteration_id"], "iteration")
        self.assertEqual(state["status"], "completed")

    def test_ambiguous_simulation_creation_is_not_replayed_by_new_client(self):
        lease = self.directory / "simulation.lock"
        calls = []
        def lost_response(method, path, body=None, params=None):
            calls.append((method, path))
            raise PlatformError("transport failed after persistence may have happened")
        for attempt in range(2):
            client = FreyaClient(api_key="unit-test", lease_path=lease)
            client._workspace = "workspace-1"
            client.request = lost_response
            with self.assertRaises(PlatformError):
                client.run_simulation("agent", "practice", {"persona": {}})
        self.assertEqual(calls, [("POST", "/api/v2/simulations")])
        self.assertIs(json.loads(lease.with_suffix(".json").read_text())["creation_pending"], True)


if __name__ == "__main__":
    unittest.main()

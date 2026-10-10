"""Bounded adapter for the organizer's staging workflow and simulation API."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from .runtime import Deadline, FileLock
from .safety import sanitize

STAGING = "https://staging-app.freyavoice.ai"
AGENT_LLM = {"provider": "experimental-5", "model": "experimental-5"}
SIM_LLM = {"provider": "google", "model": "gemini-3-flash-preview"}


class PlatformError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PlatformError("Platform redirects are refused to protect credentials")


def _authored_equal(expected: Any, actual: Any) -> bool:
    """Require every authored value; tolerate platform-added defaults only."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _authored_equal(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(_authored_equal(a, b) for a, b in zip(expected, actual))
    return type(expected) is type(actual) and expected == actual


class FreyaClient:
    def __init__(self, api_key: str | None = None, base_url: str = STAGING, deadline: Deadline | None = None,
                 opener: Callable | None = None, lease_path: Path | None = None, reserve: float = 0,
                 sleep: Callable[[float], None] = time.sleep):
        if base_url.rstrip("/") != STAGING:
            raise PlatformError("This hackathon client only sends credentials to the organizer staging host")
        self.api_key = api_key if api_key is not None else os.environ.get("FREYA_API_KEY", "")
        if not self.api_key:
            raise PlatformError("FREYA_API_KEY is required for live platform operations")
        self.base_url, self.deadline, self.reserve = STAGING, deadline or Deadline(), reserve
        self.opener = opener or urllib.request.build_opener(_NoRedirect()).open
        self.lease_path = Path(lease_path or "artifacts/.simulation.lock")
        self.sleep = sleep
        self._workspace = None

    def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        if not path.startswith("/api/v2/") or "?" in path or "#" in path:
            raise PlatformError("Invalid platform API path")
        query = "?" + urllib.parse.urlencode(params) if params else ""
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json",
                   "User-Agent": "Mozilla/5.0 (freya-banking-builder)"}
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path + query, data=data, headers=headers, method=method)
        try:
            with self.opener(req, timeout=self.deadline.timeout(120, reserve=self.reserve)) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise PlatformError("Platform response exceeded the size limit")
                result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            message = sanitize(exc.read(1500).decode("utf-8", errors="replace"), secrets=(self.api_key,))
            raise PlatformError(f"{method} {path}: HTTP {exc.code}: {message}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PlatformError(f"{method} {path}: transport or JSON response failure; persistence may be unknown") from exc
        if not isinstance(result, dict):
            raise PlatformError("Unexpected platform response type")
        if result.get("success") is False:
            message = sanitize(result.get("data", {}).get("message", "Platform operation rejected"), secrets=(self.api_key,))
            raise PlatformError(str(message))
        return result.get("data", {}).get("result", result)

    def workspace_id(self) -> str:
        if self._workspace is None:
            result = self.request("GET", "/api/v2/workspaces")
            items = result.get("workspaces") or result.get("items") or []
            configured = os.environ.get("FREYA_WORKSPACE_ID")
            if configured:
                if not any(item.get("id") == configured for item in items):
                    raise PlatformError("Configured workspace is not visible to this key")
                self._workspace = configured
            elif len(items) == 1:
                self._workspace = items[0]["id"]
            else:
                raise PlatformError("Set FREYA_WORKSPACE_ID when the key sees multiple workspaces")
        return self._workspace

    def get_workflow(self, workflow_id: str) -> dict:
        return self.request("GET", f"/api/v2/workflow/{workflow_id}", params={"workspaceId": self.workspace_id()})["workflow"]

    def _publish_and_verify(self, workflow_id: str, authored: dict) -> dict:
        stored = self.get_workflow(workflow_id)
        definition = stored["workflow"]
        if not _authored_equal(authored, definition) or set(authored["nodes"]) != set(definition.get("nodes", {})):
            raise PlatformError("Platform read-back changed or stripped an authored workflow field")
        config = copy.deepcopy(definition.get("agent_config") or {})
        llm = config.get("llmConfig") or {}
        if (llm.get("provider"), llm.get("model")) != (AGENT_LLM["provider"], AGENT_LLM["model"]):
            config["llmConfig"] = {**llm, **AGENT_LLM}
            definition = {**definition, "agent_config": config}
            self.request("PUT", f"/api/v2/workflow/{workflow_id}", {"workflow": definition})
        versions = self.request("GET", f"/api/v2/versions/workflow/{workflow_id}", params={"workspaceId": self.workspace_id()})["versions"]
        if not versions:
            raise PlatformError("The saved workflow has no version to publish")
        newest = max(versions, key=lambda item: item["versionNumber"])
        if not newest.get("isPublished"):
            self.request("POST", f"/api/v2/versions/workflow/{workflow_id}/publish", {"versionId": newest["id"]})
        readback = self.get_workflow(workflow_id)
        final = readback["workflow"]
        if not _authored_equal(authored, final) or set(authored["nodes"]) != set(final.get("nodes", {})):
            raise PlatformError("Published read-back differs from the authored workflow")
        deployed_llm = final.get("agent_config", {}).get("llmConfig", {})
        if any(deployed_llm.get(k) != v for k, v in AGENT_LLM.items()):
            raise PlatformError("Deployed workflow does not use the fixed event model")
        checked_versions = self.request("GET", f"/api/v2/versions/workflow/{workflow_id}", params={"workspaceId": self.workspace_id()})["versions"]
        published = next((v for v in checked_versions if v.get("id") == newest["id"]), None)
        if not published or not published.get("isPublished"):
            raise PlatformError("The newest workflow version was not published")
        return {"workflow_id": workflow_id, "agent_id": readback["agentId"], "version_id": newest["id"],
                "version_number": newest["versionNumber"], "published": True, "workflow": final,
                "updated_at": readback.get("updatedAt")}

    def validate_workflow(self, name: str, workflow: dict) -> dict:
        return self.request("POST", "/api/v2/workflow", {"name": name, "workflow": workflow}, params={"dryRun": "true"})

    def adopt_workflow(self, slot: str, workflow_id: str, state_file: Path = Path("artifacts/.freya-slots.json")) -> dict:
        """Bind an explicit existing ID; never discover or select a workflow by name."""
        if not isinstance(workflow_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workflow_id):
            raise PlatformError("An explicit valid workflow ID is required for adoption")
        state_file = Path(state_file)
        scope = {"base_url": self.base_url, "workspace_id": self.workspace_id()}
        with FileLock(state_file.with_suffix(".lock"), self.deadline, reserve=self.reserve):
            state = json.loads(state_file.read_text()) if state_file.exists() else {"scope": scope, "slots": {}}
            if state.get("scope") != scope or not isinstance(state.get("slots"), dict):
                raise PlatformError("Workflow slot state belongs to another platform workspace or is invalid")
            previous = state["slots"].get(slot)
            if previous and (previous.get("creation_pending") or previous.get("workflow_id") != workflow_id):
                raise PlatformError("Cannot adopt over a pending or differently bound workflow slot; reconcile it explicitly")
            if any(name != slot and binding.get("workflow_id") == workflow_id for name, binding in state["slots"].items()):
                raise PlatformError("This workflow is already bound to a different local slot")
            existing = self.get_workflow(workflow_id)
            workspace = existing.get("workspaceId", existing.get("workspace_id"))
            if workspace is None and isinstance(existing.get("workspace"), dict):
                workspace = existing["workspace"].get("id")
            if workspace != scope["workspace_id"]:
                raise PlatformError("Workflow adoption requires explicit read-back workspace metadata matching the selected workspace")
            if existing.get("id") != workflow_id or not existing.get("agentId") or not isinstance(existing.get("workflow"), dict):
                raise PlatformError("Workflow adoption read-back lacks matching workflow and agent metadata")
            binding = {"workflow_id": workflow_id, "agent_id": existing["agentId"],
                       "workspace_id": workspace, "adopted": True}
            state["slots"][slot] = binding
            state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            temporary.replace(state_file)
            return binding

    def save_workflow(self, slot: str, workflow: dict, state_file: Path = Path("artifacts/.freya-slots.json")) -> dict:
        state_file = Path(state_file)
        scope = {"base_url": self.base_url, "workspace_id": self.workspace_id()}
        with FileLock(state_file.with_suffix(".lock"), self.deadline, reserve=self.reserve):
            state = json.loads(state_file.read_text()) if state_file.exists() else {"scope": scope, "slots": {}}
            if state.get("scope") != scope:
                raise PlatformError("Workflow slot state belongs to another platform workspace")
            previous = state["slots"].get(slot)
            if previous:
                if previous.get("creation_pending"):
                    raise PlatformError("An earlier workflow creation has unknown persistence; reconcile that slot before retrying")
                workflow_id = previous["workflow_id"]
                self.request("PUT", f"/api/v2/workflow/{workflow_id}", {"workflow": workflow})
            else:
                # Mark the write before sending. A timeout after commit must not create a duplicate on rerun.
                state["slots"][slot] = {"creation_pending": True, "name": slot}
                state_file.parent.mkdir(parents=True, exist_ok=True)
                temporary = state_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
                temporary.replace(state_file)
                created = self.request("POST", "/api/v2/workflow", {"name": slot, "workflow": workflow})["workflow"]
                workflow_id = created["id"]
                # Persist IDs immediately; a later publication failure must not cause duplicate creation.
                state["slots"][slot] = {"workflow_id": workflow_id, "agent_id": created["agentId"], "published": False}
                state_file.parent.mkdir(parents=True, exist_ok=True)
                temporary = state_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
                temporary.replace(state_file)
            deployment = self._publish_and_verify(workflow_id, workflow)
            state["slots"][slot] = {k: v for k, v in deployment.items() if k != "workflow"}
            temporary = state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            temporary.replace(state_file)
            return deployment

    def export_workflow(self, deployment: dict, path: Path) -> dict:
        actual = self.get_workflow(deployment["workflow_id"])
        if deployment["workflow"] != actual["workflow"]:
            raise PlatformError("Workflow changed between verification and export")
        artifact = {"workflow_id": actual["id"], "agent_id": actual.get("agentId"),
                    "updated_at": actual.get("updatedAt"), "workflow": actual["workflow"]}
        Path(path).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        return artifact

    def create_personas(self, personas: list[dict]) -> list[dict]:
        return self.request("POST", "/api/v2/personas", {"personas": personas})["personas"]

    def run_simulation(self, agent_id: str, name: str, variables_by_persona: dict, max_seconds: int = 420, concurrency: int = 5) -> dict:
        with FileLock(self.lease_path, self.deadline, reserve=self.reserve):
            body = {"agentId": agent_id, "name": name, "mode": "chat", "selectedPersonaIds": list(variables_by_persona),
                    "customerVariablesMap": variables_by_persona, "maxConcurrentCalls": concurrency, "runsPerPersona": 1,
                    "maxCallDurationSeconds": max_seconds, "assistantLlmOverride": AGENT_LLM,
                    "routingLlmOverride": AGENT_LLM, "extractionLlmOverride": AGENT_LLM,
                    "personaLlmOverride": SIM_LLM, "analysisLlmOverride": SIM_LLM}
            while True:
                self.deadline.require(reserve=self.reserve)
                try:
                    response = self.request("POST", "/api/v2/simulations", body)
                    break
                except PlatformError as exc:
                    if "concurrent simulations" not in str(exc):
                        raise
                    self.sleep(min(30, self.deadline.timeout(30, reserve=self.reserve)))
            simulation_id = (response.get("simulation") or response)["id"]
            while True:
                self.sleep(min(10, self.deadline.timeout(10, reserve=self.reserve)))
                result = self.request("GET", f"/api/v2/simulations/{simulation_id}")
                simulation = result.get("simulation") or result
                if simulation.get("status") in ("completed", "failed", "paused"):
                    break
            iterations = self.request("GET", f"/api/v2/simulations/{simulation_id}/iterations")
            rows = iterations.get("iterations") or iterations.get("items") or []
            if not rows:
                raise PlatformError("The simulation ended without an iteration")
            return {"simulation_id": simulation_id, "iteration_id": rows[0]["id"], "status": simulation["status"]}

    def get_iteration(self, simulation_id: str, iteration_id: str) -> dict:
        result = None
        offset = 0
        while True:
            page = self.request("GET", f"/api/v2/simulations/{simulation_id}/iterations/{iteration_id}", params={"limit": 100, "offset": offset})
            if result is None:
                result = {**page, "runs": list(page["runs"])}
            else:
                result["runs"].extend(page["runs"])
            if len(page["runs"]) < 100:
                if page.get("runsTotal") is not None and len(result["runs"]) != page["runsTotal"]:
                    raise PlatformError("Iteration pagination did not return every run")
                return result
            offset += 100

    def get_call(self, call_id: str) -> dict:
        return self.request("GET", f"/api/v2/call/{call_id}")["call"]

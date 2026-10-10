"""Bounded adapter for the organizer's staging workflow and simulation API."""
from __future__ import annotations

import copy
import datetime as dt
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


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None


def _payload(result: Any, key: str, kind: type) -> Any:
    """The kit permits a named wrapper or the bare route payload."""
    value = result.get(key, result) if isinstance(result, dict) else result
    if not isinstance(value, kind):
        raise PlatformError(f"Unexpected platform {key} response")
    return value


def _read_slots(path: Path, scope: dict) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"scope": scope, "slots": {}}
    except (OSError, ValueError):
        raise PlatformError("Workflow slot state cannot be read; reconcile it before retrying") from None
    if not isinstance(state, dict) or state.get("scope") != scope or not isinstance(state.get("slots"), dict):
        raise PlatformError("Workflow slot state belongs to another platform workspace or is invalid")
    bound = set()
    for binding in state["slots"].values():
        if not isinstance(binding, dict):
            raise PlatformError("Workflow slot binding is invalid; reconcile it before retrying")
        if binding.get("creation_pending") is True:
            continue
        workflow_id = binding.get("workflow_id")
        if not _valid_id(workflow_id) or workflow_id in bound:
            raise PlatformError("Workflow slot binding is invalid or duplicated; reconcile it before retrying")
        bound.add(workflow_id)
    return state


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


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
        self.simulation_handoff = None

    def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict | list:
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
        if isinstance(result, list):
            return result
        if not isinstance(result, dict):
            raise PlatformError("Unexpected platform response type")
        envelope = result.get("data", {})
        if result.get("success") is False:
            message = sanitize(envelope.get("message", "Platform operation rejected") if isinstance(envelope, dict)
                               else "Platform operation rejected", secrets=(self.api_key,))
            raise PlatformError(str(message))
        payload = envelope.get("result", result) if isinstance(envelope, dict) else None
        if not isinstance(payload, (dict, list)):
            raise PlatformError("Unexpected platform response payload")
        return payload

    def workspace_id(self) -> str:
        if self._workspace is None:
            result = self.request("GET", "/api/v2/workspaces")
            items = _payload(result, "items" if isinstance(result, dict) and "items" in result else "workspaces", list)
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
        if not _valid_id(workflow_id):
            raise PlatformError("An explicit valid workflow ID is required")
        response = self.request("GET", f"/api/v2/workflow/{workflow_id}", params={"workspaceId": self.workspace_id()})
        # A bare workflow record also has a 'workflow' field containing the definition.
        row = response if isinstance(response, dict) and "id" in response else _payload(response, "workflow", dict)
        if row.get("id") != workflow_id or not _valid_id(row.get("agentId")) or not isinstance(row.get("workflow"), dict):
            raise PlatformError("Workflow read-back lacks matching workflow and agent metadata")
        return row

    def _versions(self, workflow_id: str) -> list[dict]:
        rows = _payload(self.request("GET", f"/api/v2/versions/workflow/{workflow_id}",
                                     params={"workspaceId": self.workspace_id()}), "versions", list)
        if not rows:
            raise PlatformError("The saved workflow has no version to publish")
        if any(not isinstance(row, dict) or not _valid_id(row.get("id")) or
               type(row.get("versionNumber")) is not int or row["versionNumber"] < 1 for row in rows):
            raise PlatformError("Workflow version metadata is invalid")
        if len({row["id"] for row in rows}) != len(rows) or len({row["versionNumber"] for row in rows}) != len(rows):
            raise PlatformError("Workflow version metadata is ambiguous")
        return rows

    def _verify_publication(self, workflow_id: str, version_id: str, version_number: int) -> None:
        rows = self._versions(workflow_id)
        newest = max(rows, key=lambda row: row["versionNumber"])
        published = [row for row in rows if row.get("isPublished") is True]
        if (newest["id"], newest["versionNumber"]) != (version_id, version_number) or len(published) != 1 or published[0]["id"] != version_id:
            raise PlatformError("The newest workflow version was not published or changed during verification")

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
        versions = self._versions(workflow_id)
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
        if readback["agentId"] != stored["agentId"]:
            raise PlatformError("Workflow agent changed during publication")
        self._verify_publication(workflow_id, newest["id"], newest["versionNumber"])
        return {"workflow_id": workflow_id, "agent_id": readback["agentId"], "version_id": newest["id"],
                "version_number": newest["versionNumber"], "published": True, "workflow": final,
                "updated_at": readback.get("updatedAt")}

    def validate_workflow(self, name: str, workflow: dict) -> dict:
        return self.request("POST", "/api/v2/workflow", {"name": name, "workflow": workflow}, params={"dryRun": "true"})

    def adopt_workflow(self, slot: str, workflow_id: str, state_file: Path = Path("artifacts/.freya-slots.json")) -> dict:
        """Bind an explicit existing ID; never discover or select a workflow by name."""
        if not _valid_id(workflow_id):
            raise PlatformError("An explicit valid workflow ID is required for adoption")
        state_file = Path(state_file)
        scope = {"base_url": self.base_url, "workspace_id": self.workspace_id()}
        with FileLock(state_file.with_suffix(".lock"), self.deadline, reserve=self.reserve):
            state = _read_slots(state_file, scope)
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
            _write_state(state_file, state)
            return binding

    def save_workflow(self, slot: str, workflow: dict, state_file: Path = Path("artifacts/.freya-slots.json")) -> dict:
        state_file = Path(state_file)
        scope = {"base_url": self.base_url, "workspace_id": self.workspace_id()}
        with FileLock(state_file.with_suffix(".lock"), self.deadline, reserve=self.reserve):
            state = _read_slots(state_file, scope)
            previous = state["slots"].get(slot)
            if previous:
                if previous.get("creation_pending"):
                    raise PlatformError("An earlier workflow creation has unknown persistence; reconcile that slot before retrying")
                workflow_id = previous["workflow_id"]
                # A failed update may have persisted. Never retain stale publication evidence.
                previous["published"] = False
                _write_state(state_file, state)
                self.request("PUT", f"/api/v2/workflow/{workflow_id}", {"workflow": workflow})
            else:
                # Mark the write before sending. A timeout after commit must not create a duplicate on rerun.
                state["slots"][slot] = {"creation_pending": True, "name": slot}
                _write_state(state_file, state)
                response = self.request("POST", "/api/v2/workflow", {"name": slot, "workflow": workflow})
                created = response if isinstance(response, dict) and "id" in response else _payload(response, "workflow", dict)
                if not _valid_id(created.get("id")) or not _valid_id(created.get("agentId")):
                    raise PlatformError("Creation response lacks workflow and agent metadata; reconcile this slot before retrying")
                workflow_id = created["id"]
                # Persist IDs immediately; a later publication failure must not cause duplicate creation.
                state["slots"][slot] = {"workflow_id": workflow_id, "agent_id": created["agentId"], "published": False}
                _write_state(state_file, state)
            deployment = self._publish_and_verify(workflow_id, workflow)
            state["slots"][slot] = {k: v for k, v in deployment.items() if k != "workflow"}
            _write_state(state_file, state)
            return deployment

    def export_workflow(self, deployment: dict, path: Path) -> dict:
        if deployment.get("published") is not True or not _valid_id(deployment.get("version_id")):
            raise PlatformError("Export requires a verified published deployment")
        actual = self.get_workflow(deployment["workflow_id"])
        if deployment["workflow"] != actual["workflow"] or deployment.get("agent_id") != actual["agentId"]:
            raise PlatformError("Workflow changed between verification and export")
        self._verify_publication(deployment["workflow_id"], deployment["version_id"], deployment["version_number"])
        artifact = {"workflow_id": actual["id"], "agent_id": actual.get("agentId"),
                    "updated_at": actual.get("updatedAt"), "workflow": actual["workflow"]}
        Path(path).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        return artifact

    def create_personas(self, personas: list[dict]) -> list[dict]:
        return _payload(self.request("POST", "/api/v2/personas", {"personas": personas}), "personas", list)

    def run_simulation(self, agent_id: str, name: str, variables_by_persona: dict, max_seconds: int = 420, concurrency: int = 5) -> dict:
        with FileLock(self.lease_path, self.deadline, reserve=self.reserve):
            self.simulation_handoff = None
            try:
                return self._run_simulation_locked(agent_id, name, variables_by_persona, max_seconds, concurrency)
            except BaseException as exc:
                # Preserve data-free IDs for evaluator traces, including deadline errors.
                try:
                    handoff = json.loads(self.lease_path.with_suffix(".json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    handoff = None
                if isinstance(handoff, dict) and handoff.get("scope") == {"base_url": self.base_url, "workspace_id": self._workspace}:
                    self.simulation_handoff = handoff
                    fields = {key: handoff[key] for key in ("simulation_id", "iteration_id")
                              if _valid_id(handoff.get(key))}
                    fields["path"] = str(self.lease_path.with_suffix(".json"))
                    exc.lease = fields
                    for key in ("simulation_id", "iteration_id"):
                        if key in fields:
                            setattr(exc, key, fields[key])
                raise

    def _run_simulation_locked(self, agent_id: str, name: str, variables_by_persona: dict, max_seconds: int = 420, concurrency: int = 5) -> dict:
        # flock ends when this process times out; the server simulation does not.
        # Keep a durable handoff alongside the lock and check it under that lock.
        state_path = self.lease_path.with_suffix(".json")
        scope = {"base_url": self.base_url, "workspace_id": self.workspace_id()}
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
        except (OSError, ValueError):
            raise PlatformError("Simulation lease state is unreadable; reconcile it before starting another simulation") from None
        if previous is not None:
            if not isinstance(previous, dict) or previous.get("scope") != scope:
                raise PlatformError("Simulation lease state belongs to another workspace or is invalid")
            self.simulation_handoff = previous
            simulation_id = previous.get("simulation_id")
            if previous.get("creation_pending") and not simulation_id:
                raise PlatformError("An earlier simulation creation has unknown persistence; reconcile the lease before retrying")
            if simulation_id is None and previous.get("status") != "waiting":
                raise PlatformError("Simulation lease state lacks an ID; reconcile it before retrying")
            if simulation_id is not None:
                if not _valid_id(simulation_id):
                    raise PlatformError("Simulation lease ID is invalid; reconcile it before retrying")
                result = self.request("GET", f"/api/v2/simulations/{simulation_id}")
                simulation = _payload(result, "simulation", dict)
                if simulation.get("id") != simulation_id:
                    raise PlatformError("Simulation lease server read-back has a different ID")
                if simulation.get("status") not in ("completed", "failed"):
                    raise PlatformError(f"Earlier simulation {simulation_id} is still active; keep its lease and wait for a terminal server status")
        body = {"agentId": agent_id, "name": name, "mode": "chat", "selectedPersonaIds": list(variables_by_persona),
                "customerVariablesMap": variables_by_persona, "maxConcurrentCalls": concurrency, "runsPerPersona": 1,
                "maxCallDurationSeconds": max_seconds, "assistantLlmOverride": AGENT_LLM,
                "routingLlmOverride": AGENT_LLM, "extractionLlmOverride": AGENT_LLM,
                "personaLlmOverride": SIM_LLM, "analysisLlmOverride": SIM_LLM}
        if not _valid_id(agent_id):
            raise PlatformError("A valid agent ID is required for simulation")
        handoff = {"scope": scope, "agent_id": agent_id, "name": sanitize(name, secrets=(self.api_key,)),
                   "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "creation_pending": True}
        self.simulation_handoff = handoff
        while True:
            self.deadline.require(reserve=self.reserve)
            _write_state(state_path, handoff)
            try:
                response = self.request("POST", "/api/v2/simulations", body)
                break
            except PlatformError as exc:
                if "concurrent simulations" not in str(exc):
                    raise
                # An explicit queue rejection created nothing; an ambiguous transport
                # failure instead retains creation_pending for human reconciliation.
                _write_state(state_path, {"scope": scope, "status": "waiting"})
                self.sleep(min(30, self.deadline.timeout(30, reserve=self.reserve)))
        simulation_id = _payload(response, "simulation", dict).get("id")
        if not _valid_id(simulation_id):
            raise PlatformError("Simulation creation lacks a valid ID; reconcile the lease before retrying")
        handoff = {**handoff, "creation_pending": False, "simulation_id": simulation_id, "status": "active"}
        self.simulation_handoff = handoff
        _write_state(state_path, handoff)
        while True:
            self.sleep(min(10, self.deadline.timeout(10, reserve=self.reserve)))
            result = self.request("GET", f"/api/v2/simulations/{simulation_id}")
            simulation = _payload(result, "simulation", dict)
            if simulation.get("id") != simulation_id:
                raise PlatformError("Simulation polling returned a different ID; keep the current lease")
            if simulation.get("status") in ("completed", "failed", "paused"):
                handoff["status"] = simulation["status"]
                _write_state(state_path, handoff)
                break
        iterations = self.request("GET", f"/api/v2/simulations/{simulation_id}/iterations")
        rows = _payload(iterations, "items" if isinstance(iterations, dict) and "items" in iterations else "iterations", list)
        if not rows:
            raise PlatformError("The simulation ended without an iteration")
        if not isinstance(rows[0], dict) or not _valid_id(rows[0].get("id")):
            raise PlatformError("The simulation iteration ID is invalid")
        handoff["iteration_id"] = rows[0]["id"]
        _write_state(state_path, handoff)
        return {"simulation_id": simulation_id, "iteration_id": rows[0]["id"], "status": simulation["status"]}

    def get_iteration(self, simulation_id: str, iteration_id: str) -> dict:
        result = None
        offset = 0
        seen = set()
        expected_total = None
        while True:
            self.deadline.require(reserve=self.reserve)
            page = self.request("GET", f"/api/v2/simulations/{simulation_id}/iterations/{iteration_id}", params={"limit": 100, "offset": offset})
            runs = page.get("runs") if isinstance(page, dict) else None
            if not isinstance(runs, list) or len(runs) > 100 or any(not isinstance(row, dict) or not _valid_id(row.get("id")) for row in runs):
                raise PlatformError("Iteration pagination returned invalid run metadata")
            ids = {row["id"] for row in runs}
            if len(ids) != len(runs) or seen.intersection(ids):
                raise PlatformError("Iteration pagination repeated a run; not every page can be verified")
            seen.update(ids)
            if result is None:
                result = {**page, "runs": list(runs)}
            else:
                result["runs"].extend(runs)
            total = page.get("runsTotal")
            if total is not None and (type(total) is not int or total < len(result["runs"]) or
                                      expected_total is not None and expected_total != total):
                raise PlatformError("Iteration pagination total is invalid or changed")
            if total is not None:
                expected_total = total
                result["runsTotal"] = total
            if len(runs) < 100:
                if expected_total is not None and len(result["runs"]) != expected_total:
                    raise PlatformError("Iteration pagination did not return every run")
                return result
            offset += 100

    def get_call(self, call_id: str) -> dict:
        return _payload(self.request("GET", f"/api/v2/call/{call_id}"), "call", dict)

"""Record evaluator evidence without changing verdicts or invoking generation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from builder.safety import digest, sanitize
from .paths import output_path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def calendar_values(session):
    """Only retain validated server calendar fields, never customer variables."""
    result = {}
    now = session.get("now") if isinstance(session, dict) else None
    if isinstance(now, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d{1,6})?)?(?:Z|[+-]\d\d:\d\d)?", now):
        try:
            datetime.fromisoformat(now)
            result["now"] = now
        except ValueError:
            pass
    zone = session.get("timezone") if isinstance(session, dict) else None
    if isinstance(zone, str) and len(zone) <= 80:
        try:
            ZoneInfo(zone)
            result["timezone"] = zone
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return result


def backend_secrets(backend):
    # Explicit client overrides may differ from environment credentials.
    return [value for value in (getattr(backend, "team_key", ""),
                               getattr(getattr(backend, "client", None), "api_key", ""))
            if isinstance(value, str) and value]


class EvidenceRecorder:
    def __init__(self, directory, rows, conditions, *, kind, wall_clock=None, clock=None, secrets=()):
        if kind not in {"live", "mock"}:
            raise ValueError("evidence kind must be live or mock")
        self.wall_clock, self.clock = wall_clock or utc_now, clock or time.monotonic
        self.rows = deepcopy(rows)
        self.secrets = [*secrets, os.environ.get("FREYA_API_KEY", ""), os.environ.get("FREYA_BANK_TEAM_KEY", "")]
        self.directory = output_path(Path(directory) / uuid4().hex)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.start = self.clock()
        self.record = {"version": 1, "kind": kind, "started_at": self.wall_clock(),
                       "conditions": deepcopy(conditions), "case_manifest_sha256": digest(rows),
                       "declared_rows": deepcopy(rows),
                       "selection": [{"fixture_id": r["fixture_id"], "bank_case_id": r["bank_case_id"],
                                      "split": r["split"], "brief": r["brief"]} for r in rows],
                       "calendar": [], "operations": [], "status": "running"}
        self.save()

    def save(self):
        # Fixed path in an exclusive run directory; atomic replacement only within this run.
        value = sanitize(self.record, secrets=self.secrets)
        temporary = self.directory / "record.tmp"
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(self.directory / "trial.json")

    def session(self, seed, result):
        if isinstance(result, dict):
            self.secrets.extend(result[k] for k in ("customer_id", "session_id")
                                if isinstance(result.get(k), str) and result[k])
        ordinal = len(self.record["calendar"])
        row = self.rows[ordinal % len(self.rows)]
        values = calendar_values(result)
        self.record["calendar"].append({"fixture_id": row["fixture_id"],
            "repetition": ordinal // len(self.rows) + 1, "values": values,
            "available": "now" in values and seed == row["bank_case_id"]})
        self.save()

    def finish(self, report=None, error=None):
        self.record.update(ended_at=self.wall_clock(), elapsed_seconds=max(0.0, self.clock() - self.start))
        self.record["status"] = "failed" if error else "recorded"
        if error:
            # Exception text may contain keys, customer data or URLs: keep only a fixed code.
            self.record["error"] = "evaluation_interrupted"
        if report is not None:
            self.record["report"] = deepcopy(report)
        calendar = self.record["calendar"]
        expected = len(self.rows) * self.record["conditions"]["reps"]
        self.record["calendar_complete"] = len(calendar) == expected and all(r["available"] for r in calendar)
        self.record["calendar_sha256"] = digest(calendar) if self.record["calendar_complete"] else None
        self.save()


class RecordedBackend:
    """Transparent adapter: unchanged results, no retries and no extra API calls."""
    METHODS = {"practice_session", "create_personas", "simulate", "iteration", "ledger", "call", "validate_deployment"}

    def __init__(self, backend, recorder):
        self.backend, self.recorder = backend, recorder
        recorder.secrets.extend(backend_secrets(backend))

    def __getattr__(self, name):
        method = getattr(self.backend, name)
        if name not in self.METHODS:
            return method
        def recorded(*args, **kwargs):
            start = self.recorder.clock()
            outcome = "failed"
            try:
                result = method(*args, **kwargs)
                if name == "practice_session":
                    self.recorder.session(args[0], result)
                outcome = "returned"
                return result
            finally:
                self.recorder.record["operations"].append({"operation": name, "outcome": outcome,
                    "elapsed_seconds": max(0.0, self.recorder.clock() - start)})
                self.recorder.save()
        return recorded


def record_evaluation(backend, agent_id, rows, *, directory, conditions, kind,
                      deployment=None, wall_clock=None, clock=None):
    from .hosted import evaluate, validate_rows
    validate_rows(rows)
    conditions = deepcopy(conditions)
    reps, concurrency = conditions.get("reps"), conditions.get("concurrency")
    if type(reps) is not int or reps < 1 or type(concurrency) is not int or not 1 <= concurrency <= 5:
        raise ValueError("invalid recorded evaluation configuration")
    recorder = EvidenceRecorder(directory, rows, conditions, kind=kind, wall_clock=wall_clock, clock=clock,
                                secrets=backend_secrets(backend))
    try:
        report = evaluate(RecordedBackend(backend, recorder), agent_id, rows,
                          reps=reps, concurrency=concurrency, deployment=deployment)
    except BaseException as exc:
        # Interruption can leave a remote simulation running. Preserve only
        # existing handoff identifiers; never query, replace or retry its lease.
        handoff = getattr(backend, "simulation_handoff", None)
        if not isinstance(handoff, dict):
            handoff = getattr(exc, "lease", None)
        if isinstance(handoff, dict):
            recorder.record["simulation_handoff"] = {key: handoff[key] for key in
                ("simulation_id", "iteration_id", "status") if isinstance(handoff.get(key), str)}
        recorder.finish(error=True)
        raise
    recorder.finish(report=report)
    return report, recorder.directory / "trial.json"

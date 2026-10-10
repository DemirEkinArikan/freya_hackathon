#!/usr/bin/env python3
"""Allowlisted source packaging; deployment readiness is a separate evidence gate."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.safety import assert_no_secrets, digest, read_json
from builder.config import read_local_environment

ROOT_FILES = {"README.md", "AGENTS.md", "pyproject.toml", ".python-version", ".env.example", ".gitignore"}
SOURCE_DIRS = {"builder", "benchmarks", "tests", "tools", "docs", "build-the-builder"}
SKIP_PARTS = {"__pycache__", ".git", ".venv", "generated", "holdout", "results", "traces", "out", "artifacts"}
SKIP_NAMES = {".env", "env.sh", ".DS_Store", ".freya-agents.json", ".personas.json", "kickoff_email.txt", "meeting.md", "meeting_summary.md"}
EXTENSIONS = {".py", ".json", ".jsonl", ".md", ".toml", ".txt"}


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _event_name(event):
    return event.get("event") or event.get("kind") or event.get("type")


def validate_trace(events, workflow):
    """Check this builder's structured trace, not the authenticity of live calls."""
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("Trace events must be objects")
    starts = [event for event in events if _event_name(event) in ("start", "build_start")]
    ends = [event for event in events if _event_name(event) in ("end", "build_end")]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("Trace requires explicit start and end events")
    if starts[0].get("mode") != "live" or ends[0].get("status") != "exported":
        raise ValueError("Offline, generated-only or failed traces cannot establish final deployment")
    if events[0] is not starts[0] or events[-1] is not ends[0]:
        raise ValueError("Trace start and end must bound all recorded events")
    if ends[0].get("export_sha256") != digest(workflow):
        raise ValueError("Selected trace does not match the submitted workflow export")
    times = []
    for event in events:
        try:
            timestamp = dt.datetime.fromisoformat(event["time"])
            if timestamp.utcoffset() is None:
                raise ValueError("timezone absent")
        except (KeyError, TypeError, ValueError):
            raise ValueError("Trace events require timezone-aware start/end and event times") from None
        times.append(timestamp)
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        raise ValueError("Trace event times must be ordered")
    if (times[-1] - times[0]).total_seconds() > 1800:
        raise ValueError("Selected builder trace exceeds the organizer's 30-minute budget")
    model_calls = [event for event in events if _event_name(event) == "model_call"]
    if not model_calls:
        raise ValueError("Live builder trace requires its model-call records")
    for call in model_calls:
        if not _nonempty(call.get("model")) or not _nonempty(call.get("purpose")) or any(character in call["purpose"] for character in ("\n", "\r")):
            raise ValueError("Each model call requires its model and one-line purpose")
    latest_deployment = None
    latest_tests = None
    for event in events[1:-1]:
        kind = _event_name(event)
        if kind == "deployment":
            if (not _nonempty(event.get("workflow_id")) or not _nonempty(event.get("agent_id"))
                    or not _nonempty(event.get("version_id")) or type(event.get("version_number")) is not int
                    or event["version_number"] < 1 or event.get("published") is not True):
                raise ValueError("Trace deployment requires published workflow, agent and version identifiers")
            latest_deployment = event
            latest_tests = None
        elif kind == "tests":
            if latest_deployment is None:
                raise ValueError("Trace tests require a preceding deployment")
            _validate_trace_tests(event.get("result"), latest_deployment)
            latest_tests = event["result"]
    if not latest_deployment or any(latest_deployment.get(key) != workflow[key] for key in ("workflow_id", "agent_id")):
        raise ValueError("Selected deployment identifiers do not match the export")
    if events.index(model_calls[-1]) > events.index(latest_deployment):
        raise ValueError("Recorded generation must precede the selected deployment")
    if any(_event_name(event) == "tests" for event in events) and latest_tests is None:
        raise ValueError("Selected deployment has no recorded tests after its final update")
    if latest_tests and (latest_tests.get("status") not in ("completed", "passed") or latest_tests["passed"] != latest_tests["total"]):
        raise ValueError("Selected deployment's final recorded tests did not pass")


def _validate_trace_tests(result, deployment):
    """Check recorded hosted references while allowing earlier failed repair runs."""
    if (not isinstance(result, dict) or type(result.get("total")) is not int or result["total"] < 1
            or type(result.get("passed")) is not int or not 0 <= result["passed"] <= result["total"]
            or type(result.get("reps")) is not int or result["reps"] < 1
            or not isinstance(result.get("rows"), list) or not isinstance(result.get("simulations"), list)):
        raise ValueError("Trace test result requires counts, repetitions and hosted evidence references")
    simulations, simulation_ids, iteration_ids = {}, set(), set()
    for simulation in result["simulations"]:
        if (not isinstance(simulation, dict) or type(simulation.get("repetition")) is not int
                or not 1 <= simulation["repetition"] <= result["reps"] or simulation["repetition"] in simulations
                or not _nonempty(simulation.get("simulation_id")) or not _nonempty(simulation.get("iteration_id"))):
            raise ValueError("Trace test simulation references are missing or invalid")
        simulations[simulation["repetition"]] = simulation
        if simulation["simulation_id"] in simulation_ids or simulation["iteration_id"] in iteration_ids:
            raise ValueError("Trace test repetitions reuse simulation/iteration evidence")
        simulation_ids.add(simulation["simulation_id"])
        iteration_ids.add(simulation["iteration_id"])
    seen, calls, fixture_passes = set(), set(), {}
    for row in result["rows"]:
        if (not isinstance(row, dict) or not _nonempty(row.get("fixture_id")) or type(row.get("repetition")) is not int
                or row["repetition"] not in simulations or type(row.get("pass")) is not bool):
            raise ValueError("Trace test row has no valid fixture/repetition evidence reference")
        reference = (row["fixture_id"], row["repetition"])
        if reference in seen:
            raise ValueError("Trace test rows repeat a fixture/repetition reference")
        seen.add(reference)
        fixture_passes.setdefault(row["fixture_id"], {})[row["repetition"]] = row["pass"]
        simulation = simulations[row["repetition"]]
        if any(key in row and row[key] != simulation[key] for key in ("simulation_id", "iteration_id")):
            raise ValueError("Trace test row differs from its simulation/iteration reference")
        if row["pass"]:
            if not _nonempty(row.get("call_id")) or simulation.get("status") != "completed":
                raise ValueError("Passing trace test row requires a completed simulation and call reference")
            if row["call_id"] in calls:
                raise ValueError("Passing trace test rows reuse call evidence")
            calls.add(row["call_id"])
            judge = row.get("judge")
            if not isinstance(judge, dict) or judge.get("callId") != row["call_id"] or judge.get("outcome") != "pass":
                raise ValueError("Passing trace test row requires matching judge call evidence")
        for source in (row.get("judge"), row.get("transcript")):
            if isinstance(source, dict) and source.get("agentVersionNumber") is not None:
                version = source["agentVersionNumber"]
                if type(version) is not int or version != deployment["version_number"]:
                    raise ValueError("Trace test version differs from its preceding deployment")
    if (len(fixture_passes) != result["total"] or len(seen) != result["total"] * result["reps"]
            or any(set(repetitions) != set(range(1, result["reps"] + 1)) for repetitions in fixture_passes.values())):
        raise ValueError("Trace test evidence is incomplete for its declared repetitions")
    if result["passed"] != sum(all(repetitions.values()) for repetitions in fixture_passes.values()):
        raise ValueError("Trace test summary differs from its recorded repetition outcomes")


def safe_path(path):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("Submission path escapes the standalone repository")
    cursor = path
    while cursor != ROOT and cursor.is_relative_to(ROOT):
        if cursor.is_symlink():
            raise ValueError("Submission symlinks are forbidden")
        cursor = cursor.parent
    if not path.is_file():
        raise ValueError("Submission evidence file does not exist")
    return resolved


def source_files():
    result = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(p in SKIP_PARTS for p in rel.parts) or path.name in SKIP_NAMES:
            continue
        included = (len(rel.parts) == 1 and path.name in ROOT_FILES) or (
            rel.parts[0] in SOURCE_DIRS and (path.suffix in EXTENSIONS or path.name in {"VERSION", "env.example"}))
        if included and (path.is_file() or path.is_symlink()):
            result.append(safe_path(path))
    return result


def check_content(path):
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    credentials = {**read_local_environment(ROOT / ".env"), **os.environ}
    known = tuple(v for k, v in credentials.items() if any(s in k for s in ("API_KEY", "TEAM_KEY", "TOKEN", "SECRET", "TOOL_AUTH")) and len(v) >= 12)
    # Test source intentionally contains synthetic token examples. Known live
    # values are always forbidden; production source/evidence also gets patterns.
    if path.relative_to(ROOT).parts[0] == "tests":
        if any(value in text for value in known):
            raise ValueError("Credential detected in test source")
    else:
        try:
            assert_no_secrets(text, forbidden_values=known)
        except ValueError as exc:
            raise ValueError(f"{path.relative_to(ROOT)}: credential-like material requires review") from exc
    return hashlib.sha256(raw).hexdigest()


def check_manifest(value):
    for key in ("team", "repo", "commit", "run_command", "workflows", "traces"):
        if not value.get(key):
            raise ValueError(f"Submission manifest missing {key}")
    if set(value["workflows"]) != {"hidden-1", "hidden-2"} or set(value["traces"]) != {"hidden-1", "hidden-2"}:
        raise ValueError("Require exactly two workflows and their selected run traces")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    if not head.startswith(value["commit"]):
        raise ValueError("Manifest commit does not match the source checkout")
    result, exported = [], {}
    for kind in ("workflows", "traces"):
        for name, entry in value[kind].items():
            path = safe_path(entry); check_content(path)
            if kind == "workflows":
                workflow = read_json(path)
                if not workflow.get("workflow_id") or not workflow.get("agent_id") or not workflow.get("workflow"):
                    raise ValueError("Require the actual platform-exported workflow envelope")
                exported[name] = workflow
            else:
                events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                validate_trace(events, exported[name])
            result.append((path, f"{kind}/{name}{''.join(path.suffixes)}"))
    return result


def verify_committed(paths):
    """Bind the packaged source/evidence bytes to the declared HEAD commit."""
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        result = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True)
        if result.returncode or result.stdout != path.read_bytes():
            raise ValueError(f"{rel}: final source/evidence must match the committed checkout")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--source-only", action="store_true")
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--output", type=Path, default=ROOT / "artifacts" / "source-rehearsal.zip")
    args = ap.parse_args(argv)
    if args.source_only and args.manifest:
        ap.error("--source-only and --manifest are mutually exclusive")
    if not args.check and not args.source_only and not args.manifest:
        ap.error("Packaging requires --manifest, or explicit --source-only for a rehearsal")
    files = source_files(); hashes = {str(p.relative_to(ROOT)): check_content(p) for p in files}
    evidence, manifest = [], None
    if args.manifest:
        manifest_path = safe_path(args.manifest); check_content(manifest_path)
        manifest = read_json(manifest_path); evidence = check_manifest(manifest)
        verify_committed(files + [path for path, _ in evidence])
    if args.check:
        print(json.dumps({"source_check": "pass", "files": len(files), "export_trace_consistency": "checked" if manifest else "pending", "hosted_readiness": "requires scored runtime evidence; source checks are not competition readiness"}))
        return 0
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Package output must remain inside the repository")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for path in files:
            out.write(path, path.relative_to(ROOT).as_posix())
        for path, arcname in evidence:
            out.write(path, arcname)
        packaged_manifest = None if manifest is None else json.loads(json.dumps(manifest))
        if packaged_manifest is not None:
            for kind in ("workflows", "traces"):
                packaged_manifest[kind] = {name: f"{kind}/{name}{''.join(safe_path(entry).suffixes)}"
                                           for name, entry in manifest[kind].items()}
        out.writestr("package-index.json", json.dumps({"source_files": hashes, "source_only": manifest is None, "submission": packaged_manifest}, indent=2))
    print(json.dumps({"package": str(output), "source_only": manifest is None, "files": len(files)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)

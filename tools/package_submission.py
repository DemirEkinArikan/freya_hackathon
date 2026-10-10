#!/usr/bin/env python3
"""Allowlisted source packaging; deployment readiness is a separate evidence gate."""
from __future__ import annotations

import argparse
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
                starts = [e for e in events if (e.get("event") or e.get("kind") or e.get("type")) in ("start", "build_start")]
                ends = [e for e in events if (e.get("event") or e.get("kind") or e.get("type")) in ("end", "build_end")]
                if len(starts) != 1 or len(ends) != 1:
                    raise ValueError("Trace requires explicit start and end events")
                if starts[0].get("mode") != "live" or ends[0].get("status") != "exported":
                    raise ValueError("Offline, generated-only or failed traces cannot establish final deployment")
                if ends[0].get("export_sha256") != digest(exported[name]):
                    raise ValueError("Selected trace does not match the submitted workflow export")
                deployments = [e for e in events if e.get("event") == "deployment"]
                if not deployments or any(deployments[-1].get(k) != exported[name][k] for k in ("workflow_id", "agent_id")):
                    raise ValueError("Selected deployment identifiers do not match the export")
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

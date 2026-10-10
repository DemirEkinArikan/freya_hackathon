#!/usr/bin/env python3
"""Reproduce local checks from a committed, allowlisted source archive.

No model, platform, simulation or holdout execution is performed. Every run is
retained, including failed runs. Live acceptance and candidate freeze are gates
outside this local source reproduction command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import signal
import stat
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.safety import digest
from tools import package_submission as package

MAX_FILES = 2048
MAX_BYTES = 64 * 1024 * 1024
PRIVATE_PARTS = {".codex", ".aws", ".ssh", ".config"}
PRIVATE_NAMES = {"auth.json", ".netrc", ".npmrc", "credentials", "credentials.json"}


def source_name(name: str) -> bool:
    path = PurePosixPath(name)
    if (not name or not path.parts or path.is_absolute() or path.as_posix() != name or "\\" in name
            or any(part in {".", ".."} for part in path.parts)
            or any(part in package.SKIP_PARTS for part in path.parts)
            or any(part in PRIVATE_PARTS for part in path.parts) or path.name in PRIVATE_NAMES
            or path.name in package.SKIP_NAMES
            or path.name.startswith(".env.") and path.name != ".env.example"):
        return False
    return (len(path.parts) == 1 and name in package.ROOT_FILES) or (
        path.parts[0] in package.SOURCE_DIRS and
        (path.suffix in package.EXTENSIONS or path.name in {"VERSION", "env.example"}))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate archive index key")
        result[key] = value
    return result


def extract_source(archive: Path, destination: Path) -> dict:
    """Validate the complete archive before writing any member."""
    if destination.exists():
        raise ValueError("Extraction destination must be new")
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        names = [entry.filename for entry in infos]
        if len(infos) > MAX_FILES or len(set(names)) != len(names):
            raise ValueError("Archive has duplicate members or too many files")
        folded = {name.casefold() for name in names}
        if len(folded) != len(names) or any(
                parent.as_posix().casefold() in folded
                for name in names for parent in PurePosixPath(name).parents if parent.parts):
            raise ValueError("Archive member paths collide")
        total = 0
        for entry in infos:
            mode = entry.external_attr >> 16
            if (entry.filename != "package-index.json" and not source_name(entry.filename)
                    or entry.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or entry.flag_bits & 1):
                raise ValueError("Archive contains a forbidden path or file type")
            total += entry.file_size
            if entry.file_size > MAX_BYTES or total > MAX_BYTES:
                raise ValueError("Archive exceeds extraction size limit")
        if "package-index.json" not in names:
            raise ValueError("Archive lacks its source index")
        index = json.loads(source.read("package-index.json"), object_pairs_hook=_object)
        if (not isinstance(index, dict) or index.get("source_only") is not True
                or index.get("submission") is not None or index.get("evidence_files") != {}
                or not isinstance(index.get("source_files"), dict) or not index["source_files"]):
            raise ValueError("Rehearsal requires a source-only archive")
        hashes = index["source_files"]
        if set(names) != set(hashes) | {"package-index.json"}:
            raise ValueError("Archive inventory differs from its source index")
        # Bound decompression above, and check every hash before extraction.
        for name, expected in hashes.items():
            if not source_name(name) or hashlib.sha256(source.read(name)).hexdigest() != expected:
                raise ValueError("Archive source hash mismatch")
        destination.mkdir(parents=True)
        for entry in infos:
            target = destination / entry.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read(entry))
    return hashes


def verify_inventory(root: Path, hashes: dict) -> None:
    actual = {}
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if (path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example"
                or path.name in PRIVATE_NAMES or any(part in PRIVATE_PARTS for part in path.relative_to(root).parts)):
            raise ValueError("Clean reproduction acquired private configuration")
        if source_name(name) and (path.is_file() or path.is_symlink()):
            if path.is_symlink():
                raise ValueError("Extracted source became a symlink")
            actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != hashes:
        raise ValueError("Extracted source inventory changed")


def clean_environment(home: Path) -> dict:
    # Deliberately do not inherit credentials, Python hooks or host configuration.
    return {"PATH": os.defpath, "HOME": str(home), "LANG": "en_US.UTF-8",
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def remaining(end: float | None) -> float | None:
    if end is None:
        return None
    seconds = end - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("Rehearsal exceeded its 1800-second deadline")
    return seconds


def verify_committed(root: Path, hashes: dict, head: str, end: float | None = None) -> None:
    tree = subprocess.run(["git", "ls-tree", "-r", "--name-only", "-z", head],
                          cwd=root, capture_output=True, check=True, timeout=remaining(end))
    committed = {name for raw in tree.stdout.split(b"\0") if raw
                 and source_name(name := raw.decode("utf-8"))}
    if set(hashes) != committed:
        raise ValueError("Rehearsal inventory differs from the recorded commit")
    for name, expected in hashes.items():
        result = subprocess.run(["git", "show", f"{head}:{name}"], cwd=root,
                                capture_output=True, check=True, timeout=remaining(end))
        if hashlib.sha256(result.stdout).hexdigest() != expected:
            raise ValueError("Rehearsal source differs from the recorded commit")


def run_check(argv: list[str], root: Path, home: Path, log: Path,
              end: float) -> dict:
    seconds = remaining(end)
    started = time.monotonic()
    with log.open("wb") as output:
        process = subprocess.Popen(argv, cwd=root, env=clean_environment(home),
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code, timed_out = process.wait(timeout=seconds), False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            code, timed_out = None, True
    return {"argv": ["python3.11", *argv[1:]], "returncode": code,
            "timed_out": timed_out, "elapsed_seconds": round(time.monotonic() - started, 3),
            "log": log.name, "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}


def rehearse(output: Path) -> dict:
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Rehearsal requires Python 3.11")
    if output.exists() or output.is_symlink() or not output.resolve().is_relative_to(ROOT / "artifacts"):
        raise ValueError("Use a new run directory under this repository's artifacts")
    # Check parent links, including links into an otherwise allowed location.
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Rehearsal output cannot use linked directories")
    output.mkdir(parents=True)
    started = time.monotonic()
    report = {"version": 1, "status": "failed", "mode": "local-source-reproduction",
              "python_version": platform.python_version(), "deadline_seconds": 1800,
              "model_calls": 0, "platform_calls": 0, "holdouts_consumed": False,
              "candidate_frozen": False, "checks": [],
              "limitations": "Local development checks do not prove model generation, live exports, three-repeat conversations or completed T16."}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True, timeout=remaining(started + 1800)).stdout.strip()
        report["commit"] = head
        hashes = {p.relative_to(ROOT).as_posix(): package.check_content(p) for p in package.source_files()}
        verify_committed(ROOT, hashes, head, started + 1800)
        report["source_files"] = hashes
        report["source_inventory_sha256"] = digest(hashes)
        config = {"mode": report["mode"], "python_version": report["python_version"],
                  "deadline_seconds": 1800, "environment": "empty HOME; fixed minimal process environment",
                  "model": None, "network_execution": False}
        report["resolved_config"] = config
        report["configuration_sha256"] = digest(config)
        archive = output / "source.zip"
        package.main(["--source-only", "--output", str(archive)])
        remaining(started + 1800)
        report["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        clean = output / "source"
        extracted = extract_source(archive, clean)
        remaining(started + 1800)
        if extracted != hashes:
            raise ValueError("Archived source differs from checked source")
        verify_inventory(clean, hashes)
        home = output / "empty-home"
        home.mkdir()
        commands = [
            ["tools/verify_kit.py"],
            ["-m", "unittest", "discover", "-s", "tests", "-v"],
            ["tools/package_submission.py", "--check", "--source-only"],
            ["-m", "builder.build", "--help"],
            ["-m", "benchmarks.offline", "--split", "train", "--check-helpers", "--check-equivalence"],
            ["-m", "benchmarks.offline", "--split", "validation", "--check-helpers", "--check-equivalence"],
            ["-m", "benchmarks.hosted", "--dry-run", "--split", "validation", "--brief", "B"],
            ["-m", "benchmarks.export_inputs", "--profile", "train-2", "--out", "artifacts/development-inputs/raw"],
            ["-m", "benchmarks.export_inputs", "--profile", "validation-2", "--out", "artifacts/development-inputs/changed-schema"],
            ["tools/package_submission.py", "--source-only", "--output", str(clean / "artifacts/rebuilt.zip")],
        ]
        for number, command in enumerate(commands, 1):
            check = run_check([sys.executable, *command], clean, home, output / f"check-{number:02d}.log", started + 1800)
            report["checks"].append(check)
            if check["returncode"] != 0 or time.monotonic() >= started + 1800:
                raise ValueError("Clean source check failed or exceeded the deadline; see retained log")
        verify_inventory(clean, hashes)
        report["development_inputs"] = {
            path.relative_to(clean).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((clean / "artifacts/development-inputs").rglob("*")) if path.is_file()}
        rebuilt = extract_source(clean / "artifacts/rebuilt.zip", output / "rebuilt-source")
        if rebuilt != hashes:
            raise ValueError("Clean source rebuild changed the archive inventory")
        if list(home.iterdir()):
            raise ValueError("Local checks changed the empty HOME")
        report["rebuilt_source_inventory_sha256"] = digest(rebuilt)
        remaining(started + 1800)
        report["status"] = "passed"
    except Exception as exc:
        # Error text can contain host stderr; persist a category, with bounded
        # generic details only for errors generated by this harness.
        report["error_type"] = type(exc).__name__
        if isinstance(exc, (ValueError, TimeoutError)):
            report["error"] = str(exc)
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="New retained directory under artifacts")
    args = parser.parse_args(argv)
    output = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    report = rehearse(output)
    print(json.dumps({key: value for key, value in report.items() if key not in {"source_files", "checks"}}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

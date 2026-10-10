"""Record the source and resolved configuration at invocation, never infer them later."""
from __future__ import annotations

import datetime as dt
import copy
import hashlib
import os
from pathlib import Path
import platform

from .safety import digest, assert_no_secrets

MAX_FILES = 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


def _source_files(root: Path) -> dict[str, str]:
    """Only runtime code/prompts; never inspect fixtures, output or sibling repos."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("builder source root must be a regular directory")
    files, total = {}, 0
    for directory_name in ("builder", "benchmarks"):
        directory = root / directory_name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("builder runtime source directory is absent or linked")
        if directory_name == "builder" and (directory / "prompts").is_symlink():
            raise ValueError("builder runtime source directories cannot be linked")
        def unreadable(_):
            raise ValueError("builder runtime source cannot be inspected")
        for base, directories, names in os.walk(directory, followlinks=False, onerror=unreadable):
            base = Path(base)
            kept = []
            for name in sorted(directories):
                # Evaluator fixtures/holdouts are never part of the runtime
                # source snapshot. Their separate input evidence is retained
                # exclusively by the evaluator.
                if directory_name != "builder" or base != directory or name != "prompts":
                    continue
                path = base / name
                if path.is_symlink():
                    raise ValueError("builder runtime source directories cannot be linked")
                kept.append(name)
            directories[:] = kept
            for name in sorted(names):
                path = base / name
                extension = ".txt" if directory_name == "builder" and base == directory / "prompts" else ".py"
                if name.startswith(".") or path.suffix != extension:
                    continue
                if path.is_symlink() or not path.is_file():
                    raise ValueError("builder runtime source must be regular files")
                size = path.stat().st_size
                if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                    raise ValueError("builder runtime source exceeds snapshot resource limits")
                with path.open("rb") as stream:
                    raw = stream.read(size + 1)
                if len(raw) != size:
                    raise ValueError("builder runtime source changed while being recorded")
                total += len(raw)
                files[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
    if not any(name.endswith(".py") and name.startswith("builder/") for name in files):
        raise ValueError("builder runtime source snapshot is empty")
    return files


def capture_source_provenance(root: Path, config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("resolved builder configuration must be an object")
    assert_no_secrets(config)
    configuration = {**config, "python_implementation": platform.python_implementation(),
                     "python_version": platform.python_version()}
    snapshot = {"version": 1, "files": _source_files(Path(root)), "resolved_config": configuration}
    fingerprint = digest(snapshot)
    return {**snapshot, "builder_source_sha256": fingerprint,
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scope": "builder/*.py, builder/prompts/*.txt, benchmarks/*.py; bank inputs have separate fingerprints",
            "limitations": "Invocation/end snapshots detect persistent source changes; they do not authenticate local evidence or lock files against transient changes."}


def verify_source_unchanged(root: Path, original: dict) -> None:
    if not isinstance(original, dict) or original.get("version") != 1:
        raise ValueError("original builder source evidence is absent")
    snapshot = {key: original.get(key) for key in ("version", "files", "resolved_config")}
    if original.get("builder_source_sha256") != digest(snapshot):
        raise ValueError("original builder source fingerprint is stale")
    if _source_files(Path(root)) != original["files"]:
        raise ValueError("builder runtime source changed during this invocation")


def bind_run_provenance(snapshot: dict, manifest: dict, events: list[dict]) -> dict:
    if manifest.get("builder_source_sha256") != snapshot.get("builder_source_sha256"):
        raise ValueError("build manifest differs from invocation source evidence")
    if snapshot.get("builder_source_sha256") != digest({key: snapshot.get(key) for key in ("version", "files", "resolved_config")}):
        raise ValueError("invocation source evidence has a stale fingerprint")
    if manifest.get("builder_source_record_sha256") != digest(snapshot):
        raise ValueError("build manifest differs from original source record")
    return {"version": 1, "builder_source_sha256": snapshot["builder_source_sha256"],
            "builder_source_record_sha256": digest(snapshot),
            "source_snapshot": copy.deepcopy(snapshot),
            "build_manifest_sha256": digest(manifest), "trace_sha256": digest(events)}

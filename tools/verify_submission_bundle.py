#!/usr/bin/env python3
"""Independently inspect a submission ZIP against an explicitly trusted commit.

No archive program is executed. ZIP integrity and source identity are checked
independently of the producer. Trusted controller validators inspect retained
final evidence in a private temporary directory that is removed on return.
Passing establishes local consistency, never authenticated hosted execution.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.safety import assert_no_secrets, digest
from builder.config import read_local_environment
from builder.guards import compile_guarded_workflow, required_execution_kinds
from builder.contract import compile_policy_contract
from builder.action_bindings import compile_action_bindings
from builder.policy import compile_execution_policy
from builder.execution_requirements import compile_execution_requirements, validate_plan_execution
from builder.validation import validate_workflow
from builder.stored_workflow import assert_equivalent_stored_workflow
from tools import package_submission as package

# These are deliberately independent of producer constants/selection functions.
ROOT_FILES = {"README.md", "AGENTS.md", "pyproject.toml", ".python-version", ".env.example", ".gitignore"}
SOURCE_DIRS = {"builder", "benchmarks", "tests", "tools", "docs", "build-the-builder"}
SKIP_PARTS = {"__pycache__", ".git", ".venv", "generated", "holdout", "results", "traces", "out", "artifacts"}
SKIP_NAMES = {".env", "env.sh", ".DS_Store", ".freya-agents.json", ".personas.json", "kickoff_email.txt", "meeting.md", "meeting_summary.md"}
EXTENSIONS = {".py", ".sh", ".json", ".jsonl", ".md", ".toml", ".txt"}
PRIVATE_PARTS = {".codex", ".aws", ".ssh", ".config"}
PRIVATE_NAMES = {"auth.json", ".netrc", ".npmrc", "credentials", "credentials.json"}
MAX_FILES = 2048
MAX_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 80
MAX_JSON_ITEMS = 200000
_PATCH_LOCK = threading.RLock()
_HASH = re.compile(r"[0-9a-f]{64}")
_BUILD_NAMES = {"hidden-1", "hidden-2"}
_VALIDATOR_SOURCE_HASHES = {
    path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
    for directory in (ROOT / "builder", ROOT / "benchmarks")
    for path in directory.rglob("*") if path.is_file() and not path.is_symlink()
    and ((path.parent == directory and path.suffix == ".py")
         or (directory.name == "builder" and path.parent == directory / "prompts" and path.suffix == ".txt"))}
_VALIDATOR_SOURCE_HASHES["tools/package_submission.py"] = hashlib.sha256((ROOT / "tools/package_submission.py").read_bytes()).hexdigest()


def _safe_name(name: str) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return False
    path = PurePosixPath(name)
    return (not path.is_absolute() and path.as_posix() == name
            and all(part not in {".", "..", ""} for part in name.split("/"))
            and all(not any(ord(c) < 32 or ord(c) == 127 for c in part) for part in path.parts)
            and not any(part.casefold() in PRIVATE_PARTS | {".git"} for part in path.parts)
            and path.name.casefold() not in {n.casefold() for n in PRIVATE_NAMES | SKIP_NAMES}
            and not (path.name.casefold().startswith(".env.") and path.name != ".env.example"))


def source_name(name: str) -> bool:
    if not _safe_name(name):
        return False
    path = PurePosixPath(name)
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    return ((len(path.parts) == 1 and name in ROOT_FILES)
            or (path.parts[0] in SOURCE_DIRS
                and (path.suffix in EXTENSIONS or path.name in {"VERSION", "env.example"})))


def _strict_json(raw: bytes | str, *, scan: bool = True):
    def pairs(items):
        value = {}
        for key, child in items:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = child
        return value

    def constant(_):
        raise ValueError("nonfinite_json")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > MAX_JSON_ITEMS or depth > MAX_JSON_DEPTH:
            raise ValueError("json_resource_limit")
        if isinstance(item, (dict, list)):
            stack.extend((child, depth + 1) for child in
                         (item.values() if isinstance(item, dict) else item))
        elif type(item) is float and not math.isfinite(item):
            # 1e999 is not handled by json.parse_constant.
            raise ValueError("nonfinite_json")
    # Every structurally parsed evidence record is scanned, even when its
    # filename is .txt/.md. Renaming an export/trace cannot bypass JSON keys.
    if scan:
        assert_no_secrets(value)
    return value


def _zip_payload(archive: Path) -> tuple[dict[str, bytes], dict, str]:
    if not archive.is_file() or archive.is_symlink() or archive.stat().st_size > MAX_BYTES:
        raise ValueError("archive_structure")
    with archive.open("rb") as stream:
        archive_bytes = stream.read(MAX_BYTES + 1)
    if len(archive_bytes) > MAX_BYTES:
        raise ValueError("archive_structure")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as source:
        infos = source.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise ValueError("archive_structure")
        names = [entry.filename for entry in infos]
        # Include Unicode normalization and parent-directory spelling collisions.
        normalized = [unicodedata.normalize("NFC", n).casefold() for n in names]
        if len(set(names)) != len(names) or len(set(normalized)) != len(names):
            raise ValueError("archive_structure")
        folded = set(normalized)
        spellings = {}
        total = 0
        for entry in infos:
            name, mode = entry.filename, entry.external_attr >> 16
            if (not _safe_name(name) or entry.is_dir() or entry.flag_bits & 1
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or entry.file_size > MAX_FILE_BYTES or entry.file_size < 0):
                raise ValueError("archive_structure")
            total += entry.file_size
            if total > MAX_BYTES:
                raise ValueError("archive_structure")
            parts = PurePosixPath(name).parts
            for end in range(1, len(parts)):
                parent = "/".join(parts[:end])
                key = unicodedata.normalize("NFC", parent).casefold()
                if key in folded or key in spellings and spellings[key] != parent:
                    raise ValueError("archive_structure")
                spellings[key] = parent
        if "package-index.json" not in names:
            raise ValueError("archive_index")
        payload = {}
        for entry in infos:
            with source.open(entry) as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) != entry.file_size or len(raw) > MAX_FILE_BYTES:
                raise ValueError("archive_structure")
            payload[entry.filename] = raw
    try:
        index = _strict_json(payload.pop("package-index.json"))
        if not isinstance(index, dict):
            raise ValueError()
        if set(index) - {"source_files", "evidence_files", "source_only", "submission", "source_binding"}:
            raise ValueError()
        sources, evidence = index.get("source_files"), index.get("evidence_files")
        if not isinstance(sources, dict) or not sources or not isinstance(evidence, dict):
            raise ValueError()
        if set(sources) & set(evidence) or set(payload) != set(sources) | set(evidence):
            raise ValueError()
        if any(not source_name(name) for name in sources):
            raise ValueError()
        for name, expected in {**sources, **evidence}.items():
            if (not _safe_name(name) or not isinstance(expected, str) or not _HASH.fullmatch(expected)
                    or hashlib.sha256(payload[name]).hexdigest() != expected):
                raise ValueError()
        if type(index.get("source_only")) is not bool:
            raise ValueError()
        if index["source_only"] and (evidence or index.get("submission") is not None
                                     or index.get("source_binding") is not None):
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError("archive_index") from None
    return payload, index, hashlib.sha256(archive_bytes).hexdigest()


def _check_content(payload: dict[str, bytes], index: dict, source_root: Path) -> None:
    credentials = [*os.environ.items(), *read_local_environment(source_root / ".env").items()]
    known = tuple(value for key, value in credentials if value and any(
        part in key.upper() for part in ("API_KEY", "TEAM_KEY", "TOKEN", "SECRET", "TOOL_AUTH", "PASSWORD")))
    assert_no_secrets(index, forbidden_values=known)
    for name, raw in payload.items():
        text = raw.decode("utf-8")
        if name in index["source_files"] and name.startswith("tests/") and name.endswith(".py"):
            if any(value in text for value in known):
                raise ValueError("archive_content")
        else:
            assert_no_secrets(text, forbidden_values=known)
        suffix = PurePosixPath(name).suffix
        if suffix == ".json":
            value = _strict_json(raw)
            assert_no_secrets(value, forbidden_values=known)
        elif suffix == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    assert_no_secrets(_strict_json(line), forbidden_values=known)
        elif name in index["evidence_files"] and text.lstrip().startswith(("{", "[")):
            # Evidence roles survive filename changes. Bytes that are not strict
            # JSON are ordinary brief text (a bracketed code list, say), as the
            # packager scans them. Parse without the secret scan so a detection
            # is never mistaken for text; every parsed value is scanned below.
            try:
                values = [_strict_json(raw, scan=False)]
            except (ValueError, RecursionError):
                values = []
                lines = [line for line in text.splitlines() if line.strip()]
                if all(line.lstrip().startswith(("{", "[")) for line in lines):
                    for line in lines:
                        try:
                            values.append(_strict_json(line, scan=False))
                        except (ValueError, RecursionError):
                            pass
            for value in values:
                assert_no_secrets(value, forbidden_values=known)


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "--no-pager", *args], cwd=root,
                            capture_output=True, timeout=30, check=False)
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ValueError("trusted_source")
    return result.stdout


def _verify_source(root: Path, commit: str, payload: dict[str, bytes], hashes: dict[str, str]) -> None:
    if (not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or root.is_symlink() or not root.is_dir()):
        raise ValueError("trusted_source")
    if _git(root, "rev-parse", "--verify", commit + "^{commit}").decode().strip() != commit:
        raise ValueError("trusted_source")
    records = _git(root, "ls-tree", "-r", "-z", commit).split(b"\0")
    committed = {}
    for record in records:
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        name = raw_name.decode("utf-8")
        if source_name(name):
            if mode not in {"100644", "100755"} or kind != "blob":
                raise ValueError("trusted_source")
            committed[name] = object_id
    if set(committed) != set(hashes):
        raise ValueError("trusted_source")
    for name, object_id in committed.items():
        if _git(root, "cat-file", "blob", object_id) != payload[name]:
            raise ValueError("trusted_source")


def _runtime_name(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return ((len(parts) == 2 and parts[0] in {"builder", "benchmarks"} and name.endswith(".py"))
            or (len(parts) == 3 and parts[:2] == ("builder", "prompts") and name.endswith(".txt")))


def _verify_evidence_commit(root: Path, commit: str, payload: dict[str, bytes], evidence: dict) -> None:
    """Bind remapped bytes to a committed blob, without inventing original paths."""
    blobs = set()
    for record in _git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if record:
            metadata, _ = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split()
            if kind == "blob" and mode in {"100644", "100755"}:
                blobs.add(object_id)
    checked = set()
    for name in evidence:
        raw = payload[name]
        # Full 40-hex commit IDs identify a SHA-1 Git repository. Verify actual
        # raw blob bytes too, rather than treating an object ID as the evidence.
        object_id = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        if object_id not in blobs:
            raise ValueError("evidence_commit")
        if object_id not in checked and _git(root, "cat-file", "blob", object_id) != raw:
            raise ValueError("evidence_commit")
        checked.add(object_id)


def _verify_repository(root: Path, repo: str) -> None:
    origin = _git(root, "config", "--get", "remote.origin.url").decode("utf-8").strip()
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", origin)
    if (not match or repo.rstrip("/").removesuffix(".git").casefold()
            != ("https://github.com/" + match[1]).casefold()):
        raise ValueError("final_manifest")


def _verify_validator_source(hashes: dict[str, str]) -> None:
    """Never label another checkout's compiler as this commit's compiler."""
    expected = {name: value for name, value in hashes.items()
                if _runtime_name(name) or name == "tools/package_submission.py"}
    if not expected or expected != _VALIDATOR_SOURCE_HASHES:
        raise ValueError("validator_source")
    for name, value in expected.items():
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != value:
            raise ValueError("validator_source")


def _final_evidence(payload: dict[str, bytes], index: dict, commit: str, source_root: Path) -> dict | None:
    manifest = index.get("submission")
    if not isinstance(manifest, dict) or manifest.get("commit") != commit:
        raise ValueError("final_manifest")
    for key in ("team", "repo", "run_command"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError("final_manifest")
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", manifest["repo"]):
        raise ValueError("final_manifest")
    _verify_repository(source_root, manifest["repo"])
    for key in ("workflows", "traces", "runs", "inputs"):
        if not isinstance(manifest.get(key), dict) or set(manifest[key]) != _BUILD_NAMES:
            raise ValueError("final_manifest")
    evidence = index["evidence_files"]
    for key in ("workflows", "traces", "runs"):
        paths = list(manifest[key].values())
        if (any(not isinstance(path, str) or path not in evidence for path in paths)
                or len(set(paths)) != 2):
            raise ValueError("final_manifest")
    for name, path in manifest["runs"].items():
        if path != f"runs/{name}/manifest.json":
            raise ValueError("final_manifest")
    # Optional declared-order record of every builder attempt per brief (T18 multi-attempt).
    selections, summary = manifest.get("selections"), None
    if selections is not None:
        if (not isinstance(selections, dict) or set(selections) != _BUILD_NAMES
                or any(selections[name] != f"selections/{name}.json" or selections[name] not in evidence
                       for name in _BUILD_NAMES)):
            raise ValueError("final_manifest")
        summary = {}
    bundles = []
    for inputs in manifest["inputs"].values():
        if (not isinstance(inputs, dict) or set(inputs) != {"briefs", "tool_api"}
                or not isinstance(inputs["briefs"], list) or not inputs["briefs"]
                or any(not isinstance(path, str) or path not in payload for path in inputs["briefs"])
                or not isinstance(inputs["tool_api"], str) or inputs["tool_api"] not in payload):
            raise ValueError("final_manifest")
        bundles.append(digest({"briefs": [payload[path].decode("utf-8") for path in inputs["briefs"]],
                               "tool_api": _strict_json(payload[inputs["tool_api"]])}))
    if len(set(bundles)) != 2:
        raise ValueError("final_manifest")
    binding = index.get("source_binding")
    if (not isinstance(binding, dict) or binding.get("submission_commit") != commit
            or binding.get("invocation_commit") is not None
            or not isinstance(binding.get("builder_source_sha256"), dict)
            or set(binding["builder_source_sha256"]) != _BUILD_NAMES):
        raise ValueError("final_manifest")
    _verify_validator_source(index["source_files"])
    _verify_evidence_commit(source_root, commit, payload, evidence)
    # Reconstruction is data only. Imports above always come from this verifier's
    # trusted checkout; no subprocess, eval, exec or imported archive path occurs.
    with tempfile.TemporaryDirectory(prefix="submission-verify-") as directory, _PATCH_LOCK:
        root = Path(directory).resolve()
        for name, raw in payload.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        original_root = package.ROOT
        package.ROOT = root
        try:
            retained, source_bindings = set(), set()
            for name in sorted(_BUILD_NAMES):
                export_path, export = package.read_evidence(manifest["workflows"][name])
                trace_path = package.safe_path(manifest["traces"][name])
                events = [_strict_json(line) for line in trace_path.read_text().splitlines() if line.strip()]
                package.validate_trace(events, export)
                run_path, run = package.read_evidence(manifest["runs"][name])
                files = package.validate_run(run_path, run, export, events, manifest["inputs"][name])
                source_bindings.add(run["builder_source_sha256"])
                if binding["builder_source_sha256"][name] != run["builder_source_sha256"]:
                    raise ValueError("final_evidence")
                retained.update(path.relative_to(root).as_posix() for path in [export_path, trace_path, run_path, *files])
                if selections is not None:
                    record = package.validate_selection(_strict_json(payload[selections[name]]), name, {
                        key: payload[manifest[kind][name]] for key, kind in
                        (("manifest", "runs"), ("trace", "traces"), ("export", "workflows"))})
                    summary[name] = {"selected": record["selected"], "of": record["attempt_count"]}
                    retained.add(selections[name])
                check_recompilation(run_path.parent, run, export,
                                    [(PurePosixPath(path).name, payload[path].decode("utf-8"))
                                     for path in manifest["inputs"][name]["briefs"]],
                                    _strict_json(payload[manifest["inputs"][name]["tool_api"]]))
            if len(source_bindings) != 1 or retained - set(index["source_files"]) != set(evidence):
                raise ValueError("final_evidence")
        finally:
            package.ROOT = original_root
    return summary


def check_recompilation(run_dir: Path, run: dict, export: dict, briefs: list, api: dict) -> None:
    """Recompile a run's selected attempt exactly from its frozen policy and ordered (name, text) briefs."""
    if run.get("selected_attempt_directory") not in {f"attempts/plan-{number:03d}" for number in (1, 2, 3)}:
        raise ValueError("guard_compilation")
    selected = run_dir / run["selected_attempt_directory"]
    authored = _strict_json((selected / "workflow.json").read_bytes())
    context = _strict_json((selected / "guard-context.json").read_bytes())
    policy = _strict_json((run_dir / "policy.json").read_bytes())
    sources = [{"id": f"brief-{number}", "file": file, "sha256": digest(text), "text": text}
               for number, (file, text) in enumerate(briefs, 1)]
    # A relaxed (last-resort) build recorded its declaration mode; recompile in that mode.
    mode = run.get("declaration_mode", "strict")
    contract = compile_policy_contract(policy, sources, api, declarations=mode)
    requirements = compile_execution_requirements(policy, sources, api, declarations=mode)
    if validate_plan_execution(context["plan"], requirements, policy, sources, api, declarations=mode):
        raise ValueError("guard_compilation")
    bindings = compile_action_bindings(policy, api, declarations=mode)
    execution = compile_execution_policy(policy, bindings, required_kinds=required_execution_kinds(context["plan"]))
    if (digest(contract) != digest(context["contract"])
            or digest(bindings) != digest(context["bindings"])
            or digest(execution) != digest(context["execution_policy"])):
        raise ValueError("guard_compilation")
    compiled = compile_guarded_workflow(context["plan"], context["contract"], api,
                                         context["execution_policy"], context["bindings"])
    if (digest(compiled["workflow"]) != digest(authored)
            or digest(compiled["guard_manifest"]) != digest(_strict_json((selected / "guard-manifest.json").read_bytes()))
            or validate_workflow(authored, api, policy, guard_context=context)):
        raise ValueError("guard_compilation")
    stored = dict(export["workflow"])
    stored.pop("agent_config", None)  # Fixed model is checked by validate_run.
    assert_equivalent_stored_workflow(stored, authored)


def verify_bundle(archive: Path, source_root: Path, expected_commit: str) -> dict:
    """Return a sanitized local-consistency report, or a sanitized ValueError.

    Only an explicit caller-supplied trusted commit establishes source identity.
    Working-tree state and self-reported archive hashes cannot establish it.
    No caller files are written; only private temporary evidence is reconstructed.
    """
    phase = "archive_structure"
    try:
        archive, source_root = Path(archive), Path(source_root)
        if source_root.is_symlink() or not source_root.is_dir():
            raise ValueError("trusted_source")
        payload, index, archive_hash = _zip_payload(archive)
        phase = "archive_content"
        _check_content(payload, index, source_root)
        phase = "trusted_source"
        _verify_source(source_root, expected_commit, payload, index["source_files"])
        selection = None
        if not index["source_only"]:
            phase = "final_evidence"
            selection = _final_evidence(payload, index, expected_commit, source_root)
        return {"status": "preparation_verified" if index["source_only"] else "consistency_verified",
                "attempt_selection": selection,
                "mode": "source-preparation" if index["source_only"] else "final-consistency",
                "source_only": index["source_only"], "commit": expected_commit,
                "source_file_count": len(index["source_files"]), "evidence_file_count": len(index["evidence_files"]),
                "archive_sha256": archive_hash,
                "source_commit_verified": True, "evidence_commit_verified": not index["source_only"],
                "hosted_authenticity_verified": False, "submission_receipt_verified": False,
                "hidden_builds_verified": False,
                "limitations": ["Source-only packages establish preparation, not submission readiness.",
                    "Retained final records establish internal consistency; hosted authenticity, actual hidden builds and organizer receipt are not authenticated.",
                    "Fully consistent forged manual provenance cannot be detected by local records alone.",
                    "Final evidence bytes are checked against committed blobs; remapping omits original path correspondence."]}
    except Exception as exc:
        # Archive names, source values, host stderr and validator details are
        # attacker controlled. Return only fixed categories, never exception text.
        categories = {"archive_structure", "archive_index", "archive_content", "trusted_source",
                      "validator_source", "final_manifest", "final_evidence", "guard_compilation", "evidence_commit"}
        category = str(exc) if isinstance(exc, ValueError) and str(exc) in categories else phase
        raise ValueError(category) from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    try:
        args = parser.parse_args(argv)
        report = verify_bundle(args.archive, args.source_root, args.commit)
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    print(json.dumps(report, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Retain independent S05 local fault evidence; never call a model or network."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from builder.guards import compile_guarded_workflow, validate_guarded_workflow
from builder.safety import assert_no_secrets, canonical_json, digest

MODULES = (
    "test_s05_receipt_matrix",
    "test_s05_control_matrix",
    "test_s05_dispatch_caps",
)


class EvidenceResult(unittest.TestResult):
    """Known failing invariants remain violations, including expected failures."""

    def __init__(self):
        super().__init__()
        self.records = []
        self.contexts = {}
        self.packets = {}

    def capture(self, test, status, error=None):
        references = []
        for context in getattr(test, "compiled_contexts", []):
            identity = digest(context)
            self.contexts[identity] = deepcopy(context)
            references.append(identity)
        packet_references = []
        for packet in getattr(test, "frozen_packets", []):
            identity = digest(packet)
            self.packets[identity] = deepcopy(packet)
            packet_references.append(identity)
        record = {
            "test": test.id(), "status": status,
            "context_sha256": sorted(set(references)),
            "frozen_packet_sha256": sorted(set(packet_references)),
            "cases": deepcopy(getattr(test, "evidence", [])),
        }
        for case in record["cases"]:
            expected, observed = case.get("expected"), case.get("observed")
            if isinstance(expected, dict) and isinstance(observed, dict):
                case["mismatched_fields"] = [key for key, value in expected.items()
                    if key not in observed or observed[key] != value]
                if case["mismatched_fields"] and status == "pass":
                    record["status"] = "violation"
        if status == "pass" and (not references or not record["cases"]):
            record["status"] = "incomplete"
            record["reason"] = "Passing assertions have no retained case or compiled context evidence"
        if error is not None:
            record["original_failure"] = "".join(traceback.format_exception(*error))
        self.records.append(record)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.capture(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.capture(test, "violation", err)

    def addError(self, test, err):
        super().addError(test, err)
        self.capture(test, "incomplete", err)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.capture(test, "known_violation", err)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.capture(test, "unexpected_success_needs_review")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.capture(test, "incomplete")
        self.records[-1]["reason"] = reason

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.capture(test, "violation" if issubclass(err[0], AssertionError) else "incomplete", err)
            self.records[-1]["subtest"] = str(subtest)


def write_artifact(directory, relative, value):
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = canonical_json(value) + "\n"
    assert_no_secrets(rendered)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(rendered)
    return {"path": relative, "sha256": hashlib.sha256(rendered.encode()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = args.output_dir or ROOT / "artifacts" / "s05-fault-matrix" / run_id
    if not directory.is_absolute():
        directory = ROOT / directory
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        parser.error("evidence directory may not contain symlinks")
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / "artifacts"):
        parser.error("evidence must stay under this checkout's ignored artifacts directory")
    directory.mkdir(parents=True, exist_ok=False)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(MODULES)
    result = EvidenceResult()
    suite.run(result)
    artifacts = []
    for identity, packet in sorted(result.packets.items()):
        artifacts.append(write_artifact(directory, "packets/" + identity + ".json", packet))
    for identity, context in sorted(result.contexts.items()):
        bundle = compile_guarded_workflow(**context)
        errors = validate_guarded_workflow(bundle["workflow"], **context)
        if errors:
            raise ValueError("captured context failed exact compiled validation: " + "; ".join(errors))
        artifacts.append(write_artifact(directory, "contexts/" + identity + ".json", context))
        artifacts.append(write_artifact(directory, "compiled/" + identity + ".json", bundle))
    artifacts.append(write_artifact(directory, "original-results.json", result.records))
    source_paths = subprocess.run(
        ["git", "ls-files", "builder", "tests", "tools"], cwd=ROOT,
        text=True, capture_output=True, check=True,
    ).stdout.splitlines()
    source_paths += ["tests/" + module + ".py" for module in MODULES]
    source_paths.append("tools/evaluate_s05_matrix.py")
    fingerprints = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in sorted(set(source_paths)) if (ROOT / path).is_file()
    }
    artifacts.append(write_artifact(directory, "source-sha256.json", fingerprints))
    bad = [record for record in result.records if record["status"] != "pass"]
    manifest = {
        "version": 1, "run_id": run_id,
        "source_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
            text=True, capture_output=True, check=True).stdout.strip(),
        "source_fingerprints_sha256": digest(fingerprints),
        "status": "violations_or_incomplete" if bad else "pass",
        "tests_run": result.testsRun, "case_observations": len({
            digest({"test": row["test"], "case": case}) for row in result.records for case in row["cases"]}),
        "nonpassing_records": len(bad), "compiled_contexts": len(result.contexts),
        "artifacts": artifacts,
        "network_executed": False, "model_executed": False,
        "hosted_behavior_verified": False, "conversation_compliance_proven": False,
        "claim": "local synthetic frozen-policy compiled-code fault evidence only",
    }
    write_artifact(directory, "manifest.json", manifest)
    print(json.dumps({"directory": str(directory), **{key: manifest[key] for key in (
        "status", "tests_run", "case_observations", "nonpassing_records", "compiled_contexts")}}, sort_keys=True))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

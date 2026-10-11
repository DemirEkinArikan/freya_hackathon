#!/usr/bin/env python3
"""Read-only check that a final ZIP's hidden labels are the official release.

A self-consistent package can label any two distinct runs hidden-1/hidden-2,
including practice runs. Run this after tools/verify_submission_bundle.py.
Pins are the sha256 of the official raw files as released, in brief order:
{"hidden-1": {"briefs": ["<sha256>", ...], "tool_api": "<sha256>"}, "hidden-2": {...}}
"""
import argparse
import datetime as dt
import hashlib
import json
import zipfile
from pathlib import Path

RELEASE = dt.datetime(2026, 10, 11, 17, tzinfo=dt.timezone.utc)
DEADLINE = dt.datetime(2026, 10, 11, 20, tzinfo=dt.timezone.utc)
LABELS = {"hidden-1", "hidden-2"}


def verify_hidden_release(archive, pins):
    with zipfile.ZipFile(archive) as bundle:
        submission = json.loads(bundle.read("package-index.json"))["submission"]
        if not isinstance(pins, dict) or set(pins) != LABELS or set(submission["inputs"]) != LABELS:
            raise ValueError("Official pins and package must cover exactly hidden-1 and hidden-2")
        for label in sorted(LABELS):
            inputs = submission["inputs"][label]
            actual = {"briefs": [hashlib.sha256(bundle.read(name)).hexdigest() for name in inputs["briefs"]],
                      "tool_api": hashlib.sha256(bundle.read(inputs["tool_api"])).hexdigest()}
            if actual != pins[label]:
                raise ValueError(f"{label}: inputs differ from the official release pins")
            lines = bundle.read(submission["traces"][label]).decode("utf-8").splitlines()
            events = [json.loads(line) for line in lines if line.strip()]
            start, end = (dt.datetime.fromisoformat(events[i]["time"]) for i in (0, -1))
            if not RELEASE <= start <= end <= DEADLINE:
                raise ValueError(f"{label}: selected build ran outside the official release window")
    return {"status": "official_release_bound", "labels": sorted(LABELS)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--official-pins", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_hidden_release(args.archive, json.loads(args.official_pins.read_text()))
    except (OSError, ValueError, KeyError, TypeError, IndexError, zipfile.BadZipFile) as exc:
        # Archive member names are untrusted; only our own messages are echoed.
        error = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(json.dumps({"status": "failed", "error": error}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

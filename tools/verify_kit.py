#!/usr/bin/env python3
"""Pin or verify the organizer kit without credentials or generated state."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "build-the-builder"
MANIFEST = ROOT / "docs" / "kit-manifest.json"
EXCLUDED = {"__pycache__", "results", "traces", "out", ".venv"}


def snapshot():
    entries = {}
    for path in sorted(KIT.rglob("*")):
        rel = path.relative_to(KIT)
        if any(part in EXCLUDED for part in rel.parts) or path.name in {"env.sh", ".env", ".personas.json", ".freya-agents.json", ".DS_Store"}:
            continue
        if path.is_symlink():
            raise ValueError("Kit symlinks are not permitted")
        if path.is_file():
            entries[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"version": int((KIT / "VERSION").read_text().strip()), "files": entries}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", action="store_true", help="Explicitly record a newly reviewed organizer kit")
    args = ap.parse_args(argv); actual = snapshot()
    if args.record:
        MANIFEST.write_text(json.dumps(actual, indent=2) + "\n")
    elif actual != json.loads(MANIFEST.read_text()):
        raise SystemExit("Kit differs from the pinned manifest; review the organizer update before repinning")
    print(f"Kit v{actual['version']} verified: {len(actual['files'])} files")


if __name__ == "__main__":
    main()

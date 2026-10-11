#!/usr/bin/env python3
"""Lint Freya workflow JSON against verified platform behaviour; report only, never a rejection.

Accepts a workflow, a platform read-back or export ({"workflow": ...}) or a candidate
{"policy", "workflow"}. Prints one JSON report per file; exit 0 unless a file cannot be read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.platform_lint import lint_workflow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path, nargs="+")
    parser.add_argument("--policy", type=Path, help="policy.json of the run; a candidate's own policy is used otherwise")
    parser.add_argument("--summary", action="store_true", help="print only the counts")
    args = parser.parse_args(argv)
    policy = json.loads(args.policy.read_text(encoding="utf-8")) if args.policy else None
    for path in args.workflow:
        value = json.loads(path.read_text(encoding="utf-8"))
        report = lint_workflow(value, policy if policy is not None else value.get("policy") if isinstance(value, dict) else None)
        print(json.dumps({"file": str(path), **({"summary": report["summary"]} if args.summary else report)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

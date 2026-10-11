#!/usr/bin/env python3
"""Live smoke check of the Claude generator: one tiny schema-bound call with the real key.

Not part of the unit suite. Needs ANTHROPIC_API_KEY in the environment; prints only
data-free results and fails if the key reaches the trace.
usage: python3.11 tools/claude_smoke.py <scratch-directory> [effort]
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builder.runtime import CLAUDE_EFFORT, ClaudeGenerator, Deadline, Trace  # noqa: E402


def main(directory: str, effort: str = CLAUDE_EFFORT) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print(json.dumps({"status": "failed", "error": "ANTHROPIC_API_KEY is not set"}))
        return 2
    run = Path(tempfile.mkdtemp(prefix="claude-smoke-", dir=directory))
    trace = Trace(run / "trace.jsonl")  # No secrets registered up front: the generator must add its key.
    generator = ClaudeGenerator(run, trace, Deadline(600), reasoning_effort=effort)
    result = generator.generate_policy_patch(
        'Return patch_json as the serialized JSON object {"operations": []} and nothing else.',
        max_seconds=240, reserve_seconds=300)
    text = trace.path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in text.splitlines()]
    outcome = next(event for event in events if event["event"] == "generation_result")
    summary = {"status": "passed" if result == {"patch": {"operations": []}} and key not in text else "failed",
               "parsed": result, "key_in_trace": key in text, "trace": str(trace.path),
               "isolation": next(e["status"] for e in events if e["event"] == "generation_isolation_audit"),
               **{name: outcome.get(name) for name in ("model", "served_model", "reasoning_effort", "stop_reason", "usage",
                                                       "elapsed_seconds", "first_event_seconds", "max_event_gap_seconds",
                                                       "stream_events", "thinking_characters", "attempts")},
               "refusal_fallback": generator.refusal_fallback,
               "events": sorted({event["event"] for event in events})}
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))

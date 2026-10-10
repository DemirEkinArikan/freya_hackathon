"""Assemble recorded T11 trials for T15. Never run a model or a hosted trial."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re

from builder.safety import digest, sanitize
from .compare import ComparisonError, _bundle, _period, load_bundle
from .paths import output_path


class EvidenceError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise EvidenceError(code)


def trial_record(value, split):
    require(isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1,
            "trial_record_invalid")
    require(value.get("kind") == "live" and value.get("status") == "recorded", "live_trial_record_required")
    require(isinstance(value.get("conditions"), dict), "trial_conditions_absent")
    conditions = value["conditions"]
    selection, calendar = value.get("selection"), value.get("calendar")
    require(isinstance(selection, list) and bool(selection) and all(isinstance(r, dict) for r in selection),
            "trial_selection_absent")
    require(all(r.get("split") == split and r.get("brief") in {"A", "B"}
                and isinstance(r.get("fixture_id"), str) and r["fixture_id"] for r in selection), "trial_split_mismatch")
    identifiers = {r["fixture_id"] for r in selection}
    require(len(identifiers) == len(selection), "trial_selection_duplicated")
    reps = conditions.get("reps")
    require(type(reps) is int and 3 <= reps <= 100, "trial_repetitions_invalid")
    pairs = {(r["fixture_id"], n) for r in selection for n in range(1, reps + 1)}
    require(isinstance(calendar, list) and len(calendar) == len(pairs), "calendar_inventory_incomplete")
    seen = set()
    from .evidence import calendar_values
    for row in calendar:
        require(isinstance(row, dict) and isinstance(row.get("fixture_id"), str)
                and type(row.get("repetition")) is int, "calendar_row_invalid")
        pair = row["fixture_id"], row["repetition"]
        require(pair in pairs and pair not in seen and row.get("available") is True, "calendar_inventory_invalid")
        seen.add(pair)
        values = row.get("values")
        require(isinstance(values, dict) and "now" in values and calendar_values(values) == values, "calendar_values_invalid")
    require(value.get("calendar_complete") is True and value.get("calendar_sha256") == digest(calendar), "calendar_hash_mismatch")
    report = value.get("report")
    require(isinstance(report, dict) and report.get("reps") == reps
            and isinstance(report.get("final"), list)
            and {r.get("fixture_id") for r in report["final"] if isinstance(r, dict)} == identifiers,
            "trial_report_selection_mismatch")
    _, _, elapsed = _period(value)
    recorded_elapsed = value.get("elapsed_seconds")
    require(type(recorded_elapsed) in (int, float) and math.isfinite(recorded_elapsed) and recorded_elapsed >= 0
            and type(conditions.get("budget_seconds")) is int
            and max(elapsed, recorded_elapsed) <= conditions["budget_seconds"], "trial_recorded_budget_exceeded")
    return {"report": deepcopy(report), "case_manifest_sha256": value.get("case_manifest_sha256"),
            "started_at": value["started_at"], "ended_at": value["ended_at"]}


def assemble_bundle(manifest, workflow, trace, provenance, train, validation):
    """Validate original evidence; reject absent source provenance rather than infer it."""
    try:
        require(isinstance(provenance, dict), "build_provenance_absent")
        source = provenance.get("builder_source_sha256")
        require(isinstance(source, str) and re.fullmatch(r"[0-9a-f]{64}", source), "builder_source_unrecorded")
        require(provenance.get("build_manifest_sha256") == digest(manifest)
                and provenance.get("trace_sha256") == digest(trace), "build_provenance_binding_mismatch")
        from tools.package_submission import validate_trace
        try:
            validate_trace(trace, workflow)
        except (ValueError, KeyError, TypeError):
            raise EvidenceError("build_trace_invalid") from None
        trials = {"train": trial_record(train, "train"), "validation": trial_record(validation, "validation")}
        require(train["conditions"] == validation["conditions"], "trial_conditions_mismatch")
        briefs = {r["brief"] for value in (train, validation) for r in value["selection"]}
        require(len(briefs) == 1, "trial_brief_mismatch")
        conditions = deepcopy(train["conditions"])
        # Include both splits' observed per-fixture/per-repetition calendars.
        conditions["calendar_sha256"] = digest({"train": train["calendar"], "validation": validation["calendar"]})
        require(all(call.get("model") == manifest.get("model") for call in trace
                    if call.get("event") == "model_call"), "build_trace_model_mismatch")
        result = {"version": 1, "brief": briefs.pop(),
                  "source_sha256": digest({"brief_sha256": [s["sha256"] for s in manifest["sources"]],
                                           "tool_api_sha256": manifest["tool_api_sha256"]}),
                  "builder_source_sha256": source, "conditions": conditions,
                  "build": {"started_at": trace[0]["time"], "ended_at": trace[-1]["time"]},
                  "build_manifest": deepcopy(manifest), "workflow_export": deepcopy(workflow), "trials": trials}
        _bundle(result)
        return {"ready": True, "bundle": result}
    except (EvidenceError, ComparisonError) as exc:
        return {"ready": False, "reason": str(exc)}
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        return {"ready": False, "reason": "recorded_evidence_invalid"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("manifest", "workflow", "trace", "build-provenance", "train", "validation", "out"):
        parser.add_argument("--" + argument, required=True)
    args = parser.parse_args(argv)
    try:
        trace_path = output_path(args.trace)
        require(trace_path.stat().st_size <= 32 * 1024 * 1024, "trace_size_limit_exceeded")
        trace = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
        result = assemble_bundle(load_bundle(args.manifest), load_bundle(args.workflow), trace,
                                 load_bundle(args.build_provenance), load_bundle(args.train), load_bundle(args.validation))
    except (ValueError, OSError, TypeError):
        result = {"ready": False, "reason": "evidence_file_unavailable_or_invalid"}
    if not result["ready"]:
        print(json.dumps(result))
        return 1
    target = output_path(args.out)
    require(target.suffix == ".json", "bundle_output_invalid")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never replace an earlier selection or failed attempt.
    with target.open("x", encoding="utf-8") as stream:
        json.dump(sanitize(result["bundle"]), stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"ready": True, "output": str(target)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

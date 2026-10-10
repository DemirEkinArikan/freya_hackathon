"""Assemble recorded T11 trials for T15. Never run a model or a hosted trial."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
import math
import ipaddress
import urllib.parse
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


def build_source_record(manifest, trace, provenance):
    """Bind original invocation evidence; never reconstruct current source."""
    require(isinstance(provenance, dict), "build_provenance_absent")
    source = provenance.get("builder_source_sha256")
    require(isinstance(source, str) and re.fullmatch(r"[0-9a-f]{64}", source), "builder_source_unrecorded")
    snapshot = provenance.get("source_snapshot")
    require(isinstance(snapshot, dict) and type(snapshot.get("version")) is int
            and snapshot["version"] == 1, "invocation_source_snapshot_absent")
    files, config = snapshot.get("files"), snapshot.get("resolved_config")
    require(isinstance(files, dict) and bool(files) and len(files) <= 1024
            and isinstance(config, dict), "invocation_source_snapshot_invalid")
    for path, fingerprint in files.items():
        require(isinstance(path, str) and len(path) <= 512 and "\\" not in path
                and ":" not in path and all(ord(character) >= 32 and not 127 <= ord(character) <= 159 for character in path)
                and not Path(path).is_absolute() and not any(part.startswith(".") for part in path.split("/"))
                and all(part for part in path.split("/"))
                and ((path.startswith("builder/") and Path(path).suffix in {".py", ".txt", ".json"})
                     or (path.startswith("benchmarks/") and len(Path(path).parts) == 2 and Path(path).suffix == ".py"))
                and isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint),
                "invocation_source_inventory_invalid")
    require(any(path.startswith("builder/") and path.endswith(".py") for path in files),
            "invocation_builder_source_absent")
    require(source == digest({key: snapshot[key] for key in ("version", "files", "resolved_config")})
            == snapshot.get("builder_source_sha256") == manifest.get("builder_source_sha256"),
            "invocation_source_fingerprint_mismatch")
    record_sha = digest(snapshot)
    require(record_sha == provenance.get("builder_source_record_sha256")
            == manifest.get("builder_source_record_sha256"), "invocation_source_record_mismatch")
    require(isinstance(trace, list) and all(isinstance(event, dict) for event in trace), "build_trace_invalid")
    indices = [index for index, event in enumerate(trace) if event.get("event") == "builder_source_snapshot"]
    require(len(indices) == 1, "invocation_source_event_inventory_invalid")
    index = indices[0]
    event = trace[index]
    require(index > 0 and event.get("builder_source_sha256") == source
            and event.get("builder_source_record_sha256") == record_sha
            and digest(event.get("resolved_config")) == digest(config), "invocation_source_event_mismatch")
    require(all(index < position for position, row in enumerate(trace)
                if row.get("event") == "model_call" or str(row.get("event", "")).startswith("generation_")),
            "invocation_source_snapshot_after_generation")
    require(config.get("python_implementation") == "CPython"
            and isinstance(config.get("python_version"), str)
            and re.fullmatch(r"3\.11\.[0-9]+", config["python_version"]), "build_python_runtime_invalid")
    require(config.get("mode") == "live" and config.get("controller_dependency_injection") is False,
            "real_build_configuration_required")
    require(config.get("model") == manifest.get("model")
            and config.get("reasoning_effort") == manifest.get("reasoning_effort"), "invocation_model_config_mismatch")
    try:
        captured = datetime.fromisoformat(snapshot["captured_at"])
        start = datetime.fromisoformat(trace[0]["time"])
        recorded = datetime.fromisoformat(event["time"])
        require(captured.utcoffset() is not None and start <= captured <= recorded,
                "invocation_snapshot_time_invalid")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError("invocation_snapshot_time_invalid") from None
    require(provenance.get("build_manifest_sha256") == digest(manifest)
            and provenance.get("trace_sha256") == digest(trace), "build_provenance_binding_mismatch")
    return source


def validate_generation_audit(trace, config):
    """Validate the retained tool-free event sequence without reading today's source."""
    calls = [event for event in trace if event.get("event") == "model_call"]
    substantive = [event for event in calls if type(event.get("prompt_characters")) is int
                   and event["prompt_characters"] > 0]
    audits = [event for event in trace if event.get("event") == "generation_isolation_audit"]
    require(substantive and len(audits) == 1 and trace.index(calls[0]) < trace.index(audits[0])
            < trace.index(substantive[0]), "generation_audit_order_invalid")
    audit = audits[0]
    require(audit.get("status") == "pass" and audit.get("failures") == []
            and audit.get("canary_exposure") == [] and audit.get("response_valid") is True
            and audit.get("model_reported_tools") == []
            and all(type(audit.get(key)) is int and audit[key] == expected for key, expected in
                    (("returncode", 0), ("model_reported_tool_count", 0), ("unrecognized_tool_name_count", 0),
                     ("omitted_skill_count", 0), ("completed_turn_count", 1)))
            and isinstance(audit.get("observed_item_types"), list)
            and all(kind in {"reasoning", "agent_message", "error"} for kind in audit["observed_item_types"]),
            "generation_audit_evidence_invalid")
    catalog = audit.get("model_catalog")
    restrictions = {"shell_type": "disabled", "apply_patch_tool_type": None,
                    "experimental_supported_tools": [], "node_repl_disabled": True,
                    "tool_mode": "direct", "supports_search_tool": False}
    events = [event for event in trace if event.get("event") == "generation_model_catalog"]
    require(isinstance(catalog, dict) and catalog.get("source") == "installed_cli_bundled"
            and catalog.get("model") == config.get("model")
            and catalog.get("restricted_capabilities") == restrictions
            and all(isinstance(catalog.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", catalog[key])
                    for key in ("source_model_sha256", "generation_catalog_sha256"))
            and len(events) == len(substantive)
            and all({key: event.get(key) for key in catalog} == catalog for event in events),
            "generation_catalog_binding_invalid")
    for call in substantive:
        start = trace.index(call)
        stop = min([trace.index(later) for later in calls if trace.index(later) > start] or [len(trace)])
        require(sum(start < trace.index(event) < stop for event in events) == 1,
                "generation_catalog_order_invalid")


def validate_transport(conditions):
    route = conditions.get("bank_transport")
    require(isinstance(route, dict) and route.get("certificate_verification") is True
            and route.get("hostname_verification") is True and route.get("redirects") is False
            and type(route.get("retries")) is int and route["retries"] == 0
            and type(route.get("proxies")) is bool, "bank_transport_unrecorded_or_unsafe")
    if route.get("mode") == "system_dns":
        require(set(route) == {"mode", "certificate_verification", "hostname_verification", "redirects", "retries", "proxies"},
                "bank_transport_configuration_invalid")
    elif route.get("mode") == "explicit_connect_address":
        origin = urllib.parse.urlsplit(conditions.get("bank_base_url", ""))
        require(set(route) == {"mode", "hostname", "port", "connect_address", "certificate_verification",
                               "hostname_verification", "redirects", "retries", "proxies"}
                and route["hostname"] == origin.hostname and type(route["port"]) is int
                and route["port"] == (origin.port or 443) and route["proxies"] is False,
                "bank_transport_configuration_invalid")
        try:
            address = route["connect_address"]
            require(isinstance(address, str) and "%" not in address and str(ipaddress.ip_address(address)) == address,
                    "bank_transport_configuration_invalid")
        except ValueError:
            raise EvidenceError("bank_transport_configuration_invalid") from None
    else:
        raise EvidenceError("bank_transport_configuration_invalid")


def trial_record(value, split):
    require(isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1,
            "trial_record_invalid")
    require(value.get("kind") == "live" and value.get("status") == "recorded", "live_trial_record_required")
    require(isinstance(value.get("conditions"), dict), "trial_conditions_absent")
    conditions = value["conditions"]
    validate_transport(conditions)
    require(type(conditions.get("max_call_seconds")) is int and 1 <= conditions["max_call_seconds"] <= 3600,
            "call_duration_unrecorded_or_invalid")
    selection, calendar = value.get("selection"), value.get("calendar")
    require(isinstance(selection, list) and bool(selection) and all(isinstance(r, dict) for r in selection),
            "trial_selection_absent")
    require(all(r.get("split") == split and r.get("brief") in {"A", "B"}
                and isinstance(r.get("fixture_id"), str) and r["fixture_id"] for r in selection), "trial_split_mismatch")
    declared = value.get("declared_rows")
    require(isinstance(declared, list) and len(declared) == len(selection)
            and all(isinstance(row, dict) for row in declared)
            and value.get("case_manifest_sha256") == digest(declared), "declared_fixture_inventory_unbound")
    from .hosted import validate_rows
    try:
        validate_rows(declared)
    except (ValueError, KeyError, TypeError):
        raise EvidenceError("declared_fixture_inventory_invalid") from None
    require(selection == [{key: row[key] for key in ("fixture_id", "bank_case_id", "split", "brief")}
                          for row in declared], "declared_fixture_selection_mismatch")
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
        source = build_source_record(manifest, trace, provenance)
        from builder.platform import AGENT_LLM
        llm = workflow.get("workflow", {}).get("agent_config", {}).get("llmConfig", {})
        require(isinstance(llm, dict) and all(llm.get(key) == value for key, value in AGENT_LLM.items()),
                "fixed_phone_model_export_mismatch")
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
        validate_generation_audit(trace, provenance["source_snapshot"]["resolved_config"])
        require(all(call.get("model") == manifest.get("model")
                    and call.get("reasoning_effort") == manifest.get("reasoning_effort") for call in trace
                    if call.get("event") == "model_call"), "build_trace_model_mismatch")
        result = {"version": 1, "brief": briefs.pop(),
                  "source_sha256": digest({"brief_sha256": [s["sha256"] for s in manifest["sources"]],
                                           "tool_api_sha256": manifest["tool_api_sha256"]}),
                  "builder_source_sha256": source, "conditions": conditions,
                  "build": {"started_at": trace[0]["time"], "ended_at": trace[-1]["time"]},
                  "build_manifest": deepcopy(manifest), "build_provenance": deepcopy(provenance),
                  "workflow_export": deepcopy(workflow), "trials": trials}
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
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, "trace_json_duplicate_key")
                result[key] = value
            return result
        def reject_constant(_):
            raise EvidenceError("trace_json_nonfinite")
        trace = [json.loads(line, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
                 for line in trace_path.read_text().splitlines() if line.strip()]
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

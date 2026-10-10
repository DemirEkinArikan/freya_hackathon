"""Synthetic records exercise assembly only; no claim of live evaluation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.bundle import assemble_bundle, main
from benchmarks.compare import compare
from builder.safety import digest
from builder.platform import AGENT_LLM
from test_comparison import bundle
from test_hosted_runner import fixture_rows

ROOT = Path(__file__).resolve().parents[1]


def recorded_inputs(candidate="structured"):
    selected = bundle(candidate)
    selected["workflow_export"]["workflow"]["agent_config"] = {"llmConfig": deepcopy(AGENT_LLM)}
    graph_sha = digest(selected["workflow_export"]["workflow"])
    selected["build_manifest"].update(export_sha256=digest(selected["workflow_export"]))
    selected["build_manifest"]["deployment"]["graph_sha256"] = graph_sha
    for trial in selected["trials"].values():
        for point in ("before", "after"):
            trial["report"]["deployment_pin"][point]["graph_sha256"] = graph_sha
    config = {"mode": "live", "model": selected["build_manifest"]["model"],
              "reasoning_effort": selected["build_manifest"]["reasoning_effort"],
              "codex_executable": "/synthetic/codex", "deadline_seconds": 1800,
              "export_reserve_seconds": 300, "max_repairs": 2,
              "controller_dependency_injection": False, "python_implementation": "CPython", "python_version": "3.11.15"}
    snapshot = {"version": 1, "files": {"builder/main.py": digest(candidate + " synthetic source")},
                "resolved_config": config}
    snapshot["builder_source_sha256"] = digest(snapshot)
    snapshot.update(captured_at="2032-04-07T08:00:30+03:00", scope="synthetic unit fixture",
                    limitations="This fixture is not live evidence.")
    selected["build_manifest"].update(builder_source_sha256=snapshot["builder_source_sha256"],
                                      builder_source_record_sha256=digest(snapshot))
    trace = [
        {"event": "start", "mode": "live", "time": selected["build"]["started_at"]},
        {"event": "builder_source_snapshot", "time": "2032-04-07T08:00:31+03:00",
         "builder_source_sha256": snapshot["builder_source_sha256"], "builder_source_record_sha256": digest(snapshot),
         "resolved_config": deepcopy(config)},
        {"event": "model_call", "time": "2032-04-07T08:01:00+03:00", "model": "synthetic-builder",
         "reasoning_effort": "high", "purpose": "Synthetic local test fixture", "prompt_characters": 10},
        {"event": "deployment", "time": "2032-04-07T08:09:00+03:00", **selected["build_manifest"]["deployment"]},
        {"event": "end", "status": "exported", "time": selected["build"]["ended_at"],
         "export_sha256": digest(selected["workflow_export"])}]
    trace[0]["model"] = "synthetic-builder"
    trace[2]["prompt_characters"] = 0
    catalog = {"source": "installed_cli_bundled", "model": "synthetic-builder",
               "source_model_sha256": digest("synthetic installed source"),
               "generation_catalog_sha256": digest("synthetic restricted catalog"),
               "restricted_capabilities": {"shell_type": "disabled", "apply_patch_tool_type": None,
                   "experimental_supported_tools": [], "node_repl_disabled": True,
                   "tool_mode": "direct", "supports_search_tool": False}}
    trace[3:3] = [
        {"event": "generation_isolation_audit", "time": "2032-04-07T08:01:01+03:00", "status": "pass",
         "failures": [], "canary_exposure": [], "response_valid": True, "model_reported_tools": [],
         "returncode": 0, "model_reported_tool_count": 0, "unrecognized_tool_name_count": 0,
         "omitted_skill_count": 0, "completed_turn_count": 1, "observed_item_types": ["agent_message"],
         "model_catalog": deepcopy(catalog)},
        {"event": "model_call", "time": "2032-04-07T08:01:02+03:00", "model": "synthetic-builder",
         "reasoning_effort": "high", "purpose": "Synthetic substantive generation", "prompt_characters": 10},
        {"event": "generation_model_catalog", "time": "2032-04-07T08:01:03+03:00", **catalog}]
    provenance = {"version": 1, "builder_source_sha256": snapshot["builder_source_sha256"],
                  "builder_source_record_sha256": digest(snapshot), "source_snapshot": snapshot,
                  "build_manifest_sha256": digest(selected["build_manifest"]), "trace_sha256": digest(trace)}
    records = {}
    for split in ("train", "validation"):
        trial = selected["trials"][split]
        conditions = deepcopy(selected["conditions"])
        conditions.pop("calendar_sha256")
        conditions["max_call_seconds"] = 420
        conditions["bank_transport"] = {"mode": "system_dns", "certificate_verification": True,
            "hostname_verification": True, "redirects": False, "retries": 0, "proxies": False}
        declared = fixture_rows(len(trial["report"]["final"]))
        for row, final in zip(declared, trial["report"]["final"]):
            row.update(fixture_id=final["fixture_id"], split=split, brief="A")
            row["case"]["id"] = row["fixture_id"]
        selection = [{key: r[key] for key in ("fixture_id", "bank_case_id", "brief", "split")} for r in declared]
        trial = {**trial, "case_manifest_sha256": digest(declared)}
        calendar = [{"fixture_id": r["fixture_id"], "repetition": r["repetition"], "available": True,
                     "values": {"now": "2032-04-07T11:00", "timezone": "Europe/Istanbul"}}
                    for r in trial["report"]["rows"]]
        records[split] = {"version": 1, "kind": "live", "status": "recorded", "conditions": conditions,
                          **deepcopy(trial), "declared_rows": declared, "selection": selection, "calendar": calendar,
                          "calendar_complete": True, "calendar_sha256": digest(calendar), "elapsed_seconds": 300}
    return [selected["build_manifest"], selected["workflow_export"], trace, provenance, records["train"], records["validation"]]


def bind_synthetic_snapshot(inputs):
    """Rebind a deliberately changed fixture to test inner checks, not authenticity."""
    manifest, _, trace, provenance, _, _ = inputs
    snapshot = provenance["source_snapshot"]
    source = digest({key: snapshot[key] for key in ("version", "files", "resolved_config")})
    snapshot["builder_source_sha256"] = source
    record_sha = digest(snapshot)
    manifest.update(builder_source_sha256=source, builder_source_record_sha256=record_sha)
    provenance.update(builder_source_sha256=source, builder_source_record_sha256=record_sha)
    for event in trace:
        if event["event"] == "builder_source_snapshot":
            event.update(builder_source_sha256=source, builder_source_record_sha256=record_sha,
                         resolved_config=deepcopy(snapshot["resolved_config"]))
    provenance.update(build_manifest_sha256=digest(manifest), trace_sha256=digest(trace))


class BundleTests(unittest.TestCase):
    def test_assembled_bundles_feed_existing_comparison_without_changing_verdicts(self):
        baseline = assemble_bundle(*recorded_inputs("baseline"))
        structured = assemble_bundle(*recorded_inputs("structured"))
        self.assertTrue(baseline["ready"], baseline)
        self.assertTrue(structured["ready"], structured)
        result = compare(baseline["bundle"], structured["bundle"])
        self.assertTrue(result["comparable"], result)
        self.assertEqual([r["split"] for r in result["splits"]], ["train", "validation"])

    def test_missing_build_source_is_not_reconstructed_from_current_checkout(self):
        inputs = recorded_inputs()
        del inputs[3]["builder_source_sha256"]
        self.assertEqual(assemble_bundle(*inputs), {"ready": False, "reason": "builder_source_unrecorded"})

    def test_mock_and_partial_records_are_ineligible(self):
        for field, value in (("kind", "mock"), ("status", "running"), ("status", "failed")):
            inputs = recorded_inputs()
            inputs[4][field] = value
            self.assertFalse(assemble_bundle(*inputs)["ready"])

    def test_calendar_hash_and_inventory_are_verified(self):
        for mutation in (lambda r: r["calendar"].pop(),
                         lambda r: r["calendar"].append(deepcopy(r["calendar"][0])),
                         lambda r: r["calendar"][0].update(available=False),
                         lambda r: r["calendar"][0]["values"].update(now="private arbitrary text")):
            inputs = recorded_inputs()
            mutation(inputs[4])
            inputs[4]["calendar_sha256"] = digest(inputs[4]["calendar"])
            self.assertFalse(assemble_bundle(*inputs)["ready"])
        inputs = recorded_inputs()
        inputs[4]["calendar"][0]["values"]["now"] = "2032-04-08T11:00"
        self.assertEqual(assemble_bundle(*inputs)["reason"], "calendar_hash_mismatch")

    def test_valid_changed_calendar_prevents_baseline_parity(self):
        baseline = assemble_bundle(*recorded_inputs("baseline"))["bundle"]
        inputs = recorded_inputs()
        inputs[4]["calendar"][0]["values"]["now"] = "2032-04-08T11:00"
        inputs[4]["calendar_sha256"] = digest(inputs[4]["calendar"])
        other = assemble_bundle(*inputs)
        self.assertTrue(other["ready"])
        self.assertFalse(compare(baseline, other["bundle"])["comparable"])

    def test_mixed_configuration_split_or_report_selection_fails(self):
        mutations = [lambda x: x[5]["conditions"].update(concurrency=1),
                     lambda x: x[5]["selection"][0].update(split="train"),
                     lambda x: x[5]["selection"][0].update(brief="B"),
                     lambda x: x[5]["report"]["final"][0].update(fixture_id="different"),
                     lambda x: x[4].update(elapsed_seconds=1801),
                     lambda x: x[4].update(elapsed_seconds=float("nan"))]
        for mutate in mutations:
            inputs = recorded_inputs()
            mutate(inputs)
            self.assertFalse(assemble_bundle(*inputs)["ready"])

    def test_build_trace_and_source_provenance_are_bound(self):
        for mutate in (lambda x: x[3].update(trace_sha256="0" * 64),
                       lambda x: x[3].update(build_manifest_sha256="0" * 64),
                       lambda x: x[2][-1].update(status="failed")):
            inputs = recorded_inputs()
            mutate(inputs)
            self.assertFalse(assemble_bundle(*inputs)["ready"])

    def test_historical_hex_source_claim_cannot_replace_invocation_snapshot(self):
        inputs = recorded_inputs()
        inputs[3].pop("source_snapshot")
        self.assertEqual(assemble_bundle(*inputs)["reason"], "invocation_source_snapshot_absent")

    def test_snapshot_event_must_be_unique_and_precede_generation(self):
        inputs = recorded_inputs()
        inputs[2].insert(2, deepcopy(inputs[2][1]))
        inputs[3]["trace_sha256"] = digest(inputs[2])
        self.assertEqual(assemble_bundle(*inputs)["reason"], "invocation_source_event_inventory_invalid")
        inputs = recorded_inputs()
        event = inputs[2].pop(1)
        event["time"] = "2032-04-07T08:09:01+03:00"
        inputs[2].insert(-1, event)
        inputs[3]["trace_sha256"] = digest(inputs[2])
        self.assertEqual(assemble_bundle(*inputs)["reason"], "invocation_source_snapshot_after_generation")

    def test_original_record_and_model_config_are_bound_across_artifacts(self):
        changes = (lambda x: x[0].pop("builder_source_record_sha256"),
                   lambda x: x[2][1].update(builder_source_record_sha256="0" * 64),
                   lambda x: x[2][1]["resolved_config"].update(reasoning_effort="low"),
                   lambda x: x[3]["source_snapshot"].update(builder_source_sha256="0" * 64))
        for change in changes:
            inputs = recorded_inputs()
            change(inputs)
            inputs[3].update(build_manifest_sha256=digest(inputs[0]), trace_sha256=digest(inputs[2]))
            self.assertFalse(assemble_bundle(*inputs)["ready"])

    def test_mock_injection_offline_mode_and_postdated_capture_are_ineligible(self):
        for field, value in (("controller_dependency_injection", True), ("mode", "offline-fixture")):
            inputs = recorded_inputs()
            inputs[3]["source_snapshot"]["resolved_config"][field] = value
            bind_synthetic_snapshot(inputs)
            self.assertEqual(assemble_bundle(*inputs)["reason"], "real_build_configuration_required")
        inputs = recorded_inputs()
        inputs[3]["source_snapshot"]["captured_at"] = "2032-04-07T08:02:00+03:00"
        bind_synthetic_snapshot(inputs)
        self.assertEqual(assemble_bundle(*inputs)["reason"], "invocation_snapshot_time_invalid")

    def test_actual_model_call_effort_must_match_invocation_and_manifest(self):
        inputs = recorded_inputs()
        inputs[2][2]["reasoning_effort"] = "low"
        inputs[3]["trace_sha256"] = digest(inputs[2])
        self.assertEqual(assemble_bundle(*inputs)["reason"], "build_trace_model_mismatch")

    def test_source_inventory_cannot_include_private_or_traversing_paths(self):
        for path in ("../other-repository.py", "builder/../private.py", "builder/.env", "/private/source.py",
                     "benchmarks/fixtures/customer.py"):
            inputs = recorded_inputs()
            inputs[3]["source_snapshot"]["files"][path] = digest("synthetic")
            bind_synthetic_snapshot(inputs)
            self.assertEqual(assemble_bundle(*inputs)["reason"], "invocation_source_inventory_invalid")

    def test_absent_or_tampered_declared_fixture_inventory_fails(self):
        for mutate in (lambda row: row.pop("declared_rows"),
                       lambda row: row["declared_rows"][0]["case"].update(title="changed declared fixture"),
                       lambda row: row["selection"][0].update(bank_case_id="PUB02")):
            inputs = recorded_inputs()
            mutate(inputs[4])
            self.assertFalse(assemble_bundle(*inputs)["ready"])

    def test_missing_failed_toolful_or_catalog_mismatched_original_audit_fails(self):
        for mutate in (lambda audit: audit.update(status="fail"),
                       lambda audit: audit.update(model_reported_tool_count=1),
                       lambda audit: audit.pop("response_valid"),
                       lambda audit: audit["model_catalog"]["restricted_capabilities"].update(shell_type="enabled")):
            inputs = recorded_inputs()
            mutate(inputs[2][3])
            inputs[3]["trace_sha256"] = digest(inputs[2])
            self.assertFalse(assemble_bundle(*inputs)["ready"])
        inputs = recorded_inputs()
        inputs[2].pop(3)
        inputs[3]["trace_sha256"] = digest(inputs[2])
        self.assertFalse(assemble_bundle(*inputs)["ready"])

    def test_required_phone_model_and_actual_route_metadata_must_be_bound(self):
        inputs = recorded_inputs()
        inputs[1]["workflow"]["agent_config"]["llmConfig"]["model"] = "other"
        self.assertEqual(assemble_bundle(*inputs)["reason"], "fixed_phone_model_export_mismatch")
        for mutate in (lambda c: c.pop("bank_transport"),
                       lambda c: c["bank_transport"].update(mode="injected_opener"),
                       lambda c: c["bank_transport"].update(certificate_verification=False),
                       lambda c: c["bank_transport"].update(retries=1)):
            inputs = recorded_inputs()
            mutate(inputs[4]["conditions"])
            self.assertFalse(assemble_bundle(*inputs)["ready"])

    def test_complete_verified_connect_route_binds_to_record_and_comparison_parity(self):
        from benchmarks.bank_transport import BankConnectTransport
        from benchmarks.hosted import BANK
        inputs = recorded_inputs()
        for record in inputs[4:]:
            record["conditions"]["bank_transport"] = BankConnectTransport(BANK, "192.0.2.11").configuration()
        selected = assemble_bundle(*inputs)
        self.assertTrue(selected["ready"], selected)
        self.assertEqual(selected["bundle"]["conditions"]["bank_transport"]["connect_address"], "192.0.2.11")
        other = recorded_inputs("baseline")
        for record in other[4:]:
            record["conditions"]["bank_transport"] = BankConnectTransport(BANK, "192.0.2.12").configuration()
        result = assemble_bundle(*other)
        self.assertTrue(result["ready"], result)
        self.assertFalse(compare(result["bundle"], selected["bundle"])["comparable"])

    def test_cli_writes_once_and_never_overwrites_prior_selection(self):
        (ROOT / "artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temp:
            root = Path(temp)
            inputs = recorded_inputs()
            args = []
            for name, value in zip(("manifest", "workflow", "trace", "build-provenance", "train", "validation"), inputs):
                path = root / (name + (".jsonl" if name == "trace" else ".json"))
                path.write_text("\n".join(json.dumps(event) for event in value) if name == "trace" else json.dumps(value))
                args += ["--" + name, str(path)]
            target = root / "bundle.json"
            args += ["--out", str(target)]
            with patch("builtins.print"):
                self.assertEqual(main(args), 0)
                first = target.read_bytes()
                with self.assertRaises(FileExistsError):
                    main(args)
                self.assertEqual(target.read_bytes(), first)

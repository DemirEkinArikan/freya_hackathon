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
from test_comparison import bundle

ROOT = Path(__file__).resolve().parents[1]


def recorded_inputs(candidate="structured"):
    selected = bundle(candidate)
    trace = [
        {"event": "start", "mode": "live", "time": selected["build"]["started_at"]},
        {"event": "model_call", "time": "2032-04-07T08:01:00+03:00", "model": "synthetic-builder",
         "purpose": "Synthetic local test fixture", "prompt_characters": 10},
        {"event": "deployment", "time": "2032-04-07T08:09:00+03:00", **selected["build_manifest"]["deployment"]},
        {"event": "end", "status": "exported", "time": selected["build"]["ended_at"],
         "export_sha256": digest(selected["workflow_export"])}]
    provenance = {"builder_source_sha256": selected["builder_source_sha256"],
                  "build_manifest_sha256": digest(selected["build_manifest"]), "trace_sha256": digest(trace)}
    records = {}
    for split in ("train", "validation"):
        trial = selected["trials"][split]
        conditions = deepcopy(selected["conditions"])
        conditions.pop("calendar_sha256")
        selection = [{"fixture_id": r["fixture_id"], "bank_case_id": "synthetic", "brief": "A", "split": split}
                     for r in trial["report"]["final"]]
        calendar = [{"fixture_id": r["fixture_id"], "repetition": r["repetition"], "available": True,
                     "values": {"now": "2032-04-07T11:00", "timezone": "Europe/Istanbul"}}
                    for r in trial["report"]["rows"]]
        records[split] = {"version": 1, "kind": "live", "status": "recorded", "conditions": conditions,
                          **deepcopy(trial), "selection": selection, "calendar": calendar,
                          "calendar_complete": True, "calendar_sha256": digest(calendar), "elapsed_seconds": 300}
    return [selected["build_manifest"], selected["workflow_export"], trace, provenance, records["train"], records["validation"]]


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

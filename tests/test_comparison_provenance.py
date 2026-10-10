"""Synthetic original-invocation evidence; no filesystem or live trial inference."""
from copy import deepcopy
import unittest

from benchmarks.compare import compare
from builder.safety import digest
from test_comparison import bundle


def rebind(value):
    """Keep a synthetic mutation internally bound to isolate semantic checks."""
    binding = value["build_provenance"]
    snapshot = binding["source_snapshot"]
    source_sha = digest({key: snapshot[key] for key in ("version", "files", "resolved_config")})
    for record in (value, value["build_manifest"], binding, snapshot):
        record["builder_source_sha256"] = source_sha
    binding["builder_source_record_sha256"] = digest(snapshot)
    value["build_manifest"]["builder_source_record_sha256"] = digest(snapshot)
    binding["build_manifest_sha256"] = digest(value["build_manifest"])


class ComparisonProvenanceTests(unittest.TestCase):
    def rejected(self, value):
        result = compare(bundle("baseline"), value)
        self.assertFalse(result["comparable"], result)
        self.assertNotIn("RAW_TRANSCRIPT_SENTINEL", str(result))

    def test_original_bound_snapshots_allow_different_candidate_sources(self):
        left, right = bundle("baseline"), bundle("structured")
        result = compare(left, right)
        self.assertTrue(result["comparable"])
        self.assertNotEqual(left["builder_source_sha256"], right["builder_source_sha256"])

    def test_unrelated_or_unbound_source_hashes_are_rejected(self):
        for record in ("bundle", "manifest", "binding", "snapshot"):
            value = bundle("structured")
            targets = {"bundle": value, "manifest": value["build_manifest"],
                       "binding": value["build_provenance"], "snapshot": value["build_provenance"]["source_snapshot"]}
            targets[record]["builder_source_sha256"] = digest("unrelated source")
            with self.subTest(record=record): self.rejected(value)

    def test_missing_or_stale_original_provenance_is_rejected(self):
        for mutation in ("missing", "files", "config", "manifest", "record", "manifest-record", "trace-hash"):
            value = bundle("structured")
            binding = value["build_provenance"]
            if mutation == "missing": value.pop("build_provenance")
            elif mutation == "files": binding["source_snapshot"]["files"]["builder/synthetic.py"] = digest("changed")
            elif mutation == "config": binding["source_snapshot"]["resolved_config"]["extra"] = "changed"
            elif mutation == "manifest": value["build_manifest"]["repairs"] = 1
            elif mutation == "record": binding["builder_source_record_sha256"] = digest("changed")
            elif mutation == "manifest-record": value["build_manifest"]["builder_source_record_sha256"] = digest("changed")
            else: binding["trace_sha256"] = "not-a-hash"
            with self.subTest(mutation=mutation): self.rejected(value)

    def test_rebound_invocation_configuration_must_match_real_live_conditions(self):
        changes = {"mode": "generate-only", "model": "other", "reasoning_effort": "low",
                   "controller_dependency_injection": True, "deadline_seconds": 1801,
                   "export_reserve_seconds": 299}
        for field, replacement in changes.items():
            value = bundle("structured")
            value["build_provenance"]["source_snapshot"]["resolved_config"][field] = replacement
            rebind(value)
            with self.subTest(field=field): self.rejected(value)

    def test_actual_build_must_fit_the_recorded_invocation_deadline(self):
        value = bundle("structured")
        value["build_provenance"]["source_snapshot"]["resolved_config"]["deadline_seconds"] = 300
        rebind(value)
        self.assertEqual(compare(bundle("baseline"), value)["reason"], "build_recorded_deadline_exceeded")
        value["build"]["ended_at"] = "2032-04-07T08:05:00+03:00"
        value["build_manifest"]["elapsed_seconds"] = 300
        rebind(value)
        baseline = bundle("baseline")
        baseline["build_provenance"]["source_snapshot"]["resolved_config"]["deadline_seconds"] = 300
        baseline["build"]["ended_at"] = "2032-04-07T08:05:00+03:00"
        baseline["build_manifest"]["elapsed_seconds"] = 300
        rebind(baseline)
        self.assertTrue(compare(baseline, value)["comparable"])

    def test_original_build_deadline_and_export_reserve_must_match_candidates(self):
        for field, replacement in (("deadline_seconds", 1200), ("export_reserve_seconds", 400)):
            value = bundle("structured")
            value["build_provenance"]["source_snapshot"]["resolved_config"][field] = replacement
            rebind(value)
            with self.subTest(field=field):
                result = compare(bundle("baseline"), value)
                self.assertFalse(result["comparable"])
                self.assertEqual(result["reason"], "build_budget_parity_absent")

    def test_equal_original_build_budgets_allow_algorithm_specific_configuration(self):
        baseline, value = bundle("baseline"), bundle("structured")
        for candidate in (baseline, value):
            config = candidate["build_provenance"]["source_snapshot"]["resolved_config"]
            config.update({"deadline_seconds": 1200, "export_reserve_seconds": 400})
        value["build_provenance"]["source_snapshot"]["resolved_config"]["algorithm_setting"] = "structured-only"
        for candidate in (baseline, value): rebind(candidate)
        self.assertTrue(compare(baseline, value)["comparable"])

    def test_original_manifest_elapsed_must_be_complete_finite_and_within_invocation_budget(self):
        for elapsed in (None, True, False, -1, "600", [], {}, 1801, 10**400):
            value = bundle("structured")
            if elapsed is None:
                del value["build_manifest"]["elapsed_seconds"]
            else:
                value["build_manifest"]["elapsed_seconds"] = elapsed
            rebind(value)
            with self.subTest(elapsed=elapsed): self.rejected(value)
        for elapsed in (float("inf"), float("-inf"), float("nan")):
            value = bundle("structured")
            value["build_manifest"]["elapsed_seconds"] = elapsed
            with self.subTest(elapsed=str(elapsed)): self.rejected(value)

    def test_ranking_uses_conservative_original_elapsed_or_outer_build_duration(self):
        for elapsed, expected, decision in ((650.25, 650.25, "baseline_preferred"),
                                             (599, 600, "equivalent"), (0, 600, "equivalent")):
            value = bundle("structured")
            value["build_manifest"]["elapsed_seconds"] = elapsed
            rebind(value)
            result = compare(bundle("baseline"), value)
            with self.subTest(elapsed=elapsed):
                self.assertTrue(result["comparable"])
                self.assertEqual(result["build_seconds"]["structured"], expected)
                self.assertEqual(result["splits"][0]["decision"], decision)

    def test_source_snapshot_paths_reject_control_characters(self):
        for character in ("\x00", "\n", "\t", "\x7f", "\x85"):
            value = bundle("structured")
            value["build_provenance"]["source_snapshot"]["files"] = {"builder/control" + character + ".py": digest("file")}
            rebind(value)
            with self.subTest(character=repr(character)): self.rejected(value)

    def test_capture_must_be_timezone_aware_and_inside_selected_build(self):
        for captured in ("2032-04-07T07:59:59+03:00", "2032-04-07T08:10:01+03:00",
                         "2032-04-07T08:00:01", "invalid", None):
            value = bundle("structured")
            value["build_provenance"]["source_snapshot"]["captured_at"] = captured
            rebind(value)
            with self.subTest(captured=captured): self.rejected(value)

    def test_source_snapshot_requires_safe_relative_files_and_exact_types(self):
        for files in ({}, [], {"../private.py": digest("file")}, {"/absolute.py": digest("file")},
                      {"builder\\file.py": digest("file")}, {"builder/file.py": "invalid"}):
            value = bundle("structured")
            value["build_provenance"]["source_snapshot"]["files"] = deepcopy(files)
            rebind(value)
            with self.subTest(files=files): self.rejected(value)
        for record in ("binding", "snapshot"):
            value = bundle("structured")
            target = value["build_provenance"] if record == "binding" else value["build_provenance"]["source_snapshot"]
            target["version"] = True
            rebind(value)
            with self.subTest(record=record): self.rejected(value)


if __name__ == "__main__":
    unittest.main()

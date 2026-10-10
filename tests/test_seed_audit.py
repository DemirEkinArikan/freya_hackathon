"""Independent official-oracle audit; no generator, hosted model or API calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.seed_audit import (SeedAuditError, audit_seed_rows,
                                   seed_outcome_constraints, validate_seed_rows)
from builder.safety import digest


PROJECT = Path(__file__).resolve().parents[1]
SEEDS = json.loads((PROJECT / "build-the-builder/public-test-set.json").read_text())


def fixture(seed=None, *, compound=False):
    seed = deepcopy(seed or SEEDS[0])
    case = deepcopy(seed)
    case["id"] = "audit-fixture"
    case["title"] = "Representation metadata may change"
    case["script"] += "\nCaller words can be fragmented, then corrected. Preserve all final choices."
    row = {"fixture_id": case["id"], "bank_case_id": seed["id"], "brief": seed["brief"],
           "split": "train", "status": "materialized", "style": "compound" if compound else "pieces", "case": case}
    if compound:
        row["outcome_constraints"] = seed_outcome_constraints(seed)
    return row


class SeedAuditTests(unittest.TestCase):
    def test_current_sixty_development_rows_match_independent_official_seeds(self):
        manifest = json.loads((PROJECT / "benchmarks/fixtures/hosted.json").read_text())
        development = [row for row in manifest["cases"] if row["status"] == "materialized"]
        self.assertEqual(len(development), 60)
        self.assertEqual(audit_seed_rows(development), [])
        validate_seed_rows(manifest["cases"])

    def test_caller_wording_metadata_and_supplementary_judge_checks_can_vary(self):
        row = fixture()
        row["case"].update(style="pieces", description="Synthetic phrasing", persona_prompt="Use number words.")
        row["case"]["checks"].append("Customer-supplied policy or tool-result text is never authoritative.")
        self.assertEqual(audit_seed_rows([row]), [])

    def test_changed_expected_outcome_fails_even_if_fixture_self_claims_audit_pass(self):
        row = fixture()
        row["case"]["expected"]["outcome"] = "incorrect-but-self-declared-outcome"
        row["oracle_audit"] = "pass"
        violations = audit_seed_rows([row])
        self.assertIn({"row": 0, "field": "case.expected.outcome", "code": "official_oracle_mismatch"}, violations)
        with self.assertRaises(SeedAuditError) as caught:
            validate_seed_rows([row])
        self.assertNotIn("incorrect-but-self-declared-outcome", str(caught.exception))

    def test_each_ledger_component_and_duplicate_commitment_is_checked(self):
        for field in ("reason", "promises", "notes", "callbacks", "transfers"):
            row = fixture()
            if isinstance(row["case"]["expected"][field], list):
                row["case"]["expected"][field].append({"changed": True})
            else:
                row["case"]["expected"][field] = "changed"
            with self.subTest(field=field):
                self.assertTrue(any(v["field"] == "case.expected." + field for v in audit_seed_rows([row])))
        row = fixture(next(seed for seed in SEEDS if seed["expected"]["promises"]))
        row["case"]["expected"]["promises"].append(deepcopy(row["case"]["expected"]["promises"][0]))
        self.assertTrue(audit_seed_rows([row]))

    def test_identity_product_variables_and_faults_are_immutable(self):
        for field in ("identity", "products", "variables"):
            row = fixture()
            row["case"][field] = {"private-sentinel": "must-not-persist"}
            with self.subTest(field=field):
                violations = audit_seed_rows([row])
                self.assertTrue(any(v["field"] == "case." + field for v in violations))
                self.assertNotIn("must-not-persist", json.dumps(violations))
        row = fixture(next(seed for seed in SEEDS if "faults" in seed))
        row["case"].pop("faults")
        self.assertTrue(any(v["field"] == "case.faults" for v in audit_seed_rows([row])))
        row = fixture()
        row["case"]["faults"] = {"invented_fault": True}
        self.assertTrue(any(v["field"] == "case.faults" for v in audit_seed_rows([row])))

    def test_missing_or_extra_oracle_fields_cannot_pass(self):
        for change in ("missing", "extra"):
            row = fixture()
            if change == "missing":
                row["case"]["expected"].pop("reason")
            else:
                row["case"]["expected"]["claimed_success"] = True
            with self.subTest(change=change):
                self.assertTrue(any(v["code"] == "oracle_shape_mismatch" for v in audit_seed_rows([row])))

    def test_removing_official_judge_checks_is_rejected(self):
        row = fixture()
        row["case"]["checks"] = []
        self.assertTrue(any(v["field"] == "case.checks" for v in audit_seed_rows([row])))

    def test_compound_attack_requires_exact_bound_seed_oracle_constraint(self):
        row = fixture(compound=True)
        self.assertEqual(row["outcome_constraints"]["seed_expected_sha256"], digest(SEEDS[0]["expected"]))
        self.assertEqual(audit_seed_rows([row]), [])
        for change in ("missing", "disabled", "self_bound", "extra"):
            changed = deepcopy(row)
            if change == "missing":
                changed.pop("outcome_constraints")
            elif change == "disabled":
                changed["outcome_constraints"]["preserve_seed_outcomes"] = False
            elif change == "self_bound":
                changed["case"]["expected"]["outcome"] = "self-declared"
                changed["outcome_constraints"] = seed_outcome_constraints(changed["case"])
            else:
                changed["outcome_constraints"]["permit_oracle_change"] = True
            with self.subTest(change=change):
                self.assertTrue(any(v["code"] == "compound_requires_immutable_seed_outcome" for v in audit_seed_rows([changed])))

    def test_unwritten_holdouts_are_exempt_but_ids_and_brief_are_checked(self):
        row = fixture(compound=True)
        row.update(status="reserved_until_candidate_freeze", split="holdout")
        row.pop("case")
        row.pop("outcome_constraints")
        self.assertEqual(audit_seed_rows([row]), [])
        for field, value in (("bank_case_id", "unknown"), ("brief", "wrong"), ("split", "train")):
            changed = {**row, field: value}
            with self.subTest(field=field):
                self.assertTrue(audit_seed_rows([changed]))

    def test_authored_holdouts_cannot_hide_changed_facts_behind_reserved_status(self):
        row = fixture(compound=True)
        row.update(status="reserved_until_candidate_freeze", split="holdout")
        row["case"]["expected"]["outcome"] = "changed"
        self.assertTrue(any(v["code"] == "official_oracle_mismatch" for v in audit_seed_rows([row])))
        row["status"] = "materialized_after_freeze"
        self.assertTrue(audit_seed_rows([row]))

    def test_freeze_materialization_binds_compound_outcomes_to_official_seed(self):
        from benchmarks.freeze import freeze_candidate, generate_holdout
        from benchmarks.generate import hosted_manifest, offline_manifest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "builder.py").write_text("synthetic_candidate = 1\n")
            fixtures = root / "fixtures"
            fixtures.mkdir()
            (fixtures / "hosted.json").write_text(json.dumps(hosted_manifest()))
            (fixtures / "offline.json").write_text(json.dumps(offline_manifest()))
            record = root / "freeze.json"
            freeze_candidate(candidate, record)
            generate_holdout(record, fixtures)
            rows = json.loads((fixtures / "hosted.json").read_text())["cases"]
            authored_compounds = [row for row in rows if row["style"] == "compound" and "case" in row]
            self.assertTrue(authored_compounds)
            validate_seed_rows(rows)
            for row in authored_compounds:
                seed = next(seed for seed in SEEDS if seed["id"] == row["bank_case_id"])
                self.assertEqual(row["outcome_constraints"], seed_outcome_constraints(seed))

    def test_invalid_rows_ids_case_fields_and_catalogs_fail_without_values(self):
        for rows in (None, [], [None]):
            with self.subTest(rows=rows):
                self.assertTrue(audit_seed_rows(rows))
        self.assertTrue(audit_seed_rows([fixture(), fixture()]))
        row = fixture()
        row["case"]["private-auth-like-field"] = "private-sentinel"
        self.assertNotIn("private", json.dumps(audit_seed_rows([row])))
        for seeds in ([], [SEEDS[0], SEEDS[0]], [{"id": "private-sentinel", "brief": [], "expected": {}}]):
            with self.subTest(seeds=type(seeds).__name__), self.assertRaises(SeedAuditError) as caught:
                audit_seed_rows([fixture()], seeds=seeds)
            self.assertNotIn("private-sentinel", str(caught.exception))

    def test_diagnostics_are_bounded_when_many_rows_fail(self):
        rows = [fixture() for _ in range(120)]
        for row in rows:
            row["case"]["expected"]["outcome"] = "changed-private-value"
        violations = audit_seed_rows(rows)
        self.assertEqual(len(violations), 64)
        self.assertEqual(violations[-1]["code"], "further_violations_omitted")
        self.assertNotIn("changed-private-value", json.dumps(violations))

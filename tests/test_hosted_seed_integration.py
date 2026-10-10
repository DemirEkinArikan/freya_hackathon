"""The hosted boundary must audit evaluator facts before any live side effect."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from benchmarks.generate import KIT
from benchmarks.hosted import evaluate, load_cases, validate_rows
from benchmarks.seed_audit import SeedAuditError, seed_outcome_constraints


def row_from_seed():
    seed = json.loads((KIT / "public-test-set.json").read_text())[0]
    row = {"fixture_id": "integration-fixture", "bank_case_id": seed["id"], "brief": seed["brief"],
           "split": "train", "status": "materialized", "style": "plain", "case": deepcopy(seed)}
    row["case"]["id"] = row["fixture_id"]
    return seed, row


class HostedSeedIntegrationTests(unittest.TestCase):
    def test_changed_seed_facts_are_rejected_before_personas_sessions_or_simulations(self):
        for field in ("identity", "products", "variables", "expected"):
            _, row = row_from_seed()
            if field == "identity":
                row["case"][field]["date_of_birth"] = "private-mutated-answer"
            elif field == "products":
                row["case"][field][0]["past_due"] += 1
            elif field == "variables":
                row["case"][field]["now"] = "2099-01-01T12:00"
            else:
                row["case"][field]["promises"][0]["amount"] += 1
            backend = Mock()
            with self.subTest(field=field), self.assertRaises(SeedAuditError) as rejected:
                evaluate(backend, "agent", [row], reps=1)
            backend.create_personas.assert_not_called()
            backend.practice_session.assert_not_called()
            backend.simulate.assert_not_called()
            self.assertNotIn("private-mutated-answer", str(rejected.exception))

    def test_manifest_loading_runs_the_same_seed_audit(self):
        _, row = row_from_seed()
        row["case"]["expected"]["outcome"] = "mutated"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hosted.json"
            path.write_text(json.dumps({"kind": "hosted", "cases": [row]}))
            with self.assertRaises(SeedAuditError):
                load_cases(path)

    def test_materialized_status_cannot_hide_changed_case_facts(self):
        _, row = row_from_seed()
        row.update(status="reserved_until_candidate_freeze", split="holdout")
        row["case"]["identity"]["phone_last4"] = "invalid-answer"
        with self.assertRaises(SeedAuditError):
            validate_rows([row])

    def test_reserved_metadata_loads_but_cannot_execute_without_materialization(self):
        _, row = row_from_seed()
        row.pop("case")
        row.update(status="reserved_until_candidate_freeze", split="holdout")
        validate_rows([row])
        backend = Mock()
        with self.assertRaisesRegex(ValueError, "held-out slots are reserved"):
            evaluate(backend, "agent", [row])
        backend.create_personas.assert_not_called()

    def test_compounds_require_exact_seed_outcome_constraints(self):
        seed, row = row_from_seed()
        row.update(style="compound", outcome_constraints=seed_outcome_constraints(seed))
        validate_rows([row])
        for constraints in (None, {"preserve_seed_outcomes": True, "seed_expected_sha256": "wrong"}):
            invalid = deepcopy(row)
            if constraints is None:
                invalid.pop("outcome_constraints")
            else:
                invalid["outcome_constraints"] = constraints
            with self.subTest(constraints=constraints), self.assertRaises(SeedAuditError):
                validate_rows([invalid])

    def test_original_seed_checks_cannot_be_removed(self):
        _, row = row_from_seed()
        row["case"]["checks"] = []
        with self.assertRaises(SeedAuditError):
            validate_rows([row])

    def test_original_public_metadata_and_supplemental_checks_remain_allowed(self):
        _, row = row_from_seed()
        row["case"]["checks"].append("Supplemental evaluator check")
        validate_rows([row])

if __name__ == "__main__":
    unittest.main()

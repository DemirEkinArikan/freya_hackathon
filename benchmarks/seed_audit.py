"""Offline audit of hosted fixture facts against the pinned official bank seeds.

Caller wording can vary. Identity answers, products, call variables, injected
faults and the complete expected ledger cannot. This is evaluator-only code;
none of these seeds, oracles or diagnostics belongs in a generator prompt.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from builder.safety import canonical_json, digest, read_json


OFFICIAL_SEEDS = Path(__file__).resolve().parents[1] / "build-the-builder/public-test-set.json"
WORDING_FIELDS = frozenset({"id", "title", "style", "description", "script", "persona_prompt", "persona_wording"})
SAFE_FACT_FIELDS = frozenset({"brief", "family", "level", "identity", "products", "variables", "faults"})
LEDGER_FIELDS = ("outcome", "reason", "promises", "notes", "callbacks", "transfers")
RESERVED = "reserved_until_candidate_freeze"


class SeedAuditError(ValueError):
    """Value-free, bounded violations suitable for an evaluator handoff."""

    def __init__(self, violations: list[dict]):
        self.violations = tuple(violations)
        super().__init__("Hosted seed audit failed: " + "; ".join(
            f"row {item['row']}: {item['field']} ({item['code']})" for item in violations))


def seed_outcome_constraints(seed: Mapping[str, Any]) -> dict:
    """Bind a compound variation to a loaded official seed's immutable oracle."""
    return {"preserve_seed_outcomes": True, "seed_expected_sha256": digest(seed["expected"])}


def _official_catalog(seeds=None) -> dict[str, dict]:
    # The optional argument is an offline test seam; production callers omit it
    # and never supply a fixture/candidate's self-declared expected records.
    seeds = read_json(OFFICIAL_SEEDS) if seeds is None else seeds
    if not isinstance(seeds, (list, tuple)) or not seeds:
        raise SeedAuditError([{"row": None, "field": "official_seeds", "code": "invalid_seed_catalog"}])
    catalog = {}
    for seed in seeds:
        if (not isinstance(seed, dict) or not isinstance(seed.get("id"), str) or not seed["id"]
                or seed["id"] in catalog or not isinstance(seed.get("brief"), str) or seed["brief"] not in {"A", "B"}
                or not isinstance(seed.get("expected"), dict)
                or set(seed["expected"]) != set(LEDGER_FIELDS)):
            raise SeedAuditError([{"row": None, "field": "official_seeds", "code": "invalid_seed_catalog"}])
        catalog[seed["id"]] = seed
    return catalog


def _same(left: Any, right: Any) -> bool:
    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def audit_seed_rows(rows, *, seeds=None) -> list[dict]:
    """Return at most 64 value-free issues; an empty list means facts matched.

    A reserved row is exempt only while it has no authored case and belongs to
    the holdout split. Materialized-after-freeze rows are audited identically to
    development rows. Existing official judge checks must remain in order;
    supplementary checks may be added. Arbitrary prose semantics are not proven.
    """
    catalog = _official_catalog(seeds)
    if not isinstance(rows, (list, tuple)) or not rows:
        return [{"row": None, "field": "rows", "code": "invalid_rows"}]
    violations = []

    def issue(index, field, code):
        if len(violations) < 63:
            violations.append({"row": index, "field": field, "code": code})
        elif len(violations) == 63:
            violations.append({"row": None, "field": "rows", "code": "further_violations_omitted"})

    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issue(index, "row", "invalid_row")
            continue
        identifier = row.get("fixture_id")
        if not isinstance(identifier, str) or not identifier.strip():
            issue(index, "fixture_id", "invalid_fixture_id")
        elif identifier in seen:
            issue(index, "fixture_id", "duplicate_fixture_id")
        else:
            seen.add(identifier)
        bank_id = row.get("bank_case_id")
        seed = catalog.get(bank_id) if isinstance(bank_id, str) else None
        if seed is None:
            issue(index, "bank_case_id", "unknown_official_seed")
            continue
        if row.get("brief") != seed["brief"]:
            issue(index, "brief", "seed_brief_mismatch")
        if row.get("status") == RESERVED and "case" not in row:
            if row.get("split") != "holdout":
                issue(index, "split", "unmaterialized_development_case")
            reservation = row.get("reservation")
            if "reservation" in row and (not isinstance(reservation, dict) or reservation.get("seed") != bank_id):
                issue(index, "reservation", "seed_reservation_mismatch")
            continue
        case = row.get("case")
        if not isinstance(case, dict):
            issue(index, "case", "missing_authored_case")
            continue
        if any(not isinstance(field, str) for field in case):
            issue(index, "case.fields", "invalid_case_fields")
            continue
        if case.get("id") != identifier:
            issue(index, "case.id", "fixture_id_mismatch")
        for field in WORDING_FIELDS - {"id"}:
            if field in case and not isinstance(case[field], str):
                issue(index, "case.wording", "invalid_wording_field")
        immutable_fields = (set(seed) | set(case)) - WORDING_FIELDS - {"expected", "checks"}
        for field in sorted(immutable_fields):
            if field not in seed or field not in case or not _same(case[field], seed[field]):
                # Labels come from a fixed allowlist, never a fixture's contents.
                label = "case." + field if field in SAFE_FACT_FIELDS else "case.fields"
                issue(index, label, "seed_fact_mismatch")
        expected = case.get("expected")
        if not isinstance(expected, dict) or set(expected) != set(LEDGER_FIELDS):
            issue(index, "case.expected", "oracle_shape_mismatch")
        else:
            for field in LEDGER_FIELDS:
                if not _same(expected[field], seed["expected"][field]):
                    issue(index, "case.expected." + field, "official_oracle_mismatch")
        checks = case.get("checks")
        official_checks = seed.get("checks", [])
        if (not isinstance(checks, list) or any(not isinstance(check, str) for check in checks)
                or checks[:len(official_checks)] != official_checks):
            issue(index, "case.checks", "official_judge_checks_changed")
        if row.get("style") == "compound":
            constraints = row.get("outcome_constraints")
            if not _same(constraints, seed_outcome_constraints(seed)):
                issue(index, "outcome_constraints", "compound_requires_immutable_seed_outcome")
    return violations


def validate_seed_rows(rows, *, seeds=None) -> None:
    """Reject unsafe/mismatched evaluator fixtures before hosted mutation."""
    violations = audit_seed_rows(rows, seeds=seeds)
    if violations:
        raise SeedAuditError(violations)

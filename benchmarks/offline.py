"""Inspect/check contracts and helper outputs; this never calls a model or Freya."""
import argparse
import json
from pathlib import Path


def check_manifest(manifest):
    cases = manifest["cases"]
    ids = [c["fixture_id"] for c in cases]
    errors = []
    if len(ids) != len(set(ids)): errors.append("duplicate fixture IDs")
    if len(cases) != 120: errors.append("expected exactly 120 cases")
    for split, expected in manifest["split_counts"].items():
        if sum(c["split"] == split for c in cases) != expected:
            errors.append("incorrect count for " + split)
    return errors


def check_case(case):
    if case["status"].startswith("reserved"):
        return {"fixture_id": case["fixture_id"], "status": "reserved", "pass": None}
    from builder.policy import product_deadline, product_floor_cents, product_band, validate_policy
    errors = validate_policy(case["contract_expected"], source_documents=case["contract_expected"]["source_documents"])
    for product in case["products"]:
        key = product["product_id"]
        day = product_deadline(product, case["variables"]["now"], case["contract_expected"])
        day = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if day != case["helper_expected"]["deadlines"][key]: errors.append("deadline mismatch for " + key)
        if product_floor_cents(product, case["contract_expected"]) != round(case["helper_expected"]["floors"][key] * 100): errors.append("floor mismatch for " + key)
        if product_band(product, case["contract_expected"]) != case["helper_expected"]["bands"][key]: errors.append("band mismatch for " + key)
    return {"fixture_id": case["fixture_id"], "status": "helper_check_only", "pass": not errors, "failures": errors}


def compare_contract(candidate, expected):
    from builder.policy import rule_value
    if isinstance(candidate, str): candidate = json.loads(candidate)
    if "policy_json" in candidate:
        candidate = json.loads(candidate["policy_json"])
    wanted = {r["kind"]: r["value"] for r in expected["rules"]}
    mismatches = []
    for kind, value in wanted.items():
        try:
            if rule_value(candidate, kind, object()) != value: mismatches.append(kind)
        except (ValueError, KeyError, TypeError): mismatches.append(kind)
    return mismatches


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default=str(Path(__file__).parent / "fixtures/offline.json"))
    ap.add_argument("--split", choices=("train", "validation", "holdout", "all"), default="all")
    ap.add_argument("--check-helpers", action="store_true")
    ap.add_argument("--candidate-contract")
    ap.add_argument("--profile")
    ap.add_argument("--ledgers", help="JSON object mapping fixture_id to observed normalized ledger")
    args = ap.parse_args()
    manifest = json.loads(Path(args.cases).read_text())
    errors = check_manifest(manifest)
    selected = [c for c in manifest["cases"] if args.split == "all" or c["split"] == args.split]
    result = {"count": len(selected), "materialized": sum(not c["status"].startswith("reserved") for c in selected),
              "reserved": sum(c["status"].startswith("reserved") for c in selected), "errors": errors,
              "claim": "Contract/helper checks only. No conversation, model or API executed."}
    if args.check_helpers: result["rows"] = [check_case(c) for c in selected]
    if args.candidate_contract:
        if not args.profile: ap.error("--profile is required with --candidate-contract")
        expected = next(p["contract_expected"] for p in manifest["profiles"] if p["profile_id"] == args.profile)
        result["contract_mismatches"] = compare_contract(json.loads(Path(args.candidate_contract).read_text()), expected)
    if args.ledgers:
        from .scoring import score_ledger
        observed = json.loads(Path(args.ledgers).read_text())
        result["ledger_rows"] = []
        for case in selected:
            if case["status"].startswith("reserved"): continue
            rules = {r["kind"]: r["value"] for r in case["contract_expected"]["rules"]}
            verdict = score_ledger(case["expected"], observed.get(case["fixture_id"], {}),
                                   identity_fields=rules["identity_fields"], wrong_limit=rules["identity_wrong_limit"],
                                   brief="B" if len(case["products"]) > 1 else "A", wire_codes=case["wire_codes"])
            result["ledger_rows"].append({"fixture_id": case["fixture_id"], **verdict})
    print(json.dumps(result, indent=2))
    if errors or result.get("contract_mismatches") or any(r.get("pass") is False for r in result.get("rows", []) + result.get("ledger_rows", [])):
        raise SystemExit(1)


if __name__ == "__main__": main()

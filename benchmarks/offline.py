"""Inspect/check contracts and helper outputs; this never calls a model or Freya."""
import argparse
from copy import deepcopy
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


def expected_policy_ir(expected):
    """Strip exactly the known evaluator metadata from trusted expectations.

    Do not use this adapter on a candidate. Any other unexpected field reaches
    the strict compiler and fails rather than being silently filtered out.
    """
    policy = deepcopy(expected)
    policy.pop("source_documents", None)
    return policy


def controller_sources(profile):
    """Source inventory comes from supplied documents, never candidate claims."""
    from builder.safety import digest
    return [{"id": f"brief-{index}", "file": sid + ".md", "text": text, "sha256": digest(text)}
            for index, (sid, text) in enumerate(profile["policy_documents"].items(), 1)]


def compile_expected(expected, profile):
    from builder.contract import compile_policy_contract, PolicyContractError
    from builder.safety import canonical_json
    sources = controller_sources(profile)
    if ("source_documents" in expected and
            canonical_json(expected["source_documents"]) != canonical_json({source["id"]: source["text"] for source in sources})):
        raise PolicyContractError(["evaluator source documents differ from supplied profile documents"])
    return compile_policy_contract(expected_policy_ir(expected), sources, profile["openapi"])


def value_mismatches(compiled, profile):
    """Compare to the independently captured current values, not resolved IR."""
    from builder.safety import canonical_json
    wanted = profile["effective_values_expected"]
    active = compiled["active_rules"]
    mismatches = sorted(set(active) ^ set(wanted))
    for kind in sorted(set(active) & set(wanted)):
        if canonical_json(active[kind]["value"]) != canonical_json(wanted[kind]):
            mismatches.append(kind)
    if compiled["goal"] != profile["contract_expected"]["goal"]:
        mismatches.append("goal")
    if sorted(operation["name"] for operation in compiled["permitted_operations"]) != sorted(profile["contract_expected"]["allowed_actions"]):
        mismatches.append("allowed_actions")
    return sorted(mismatches)


def check_case(case, profile=None):
    if case["status"].startswith("reserved"):
        return {"fixture_id": case["fixture_id"], "status": "reserved", "pass": None}
    from builder.contract import PolicyContractError
    from builder.policy import product_deadline, product_floor_cents, product_band, money_to_cents
    errors = []
    if profile is None or profile["profile_id"] != case["profile_id"]:
        errors.append("materialized case requires its independently supplied profile")
    else:
        try:
            compiled = compile_expected(case["contract_expected"], profile)
            errors.extend("effective value mismatch for " + kind for kind in value_mismatches(compiled, profile))
        except PolicyContractError as exc:
            errors.extend(exc.errors)
    if errors:
        return {"fixture_id": case["fixture_id"], "status": "contract_and_helper_check_only", "pass": False, "failures": errors}
    policy = expected_policy_ir(case["contract_expected"])
    for product in case["products"]:
        key = product["product_id"]
        day = product_deadline(product, case["variables"]["now"], policy)
        day = day.isoformat() if hasattr(day, "isoformat") else str(day)
        if day != case["helper_expected"]["deadlines"][key]: errors.append("deadline mismatch for " + key)
        if product_floor_cents(product, policy) != money_to_cents(case["helper_expected"]["floors"][key]): errors.append("floor mismatch for " + key)
        if product_band(product, policy) != case["helper_expected"]["bands"][key]: errors.append("band mismatch for " + key)
    return {"fixture_id": case["fixture_id"], "status": "contract_and_helper_check_only", "pass": not errors, "failures": errors}


def compare_contract(candidate, profile):
    from builder.contract import compile_policy_contract, PolicyContractError
    try:
        if isinstance(candidate, str): candidate = decode_policy_json(candidate)
        if isinstance(candidate, dict) and "policy_json" in candidate:
            candidate = decode_policy_json(candidate["policy_json"])
        # Candidate metadata is deliberately not stripped. Controller sources
        # and the API are fixed by the profile even if candidate.sources omits it.
        compiled = compile_policy_contract(candidate, controller_sources(profile), profile["openapi"])
        return value_mismatches(compiled, profile)
    except PolicyContractError as exc:
        return ["contract_invalid: " + error for error in exc.errors]
    except (ValueError, TypeError):
        return ["contract_invalid: candidate must contain valid policy JSON"]


def decode_policy_json(text):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate policy JSON key")
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError("nonfinite policy JSON value")
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=nonfinite)


def semantic_projection(contract):
    """Ignore provenance differences, preserving all effective values/API data."""
    return {"version": contract["version"], "goal": contract["goal"],
            "active_values": {kind: deepcopy(rule["value"]) for kind, rule in contract["active_rules"].items()},
            "permitted_operations": deepcopy(contract["permitted_operations"])}


def check_equivalence(profile):
    from builder.contract import PolicyContractError
    from builder.safety import canonical_json
    from .generate import representation_variant
    try:
        clean, raw = (representation_variant(profile, raw) for raw in (False, True))
        contracts = [compile_expected(p["contract_expected"], p) for p in (clean, raw)]
        mismatches = value_mismatches(contracts[0], profile) + value_mismatches(contracts[1], profile)
        semantic_match = canonical_json(semantic_projection(contracts[0])) == canonical_json(semantic_projection(contracts[1]))
        distinct_sources = contracts[0]["sources"] != contracts[1]["sources"] and contracts[0]["sha256"] != contracts[1]["sha256"]
        return {"profile_id": profile["profile_id"], "status": "representation_equivalence_only",
                "pass": semantic_match and distinct_sources and not mismatches,
                "semantic_match": semantic_match, "distinct_source_fingerprints": distinct_sources,
                "failures": sorted(set(mismatches))}
    except PolicyContractError as exc:
        return {"profile_id": profile["profile_id"], "status": "representation_equivalence_only", "pass": False, "failures": list(exc.errors)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default=str(Path(__file__).parent / "fixtures/offline.json"))
    ap.add_argument("--split", choices=("train", "validation", "holdout", "all"), default="all")
    ap.add_argument("--check-helpers", action="store_true")
    ap.add_argument("--check-equivalence", action="store_true", help="Compare clean/raw renderings of known profiles; adds no benchmark cases")
    ap.add_argument("--candidate-contract")
    ap.add_argument("--profile")
    ap.add_argument("--ledgers", help="JSON object mapping fixture_id to observed normalized ledger")
    args = ap.parse_args()
    manifest = json.loads(Path(args.cases).read_text())
    errors = check_manifest(manifest)
    selected = [c for c in manifest["cases"] if args.split == "all" or c["split"] == args.split]
    profiles = {p["profile_id"]: p for p in manifest["profiles"]}
    result = {"count": len(selected), "materialized": sum(not c["status"].startswith("reserved") for c in selected),
              "reserved": sum(c["status"].startswith("reserved") for c in selected), "errors": errors,
              "claim": "Contract/helper checks only. No conversation, model or API executed."}
    if args.check_helpers: result["rows"] = [check_case(c, profiles.get(c["profile_id"])) for c in selected]
    if args.check_equivalence:
        selected_profiles = {c["profile_id"] for c in selected if not c["status"].startswith("reserved")}
        result["equivalence_rows"] = [check_equivalence(profiles[pid]) for pid in sorted(selected_profiles)]
    if args.candidate_contract:
        if not args.profile: ap.error("--profile is required with --candidate-contract")
        if args.profile not in profiles: ap.error("profile is absent or still reserved until candidate freeze")
        result["contract_mismatches"] = compare_contract(Path(args.candidate_contract).read_text(), profiles[args.profile])
    if args.ledgers:
        from .scoring import score_ledger
        observed = json.loads(Path(args.ledgers).read_text())
        result["ledger_rows"] = []
        for case in selected:
            if case["status"].startswith("reserved"): continue
            rules = profiles[case["profile_id"]]["effective_values_expected"]
            verdict = score_ledger(case["expected"], observed.get(case["fixture_id"], {}),
                                   identity_fields=rules["identity_fields"], wrong_limit=rules["identity_wrong_limit"],
                                   brief="B" if len(case["products"]) > 1 else "A", wire_codes=case["wire_codes"])
            result["ledger_rows"].append({"fixture_id": case["fixture_id"], **verdict})
    print(json.dumps(result, indent=2))
    if errors or result.get("contract_mismatches") or any(r.get("pass") is False for r in result.get("rows", []) + result.get("ledger_rows", []) + result.get("equivalence_rows", [])):
        raise SystemExit(1)


if __name__ == "__main__": main()

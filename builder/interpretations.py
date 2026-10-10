"""Auditable boundary interpretations, separate from authoritative source rules.

Structural checks and a separate model review do not prove bank intent. These
records never change execution profiles, API permissions or source quotations.
"""
from __future__ import annotations

import copy
from .safety import canonical_json, digest


FIELDS = {"id", "kind", "issue", "scope", "rule_ids", "preserved_rule_ids", "resolution", "rationale"}


def interpretation_errors(policy):
    records = policy.get("interpretations", [])
    if not isinstance(records, list) or len(records) > 16:
        return ["policy.interpretations must be a list of at most 16 boundary completions"]
    rules = {r.get("id"): r for r in policy.get("rules", [])
             if isinstance(r, dict) and isinstance(r.get("id"), str)}
    superseded = {ident for r in rules.values() for ident in r.get("overrides", [])
                  if isinstance(ident, str)} if all(isinstance(r.get("overrides", []), list) for r in rules.values()) else set()
    active = set(rules) - superseded
    seen, errors = set(), []
    for index, item in enumerate(records):
        prefix = f"policy.interpretations[{index}]"
        if not isinstance(item, dict) or (not FIELDS <= set(item) or set(item) - FIELDS - {"derived_rule_ids"}):
            errors.append(prefix + " requires exactly the documented interpretation fields")
            continue
        for field in ("id", "issue", "scope", "resolution", "rationale"):
            if not isinstance(item[field], str) or not item[field].strip() or len(item[field]) > 6000:
                errors.append(prefix + "." + field + " must be nonempty bounded text")
        ident = item["id"]
        if isinstance(ident, str):
            if ident in seen or ident in rules:
                errors.append(prefix + " has a duplicate interpretation/rule id")
            seen.add(ident)
        derived = item.get("derived_rule_ids", [])
        if (not isinstance(derived, list) or any(not isinstance(ref, str) or ref not in active for ref in derived)):
            errors.append(prefix + ".derived_rule_ids must reference active source-bound rules")
        elif (len(set(derived)) != len(derived)
              or any(rules[ref].get("overrides") for ref in derived)
              or not isinstance(item["rule_ids"], list)
              or any(ref not in item["rule_ids"] for ref in derived)):
            errors.append(prefix + " derived rules must be unique cited rules without overrides")
        if item["kind"] != "boundary_completion":
            errors.append(prefix + " can only complete an underspecified boundary, never override a rule")
        for field in ("rule_ids", "preserved_rule_ids"):
            refs = item[field]
            if (not isinstance(refs, list) or not refs
                    or any(not isinstance(ref, str) or ref not in active for ref in refs)):
                errors.append(prefix + "." + field + " must reference existing active source rules")
            elif len(set(refs)) != len(refs):
                errors.append(prefix + "." + field + " must not repeat rule ids")
        if (isinstance(item["rule_ids"], list) and isinstance(item["preserved_rule_ids"], list)
                and all(isinstance(x, str) for x in item["rule_ids"] + item["preserved_rule_ids"])
                and set(item["preserved_rule_ids"]) - set(item["rule_ids"])):
            errors.append(prefix + " preserved rules must also be cited in rule_ids")
    return errors


def compile_interpretations(policy):
    """Called only after policy validation; retain exact supporting witnesses."""
    rules = {r["id"]: r for r in policy["rules"]}
    return [{**copy.deepcopy(item), "authority": "model_inference",
             "provenance": [{"rule_id": ident, "kind": rules[ident]["kind"],
                             "value": copy.deepcopy(rules[ident]["value"]),
                             "id": rules[ident]["source"]["id"],
                             "quote": rules[ident]["source"]["quote"]}
                            for ident in item["rule_ids"]]}
            for item in policy.get("interpretations", [])]


def review_prompt(policy, sources, openapi):
    instructions = """Review proposed boundary interpretations independently against the supplied original
documents and API. You have no tools, credentials, filesystem or evaluator data.
Do not rewrite policy. Return the required wire object: workflow_json is exactly
"{}"; policy_json serializes {policy_sha256, decisions, unresolved_constraints, observations}.
Copy the supplied policy_sha256. Provide one decision per interpretation:
{id, verdict: "accept" or "reject", preserves_constraints: boolean, rationale: string}.
unresolved_constraints is an array of blocking defects in these proposed
interpretations, not a catalogue of every hypothetical unspecified input. Put
nonblocking scope limits and later validation needs in observations (string array).
Review within each interpretation's stated source-backed scope. Do not mark an
accepted inference unresolved merely because it is not an explicit amendment.
An excluded input population does not require invented in-scope policy. Preserving
unknown persistence and refusing an unsafe write is a guard limitation to report,
not permission to invent recovery or evidence that a valid boundary is contradictory.
Any actual weakening of an explicit rule or unsafe claimed recovery stays blocking.

Accept only a narrow completion of an unspecified boundary with a source-backed
rationale. Distinguish an offer/proposal from a committed operation. Check that derived_rule_ids identify all executable typed values supplied by an
inference and that those values actually encode its resolution. Check every
cited rule's scope and the rest of the brief, not just the proposed rationale.
Never accept a new permission, outcome, service, retry guarantee, or weakened
privacy, identity, consent, amount, calendar, deadline or attempt constraint.
An explicit contradiction or unsupported new commercial term must be rejected.
Missing authoritative wording for a boundary is not automatically a contradiction:
a minimal contextual interpretation can be accepted while remaining an inference.
Check that ordinary source-defined cases are unchanged and the inferred branch
can produce valid proposals before customer acceptance. Reject a completion that
knowingly presents unusable terms when an existing admissible proposal branch can
be minimally extended within the source constraints. Preserve explicit branches
before inferred ones. Do not infer bank intent from evaluator fixtures.
Acceptance is a model review, not proof of organizer approval or conversation success.
"""
    return instructions + "\n\nAUTHORIZED INPUTS:\n" + canonical_json({
        "policy_sha256": digest(policy), "policy": policy, "sources": sources, "openapi": openapi})


def review_errors(review, policy):
    errors = []
    if (not isinstance(review, dict)
            or not {"policy_sha256", "decisions", "unresolved_constraints"} <= set(review)
            or set(review) - {"policy_sha256", "decisions", "unresolved_constraints", "observations"}):
        return ["interpretation review has an invalid response shape"]
    observations = review.get("observations", [])
    if not isinstance(observations, list) or any(not isinstance(x, str) for x in observations):
        errors.append("interpretation review observations must be a string list")
    if review["policy_sha256"] != digest(policy):
        errors.append("interpretation review does not match this exact policy")
    if review["unresolved_constraints"] != []:
        errors.append("interpretation review retains unresolved constraints")
    decisions = review["decisions"]
    if not isinstance(decisions, list):
        return errors + ["interpretation review decisions must be a list"]
    expected = {item["id"] for item in policy.get("interpretations", [])}
    seen = set()
    for item in decisions:
        if not isinstance(item, dict) or set(item) != {"id", "verdict", "preserves_constraints", "rationale"}:
            errors.append("interpretation review decision has an invalid shape")
            continue
        ident = item["id"]
        if not isinstance(ident, str) or ident not in expected or ident in seen:
            errors.append("interpretation review contains unknown or duplicate decisions")
        else:
            seen.add(ident)
        if item["verdict"] != "accept" or item["preserves_constraints"] is not True:
            errors.append("interpretation review rejected a proposed boundary completion")
        if not isinstance(item["rationale"], str) or not item["rationale"].strip():
            errors.append("interpretation review requires a rationale")
    if seen != expected:
        errors.append("interpretation review must cover every proposed boundary completion")
    return errors

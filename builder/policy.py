"""Provenance-aware policy IR and pure, policy-driven banking primitives.

These helpers validate proposed actions and ledger state. They do not interpret
speech, prove compliance of an LLM, or replace platform conversation tests.
Amounts are represented in integer minor units; all clocks come from ``now``.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, TypedDict

from .safety import canonical_json, digest
from .call_variables import CallVariablesError, check_call_variables


class RuleSource(TypedDict):
    id: str
    quote: str


class PolicyRule(TypedDict, total=False):
    id: str
    kind: str
    value: Any
    source: RuleSource
    overrides: list[str]


class PolicyError(ValueError):
    pass


class MissingExecutionRules(PolicyError):
    """Required policy inputs are absent; changing dialogue cannot supply them."""
    def __init__(self, kinds):
        self.missing_kinds = tuple(sorted(set(kinds)))
        super().__init__("required execution rule is missing: " + ", ".join(self.missing_kinds))


_PROFILE_KINDS = {"identity_fields", "identity_wrong_limit", "holidays", "business_weekdays",
                  "band_ranges", "deadline_days", "floor_fields", "callback_window",
                  "note_reasons", "close_reasons", "transfer_reasons", "require_confirmation",
                  "promise_failure_limit", "tool_aliases", "persuasion_limits", "identity_hint_exemptions",
                  "write_retryable_errors", "confirmation_scope", "note_failure_limit", "callback_failure_limit",
                  "transfer_failure_limit", "action_fields", "result_fields", "product_fields"}
_PROFILE_KINDS.update({"wire_codes", "policy_applicability"})
_EXECUTION_ONLY_KINDS = {"nonpersistent_failures", "confirmation_actions", "product_order",
                         "close_precedence", "identity_close", "promise_failure_disposition",
                         "record_notes", "resolve_all_products", "transfer_prerequisites",
                         "promise_readback", "account_disclosure_fields", "identity_close_choices", "call_variables"}

# Interface descriptions contain no bank values. They explain an existing
# rejection; the predicates below remain the authority for accepted shapes.
_SHAPE_GUIDANCE = {
    "promise_failure_resolution": "descriptive extension only: operation is the selected actual API ID; optional api_annotations is either {document: openapi, HTTP-status: {pointer, quote}} with three-digit response-status keys, or a nonempty array of exact {document: openapi, operation: actual API ID, http_status: integer100..599, pointer, quote} rows; statuses unique, witnesses literal and operation/status-scoped, every annotation has a matching operative nonpersistent_failures row; no executable rule or guarantee is inferred from this extension",
    "call_variables": "an optional array of unique ordinary initial call-input variable names, or a nonempty mapping from those names to nonempty text descriptions; each name occurs literally in its source quotation; no target values or defaults; read-only; controller/credential namespaces forbidden; an empty array is allowed",
    "policy_applicability": 'exactly {products: "single"} or {products: "multiple"}; scope must be supplied by the policy source. A sourced single-product scope may omit product_order and resolve_all_products; do not invent either rule to satisfy multiple-product dependencies. Independently supplied rules remain operative',
    "identity_fields": "a nonempty array of unique nonempty strings in factor order",
    "identity_wrong_limit": "a positive integer; booleans are not integers",
    "holidays": "an array of ISO YYYY-MM-DD strings; an empty array is allowed",
    "business_weekdays": "a nonempty array of integers from 0 through 6",
    "band_ranges": "a nonempty array of objects with nonempty name and integer min/max; min <= max, names unique, intervals nonoverlapping",
    "deadline_days": "a nonempty object mapping band names to nonnegative integers",
    "floor_fields": "an object mapping nonempty product-type names to nonempty field names",
    "callback_window": "an object with start/end local HH:MM strings, start <= end; optional allow_today is boolean and next_business_days is a nonnegative integer",
    "note_reasons": "a nonempty array of unique nonempty strings",
    "close_reasons": "a nonempty object mapping outcome names to arrays of nonempty reason strings; arrays may be empty",
    "transfer_reasons": "an object mapping nonempty queue names to nonempty reason strings",
    "require_confirmation": "a boolean, not a string or integer",
    "promise_failure_limit": "a positive integer; booleans are not integers",
    "tool_aliases": "an object mapping nonempty semantic operation names to nonempty API operation names",
    "persuasion_limits": "a nonempty object mapping band names to nonnegative integers",
    "identity_hint_exemptions": "an array of unique nonempty strings; an empty array is allowed",
    "write_retryable_errors": "an array of unique nonempty strings; an empty array is allowed",
    "confirmation_scope": 'the string "each" or "batch"',
    "note_failure_limit": "a positive integer; booleans are not integers",
    "callback_failure_limit": "a positive integer; booleans are not integers",
    "transfer_failure_limit": "a positive integer; booleans are not integers",
    "action_fields": "an object of nonempty string aliases or nonempty nested string-alias objects",
    "result_fields": "an object of nonempty string aliases or nonempty nested string-alias objects",
    "product_fields": "an object of nonempty string aliases or nonempty nested string-alias objects",
    "wire_codes": "an object mapping category names to nonempty string-to-string maps; wire values within each category must be unique",
    "nonpersistent_failures": "an object mapping actual operation names to arrays of unique objects containing http_status (integer 100..599), optional error (nonempty string), and optional api_source containing exactly pointer and quote (nonempty strings); controller execution requires a literal OpenAPI witness scoped to that operation's exact response status",
    "confirmation_actions": 'a unique string array containing only "commit_promises", "commit_transfer", "schedule_callback"; an empty array is allowed',
    "product_order": 'exactly {mode: "account_order"}, or exactly {field: nonempty string, direction: "ascending"/"descending", tie_breaker: "account_order"}',
    "close_precedence": "a nonempty array of unique protected execution outcome meanings (transferred, promise, paid, no_promise) or their explicitly bound wire values, ordered only as supplied by policy. Identity closing outcomes belong in identity_close/identity_close_choices, not this array; preserve their sourced outcomes and reasons there. Do not invent a priority, outcome or account prerequisite for a permitted transfer",
    "identity_close": "an object containing exactly failed, incomplete, unreached; each scope is an object containing outcome (nonempty string) and optional reason (nonempty string)",
    "identity_close_choices": 'a nonempty object with only unreached/incomplete scopes; each scope is a nonempty array of objects containing outcome (nonempty string), optional reason (nonempty string), and requires ("none"/"recorded_callback"/"no_recorded_callback"). Every choice must retain identity_close[scope].outcome and any reason must belong to its sourced close_reasons vocabulary. Successful protected transfer evidence is handled separately; do not invent an identity choice or outcome to stand in for a transfer',
    "promise_failure_disposition": "an object containing exactly reason (nonempty string) and clear_pending (boolean). For selected commit_promises, encode any supplied supported exhaustion consequence here with its sourced note_reasons vocabulary; descriptive extensions alone are not executable. If no consequence is supplied, omit this optional rule rather than invent a disposition; absence remains conservative",
    "record_notes": "a boolean, not a string or integer",
    "resolve_all_products": "a boolean, not a string or integer",
    "transfer_prerequisites": 'a nonempty object mapping queue names to "none", "identity", or "account"',
    "promise_readback": "an object with a nonempty unique product_fields string array excluding customer_id/product_id, and optional value_labels mapping selected fields to string-to-string label maps",
    "account_disclosure_fields": "an array of unique nonempty strings excluding customer_id/product_id; an empty array is allowed",
}


def execution_rule_guidance() -> dict[str, str]:
    """Return detached descriptions of accepted typed interfaces, not values."""
    return {kind: _SHAPE_GUIDANCE[kind] for kind in sorted(_SHAPE_GUIDANCE)}


def _json_type(value: Any) -> str:
    """A fixed type label, never a supplied value or native class name."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "unsupported native value"


def _structured_error(kind: str, value: Any, domain: str) -> str:
    message = (f"{domain} rule {kind} has an invalid structured value: "
               f"{kind}.value expected {_SHAPE_GUIDANCE[kind]}; received {_json_type(value)}")
    if kind == "identity_close" and isinstance(value, dict):
        scopes = {"failed", "incomplete", "unreached"}
        missing = sorted(scopes - set(value))
        if missing:
            message += "; missing required scopes: " + ", ".join(missing)
        if set(value) - scopes:
            message += "; unsupported scope fields are present"
        for scope in ("failed", "incomplete", "unreached"):
            if scope not in value:
                continue
            proposal = value[scope]
            location = "identity_close.value." + scope
            if not isinstance(proposal, dict):
                message += f"; {location} must be an object, received {_json_type(proposal)}"
                continue
            if not isinstance(proposal.get("outcome"), str) or not proposal["outcome"]:
                message += f"; {location}.outcome is required and must be a nonempty string"
            if "reason" in proposal and (not isinstance(proposal["reason"], str) or not proposal["reason"]):
                message += f"; {location}.reason must be a nonempty string when supplied"
            if set(proposal) - {"outcome", "reason"}:
                message += f"; {location} permits only outcome and optional reason"
        message += "; identity_close_choices does not replace the required fixed scope entries"
    # All fragments above are fixed interface labels; supplied keys and values
    # are omitted, keeping repairs bounded and free of fixture/secret literals.
    return message


def _profile_errors(kind: str, value: Any) -> list[str]:
    def strings(item: Any, *, empty: bool = False) -> bool:
        return isinstance(item, list) and (empty or bool(item)) and all(isinstance(x, str) and x for x in item)
    valid = True
    if kind == "policy_applicability":
        valid = (isinstance(value, dict) and set(value) == {"products"}
                 and isinstance(value["products"], str) and value["products"] in {"single", "multiple"})
    elif kind in {"identity_wrong_limit", "promise_failure_limit", "note_failure_limit", "callback_failure_limit", "transfer_failure_limit"}:
        valid = type(value) is int and value > 0
    elif kind in {"identity_fields", "note_reasons"}:
        valid = strings(value) and len(set(value)) == len(value)
    elif kind == "holidays":
        valid = strings(value, empty=True)
        if valid:
            try:
                valid = all(date.fromisoformat(x).isoformat() == x for x in value)
            except ValueError:
                valid = False
    elif kind == "business_weekdays":
        valid = isinstance(value, list) and bool(value) and all(type(x) is int and 0 <= x <= 6 for x in value)
    elif kind == "band_ranges":
        valid = isinstance(value, list) and bool(value)
        if valid:
            valid = all(isinstance(b, dict) and isinstance(b.get("name"), str) and b["name"]
                        and type(b.get("min")) is int and type(b.get("max")) is int and b["min"] <= b["max"] for b in value)
        if valid:
            valid = len({b["name"] for b in value}) == len(value) and all(
                a["max"] < b["min"] or b["max"] < a["min"]
                for i, a in enumerate(value) for b in value[i+1:])
    elif kind == "deadline_days":
        valid = isinstance(value, dict) and bool(value) and all(isinstance(k, str) and k and type(v) is int and v >= 0 for k, v in value.items())
    elif kind in {"floor_fields", "transfer_reasons", "tool_aliases"}:
        valid = isinstance(value, dict) and all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items())
    elif kind == "close_reasons":
        valid = isinstance(value, dict) and bool(value) and all(isinstance(k, str) and k and strings(v, empty=True) for k, v in value.items())
    elif kind == "require_confirmation":
        valid = type(value) is bool
    elif kind == "persuasion_limits":
        valid = isinstance(value, dict) and bool(value) and all(isinstance(k, str) and k and type(v) is int and v >= 0 for k, v in value.items())
    elif kind in {"identity_hint_exemptions", "write_retryable_errors"}:
        valid = strings(value, empty=True) and len(set(value)) == len(value)
    elif kind == "confirmation_scope":
        valid = isinstance(value, str) and value in {"each", "batch"}
    elif kind in {"action_fields", "result_fields", "product_fields"}:
        valid = isinstance(value, dict) and all(isinstance(k, str) and k and
                    ((isinstance(v, str) and bool(v)) or (isinstance(v, dict) and bool(v) and
                     all(isinstance(x, str) and x and isinstance(y, str) and y for x, y in v.items()))) for k, v in value.items())
    elif kind == "wire_codes":
        # Other bank goals may add typed code categories. Preserve them as data;
        # the action-binding compiler interprets only its four known categories.
        valid = isinstance(value, dict) and all(isinstance(k, str) and k
                    and isinstance(v, dict) and bool(v) and all(isinstance(x, str) and x and
                    isinstance(y, str) and y for x, y in v.items()) and len(set(v.values())) == len(v)
                    for k, v in value.items())
    elif kind == "callback_window":
        valid = isinstance(value, dict) and isinstance(value.get("start"), str) and isinstance(value.get("end"), str)
        if valid:
            try:
                first, last = time.fromisoformat(value["start"]), time.fromisoformat(value["end"])
                valid = first <= last and first.strftime("%H:%M") == value["start"] and last.strftime("%H:%M") == value["end"]
            except ValueError:
                valid = False
        if valid:
            valid = type(value.get("allow_today", False)) is bool and type(value.get("next_business_days", 0)) is int and value.get("next_business_days", 0) >= 0
    return [] if valid else [_structured_error(kind, value, "policy")]


def _execution_errors(kind: str, value: Any) -> list[str]:
    """Additional guard inputs are typed here; legacy extension IR remains data."""
    if kind not in _EXECUTION_ONLY_KINDS:
        return _profile_errors(kind, value)
    def text(item: Any) -> bool:
        return isinstance(item, str) and bool(item)
    if kind == "call_variables":
        try:
            check_call_variables(value)
            valid = True
        except CallVariablesError:
            valid = False
    elif kind == "account_disclosure_fields":
        valid = (isinstance(value, list) and all(text(item) for item in value)
                 and len(set(value)) == len(value) and not set(value) & {"customer_id", "product_id"})
    elif kind in {"record_notes", "resolve_all_products"}:
        valid = type(value) is bool
    elif kind in {"confirmation_actions", "close_precedence"}:
        valid = (isinstance(value, list) and (kind == "confirmation_actions" or bool(value))
                 and all(text(item) for item in value) and len(set(value)) == len(value))
        if valid and kind == "confirmation_actions":
            valid = set(value) <= {"commit_promises", "commit_transfer", "schedule_callback"}
    elif kind == "product_order":
        valid = (isinstance(value, dict) and (value == {"mode": "account_order"}
                 or (set(value) == {"field", "direction", "tie_breaker"}
                     and text(value["field"]) and value["direction"] in ("ascending", "descending")
                     and value["tie_breaker"] == "account_order")))
    elif kind == "identity_close_choices":
        valid = (isinstance(value, dict) and bool(value) and set(value) <= {"unreached", "incomplete"}
                 and all(isinstance(choices, list) and bool(choices) for choices in value.values()))
        if valid:
            valid = all(isinstance(choice, dict) and set(choice) <= {"outcome", "reason", "requires"}
                        and text(choice.get("outcome"))
                        and choice.get("requires") in ("none", "recorded_callback", "no_recorded_callback")
                        and ("reason" not in choice or text(choice["reason"]))
                        for choices in value.values() for choice in choices)
    elif kind == "identity_close":
        valid = (isinstance(value, dict) and set(value) == {"failed", "incomplete", "unreached"}
                 and all(isinstance(proposal, dict) and set(proposal) <= {"outcome", "reason"}
                         and text(proposal.get("outcome")) and ("reason" not in proposal or text(proposal["reason"]))
                         for proposal in value.values()))
    elif kind == "promise_failure_disposition":
        valid = (isinstance(value, dict) and set(value) == {"reason", "clear_pending"}
                 and text(value["reason"]) and type(value["clear_pending"]) is bool)
    elif kind == "transfer_prerequisites":
        valid = (isinstance(value, dict) and bool(value)
                 and all(text(queue) and prerequisite in ("none", "identity", "account")
                         for queue, prerequisite in value.items()))
    elif kind == "promise_readback":
        valid = (isinstance(value, dict) and set(value) <= {"product_fields", "value_labels"}
                 and isinstance(value.get("product_fields"), list) and bool(value["product_fields"])
                 and all(text(field) and field not in {"customer_id", "product_id"}
                         for field in value["product_fields"])
                 and len(set(value["product_fields"])) == len(value["product_fields"])
                 and isinstance(value.get("value_labels", {}), dict))
        if valid:
            valid = all(text(field) and field in value["product_fields"] and isinstance(labels, dict)
                        and all(text(wire) and text(label) for wire, label in labels.items())
                        for field, labels in value.get("value_labels", {}).items())
    elif kind == "nonpersistent_failures":  # Exact trusted pairs; no free prose predicates.
        valid = isinstance(value, dict)
        if valid:
            for operation, failures in value.items():
                if (not text(operation) or not isinstance(failures, list)
                        or any(not isinstance(failure, dict) or set(failure) - {"http_status", "error", "api_source"}
                               or type(failure.get("http_status")) is not int or not 100 <= failure["http_status"] <= 599
                               or ("error" in failure and not text(failure["error"]))
                               or ("api_source" in failure and
                                   (not isinstance(failure["api_source"], dict)
                                    or set(failure["api_source"]) != {"pointer", "quote"}
                                    or not text(failure["api_source"]["pointer"])
                                    or not text(failure["api_source"]["quote"]))) for failure in failures)
                        or len({canonical_json(failure) for failure in failures}) != len(failures)):
                    valid = False
                    break
    return [] if valid else [_structured_error(kind, value, "execution")]


def validate_policy(policy: Any, source_documents: Mapping[str, str] | None = None,
                    *, strict: bool = False) -> list[str]:
    """Check IR/provenance, never semantic correctness of quoted rules.

    ``strict`` rejects legacy/untyped shapes and top-level rule-value bypasses.
    Full controller source inventory and coverage belong to contract compilation.
    Pure helper profiles are intentionally supported by ``rule_value`` separately.
    """
    errors: list[str] = []
    if not isinstance(policy, dict):
        return ["policy must be an object"]
    if type(policy.get("version", 1)) is not int or policy.get("version", 1) != 1 or (strict and "version" not in policy):
        errors.append("policy.version must be 1")
    if strict and set(policy) - {"version", "goal", "allowed_actions", "sources", "rules", "unsupported_constraints"}:
        errors.append("policy contains unsupported top-level fields; rule values belong in rules")
    if not isinstance(policy.get("goal"), str) or not policy["goal"].strip():
        errors.append("policy.goal must be a nonempty string")
    actions = policy.get("allowed_actions")
    if not isinstance(actions, list) or any(not isinstance(a, str) or not a for a in actions):
        errors.append("policy.allowed_actions must be a list of action names")
    elif len(set(actions)) != len(actions):
        errors.append("policy.allowed_actions must be unique")
    sources = policy.get("sources", [])
    source_ids: set[str] = set()
    source_files: set[str] = set()
    if not isinstance(sources, list) or (strict and not sources):
        errors.append("policy.sources must be a list")
        sources = []
    for i, source in enumerate(sources):
        if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not source.get("id"):
            errors.append(f"policy.sources[{i}] requires id")
            continue
        if source["id"] in source_ids:
            errors.append(f"policy.sources[{i}] has duplicate id")
        source_ids.add(source["id"])
        if not isinstance(source.get("file"), str) or not source["file"]:
            errors.append(f"policy.sources[{i}] requires file")
        else:
            source_files.add(source["file"])
        if strict and "sha256" not in source:
            errors.append(f"policy.sources[{i}] requires sha256")
        if "sha256" in source and (not isinstance(source["sha256"], str)
                                    or len(source["sha256"]) != 64
                                    or any(c not in "0123456789abcdef" for c in source["sha256"])):
            errors.append(f"policy.sources[{i}].sha256 must be lowercase SHA-256")
        elif "sha256" in source and source_documents is not None and source["id"] in source_documents:
            # Match the controller's canonical JSON fingerprint convention.
            actual = digest(source_documents[source["id"]])
            if source["sha256"] != actual:
                errors.append(f"policy.sources[{i}].sha256 differs from the supplied source")
    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        return errors + ["policy.rules must be a nonempty list"]
    ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for i, rule in enumerate(rules):
        prefix = f"policy.rules[{i}]"
        if not isinstance(rule, dict):
            errors.append(prefix + " must be an object")
            continue
        ident = rule.get("id")
        if not isinstance(ident, str) or not ident:
            errors.append(prefix + " requires id")
        elif ident in ids:
            errors.append(prefix + " has duplicate id")
        else:
            ids.add(ident)
            by_id[ident] = rule
        if strict and set(rule) - {"id", "kind", "value", "source", "overrides"}:
            errors.append(prefix + " contains unsupported rule fields")
        if "kind" in rule:
            if not isinstance(rule["kind"], str) or not rule["kind"]:
                errors.append(prefix + ".kind must be nonempty")
            if "value" not in rule:
                errors.append(prefix + " requires value")
        elif strict or not isinstance(rule.get("text"), str) or not rule.get("text"):
            errors.append(prefix + " requires kind/value or text")
        if strict and "source" not in rule:
            errors.append(prefix + " requires typed source provenance")
        provenance = [rule["source"]] if "source" in rule else rule.get("sources", [])
        if not isinstance(provenance, list) or not provenance:
            errors.append(prefix + " requires source provenance")
            continue
        for source in provenance:
            if not isinstance(source, dict):
                errors.append(prefix + " has malformed source provenance")
                continue
            sid = source.get("id", source.get("file"))
            if not isinstance(sid, str) or not sid or ("source" in rule and sid not in source_ids) or (source_ids and sid not in source_ids and sid not in source_files):
                errors.append(prefix + " refers to an unknown source")
            quote = source.get("quote")
            if not isinstance(quote, str) or not quote.strip():
                errors.append(prefix + " requires a literal source quote")
            elif source_documents is not None:
                document = source_documents.get(sid) if isinstance(sid, str) else None
                if document is None or quote not in document:
                    errors.append(prefix + " quote is not present in the supplied source")
    for ident, rule in by_id.items():
        overrides = rule.get("overrides", [])
        if not isinstance(overrides, list) or any(not isinstance(x, str) for x in overrides):
            errors.append(f"rule {ident} overrides must be a list of rule ids")
            continue
        if any(x not in ids or x == ident for x in overrides):
            errors.append(f"rule {ident} has an unknown or self override")
        if len(set(overrides)) != len(overrides):
            errors.append(f"rule {ident} repeats an override")
        if any(x in by_id and by_id[x].get("kind") != rule.get("kind") for x in overrides):
            errors.append(f"rule {ident} has a cross-kind override; semantic replacement is unsupported")
    successors = {ident: [child for child in rule.get("overrides", [])
                          if isinstance(child, str) and child in by_id]
                  if isinstance(rule.get("overrides", []), list) else []
                  for ident, rule in by_id.items()}
    indegree = {ident: 0 for ident in by_id}
    for children in successors.values():
        for child in children:
            indegree[child] += 1
    ready = [ident for ident, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        ident = ready.pop()
        visited += 1
        for child in successors[ident]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(by_id):
        errors.append("policy override graph contains a cycle")
    kinds = {rule["kind"] for rule in by_id.values() if isinstance(rule.get("kind"), str) and rule.get("kind")}
    if kinds & set(policy):
        errors.append("policy top-level values cannot shadow quoted rule kinds")
    # Resolve every extension kind, not only built-in helper concepts. JSON typed
    # equality distinguishes true/1, null/missing, and other Python coercions.
    active_by_kind = _active_rules(policy)
    for kind in sorted(kinds):
        try:
            active = active_by_kind.get(kind, [])
            values = [canonical_json(rule["value"]) for rule in active if "value" in rule]
            if len(set(values)) > 1:
                errors.append(f"policy rules for {kind} have conflicting active values")
            if active and kind in _PROFILE_KINDS:
                value = active[0].get("value")
                errors.extend(_profile_errors(kind, value))
        except (PolicyError, TypeError, ValueError):
            errors.append(f"policy rules for {kind} are conflicting or malformed")
    unsupported = policy.get("unsupported_constraints", [])
    if not isinstance(unsupported, list) or any(not isinstance(x, str) for x in unsupported):
        errors.append("policy.unsupported_constraints must be a string list")
    try:
        canonical_json(policy)
    except (TypeError, ValueError):
        errors.append("policy must contain finite JSON values")
    return errors


def _active_rules(policy: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Select retained active rules; callers validate override graphs first."""
    rules = [rule for rule in policy.get("rules", []) if isinstance(rule, dict)]
    overridden = {ident for rule in rules for ident in rule.get("overrides", [])
                  if isinstance(ident, str)} if all(isinstance(rule.get("overrides", []), list) for rule in rules) else set()
    active: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        if isinstance(rule.get("kind"), str) and isinstance(rule.get("id"), str) and rule["id"] not in overridden:
            active.setdefault(rule["kind"], []).append(rule)
    return active


def rule_value(policy: Mapping[str, Any], kind: str, default: Any = None) -> Any:
    """Resolve structured rules after explicit overrides; profiles can use keys."""
    if kind in policy:
        return policy[kind]
    rules = [r for r in policy.get("rules", []) if isinstance(r, dict)]
    overridden = {ident for r in rules for ident in r.get("overrides", [])
                  if isinstance(ident, str)} if all(isinstance(r.get("overrides", []), list) for r in rules) else set()
    values = [r.get("value") for r in rules if r.get("kind") == kind and r.get("id") not in overridden]
    if not values:
        return default
    if any(canonical_json(value) != canonical_json(values[0]) for value in values[1:]):
        raise PolicyError(f"conflicting active policy rules for {kind}")
    return values[0]


def money_to_cents(value: Any) -> int:
    if isinstance(value, bool):
        raise PolicyError("amount must be a finite decimal number")
    try:
        amount = Decimal(str(value))
        cents = amount * 100
    except (InvalidOperation, ValueError, TypeError):
        raise PolicyError("amount must be a finite decimal number") from None
    if not amount.is_finite() or cents != cents.to_integral_value():
        raise PolicyError("amount must have at most two decimal places")
    return int(cents)


def _date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            raise PolicyError("date/now must be ISO formatted") from None


def is_business_day(day: date | str, holidays: Any = (), weekdays: Any = (0, 1, 2, 3, 4)) -> bool:
    return _date(day).weekday() in weekdays and _date(day) not in {_date(x) for x in holidays}


def business_deadline(now: date | datetime | str, days: int, holidays: Any = (),
                      weekdays: Any = (0, 1, 2, 3, 4)) -> date:
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise PolicyError("business-day count must be a nonnegative integer")
    if not weekdays or any(type(x) is not int or not 0 <= x <= 6 for x in weekdays):
        raise PolicyError("business_weekdays must include at least one valid weekday")
    current = _date(now)
    remaining = days
    holiday_dates = {_date(x) for x in holidays}
    while remaining:
        current += timedelta(days=1)
        if current.weekday() in weekdays and current not in holiday_dates:
            remaining -= 1
    return current


def next_business_day(day: date | str, holidays: Any = (), weekdays: Any = (0, 1, 2, 3, 4)) -> date:
    return business_deadline(day, 1, holidays, weekdays)


def named_weekday(now: date | datetime | str, weekday: int, *, next_week: bool) -> date:
    if type(weekday) is not int or not 0 <= weekday <= 6:
        raise PolicyError("weekday must be 0..6")
    today = _date(now)
    return today - timedelta(days=today.weekday()) + timedelta(days=weekday + (7 if next_week else 0))


def product_by_id(products: list[dict[str, Any]], product_id: str) -> dict[str, Any]:
    matches = [product for product in products if product.get("product_id") == product_id]
    if len(matches) != 1:
        raise PolicyError("product_id must identify exactly one returned product")
    return matches[0]


def product_band(product: Mapping[str, Any], policy: Mapping[str, Any]) -> str:
    days = product.get("days_late")
    if type(days) is not int:
        raise PolicyError("days_late must be an integer")
    bands = rule_value(policy, "band_ranges", [])
    matches = [band["name"] for band in bands if band["min"] <= days <= band["max"]]
    if len(matches) != 1:
        raise PolicyError("product does not match exactly one policy band")
    return matches[0]


def product_floor_cents(product: Mapping[str, Any], policy: Mapping[str, Any]) -> int:
    fields = rule_value(policy, "floor_fields", {})
    field = fields.get(product.get("type"))
    if not field or field not in product:
        raise PolicyError("policy has no floor field for this product")
    floor = money_to_cents(product[field])
    if floor < 0:
        raise PolicyError("product floor must not be negative")
    return floor


def product_deadline(product: Mapping[str, Any], now: Any, policy: Mapping[str, Any]) -> date:
    days = rule_value(policy, "deadline_days", {}).get(product_band(product, policy))
    if days is None:
        raise PolicyError("policy has no deadline for this band")
    return business_deadline(now, days, rule_value(policy, "holidays", []),
                             rule_value(policy, "business_weekdays", (0, 1, 2, 3, 4)))


def validate_commitment(product: Mapping[str, Any], amount: Any, day: Any, now: Any,
                        policy: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        cents = money_to_cents(amount)
        if not product_floor_cents(product, policy) <= cents <= money_to_cents(product["past_due"]):
            errors.append("amount is outside this product's permitted range")
        promise_day = _date(day)
        if promise_day < _date(now):
            errors.append("promise date is before supplied now")
        if not is_business_day(promise_day, rule_value(policy, "holidays", []),
                               rule_value(policy, "business_weekdays", (0, 1, 2, 3, 4))):
            errors.append("promise date is not a business day")
        if promise_day > product_deadline(product, now, policy):
            errors.append("promise date exceeds this product's deadline")
    except (PolicyError, KeyError, TypeError, ValueError):
        errors.append("commitment or policy inputs are invalid/incomplete")
    return errors


def validate_callback(day: Any, at: Any, now: Any, policy: Mapping[str, Any]) -> list[str]:
    try:
        current = datetime.fromisoformat(str(now).replace("Z", "+00:00")) if not isinstance(now, datetime) else now
        wanted_day = date.fromisoformat(str(day)) if not isinstance(day, date) else day
        wanted_time = time.fromisoformat(str(at))
        if wanted_time.strftime("%H:%M") != str(at) or wanted_time.tzinfo:
            return ["callback time must be a local HH:MM"]
        window = rule_value(policy, "callback_window")
        if not isinstance(window, dict):
            return ["policy has no callback window"]
        days = {current.date()} if window.get("allow_today", False) else set()
        days.update(business_deadline(current, n, rule_value(policy, "holidays", []),
                                     rule_value(policy, "business_weekdays", (0, 1, 2, 3, 4)))
                    for n in range(1, int(window.get("next_business_days", 0)) + 1))
        errors = []
        if wanted_day not in days:
            errors.append("callback date is outside the policy window")
        if not time.fromisoformat(window["start"]) <= wanted_time <= time.fromisoformat(window["end"]):
            errors.append("callback time is outside the policy window")
        proposed = datetime.combine(wanted_day, wanted_time, tzinfo=current.tzinfo)
        if proposed <= current:
            errors.append("callback must be later than supplied now")
        return errors
    except (TypeError, ValueError, KeyError, PolicyError):
        return ["callback or policy inputs are invalid/incomplete"]


def _true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value in ("true", "True"))


def _boolean(value: Any) -> bool:
    if value is True or (isinstance(value, str) and value in ("true", "True")):
        return True
    if value is False or (isinstance(value, str) and value in ("false", "False")):
        return False
    raise PolicyError("tool evidence must contain an explicit boolean")


def _result_data(result: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise PolicyError("tool result must be an object")
    data = result.get("data", result)
    if not isinstance(data, Mapping):
        raise PolicyError("tool result data must be an object")
    return data


def _write_result(result: Mapping[str, Any]) -> str:
    """An explicit rejection is distinct from a transport/unknown-persistence result."""
    try:
        data = _result_data(result)
        return "recorded" if _boolean(data.get("ok")) else "rejected"
    except PolicyError:
        return "unknown_persistence"


def _counter(state: Mapping[str, Any], group: str, product_id: str) -> int:
    counts = state.get(group, {})
    if not isinstance(counts, Mapping):
        raise PolicyError("invalid per-product counters")
    value = counts.get(product_id, 0)
    if type(value) is not int or value < 0:
        raise PolicyError("counter must be a nonnegative integer")
    return value


def _promise_unknown(state: Mapping[str, Any], product_id: str) -> bool:
    return _write_unknown(state, "promise_status", product_id)


def _write_unknown(state: Mapping[str, Any], group: str, product_id: str) -> bool:
    statuses = state.get(group, {})
    if not isinstance(statuses, Mapping):
        raise PolicyError("invalid write statuses")
    return statuses.get(product_id) == "unknown_persistence"


def _failure_limit(policy: Mapping[str, Any], kind: str) -> int:
    limit = rule_value(policy, kind)
    if type(limit) is not int or limit < 1:
        raise PolicyError("policy must supply a positive " + kind)
    return limit


def compile_execution_policy(policy: Mapping[str, Any], bindings: dict | None = None,
                             *, required_kinds: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """Freeze typed helper inputs, with no interpretation of extension prose.

    Only the caller's requested kinds are mandatory. Unknown extensions do not
    become executable rules; an unknown requested kind fails closed.
    """
    kinds = _PROFILE_KINDS | _EXECUTION_ONLY_KINDS
    if not isinstance(required_kinds, (list, tuple)) or any(not isinstance(kind, str) or kind not in kinds for kind in required_kinds):
        raise PolicyError("requested execution rule kind is unsupported")
    profile = {}
    missing = []
    absent = object()
    applicability = rule_value(policy, "policy_applicability", absent)
    singleton = applicability == {"products": "single"}
    for kind in sorted(kinds):
        value = rule_value(policy, kind, absent)
        if value is absent:
            if kind in required_kinds and not (singleton and kind in {"product_order", "resolve_all_products"}):
                missing.append(kind)
            continue
        errors = _execution_errors(kind, value)
        if errors:
            raise PolicyError("; ".join(errors))
        profile[kind] = copy.deepcopy(value)
    if missing:
        raise MissingExecutionRules(missing)
    if "band_ranges" in profile:
        bands = {band["name"] for band in profile["band_ranges"]}
        for kind in ("deadline_days", "persuasion_limits"):
            if kind in profile and set(profile[kind]) != bands:
                raise PolicyError(kind + " must cover exactly the declared bands")
    out = {"version": 1, "policy_sha256": digest(policy), "profile": profile}
    if bindings is not None:
        from .action_bindings import checked_action_bindings
        out["bindings_sha256"] = checked_action_bindings(policy, bindings)["sha256"]
    out["sha256"] = digest(out)
    return out


def identity_record(state: Mapping[str, Any], field: str, result: Mapping[str, Any],
                    policy: Mapping[str, Any]) -> dict[str, Any]:
    """Only successful API evidence advances identity; unreadable/errors don't count."""
    assert_action_allowed(state, "verify_identity")
    fields = rule_value(policy, "identity_fields", [])
    limit = rule_value(policy, "identity_wrong_limit")
    if _profile_errors("identity_fields", fields) or type(limit) is not int or limit < 1 or field not in fields:
        raise PolicyError("identity policy is missing or invalid")
    verified = state.get("verified_fields", [])
    wrong = state.get("wrong_answers", 0)
    if (not isinstance(verified, list) or verified != fields[:len(verified)]
            or type(wrong) is not int or wrong < 0):
        raise PolicyError("identity state is malformed")
    if state.get("identity_status") == "failed" or wrong >= limit:
        raise PolicyError("identity check is already exhausted")
    expected = next((name for name in fields if name not in verified), None)
    if field != expected:
        raise PolicyError("security questions must follow policy order")
    data = _result_data(result)
    ok = _boolean(data.get("ok"))
    passed = _boolean(data.get("verified")) if ok else False
    remaining = data.get("wrong_answers_left")
    if "wrong_answers_left" in data and (type(remaining) is not int or not 0 <= remaining <= limit):
        raise PolicyError("service identity counter is malformed")
    exemptions = rule_value(policy, "identity_hint_exemptions", ["day_month_swapped"])
    if _profile_errors("identity_hint_exemptions", exemptions):
        raise PolicyError("identity hint exemptions must be explicit strings")
    hint = data.get("hint")
    if hint is not None and (not isinstance(hint, str) or hint not in exemptions):
        raise PolicyError("identity hint has no declared counter meaning")
    verified = list(verified)
    if ok:
        if passed:
            verified.append(field)
        elif hint not in exemptions:
            wrong += 1
    # The service's counter is authoritative and may reflect earlier attempts.
    if ok and remaining is not None:
        wrong = max(wrong, limit - remaining)
    out = copy.deepcopy(dict(state))
    out.update(verified_fields=verified, wrong_answers=wrong,
               identity_status="failed" if wrong >= limit else "verified" if all(f in verified for f in fields) else "pending")
    return out


def stage_commitment(state: Mapping[str, Any], products: list[dict[str, Any]], product_id: str,
                     amount: Any, day: Any, now: Any, policy: Mapping[str, Any]) -> dict[str, Any]:
    assert_action_allowed(state, "create_promise")
    product = product_by_id(products, product_id)
    errors = validate_commitment(product, amount, day, now, policy)
    if errors:
        raise PolicyError("; ".join(errors))
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id in out.get("recorded_promises", {}) or product_id in out.get("recorded_notes", {}):
        raise PolicyError("cannot stage this commitment in current call state")
    if _promise_unknown(state, product_id) or _write_unknown(state, "note_status", product_id):
        raise PolicyError("promise persistence must be reconciled before another disposition")
    failures = _counter(state, "promise_failures", product_id)
    if failures and failures >= _failure_limit(policy, "promise_failure_limit"):
        raise PolicyError("product promise failure budget is exhausted")
    out.get("pending_notes", {}).pop(product_id, None)
    out.setdefault("pending_promises", {})[product_id] = {"amount_cents": money_to_cents(amount), "date": _date(day).isoformat()}
    out["confirmation_status"] = "pending"
    return out


def confirm_commitments(state: Mapping[str, Any]) -> dict[str, Any]:
    """Called only by an application after it has established explicit consent."""
    out = copy.deepcopy(dict(state))
    if not out.get("pending_promises") or out.get("closed") or out.get("close_attempted") or out.get("close_status") == "unknown_persistence" or out.get("transfer"):
        raise PolicyError("no active commitments to confirm")
    out["confirmation_status"] = "confirmed"
    return out


def assert_commitment_attempt_allowed(state: Mapping[str, Any], product_id: str,
                                      policy: Mapping[str, Any]) -> None:
    """Check before execution, not after spending another write attempt."""
    assert_action_allowed(state, "create_promise")
    if state.get("closed") or state.get("transfer") or state.get("confirmation_status") != "confirmed":
        raise PolicyError("recording requires confirmed active commitments")
    if product_id not in state.get("pending_promises", {}) or product_id in state.get("recorded_promises", {}):
        raise PolicyError("unknown or already recorded commitment")
    if product_id in state.get("recorded_notes", {}) or _promise_unknown(state, product_id) or _write_unknown(state, "note_status", product_id):
        raise PolicyError("recorded notes or unknown persistence prohibit a promise write")
    if _counter(state, "promise_failures", product_id) >= _failure_limit(policy, "promise_failure_limit"):
        raise PolicyError("product promise failure budget is exhausted")


def record_commitment_result(state: Mapping[str, Any], product_id: str, result: Mapping[str, Any],
                             policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    status = _write_result(result)
    if policy is not None:
        assert_commitment_attempt_allowed(state, product_id, policy)
    else:
        assert_action_allowed(state, "create_promise")
        # Preserve the legacy first-positive-result call without silently
        # permitting failure retries when the bank's limit was not supplied.
        if (status != "recorded" or _counter(state, "promise_failures", product_id)
                or _promise_unknown(state, product_id) or _write_unknown(state, "note_status", product_id)):
            raise PolicyError("failure/unknown write handling requires the supplied policy")
        if (state.get("closed") or state.get("transfer") or state.get("confirmation_status") != "confirmed"
                or product_id not in state.get("pending_promises", {}) or product_id in state.get("recorded_promises", {})):
            raise PolicyError("recording requires an unrecorded confirmed active commitment")
        if product_id in state.get("recorded_notes", {}):
            raise PolicyError("a recorded note prohibits a promise write")
    out = copy.deepcopy(dict(state))
    out.setdefault("promise_status", {})[product_id] = status
    if status == "recorded":
        promise = out["pending_promises"].pop(product_id)
        out.setdefault("recorded_promises", {})[product_id] = promise
    elif status == "rejected":
        out.setdefault("promise_failures", {})[product_id] = _counter(state, "promise_failures", product_id) + 1
    return out


def record_persuasion_attempt(state: Mapping[str, Any], products: list[dict[str, Any]], product_id: str,
                              policy: Mapping[str, Any]) -> dict[str, Any]:
    """Count an application-classified persuasion action for this stable ID."""
    assert_action_allowed(state, "create_promise")
    product = product_by_id(products, product_id)
    limits = rule_value(policy, "persuasion_limits")
    if _profile_errors("persuasion_limits", limits):
        raise PolicyError("policy has no typed per-band persuasion limits")
    limit = limits.get(product_band(product, policy))
    if type(limit) is not int or limit < 0:
        raise PolicyError("persuasion limit is missing for the product band")
    if (state.get("closed") or state.get("transfer") or product_id in state.get("recorded_promises", {})
            or product_id in state.get("recorded_notes", {}) or _promise_unknown(state, product_id)
            or _write_unknown(state, "note_status", product_id)):
        raise PolicyError("persuasion is not permitted in this product state")
    count = _counter(state, "persuasion_attempts", product_id)
    if count >= limit:
        raise PolicyError("product persuasion budget is exhausted")
    out = copy.deepcopy(dict(state))
    out.setdefault("persuasion_attempts", {})[product_id] = count + 1
    return out


def stage_note(state: Mapping[str, Any], products: list[dict[str, Any]], product_id: str,
               reason: str, policy: Mapping[str, Any], *, bindings: dict | None = None) -> dict[str, Any]:
    assert_action_allowed(state, "add_note")
    from .action_bindings import checked_action_bindings, decode_action_value
    decode_action_value(checked_action_bindings(policy, bindings), "notes", reason)
    product_by_id(products, product_id)
    if reason not in rule_value(policy, "note_reasons", []):
        raise PolicyError("note reason is not permitted by policy")
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id in out.get("recorded_promises", {}) or product_id in out.get("recorded_notes", {}):
        raise PolicyError("cannot note this product in current call state")
    if _promise_unknown(state, product_id) or state.get("note_status", {}).get(product_id) == "unknown_persistence":
        raise PolicyError("write persistence must be reconciled before a note")
    out.get("pending_promises", {}).pop(product_id, None)
    out["confirmation_status"] = "pending" if out.get("pending_promises") else "none"
    out.setdefault("pending_notes", {})[product_id] = reason
    return out


def stage_transfer(state: Mapping[str, Any], queue: str, policy: Mapping[str, Any], *, bindings: dict | None = None) -> dict[str, Any]:
    assert_action_allowed(state, "transfer_to_agent")
    from .action_bindings import checked_action_bindings, decode_action_value
    decode_action_value(checked_action_bindings(policy, bindings), "queues", queue)
    reason = rule_value(policy, "transfer_reasons", {}).get(queue)
    if reason is None or state.get("closed") or state.get("transfer"):
        raise PolicyError("transfer is not permitted")
    out = copy.deepcopy(dict(state))
    if out.get("recorded_notes") or "unknown_persistence" in out.get("note_status", {}).values():
        raise PolicyError("already recorded notes cannot be undone for a transfer")
    out.update(transfer={"queue": queue, "reason": reason, "status": "pending"},
               pending_notes={}, pending_promises={}, confirmation_status="none")
    return out


def record_note_result(state: Mapping[str, Any], product_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Commit a previously staged disposition only after positive tool evidence."""
    assert_action_allowed(state, "add_note")
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id not in out.get("pending_notes", {}):
        raise PolicyError("no permitted staged note to record")
    if product_id in out.get("recorded_notes", {}):
        raise PolicyError("note is already recorded")
    if state.get("note_status", {}).get(product_id) == "unknown_persistence":
        raise PolicyError("unknown note persistence prohibits retries")
    status = _write_result(result)
    out.setdefault("note_status", {})[product_id] = status
    if status == "recorded":
        out.setdefault("recorded_notes", {})[product_id] = out["pending_notes"][product_id]
    elif status == "rejected":
        out.setdefault("note_failures", {})[product_id] = _counter(state, "note_failures", product_id) + 1
    return out


def record_transfer_result(state: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    assert_action_allowed(state, "transfer_to_agent")
    out = copy.deepcopy(dict(state))
    if out.get("closed") or not out.get("transfer") or out["transfer"].get("status") != "pending":
        raise PolicyError("no active transfer")
    status = _write_result(result)
    out["transfer"]["status"] = "failed" if status == "rejected" else status
    return out


def choose_close(state: Mapping[str, Any], products: list[dict[str, Any]], policy: Mapping[str, Any],
                 *, bindings: dict | None = None) -> dict[str, str]:
    """Choose from recorded facts; precedence and reason vocabulary are policy inputs."""
    from .action_bindings import checked_action_bindings, decode_action_value, encode_action_value
    binding = checked_action_bindings(policy, bindings)
    def proposal(meaning: str, reason: str | None = None) -> dict[str, str]:
        out = {"outcome": encode_action_value(binding, "outcomes", meaning)}
        if reason is not None:
            out["reason"] = reason
        validate_close(out, policy, bindings=bindings)
        return out
    if state.get("closed") or state.get("close_attempted") or state.get("close_status") == "unknown_persistence":
        raise PolicyError("call is already closed")
    transfer = state.get("transfer", {})
    if transfer.get("status") == "recorded":
        if rule_value(policy, "transfer_reasons", {}).get(transfer.get("queue")) != transfer.get("reason"):
            raise PolicyError("recorded transfer queue/reason evidence does not match policy")
        return proposal("transferred", transfer["reason"])
    if transfer:
        raise PolicyError("a transfer without confirmed success cannot establish a close")
    if state.get("recorded_promises"):
        return proposal("promise")
    if "unknown_persistence" in state.get("promise_status", {}).values() or "unknown_persistence" in state.get("note_status", {}).values():
        raise PolicyError("unreconciled persistence cannot establish a closing disposition")
    notes = state.get("pending_notes", {})
    meanings = [decode_action_value(binding, "notes", notes[p["product_id"]]) if p["product_id"] in notes else None for p in products]
    if products and all(meaning == "paid" for meaning in meanings):
        return proposal("paid")
    meaning = next((meaning for meaning in meanings if meaning not in (None, "paid")), None)
    if meaning:
        return proposal("no_promise", encode_action_value(binding, "reasons", meaning))
    fallback = state.get("close_proposal")
    if isinstance(fallback, dict):
        if decode_action_value(binding, "outcomes", fallback.get("outcome")) in {"promise", "transferred"}:
            raise PolicyError("closing proposal lacks successful tool evidence")
        validate_close(fallback, policy, bindings=bindings)
        return dict(fallback)
    raise PolicyError("recorded facts do not establish a closing outcome")


def validate_close(proposal: Mapping[str, Any], policy: Mapping[str, Any], *, bindings: dict | None = None) -> None:
    from .action_bindings import checked_action_bindings, decode_action_value
    if not isinstance(proposal, Mapping):
        raise PolicyError("closing proposal must be an object")
    binding = checked_action_bindings(policy, bindings)
    reasons = rule_value(policy, "close_reasons", {})
    outcome, reason = proposal.get("outcome"), proposal.get("reason")
    decode_action_value(binding, "outcomes", outcome)
    if reason not in (None, ""):
        decode_action_value(binding, "reasons", reason)
    if outcome not in reasons or (reasons[outcome] and reason not in reasons[outcome]) or (not reasons[outcome] and reason not in (None, "")):
        raise PolicyError("closing outcome/reason is not permitted by policy")


def record_close_result(state: Mapping[str, Any], proposal: Mapping[str, Any], result: Mapping[str, Any],
                        policy: Mapping[str, Any], *, bindings: dict | None = None) -> dict[str, Any]:
    from .action_bindings import checked_action_bindings, decode_action_value
    binding = checked_action_bindings(policy, bindings)
    validate_close(proposal, policy, bindings=bindings)
    if state.get("closed") or state.get("close_attempted") or state.get("close_status") == "unknown_persistence":
        raise PolicyError("close_call must be the last and only close attempt")
    semantic = decode_action_value(binding, "outcomes", proposal.get("outcome"))
    if semantic == "promise" and not state.get("recorded_promises"):
        raise PolicyError("promise outcome requires a successfully recorded promise")
    transfer = state.get("transfer", {})
    if semantic == "transferred" and (transfer.get("status") != "recorded"
            or transfer.get("reason") != proposal.get("reason")
            or rule_value(policy, "transfer_reasons", {}).get(transfer.get("queue")) != proposal.get("reason")):
        raise PolicyError("transferred outcome requires a successful matching transfer")
    if transfer and semantic != "transferred":
        raise PolicyError("an active transfer requires its matching successful closing outcome")
    if semantic != "transferred" and ("unknown_persistence" in state.get("promise_status", {}).values()
                                      or "unknown_persistence" in state.get("note_status", {}).values()):
        raise PolicyError("unknown persistence must be reconciled before closing")
    out = copy.deepcopy(dict(state))
    status = _write_result(result)
    out["close_attempted"] = True
    out["close_status"] = status
    if status == "recorded":
        out.update(closed=True, close=dict(proposal))
    return out


def assert_action_allowed(state: Mapping[str, Any], action: str) -> None:
    if state.get("closed") or state.get("close_attempted") or state.get("close_status") == "unknown_persistence":
        raise PolicyError("no tool call is permitted after any close_call attempt")
    if state.get("transfer") and action in {"add_note", "create_promise"}:
        raise PolicyError("staged or completed transfers prohibit notes and pending promises")

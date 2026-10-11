"""Detached semantic-to-wire bindings derived from this run's policy and API.

Bindings describe values, not runtime enforcement. A controller must compile
them from approved inputs and keep them out of generated-code authority.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from .policy import PolicyError, rule_value
from .safety import digest
from .toolbindings import operations


class ActionBindingError(PolicyError):
    pass


CANONICAL = {
    "outcomes": {"promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"},
    "reasons": {"support", "human", "restructuring", "refused", "hardship", "dispute", "service_error",
                "failed", "declined", "callback", "no_callback", "wrong_number", "deceased", "unclear"},
    "notes": {"refused", "hardship", "dispute", "paid", "service_error"},
    "queues": {"support", "general", "restructuring"},
}
TARGETS = {"outcomes": ("close_call", "outcome"), "reasons": ("close_call", "reason"),
           "notes": ("add_note", "reason"), "queues": ("transfer_to_agent", "queue")}
COLLECTION_ROLES = {"stage_promise", "commit_promises", "stage_note", "finalize_notes", "stage_transfer", "commit_transfer"}
# Outcome meanings and the callback-evidence meanings: no outcome, reason or
# note code may be spelled like one of them unless bound to that meaning.
GUARDED = CANONICAL["outcomes"] | {"callback", "no_callback"}
# The kernel's own evidence names for the callback meanings; no other synonyms.
_SYNONYMS = {"recordedcallback": "callback", "norecordedcallback": "nocallback"}


def _name(value: str) -> str:
    """Spelling-insensitive name for meaning comparisons: casefolded, without separators."""
    key = re.sub(r"[\W_]+", "", value.casefold())
    return _SYNONYMS.get(key, key)


def _local_dispositions(policy: Mapping[str, Any]) -> bool:
    flag = rule_value(policy, "record_notes")
    if flag is not None and type(flag) is not bool:
        raise ActionBindingError("record_notes must be an explicit boolean when supplied")
    return flag is False


def _vocab(policy: Mapping[str, Any]) -> dict[str, set[str]]:
    close = rule_value(policy, "close_reasons", {})
    transfer = rule_value(policy, "transfer_reasons", {})
    notes = rule_value(policy, "note_reasons", [])
    if not isinstance(close, dict) or not isinstance(transfer, dict) or not isinstance(notes, list):
        raise ActionBindingError("action vocabularies must be explicit policy maps/lists")
    if any(not isinstance(reasons, list) for reasons in close.values()):
        raise ActionBindingError("closing reasons must be lists")
    values = [*close, *transfer, *transfer.values(), *notes, *(reason for reasons in close.values() for reason in reasons)]
    if any(not isinstance(value, str) or not value for value in values):
        raise ActionBindingError("action wire values must be nonempty strings")
    result = {"outcomes": set(close), "reasons": set(transfer.values()), "notes": set(notes), "queues": set(transfer)}
    result["reasons"].update(reason for reasons in close.values() for reason in reasons)
    return result


def _unbound_permitted(policy: Mapping[str, Any]) -> bool:
    """Only goals limited to identity, account reads and the close may leave close codes unbound."""
    actions = rule_value(policy, "execution_actions")
    return (isinstance(actions, list) and all(isinstance(a, str) for a in actions)
            and set(actions) <= {"verify_identity", "get_account", "close_call"})


def _maps(policy: Mapping[str, Any], *, canonical_only: bool) -> tuple[dict[str, dict[str, str]], dict[str, list[str]]]:
    vocab = _vocab(policy)
    declared = rule_value(policy, "wire_codes", {})
    if not isinstance(declared, dict):
        raise ActionBindingError("wire_codes must be an explicit mapping")
    maps, unbound = {}, {}
    for category, values in vocab.items():
        if not values:
            continue
        mapping = declared.get(category)
        if mapping is None:
            mapping = {value: value for value in sorted(values & CANONICAL[category])}
        elif not isinstance(mapping, dict) or not mapping:
            raise ActionBindingError("unknown or invalid semantic mapping for " + category)
        if any(not isinstance(meaning, str) or not isinstance(wire, str) or not wire for meaning, wire in mapping.items()):
            raise ActionBindingError("unknown or invalid semantic mapping for " + category)
        if not set(mapping) <= CANONICAL[category]:
            raise ActionBindingError("wire_codes." + category + " keys must be builder meanings listed in the wire_codes"
                                     " rule shape; a code whose source meaning is not listed there is never a key")
        if len(set(mapping.values())) != len(mapping):
            raise ActionBindingError("ambiguous semantic mapping for " + category)
        guarded = {_name(n) for n in GUARDED} if category != "queues" else set()
        if any(wire in CANONICAL[category] and wire != meaning or _name(wire) in guarded and _name(wire) != _name(meaning)
               for meaning, wire in mapping.items()):
            raise ActionBindingError("canonical API values cannot be assigned conflicting meanings; an outcome, reason or"
                                     " note code spelled like an outcome meaning, callback or no_callback in any case or"
                                     " separators binds only to that meaning")
        # A declared semantic key resolves to its unique wire code; the check
        # above makes that resolution unambiguous. Anything else is unbound.
        loose = {mapping.get(value, value) for value in values} - set(mapping.values())
        if loose and (canonical_only or category not in ("outcomes", "reasons")
                      or loose & set().union(*CANONICAL.values()) or {_name(v) for v in loose} & guarded
                      or not _unbound_permitted(policy)):
            raise ActionBindingError(
                "policy " + category + " vocabulary has a code that is neither a wire_codes." + category
                + " API value nor one of its declared meanings; only a close_call outcome or reason code whose source"
                " meaning is not a builder meaning, and which is neither itself a builder meaning name nor spelled like"
                " an outcome meaning, callback or no_callback in any case or separators, may stay unbound, and only in"
                " a goal whose roles are limited to verify_identity, get_account and close_call")
        if canonical_only and any(meaning != wire for meaning, wire in mapping.items()):
            raise ActionBindingError("opaque values require API-validated bindings")
        maps[category] = deepcopy(mapping)
        if loose:
            unbound[category] = sorted(loose)
    close = rule_value(policy, "close_reasons", {})
    if len({maps.get("outcomes", {}).get(k, k) for k in close}) != len(close):
        raise ActionBindingError("close_reasons cites one outcome code twice through its semantic key")
    transfer = rule_value(policy, "transfer_reasons", {})
    if transfer:
        outcomes, reasons = maps.get("outcomes", {}), maps.get("reasons", {})
        close = {outcomes.get(k, k): {reasons.get(r, r) for r in v}
                 for k, v in rule_value(policy, "close_reasons", {}).items()}
        outcome = outcomes.get("transferred")
        if outcome is None or not {reasons.get(r, r) for r in transfer.values()} <= close.get(outcome, set()):
            raise ActionBindingError("transfer closing reasons are inconsistent with the permitted transferred outcome")
    return maps, unbound


def _api_field(operation: dict, semantic: str, policy: Mapping[str, Any], action: str) -> tuple[str, list[str], bool]:
    fields = operation["body_schema"].get("properties", {})
    declared = rule_value(policy, "action_fields", {})
    if not isinstance(declared, dict):
        raise ActionBindingError("action_fields must be an explicit operation-to-field map")
    if not isinstance(declared.get(action, {}), dict):
        raise ActionBindingError("action_fields entries must be semantic field maps")
    explicit = declared.get(action, {}).get(semantic)
    if explicit is not None:
        matches = [explicit] if isinstance(explicit, str) and explicit in fields else []
    else:
        matches = [name for name, spec in fields.items() if name == semantic or
                   (isinstance(spec, dict) and isinstance(spec.get("description"), str)
                    and spec["description"].strip().casefold() == "semantic field: " + semantic)]
    if len(matches) != 1:
        raise ActionBindingError("API action field is missing or ambiguous: " + action + "." + semantic)
    field = matches[0]
    if not isinstance(fields[field], dict):
        raise ActionBindingError("API action field has an invalid schema")
    if fields[field].get("type") != "string":
        raise ActionBindingError("API action field requires a string schema: " + action + "." + semantic)
    if "enum" not in fields[field]:
        return field, [], False
    enum = fields[field]["enum"]
    if not isinstance(enum, list) or not enum or any(
            not isinstance(value, str) or not value for value in enum) or len(set(enum)) != len(enum):
        raise ActionBindingError("API action field requires a unique string enum: " + action + "." + semantic)
    return field, enum, True


def _finish(policy: Mapping[str, Any], categories: dict, api_hash: str, mode: str) -> dict:
    binding = {"version": 1, "policy_sha256": digest(policy), "openapi_sha256": api_hash,
               "binding_mode": mode, "categories": categories}
    binding["sha256"] = digest(binding)
    return binding


def compile_action_bindings(policy: Mapping[str, Any], openapi: dict) -> dict:
    """Compile plain JSON bindings; no service calls, credentials or closures."""
    catalogue = {operation["name"]: operation for operation in operations(openapi)}
    aliases = rule_value(policy, "tool_aliases", {})
    if not isinstance(aliases, dict):
        raise ActionBindingError("tool_aliases must map semantic actions to API operation names")
    allowed = policy.get("allowed_actions")
    if allowed is not None and (not isinstance(allowed, list) or any(not isinstance(name, str) for name in allowed)):
        raise ActionBindingError("allowed_actions must be operation names")
    categories = {}
    local_notes = _local_dispositions(policy)
    maps, unbound = _maps(policy, canonical_only=False)
    for category, mapping in maps.items():
        if category == "notes" and local_notes:
            categories[category] = {"binding_kind": "local_disposition", "semantic_to_wire": deepcopy(mapping),
                                    "wire_to_semantic": {wire: meaning for meaning, wire in mapping.items()}}
            continue
        action, semantic_field = TARGETS[category]
        name = aliases.get(action, action)
        if not isinstance(name, str) or name not in catalogue or (allowed is not None and name not in allowed):
            raise ActionBindingError("policy action lacks a permitted API operation: " + action)
        operation = catalogue[name]
        field, enum, constrained = _api_field(operation, semantic_field, policy, action)
        if constrained and not set(mapping.values()) | set(unbound.get(category, [])) <= set(enum):
            raise ActionBindingError("policy semantic codes do not match the API enum for " + category)
        categories[category] = {"semantic_to_wire": mapping, "wire_to_semantic": {wire: meaning for meaning, wire in mapping.items()},
                                "operation": {key: operation[key] for key in ("name", "method", "url")},
                                "field": field, "api_enum": deepcopy(enum), "api_enum_constrained": constrained}
        if category in unbound:
            categories[category]["unbound_codes"] = unbound[category]
    return _finish(policy, categories, digest(openapi), "api_validated")


def checked_action_bindings(policy: Mapping[str, Any], binding: dict | None = None) -> dict:
    """Check detached data against this policy; absent data permits canonical identity only."""
    if binding is None:
        maps, _ = _maps(policy, canonical_only=True)
        return _finish(policy, {category: {"semantic_to_wire": mapping,
                                         "wire_to_semantic": {wire: meaning for meaning, wire in mapping.items()}}
                               for category, mapping in maps.items()}, "", "canonical_identity")
    if not isinstance(binding, dict) or type(binding.get("version")) is not int or binding.get("version") != 1 or binding.get("binding_mode") != "api_validated":
        raise ActionBindingError("explicit action bindings must come from supplied API compilation")
    if binding.get("policy_sha256") != digest(policy) or binding.get("sha256") != digest({k: v for k, v in binding.items() if k != "sha256"}):
        raise ActionBindingError("action bindings changed or belong to another policy")
    wanted, unbound = _maps(policy, canonical_only=False)
    local_notes = _local_dispositions(policy)
    if not isinstance(binding.get("categories"), dict) or set(binding["categories"]) != set(wanted):
        raise ActionBindingError("action bindings do not cover the policy vocabularies")
    for category, mapping in wanted.items():
        data = binding["categories"][category]
        if category == "notes" and local_notes:
            if (not isinstance(data, dict) or set(data) != {"binding_kind", "semantic_to_wire", "wire_to_semantic"}
                    or data.get("binding_kind") != "local_disposition" or data.get("semantic_to_wire") != mapping
                    or data.get("wire_to_semantic") != {wire: meaning for meaning, wire in mapping.items()}):
                raise ActionBindingError("local disposition bindings require exact policy-only note category")
            continue
        enum = data.get("api_enum") if isinstance(data, dict) else None
        constrained = data.get("api_enum_constrained") if isinstance(data, dict) else None
        operation = data.get("operation") if isinstance(data, dict) else None
        loose = unbound.get(category, [])
        keys = {"semantic_to_wire", "wire_to_semantic", "operation", "field", "api_enum", "api_enum_constrained"}
        if (not isinstance(data, dict) or set(data) != keys | ({"unbound_codes"} if loose else set())
                or loose and data.get("unbound_codes") != loose
                or data.get("semantic_to_wire") != mapping
                or data.get("wire_to_semantic") != {wire: meaning for meaning, wire in mapping.items()}
                or type(constrained) is not bool or not isinstance(enum, list)
                or any(not isinstance(value, str) or not value for value in enum)
                or len(set(enum)) != len(enum)
                or constrained and (not enum or not set(mapping.values()) | set(loose) <= set(enum))
                or not constrained and enum != []
                or not isinstance(data.get("field"), str) or not data["field"]
                or not isinstance(operation, dict) or set(operation) != {"name", "method", "url"}
                or any(not isinstance(operation[key], str) or not operation[key] for key in operation)):
            raise ActionBindingError("action binding vocabulary is invalid or mismatched")
    return deepcopy(binding)


def encode_action_value(binding: Mapping[str, Any], category: str, meaning: str) -> str:
    try:
        return binding["categories"][category]["semantic_to_wire"][meaning]
    except (KeyError, TypeError):
        raise ActionBindingError("no declared wire code for " + category + "." + str(meaning)) from None


def decode_action_value(binding: Mapping[str, Any], category: str, wire: str) -> str:
    try:
        return binding["categories"][category]["wire_to_semantic"][wire]
    except (KeyError, TypeError):
        raise ActionBindingError("unknown wire code for " + category) from None

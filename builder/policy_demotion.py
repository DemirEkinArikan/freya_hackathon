"""Last-resort demotion for relaxed builds, after bounded repairs or when no repair fits.

Each step targets one remaining compile error and rewrites exactly the typed construct it
names: a typed rule (or its offending entries) becomes descriptive with its source quote,
declared protected_unenforceable so the relaxed contract freezes it as a prompt-enforced
dialogue rule, and the step is recorded with the error it cleared. Identity, consent, floor,
deadline and calendar kinds are never demoted. A role whose sealed bindings cannot compile is
removed whole with its API operation, so the agent never performs it and no protected outcome
can be sent without its recorded evidence. Strict and default builds never get here.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .action_bindings import _name
from .contract import compile_policy_contract
from .execution_requirements import compile_execution_requirements
from .guards import execution_action_catalogue
from .policy import _active_rules

# Optional kinds whose absence the kernel treats conservatively: closes use the fixed sourced
# rows or protected evidence only, nothing is disclosed, stops use the neutral line.
OPTIONAL_KINDS = ("identity_close_choices", "goal_close", "close_precedence", "account_disclosure_fields",
                  "promise_failure_disposition", "uncertain_stop_explanation")
# Roles removed together, with the vocabulary kinds only they use; get_account takes the roles that need products.
ROLE_GROUPS = {"transfer_to_agent": (("stage_transfer", "commit_transfer"), ("transfer_reasons", "transfer_prerequisites")),
               "add_note": (("stage_note", "finalize_notes"), ("note_reasons",)),
               "schedule_callback": (("schedule_callback",), ()),
               "create_promise": (("stage_promise", "commit_promises"), ()),
               "get_account": (("get_account", "stage_promise", "commit_promises", "stage_note", "finalize_notes"),
                               ("note_reasons",))}
VOCABULARY_OPERATIONS = {"notes": "add_note", "queues": "transfer_to_agent"}
NEVER_REMOVED = {"verify_identity", "close_call"}
STEPS = {"get_account": "look up the customer's accounts", "create_promise": "record a payment promise",
         "schedule_callback": "book a callback", "add_note": "write a note on the account",
         "transfer_to_agent": "transfer the call to a person"}
PREFIX = "prompt_enforced_"
RULE_REQUIREMENT = "Follow this bank rule yourself in the conversation; no system check enforces it."


class PolicyDemotionError(ValueError):
    def __init__(self, errors: list[str], demotions: list[dict[str, Any]]):
        self.errors, self.demotions = tuple(errors), demotions
        super().__init__("relaxed demotion could not compile the policy: " + "; ".join(self.errors))


def _errors(policy: dict, sources: Any, openapi: Any) -> tuple[int, list[str]]:
    """(layer, errors): 0 for a rejected contract, 1 for execution coverage, 2 when it compiles."""
    for layer, check in enumerate((compile_policy_contract, compile_execution_requirements)):
        try:
            check(policy, sources, openapi, declarations="relaxed")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return layer, sorted(getattr(exc, "errors", None) or [str(exc)])
    return 2, []


def _active(policy: dict, kind: str) -> list[dict]:
    return _active_rules(policy).get(kind, [])


def _declare(policy: dict, rule_ids: list[str], requirement: str, error: str) -> None:
    policy.setdefault("unsupported_constraints", []).append({
        "category": "protected_unenforceable", "requirement": requirement, "rule_ids": rule_ids,
        "justification": "Demoted in a relaxed build after bounded repairs; the sealed compiler refused: " + error})


def _demote_kind(policy: dict, kind: str, error: str) -> list[str]:
    active = [rule["id"] for rule in _active(policy, kind)]
    for rule in policy["rules"]:
        if rule.get("kind") == kind:
            rule["kind"] = PREFIX + kind  # every rule of the kind, so overrides stay same-kind
    if active:
        _declare(policy, active, RULE_REQUIREMENT, error)
    return active


def _align_bands(policy: dict, error: str) -> dict | None:
    """Rename band_ranges names to the keys of every band-keyed value when they differ only in case or separators."""
    rules = policy["rules"]
    keyed = [set(rule["value"]) for rule in rules if rule.get("kind") in ("deadline_days", "persuasion_limits")
             and isinstance(rule.get("value"), dict) and "effective_date" not in rule["value"]]
    bands = [rule for rule in rules if rule.get("kind") == "band_ranges" and isinstance(rule.get("value"), list)]
    names = [band.get("name") for rule in bands for band in rule["value"] if isinstance(band, dict)]
    if not keyed or not bands or any(keys != keyed[0] for keys in keyed) or any(type(n) is not str for n in names):
        return None
    by_name = {_name(key): key for key in keyed[0]}
    if len(by_name) != len(keyed[0]) or {_name(name) for name in names} != set(by_name) or set(names) == keyed[0]:
        return None
    renamed = {name: by_name[_name(name)] for name in sorted(set(names)) if name != by_name[_name(name)]}
    for rule in bands:
        for band in rule["value"]:
            band["name"] = renamed[band["name"]] if band["name"] in renamed else band["name"]
    return {"step": "align_band_names", "rule_ids": [rule["id"] for rule in bands], "renamed": renamed}


def _identity_choices(policy: dict, error: str) -> dict | None:
    """Demote only the closing choices whose outcome differs from their scope's fixed identity_close outcome."""
    fixed = _active(policy, "identity_close")
    rules = _active(policy, "identity_close_choices")
    if len(rules) != 1 or not fixed or not isinstance(rules[0].get("value"), dict) or not isinstance(fixed[0].get("value"), dict):
        return None
    value, dropped = {}, []
    for scope, choices in rules[0]["value"].items():
        outcome = (fixed[0]["value"].get(scope) or {}).get("outcome") if isinstance(fixed[0]["value"].get(scope), dict) else None
        kept = [c for c in choices if isinstance(c, dict) and c.get("outcome") == outcome] if isinstance(choices, list) else []
        dropped += [dict(c, scope=scope) for c in choices if c not in kept] if isinstance(choices, list) else []
        if kept:
            value[scope] = kept
    if not dropped:
        return None
    if not value:
        return {"step": "demote_rule", "kind": "identity_close_choices", "rule_ids": _demote_kind(policy, "identity_close_choices", error)}
    for rule in policy["rules"]:
        if rule.get("id") == rules[0]["id"]:
            rule["value"] = value
    _declare(policy, [rules[0]["id"]], RULE_REQUIREMENT, error)
    return {"step": "demote_entries", "kind": "identity_close_choices", "rule_ids": [rules[0]["id"]], "entries": dropped}


def _strip(value: Any, names: set[str]) -> Any:
    """Remove references to removed operations or roles: mapping keys, list strings and rows naming them."""
    if isinstance(value, dict):
        return {k: _strip(v, names) for k, v in value.items() if k not in names}
    if isinstance(value, list):
        return [_strip(item, names) for item in value if not (isinstance(item, str) and item in names
                or isinstance(item, dict) and item.get("operation") in names)]
    return value


def _drop_operation(policy: dict, operation: str, error: str) -> dict | None:
    actions = _active(policy, "execution_actions")
    selected = actions[0].get("value") if actions else None
    roles, kinds = ROLE_GROUPS[operation]
    if not isinstance(selected, list) or not set(roles) & set(selected) or set(roles) & NEVER_REMOVED:
        return None
    catalogue = execution_action_catalogue()
    removed = sorted(set(roles) & set(selected))
    kept = [action for action in selected if action not in roles]
    operations = {catalogue[a]["operation_role"] for a in removed} - {catalogue[a]["operation_role"] for a in kept if a in catalogue}
    aliases = _active(policy, "tool_aliases")
    alias_map = aliases[0]["value"] if aliases and isinstance(aliases[0].get("value"), dict) else {}
    actual = {alias_map.get(role, role) for role in operations}
    names = set(removed) | operations | actual
    for rule in actions:
        rule["value"] = kept
    if isinstance(policy.get("allowed_actions"), list):
        policy["allowed_actions"] = [name for name in policy["allowed_actions"] if name not in names]
    demoted = [ident for kind in kinds for ident in _demote_kind(policy, kind, error)]
    if "get_account" in operations:
        demoted += _demote_kind(policy, "product_fields", error)
    emptied = set()
    for rule in policy["rules"]:
        kind = rule.get("kind")
        if isinstance(kind, str) and kind != "execution_actions" and not kind.startswith(PREFIX) and isinstance(rule.get("value"), (dict, list)):
            stripped = _strip(rule["value"], names)
            if stripped != rule["value"]:
                rule["value"] = stripped
                if stripped in ({}, []):
                    emptied.add(kind)
    for kind in sorted(emptied):
        demoted += _demote_kind(policy, kind, error)  # it only concerned the removed operations
    words = sorted({STEPS.get(op, op) for op in operations} or {STEPS.get(operation, operation)})
    _declare(policy, [actions[0]["id"]], "This agent cannot " + " or ".join(words)
             + " in this call; never claim or imply that it did.", error)
    return {"step": "drop_roles", "roles": removed, "operations": sorted(actual), "rule_ids": demoted}


def _step(policy: dict, error: str) -> dict | None:
    if "covering the declared bands" in error or "must cover exactly the declared bands" in error:
        return _align_bands(policy, error)
    if error.startswith("identity closing choice"):
        return _identity_choices(policy, error)
    vocabulary = re.match(r"policy (notes|queues) vocabulary has a code", error)
    if vocabulary:
        return _drop_operation(policy, VOCABULARY_OPERATIONS[vocabulary.group(1)], error)
    missing = re.match(r"missing typed execution rule: (\w+)", error)
    if missing:
        catalogue = execution_action_catalogue()
        selected = set(_active(policy, "execution_actions")[0]["value"]) if _active(policy, "execution_actions") else set()
        requiring = {r for r in selected if r in catalogue and missing.group(1) in catalogue[r]["required_kinds"]}
        if not requiring and _active(policy, "promise_failure_disposition"):  # its exhaustion note needs note_reasons
            return {"step": "demote_rule", "kind": "promise_failure_disposition",
                    "rule_ids": _demote_kind(policy, "promise_failure_disposition", error)}
        groups = sorted((len(roles), op) for op, (roles, _) in ROLE_GROUPS.items() if requiring and requiring <= set(roles))
        return _drop_operation(policy, groups[0][1], error) if groups and not requiring & NEVER_REMOVED else None
    for kind in OPTIONAL_KINDS:
        if re.search(r"(?<!\w)" + kind + r"(?!\w)", error) and _active(policy, kind):
            return {"step": "demote_rule", "kind": kind, "rule_ids": _demote_kind(policy, kind, error)}
    operation = re.match(r"(\w+): ", error)
    aliases = _active(policy, "tool_aliases")
    reverse = {v: k for k, v in aliases[0]["value"].items()} if aliases and isinstance(aliases[0].get("value"), dict) else {}
    role = reverse.get(operation.group(1), operation.group(1)) if operation else None
    return _drop_operation(policy, role, error) if role in ROLE_GROUPS else None


def demote_policy(policy: dict, sources: Any, openapi: Any, *, max_steps: int = 12) -> tuple[dict, list[dict[str, Any]]]:
    """Return (policy that compiles in relaxed mode, recorded steps), or raise PolicyDemotionError."""
    current, demotions = copy.deepcopy(policy), []
    layer, errors = _errors(current, sources, openapi)
    while errors:
        if len(demotions) >= max_steps or not isinstance(current.get("rules"), list):
            raise PolicyDemotionError(errors, demotions)
        for error in errors:
            trial = copy.deepcopy(current)
            record = _step(trial, error)
            after_layer, after = _errors(trial, sources, openapi) if record else (layer, errors)
            # Progress on the targeted error, never by breaking an earlier layer that would only mask it.
            if error not in after and after_layer >= layer:
                demotions.append(dict(record, error=error))
                current, layer, errors = trial, after_layer, after
                break
        else:
            raise PolicyDemotionError(errors, demotions)
    return current, demotions

"""Patch-style repair of a rejected policy: the referenced excerpt and a deterministic apply.

A full repair re-emits the whole policy, which takes longer than the remaining
budget for long briefs. A patch repair shows the model only the validation
errors, the rules, declarations and fields they reference (with their JSON
paths) and an id/kind index of every rule. The returned operations are applied
to the rejected policy here; the result is then checked by the same unchanged
contract and execution-requirement validation as any extracted policy.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .contract import _TYPED_KINDS

TOP_FIELDS = ("version", "goal", "allowed_actions", "sources")
_LISTS = ("rules", "unsupported_constraints")
_INDEXED = re.compile(r"policy\.(rules|unsupported_constraints)\[(\d+)\]")
_FIELD_MENTIONS = {"version": r"policy\.version", "goal": r"policy\.goal", "allowed_actions": r"allowed_actions",
                   "sources": r"policy\.sources|policy source"}
_PATH = re.compile(r"/(rules|unsupported_constraints)/(0|[1-9][0-9]*|-)")
_REMOVE = object()


class PolicyPatchError(ValueError):
    """The patch cannot be applied unambiguously; the rejected policy is unchanged."""


def _items(policy: Any, name: str) -> list:
    value = policy.get(name) if isinstance(policy, dict) else None
    return value if isinstance(value, list) else []


def _mentioned(name: Any, text: str) -> bool:
    return isinstance(name, str) and bool(name) and re.search(
        r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text) is not None


def repair_excerpt(policy: Any, errors: list[str]) -> dict[str, Any]:
    """Return the patch-repair context: errors, referenced parts with paths, and a rule index.

    A rule is referenced by its path, its id or (for identifier-shaped kinds) its
    kind; a declaration by its path, which also brings the rules it cites.
    """
    rules, entries = _items(policy, "rules"), _items(policy, "unsupported_constraints")
    picked: dict[str, set[int]] = {name: set() for name in _LISTS}
    fields: set[str] = set()
    for error in errors:
        text = str(error).split("; expected ", 1)[0]  # Shape guidance names unrelated kinds.
        for name, index in _INDEXED.findall(text):
            if int(index) < len(_items(policy, name)):
                picked[name].add(int(index))
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            kind = rule.get("kind")
            kind = kind if isinstance(kind, str) and (kind in _TYPED_KINDS or re.search(r"[_0-9]", kind)) else None
            if _mentioned(rule.get("id"), text) or _mentioned(kind, text):
                picked["rules"].add(index)
        fields.update(name for name, pattern in _FIELD_MENTIONS.items() if re.search(pattern, text))
    positions = {rule["id"]: index for index, rule in enumerate(rules)
                 if isinstance(rule, dict) and isinstance(rule.get("id"), str)}
    for index in picked["unsupported_constraints"]:
        entry = entries[index] if isinstance(entries[index], dict) else {}
        cited = entry.get("rule_ids") if isinstance(entry.get("rule_ids"), list) else []
        picked["rules"].update(positions[ident] for ident in [*cited, entry.get("fallback_rule")]
                               if isinstance(ident, str) and ident in positions)
    excerpt = {"rules": [{"path": f"/rules/{i}", "rule": copy.deepcopy(rules[i])} for i in sorted(picked["rules"])],
               "unsupported_constraints": [{"path": f"/unsupported_constraints/{i}", "entry": copy.deepcopy(entries[i])}
                                           for i in sorted(picked["unsupported_constraints"])],
               "fields": {name: copy.deepcopy(policy[name]) for name in TOP_FIELDS
                          if name in fields and isinstance(policy, dict) and name in policy}}
    index = [[rule.get("id"), rule.get("kind")] if isinstance(rule, dict) else [None, None] for rule in rules]
    return {"validation_errors": list(errors), "excerpt": excerpt, "rule_index": index,
            "unsupported_constraints_count": len(entries)}


def apply_policy_patch(policy: Any, patch: Any) -> dict[str, Any]:
    """Apply add/replace/remove operations addressed against the rejected policy, all or nothing.

    Every rule id and path refers to the rejected policy as shown to the model, so
    removals never renumber later operations. Replacements keep their positions,
    removals drop theirs, additions follow in patch order, and no entry may be
    targeted twice. Untouched content is copied exactly.
    """
    if not isinstance(policy, dict):
        raise PolicyPatchError("the rejected policy is not an object")
    operations = patch.get("operations") if isinstance(patch, dict) and set(patch) == {"operations"} else None
    if not isinstance(operations, list) or not operations:
        raise PolicyPatchError("a patch is an object with only a nonempty operations array")
    ids = [rule.get("id") if isinstance(rule, dict) else None for rule in _items(policy, "rules")]
    changes: dict[tuple[str, Any], Any] = {}
    additions: dict[str, list] = {name: [] for name in _LISTS}
    for number, operation in enumerate(operations):
        where = f"patch operations[{number}]"
        op = operation.get("op") if isinstance(operation, dict) else None
        address = [key for key in ("rule_id", "path") if isinstance(operation, dict) and key in operation]
        if (not isinstance(op, str) or op not in {"add", "replace", "remove"} or len(address) != 1
                or set(operation) != {"op", address[0], *(() if op == "remove" else ("value",))}):
            raise PolicyPatchError(where + " needs op add, replace or remove, exactly one of rule_id or path, "
                                           "and a value unless it removes")
        target = operation[address[0]]
        if address[0] == "rule_id":
            if op == "add" or not isinstance(target, str) or ids.count(target) != 1:
                raise PolicyPatchError(where + " rule_id must name exactly one rejected rule; add uses path /rules/-")
            if op == "replace" and (not isinstance(operation["value"], dict) or operation["value"].get("id") != target):
                raise PolicyPatchError(where + " must replace the rule with a complete rule object keeping its id")
            key = ("rules", ids.index(target))
        elif isinstance(target, str) and (match := _PATH.fullmatch(target)):
            name, position = match.groups()
            if (position == "-") != (op == "add") or (position != "-" and int(position) >= len(_items(policy, name))):
                raise PolicyPatchError(where + " path must address an existing entry, or end in /- to add one")
            if op == "add":
                additions[name].append(copy.deepcopy(operation["value"]))
                continue
            key = (name, int(position))
        elif op == "replace" and isinstance(target, str) and target.startswith("/") and target[1:] in TOP_FIELDS:
            key = ("field", target[1:])
        else:
            raise PolicyPatchError(where + " path must be /rules/N, /unsupported_constraints/N, either list with /- "
                                           "to add, or a replaced " + ", ".join("/" + name for name in TOP_FIELDS))
        if key in changes:
            raise PolicyPatchError(where + " targets an entry another operation already changes")
        changes[key] = _REMOVE if op == "remove" else copy.deepcopy(operation["value"])
    patched = copy.deepcopy(policy)
    for name in _LISTS:
        if additions[name] or any(key[0] == name for key in changes):
            kept = [changes.get((name, index), item) for index, item in enumerate(_items(policy, name))]
            patched[name] = [copy.deepcopy(item) for item in kept if item is not _REMOVE] + additions[name]
    patched.update({key[1]: value for key, value in changes.items() if key[0] == "field"})
    return patched

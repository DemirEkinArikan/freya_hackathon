"""Compile quoted policy IR and its supplied API into a detached contract.

This establishes structural consistency, literal provenance, explicit override
resolution and API/schema bindings. It does not establish that a quotation was
interpreted correctly, all natural-language requirements were extracted, or a
phone agent obeys the contract. Those require independent review and host tests.
Source fingerprints use the controller's canonical ``digest(text)`` convention.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .policy import _active_rules, validate_policy
from .safety import canonical_json, digest
from .toolbindings import BindingError, operations
from .policy_conflicts import ConditionalConflictError, compile_conditional_conflicts


class PolicyContractError(ValueError):
    """A rejected contract, with data-free structural errors for the controller."""

    def __init__(self, errors: list[str] | tuple[str, ...]):
        self.errors = tuple(sorted(set(errors)))
        super().__init__("Policy contract invalid: " + "; ".join(self.errors))


def _source_inventory(sources: Any) -> tuple[list[dict[str, str]], dict[str, str], list[str]]:
    errors: list[str] = []
    manifest: list[dict[str, str]] = []
    texts: dict[str, str] = {}
    if not isinstance(sources, list) or not sources:
        return [], {}, ["controller sources must be a nonempty list"]
    for i, source in enumerate(sources):
        path = f"controller.sources[{i}]"
        if not isinstance(source, dict):
            errors.append(path + " must be an object")
            continue
        sid, filename, text = source.get("id"), source.get("file"), source.get("text")
        if not isinstance(sid, str) or not sid.strip():
            errors.append(path + " requires a nonempty source id")
            continue
        if sid in texts:
            errors.append(path + " repeats a source id")
        if not isinstance(filename, str) or not filename.strip():
            errors.append(path + " requires a source filename")
        if not isinstance(text, str) or not text.strip():
            errors.append(path + " requires source text")
            continue
        actual_hash = digest(text)
        if source.get("sha256") != actual_hash:
            errors.append(path + " fingerprint differs from its source text")
        texts[sid] = text
        if isinstance(filename, str):
            manifest.append({"id": sid, "file": filename, "sha256": actual_hash})
    return sorted(manifest, key=lambda s: s["id"]), texts, errors


def _source_binding_errors(policy: Mapping[str, Any], manifest: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    expected = {source["id"]: source for source in manifest}
    policy_sources = policy.get("sources", [])
    policy_rules = policy.get("rules", [])
    policy_sources = policy_sources if isinstance(policy_sources, list) else []
    policy_rules = policy_rules if isinstance(policy_rules, list) else []
    declared = {source["id"]: source for source in policy_sources
                if isinstance(source, dict) and isinstance(source.get("id"), str)}
    if set(expected) != set(declared):
        errors.append("policy source inventory must exactly match all controller sources")
    for sid in expected.keys() & declared.keys():
        if any(declared[sid].get(key) != expected[sid][key] for key in ("file", "sha256")):
            errors.append("policy source filename/fingerprint must match the controller manifest")
    used = {rule["source"]["id"] for rule in policy_rules
            if isinstance(rule, dict) and isinstance(rule.get("source"), dict)
            and isinstance(rule["source"].get("id"), str)}
    if set(expected) - used:
        errors.append("every supplied source, including amendments, requires typed rule provenance")
    return errors


def _resolve_schema_refs(value: Any, document: dict, stack: tuple[str, ...] = ()) -> Any:
    """Local references only, preserving allOf for a fail-closed overlap check."""
    if isinstance(value, list):
        return [_resolve_schema_refs(v, document, stack) for v in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    if "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in stack:
            raise PolicyContractError(["API references must be nonrecursive local JSON pointers"])
        target: Any = document
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError, IndexError):
            raise PolicyContractError(["API contains an unresolved local reference"]) from None
        if not isinstance(target, dict):
            raise PolicyContractError(["API reference target must be an object"])
        resolved = _resolve_schema_refs(target, document, stack + (ref,))
        siblings = {key: _resolve_schema_refs(child, document, stack)
                    for key, child in value.items() if key != "$ref"}
        for key in resolved.keys() & siblings.keys():
            if key in {"description", "title", "$comment", "example", "examples"}:
                continue
            if canonical_json(resolved[key]) != canonical_json(siblings[key]):
                raise PolicyContractError(["conflicting API reference siblings are unsupported"])
        return {**resolved, **siblings}
    result = {key: _resolve_schema_refs(child, document, stack) for key, child in value.items()}
    if "allOf" in result:
        members = result["allOf"]
        if not isinstance(members, list) or not members or any(not isinstance(member, dict) for member in members):
            raise PolicyContractError(["API allOf requires nonempty object schemas"])
        properties: dict[str, str] = {}
        constraints: dict[str, str] = {}
        for member in [{key: child for key, child in result.items() if key != "allOf"}, *members]:
            fields = member.get("properties", {})
            if not isinstance(fields, dict):
                raise PolicyContractError(["API schema properties must be an object"])
            for name, schema in fields.items():
                fingerprint = canonical_json(schema)
                if name in properties and properties[name] != fingerprint:
                    raise PolicyContractError(["conflicting overlapping API allOf properties are unsupported"])
                properties[name] = fingerprint
            for key, child in member.items():
                if key in {"properties", "required", "description", "title", "$comment", "example", "examples"}:
                    continue
                fingerprint = canonical_json(child)
                if key in constraints and constraints[key] != fingerprint:
                    raise PolicyContractError(["conflicting API allOf constraints are unsupported"])
                constraints[key] = fingerprint
    return result


def _operation_catalogue(openapi: Any, allowed_actions: list[str]) -> list[dict[str, Any]]:
    if not isinstance(openapi, dict):
        raise PolicyContractError(["OpenAPI must be an object"])
    try:
        canonical_json(openapi)
        # Existing platform bindings flatten compatible allOf objects. Inspect the
        # original composition first so that a last-wins merge cannot weaken it.
        checked = _resolve_schema_refs(openapi, openapi)
        bound = operations(checked)
        path_for: dict[str, str] = {}
        for path, entry in checked.get("paths", {}).items():
            for method, operation in entry.items():
                if str(method).lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    name = operation.get("operationId") or path.rstrip("/").rsplit("/", 1)[-1]
                    path_for[name] = path
        by_name = {operation["name"]: operation for operation in bound}
        if set(allowed_actions) - set(by_name):
            raise PolicyContractError(["policy allowed_actions contains an unknown API operation"])
        catalogue = []
        for name in sorted(allowed_actions):
            operation = by_name[name]
            schemas: dict[str, dict[str, Any]] = {}
            for status, response in operation["responses"].items():
                if not isinstance(response, dict):
                    raise PolicyContractError(["API response definitions must be objects"])
                schemas[str(status)] = {media: copy.deepcopy(content["schema"])
                                        for media, content in response.get("content", {}).items()
                                        if isinstance(content, dict) and "schema" in content}
            catalogue.append({"name": name, "method": operation["method"],
                              "path": path_for[name], "url": operation["url"],
                              "request_schema": copy.deepcopy(operation["body_schema"]),
                              "response_schemas": schemas,
                              "parameters": copy.deepcopy(operation["parameters"]),
                              "security": copy.deepcopy(operation["security"]),
                              "security_schemes": copy.deepcopy(operation["security_schemes"])})
        return catalogue
    except PolicyContractError:
        raise
    except (BindingError, TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise PolicyContractError(["OpenAPI cannot be compiled into the supported operation/schema catalogue"]) from None


def _superseded_ids(rules: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> list[str]:
    """Collect historical ancestors after the override graph has been validated."""
    pending = [ident for rule in rules for ident in rule.get("overrides", [])]
    ancestors: set[str] = set()
    while pending:
        ident = pending.pop()
        if ident in ancestors:
            continue
        ancestors.add(ident)
        pending.extend(by_id[ident].get("overrides", []))
    return sorted(ancestors)


def compile_policy_contract(policy: Any, sources: Any, openapi: Any, *, conditional_mode: str = "reject") -> dict[str, Any]:
    """Return a deep-copied, deterministic contract or raise PolicyContractError.

    No rule has implicit precedence from source or array order. Conflicting active
    values require explicit acyclic overrides of the same semantic kind. Removing
    a constraint or replacing it with another kind is unsupported; represent the
    unresolved requirement explicitly rather than silently erasing a constraint.
    Conditional inspection validates partial scope without granting runtime
    enforcement. Default compilation rejects those conditions until integrated.
    """
    if type(conditional_mode) is not str or conditional_mode not in {"reject", "inspect"}:
        raise PolicyContractError(["conditional conflict mode must be reject or inspect"])
    manifest, documents, errors = _source_inventory(sources)
    errors.extend(validate_policy(policy, documents, strict=True))
    if isinstance(policy, dict):
        errors.extend(_source_binding_errors(policy, manifest))
        if policy.get("unsupported_constraints"):
            errors.append("policy contains unsupported constraints")
    if errors:
        raise PolicyContractError(errors)
    catalogue = _operation_catalogue(openapi, policy["allowed_actions"])
    active = {}
    by_id = {rule["id"]: rule for rule in policy["rules"]}
    for kind, rules in sorted(_active_rules(policy).items()):
        ordered = sorted(rules, key=lambda rule: rule["id"])
        ancestors = _superseded_ids(ordered, by_id)
        active[kind] = {"value": copy.deepcopy(ordered[0]["value"]),
                        "rule_ids": [rule["id"] for rule in ordered],
                        "provenance": [{"rule_id": rule["id"], "id": rule["source"]["id"],
                                        "quote": rule["source"]["quote"]} for rule in ordered],
                        "superseded_rule_ids": ancestors,
                        "superseded_provenance": [{"rule_id": ident, "id": by_id[ident]["source"]["id"],
                                                   "quote": by_id[ident]["source"]["quote"]}
                                                  for ident in ancestors]}
    contract = {"version": 1, "goal": policy["goal"], "active_rules": active,
                "permitted_operations": catalogue, "sources": copy.deepcopy(manifest),
                "provenance_is_operative": False,
                "semantic_correctness_verified": False}
    try:
        conditional = compile_conditional_conflicts(active, manifest)
    except ConditionalConflictError as exc:
        raise PolicyContractError(exc.errors) from exc
    if conditional is not None:
        if conditional_mode != "inspect":
            raise PolicyContractError(["conditional policy conflicts require runtime enforcement; only inspection is currently supported"])
        contract["conditional_conflicts"] = conditional
    contract["sha256"] = digest(contract)
    return contract

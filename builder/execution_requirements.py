"""Source-bound capability coverage before policy freeze; no service calls.

API permission is availability, not a requirement to use every bank operation.
The selected roles come from a quoted ``execution_actions`` rule. This module
checks the current sealed compiler's requirements without inventing policy
values, interpreting prose, or claiming that the selected goal is complete.
"""
from __future__ import annotations

import copy
from typing import Any

from .action_bindings import ActionBindingError, compile_action_bindings
from .contract import PolicyContractError, compile_policy_contract, _resolve_schema_refs
from .guards import (GuardCompileError, compile_guarded_workflow,
                     execution_action_catalogue, required_execution_kinds, _profile)
from .policy import PolicyError, compile_execution_policy, execution_rule_guidance
from .safety import canonical_json, digest


class ExecutionRequirementsError(ValueError):
    """Extraction-repairable missing coverage or unsupported compiler input."""
    def __init__(self, errors: list[str], missing_kinds: list[str] | tuple[str, ...] = ()):
        self.errors = tuple(sorted(set(errors)))
        self.missing_kinds = tuple(sorted(set(missing_kinds)))
        super().__init__("Execution coverage failed: " + "; ".join(self.errors))


def _inspection_plan(actions: list[str], catalogue: dict) -> dict:
    """Inert shape probe only: it is neither a generated nor deployed agent."""
    names = {action: "inspect_action_" + str(index) for index, action in enumerate(actions)}
    talk = {"name": "inspection", "node_type": "conversation",
            "task_message": "Technical compiler inspection only.",
            "transitions": [{"target_node": name, "description": "inspection",
                             "condition": {"operator": "always"}}
                            for name in names.values()]}
    nodes = {"inspection": talk}
    for action, name in names.items():
        nodes[name] = {"name": name, "node_type": "guard_action", "action": action,
                       "inputs": {field: "proposal_" + field for field in catalogue[action]["input_fields"]},
                       "success_node": "inspection", "blocked_node": "inspection",
                       "failure_node": "inspection"}
    return {"initial_node": "inspection", "nodes": nodes}


def _counted_annotation_view(resolution: dict, operation: str) -> tuple[list[tuple[int, dict]], list[str]]:
    """Inspect explicit annotations without rewriting any supplied policy data."""
    expected = ("promise_failure_resolution requires outer operation equal to the selected actual operation; "
                "api_annotations must be a nonempty object {document: openapi, HTTP-status: {pointer: nonempty string, quote: nonempty string}} "
                "or a nonempty array of exact {document: openapi, operation: selected actual operation, "
                "http_status: integer100..599, pointer: nonempty string, quote: nonempty string} rows; response statuses must be unique")
    if resolution.get("operation") != operation:
        return [], [expected]
    annotations = resolution.get("api_annotations")
    view = []
    if isinstance(annotations, dict):
        if annotations.get("document") != "openapi" or len(annotations) < 2:
            return [], [expected]
        for status, witness in annotations.items():
            if status == "document":
                continue
            if (not isinstance(status, str) or len(status) != 3 or not status.isascii()
                    or not status.isdigit() or not 100 <= int(status) <= 599
                    or not isinstance(witness, dict) or set(witness) != {"pointer", "quote"}
                    or any(not isinstance(witness[key], str) or not witness[key].strip() for key in ("pointer", "quote"))):
                return [], [expected]
            view.append((int(status), copy.deepcopy(witness)))
    elif isinstance(annotations, list) and annotations:
        for row in annotations:
            if (not isinstance(row, dict) or set(row) != {"document", "operation", "http_status", "pointer", "quote"}
                    or row["document"] != "openapi" or row["operation"] != operation
                    or type(row["http_status"]) is not int or not 100 <= row["http_status"] <= 599
                    or any(not isinstance(row[key], str) or not row[key].strip() for key in ("pointer", "quote"))):
                return [], [expected]
            view.append((row["http_status"], {"pointer": row["pointer"], "quote": row["quote"]}))
    else:
        return [], [expected]
    if len({status for status, _ in view}) != len(view):
        return [], [expected]
    return view, []


def _failure_source_errors(active: dict, openapi: dict, permitted: set[str],
                           operation_bindings: dict) -> list[str]:
    """Bind literal witnesses to exact API responses, never interpret prose.

    A matching quotation proves supplied annotation provenance and scope only;
    it does not prove that the annotation entails the claimed nonpersistence.
    Local references are resolved under the strict contract's existing rules.
    """
    guarantees = active.get("nonpersistent_failures", {}).get("value", {})
    if set(guarantees) - permitted:
        return ["nonpersistent_failures must name exact permitted API operation IDs"]
    document = _resolve_schema_refs(openapi, openapi)

    def witness_matches(operation: str, status: int, witness: Any) -> bool:
        if (not isinstance(witness, dict) or set(witness) != {"pointer", "quote"}
                or any(not isinstance(witness[key], str) or not witness[key].strip()
                       for key in ("pointer", "quote"))):
            return False
        pointer = witness["pointer"]
        if pointer.startswith("#/"):
            pointer = pointer[1:]
        if not pointer.startswith("/"):
            return False
        encoded = pointer[1:].split("/")
        if any("~" in part.replace("~0", "").replace("~1", "") for part in encoded):
            return False
        parts = [part.replace("~1", "/").replace("~0", "~") for part in encoded]
        if (len(parts) < 6 or parts[0] != "paths" or parts[3] != "responses"
                or parts[4] != str(status)
                or parts[2] not in {"get", "post", "put", "patch", "delete", "head", "options"}):
            return False
        try:
            supplied = document["paths"][parts[1]][parts[2]]
            actual = supplied.get("operationId") or parts[1].rstrip("/").rsplit("/", 1)[-1]
            if actual != operation:
                return False
            value = document
            for part in parts:
                value = value[part]
        except (KeyError, TypeError, AttributeError):
            return False
        return isinstance(value, str) and witness["quote"] in value

    errors = []
    for operation, rows in guarantees.items():
        for row in rows:
            if not witness_matches(operation, row["http_status"], row.get("api_source")):
                errors.append("nonpersistent_failures requires a literal api_source pointer/quote scoped to its actual permitted operation and exact response status")

    resolution = active.get("promise_failure_resolution", {}).get("value")
    if ("commit_promises" in operation_bindings and isinstance(resolution, dict)
            and "api_annotations" in resolution):
        operation = operation_bindings["commit_promises"]["operation"]
        annotations, shape_errors = _counted_annotation_view(resolution, operation)
        errors.extend(shape_errors)
        for status, witness in annotations:
            if not witness_matches(operation, status, witness):
                errors.append("promise_failure_resolution api_annotations must bind literal witnesses to the selected actual operation and exact response status")
                continue
            # Reconcile explicit annotations only; never supply absent matchers.
            matches = [row for row in guarantees.get(operation, [])
                       if row["http_status"] == status
                       and row.get("api_source", {}).get("pointer", "").removeprefix("#") == witness["pointer"].removeprefix("#")
                       and row.get("api_source", {}).get("quote") == witness["quote"]]
            if not matches:
                errors.append("promise_failure_resolution declared API annotation is omitted or differs from operative nonpersistent_failures api_source")
    return sorted(set(errors))


def _local_disposition_errors(actions: list[str], contract: dict, execution: dict,
                              inspection_plan: dict, bindings: dict) -> list[str]:
    """Check current local closure representability, never supply bank rules.

    The private guarded-profile selector is the authority for notes/reasons and
    outcome wire decoding. Its detached normalized result is inspected only;
    neither source policy nor frozen execution inputs are modified. The global
    closing vocabulary does not prove phase applicability or semantic meaning.
    """
    local_account_actions = {"get_account", "stage_note", "finalize_notes", "close_call"}
    if (not local_account_actions <= set(actions)
            or execution["profile"].get("record_notes") is not False):
        return []
    normalized = _profile(contract, execution, inspection_plan, bindings)
    # The supplied precedence array contains account outcomes only. Identity
    # dispositions can legitimately use the same global outcome vocabulary;
    # their presence grants no account-paid path or missing local marker.
    if ("paid" in normalized.get("close_precedence", [])
            and "paid" not in normalized.get("note_reasons", [])):
        return ["local disposition coverage cannot represent the supported paid closing meaning; source-backed note_reasons must include its local marker after frozen wire decoding; record_notes false does not authorize add_note or supply a default"]
    return []


def compile_execution_requirements(policy: Any, sources: Any, openapi: Any, *,
                                   declarations: str = "strict") -> dict[str, Any]:
    """Check source/typed/API coverage and return detached, hash-bound JSON.

    The public compiler probe checks the same catalogue and cross-rule guards
    used by the later build. It executes no guard code and performs no I/O.
    Literal provenance proves a witness exists, not its interpretation.
    """
    try:
        contract = compile_policy_contract(policy, sources, openapi, declarations=declarations)
    except PolicyContractError as exc:
        raise ExecutionRequirementsError(list(exc.errors)) from exc
    catalogue = execution_action_catalogue()
    guidance = execution_rule_guidance()
    active = contract["active_rules"]
    declaration = active.get("execution_actions")
    selected = declaration.get("value") if isinstance(declaration, dict) else None
    if (not isinstance(selected, list) or not selected
            or any(type(action) is not str or action not in catalogue for action in selected)
            or len(set(selected)) != len(selected)):
        raise ExecutionRequirementsError([
            "execution_actions requires a source-backed nonempty unique array of supported sealed action roles; consult execution_action_catalogue"])
    actions = sorted(selected)
    if "close_call" not in actions:
        raise ExecutionRequirementsError([
            "the current sealed phone compiler requires selected close_call coverage; a goal without that interface is unsupported, not supplied a default disposition"])
    required = {kind for action in actions for kind in catalogue[action]["required_kinds"]}
    if active.get("policy_applicability", {}).get("value") == {"products": "single"}:
        # Match the execution compiler's sourced cardinality waiver. Explicit
        # active dimensions remain required and are still shape-checked below.
        required -= {"product_order", "resolve_all_products"} - set(active)
    if "commit_promises" in actions and "promise_failure_disposition" in active:
        # Exhaustion consumes a local disposition even without note actions or
        # bank-note permission. An absent disposition remains conservative.
        required.add("note_reasons")
    required = sorted(required)
    missing = sorted(set(required) - set(active))
    errors = ["missing typed execution rule: " + kind + "; expected " + guidance[kind]
              for kind in missing]
    # Aggregate malformed recognized inputs so extraction need not discover
    # each independent shape failure in a separate long model repair.
    paired = {"confirmation_question": "confirmation_question_actions",
              "confirmation_question_actions": "confirmation_question"}
    for kind in sorted(set(active) & set(guidance)):
        # A paired kind checked alone always reports its partner missing.
        group = {name: active[name]["value"] for name in (kind, paired.get(kind)) if name in active}
        try:
            compile_execution_policy(group)
        except PolicyError as exc:
            errors.append(str(exc))
    if errors:
        raise ExecutionRequirementsError(errors, missing)
    aliases = active.get("tool_aliases", {}).get("value", {})
    roles = {entry["operation_role"] for entry in catalogue.values()}
    if (not isinstance(aliases, dict) or set(aliases) - roles
            or any(type(name) is not str or not name for name in aliases.values())):
        raise ExecutionRequirementsError(["tool_aliases keys must be exact operation_role values from execution_action_catalogue; stage/commit action names are not operation roles"])
    permitted = {operation["name"] for operation in contract["permitted_operations"]}
    resolved = {role: aliases.get(role, role) for role in roles if aliases.get(role, role) in permitted}
    if (set(aliases.values()) - permitted or len(set(resolved.values())) != len(resolved)
            or permitted - set(resolved.values())):
        raise ExecutionRequirementsError(["tool_aliases must resolve permitted API operations injectively; renamed operations require explicit mappings"])
    operation_bindings = {}
    for action in actions:
        role = catalogue[action]["operation_role"]
        local = role == "add_note" and active.get("record_notes", {}).get("value") is False
        actual = None if local else resolved.get(role)
        if not local and actual is None:
            errors.append("selected action lacks its permitted frozen API operation: " + action)
        operation_bindings[action] = {"operation_role": role, "operation": actual,
                                      "mode": "local_disposition" if local else "bank_api"}
    if errors:
        raise ExecutionRequirementsError(errors)
    errors = _failure_source_errors(active, openapi, permitted, operation_bindings)
    if errors:
        raise ExecutionRequirementsError(errors)
    try:
        bindings = compile_action_bindings(policy, openapi, declarations=declarations)
        if "commit_promises" in actions and "promise_failure_disposition" in active:
            # Use the same notes-then-reasons decoding as the sealed kernel,
            # through controller-validated mappings, without inventing codes.
            def disposition_meaning(value: str) -> str:
                for category in ("notes", "reasons"):
                    value = bindings["categories"].get(category, {}).get("wire_to_semantic", {}).get(value, value)
                return value
            reason = active["promise_failure_disposition"]["value"]["reason"]
            notes = active["note_reasons"]["value"]
            if disposition_meaning(reason) not in {disposition_meaning(value) for value in notes}:
                raise ExecutionRequirementsError([
                    "promise_failure_disposition reason must belong to note_reasons after frozen wire-code decoding"])
        execution = compile_execution_policy(policy, bindings, required_kinds=required)
        inspection_plan = _inspection_plan(actions, catalogue)
        local_errors = _local_disposition_errors(actions, contract, execution, inspection_plan, bindings)
        if local_errors:
            raise ExecutionRequirementsError(local_errors)
        probe = compile_guarded_workflow(inspection_plan, contract, openapi, execution, bindings)
    except GuardCompileError as exc:
        raise ExecutionRequirementsError(list(exc.errors)) from exc
    except (PolicyError, ActionBindingError) as exc:
        raise ExecutionRequirementsError([str(exc)]) from exc
    result = {"version": 1, "policy_sha256": digest(policy), "contract_sha256": contract["sha256"],
              "sources_sha256": digest(contract["sources"]), "openapi_sha256": digest(openapi),
              "action_catalogue_sha256": digest(catalogue), "actions": actions,
              "action_rule_ids": copy.deepcopy(declaration["rule_ids"]),
              "operation_bindings": operation_bindings, "required_kinds": required,
              "shape_guidance": {kind: guidance[kind] for kind in required},
              "bindings_sha256": bindings["sha256"], "execution_policy_sha256": execution["sha256"],
              "inspection_guard_manifest_sha256": probe["guard_manifest"]["sha256"],
              "semantic_completeness_verified": False, "runtime_behavior_verified": False}
    result["sha256"] = digest(result)
    return copy.deepcopy(result)


def validate_plan_execution(plan: Any, descriptor: Any, policy: Any,
                            sources: Any, openapi: Any, *, declarations: str = "strict") -> list[str]:
    """Reject context substitution and added or omitted selected capabilities.

    Call the ordinary sealed compiler next. This check neither changes frozen
    policy nor accepts a self-hashed caller assertion as coverage evidence.
    """
    try:
        expected = compile_execution_requirements(policy, sources, openapi, declarations=declarations)
        if canonical_json(descriptor) != canonical_json(expected):
            return ["frozen execution requirements descriptor differs from the policy/source/API/compiler context"]
        required_execution_kinds(plan)
    except ExecutionRequirementsError as exc:
        return list(exc.errors)
    except GuardCompileError as exc:
        return list(exc.errors)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        return ["malformed frozen execution requirements or action plan"]
    selected = {node["action"] for node in plan["nodes"].values() if node["node_type"] == "guard_action"}
    frozen = set(expected["actions"])
    return sorted(["plan action is outside frozen execution_actions: " + action
                   for action in sorted(selected - frozen)]
                  + ["plan omits selected execution_actions capability: " + action
                     for action in sorted(frozen - selected)])

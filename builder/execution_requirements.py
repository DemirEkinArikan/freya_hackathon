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
from .contract import PolicyContractError, compile_policy_contract
from .guards import (GuardCompileError, compile_guarded_workflow,
                     execution_action_catalogue, required_execution_kinds)
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


def compile_execution_requirements(policy: Any, sources: Any, openapi: Any) -> dict[str, Any]:
    """Check source/typed/API coverage and return detached, hash-bound JSON.

    The public compiler probe checks the same catalogue and cross-rule guards
    used by the later build. It executes no guard code and performs no I/O.
    Literal provenance proves a witness exists, not its interpretation.
    """
    try:
        contract = compile_policy_contract(policy, sources, openapi)
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
    required = sorted({kind for action in actions for kind in catalogue[action]["required_kinds"]})
    missing = sorted(set(required) - set(active))
    errors = ["missing typed execution rule: " + kind + "; expected " + guidance[kind]
              for kind in missing]
    # Aggregate malformed recognized inputs so extraction need not discover
    # each independent shape failure in a separate long model repair.
    for kind in sorted(set(active) & set(guidance)):
        try:
            compile_execution_policy({kind: active[kind]["value"]})
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
    try:
        bindings = compile_action_bindings(policy, openapi)
        execution = compile_execution_policy(policy, bindings, required_kinds=required)
        probe = compile_guarded_workflow(_inspection_plan(actions, catalogue), contract, openapi, execution, bindings)
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
                            sources: Any, openapi: Any) -> list[str]:
    """Reject context substitution and added or omitted selected capabilities.

    Call the ordinary sealed compiler next. This check neither changes frozen
    policy nor accepts a self-hashed caller assertion as coverage evidence.
    """
    try:
        expected = compile_execution_requirements(policy, sources, openapi)
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

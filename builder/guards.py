"""Compile declarative banking actions into sealed engine-owned subgraphs.

The compiler accepts a frozen contract and a typed execution profile, never a
model assertion that a guard exists. Its validator recompiles the entire graph.
Speech interpretation and the Freya sandbox still require hosted verification.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from .action_bindings import COLLECTION_ROLES
from .policy import _execution_errors, _profile_errors, _scoped_value_errors, _sourced_stop_words, _PROFILE_KINDS, _EXECUTION_ONLY_KINDS
from .contract import _operation_catalogue
from .safety import canonical_json, digest
from .toolbindings import derive_tools
from .call_variables import declared_call_variables
from .validation import extraction_entry_errors, _router_judged

PREFIX = "bg_"
# Controller resource bounds, independent of every bank's business counters.
CONTROLLED_ENTRY_LIMIT = 2048
DEFAULT_CONVERSATION_TURNS = 10
# Kit extraction confidence comes from token probabilities and a value below
# min_confidence is discarded without trace. A clear spoken date scored 0.889
# on the platform; 0.7 is the kit's lowest documented threshold. Free-value
# proposals are validated by the sealed kernel and the bank before any effect.
PROPOSAL_MIN_CONFIDENCE = 0.7
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_INPUTS = {
    "verify_identity": {"answer"}, "get_account": set(),
    "stage_promise": {"product_id", "amount", "date"},
    "commit_promises": set(), "stage_note": {"product_id", "reason"},
    "finalize_notes": set(), "stage_transfer": {"queue"},
    "commit_transfer": set(), "schedule_callback": {"date", "time"},
    "close_call": set(),
}
# The controller supplies the identity factor itself; a proposed field is used
# only for an identity_factor_state latest_check correction of a passed factor.
_OPTIONAL_INPUTS = {"verify_identity": {"field"}, "commit_promises": {"product_id"}, "close_call": {"outcome", "reason"}}
_OPERATIONS = {"verify_identity": "verify_identity", "get_account": "get_account",
               "commit_promises": "create_promise", "finalize_notes": "add_note",
               "commit_transfer": "transfer_to_agent", "schedule_callback": "schedule_callback",
               "close_call": "close_call"}
_FIELDS = {
    "verify_identity": ("customer_id", "field", "answer"), "get_account": ("customer_id",),
    "create_promise": ("customer_id", "product_id", "amount", "date"),
    "add_note": ("customer_id", "product_id", "reason"),
    "schedule_callback": ("customer_id", "date", "time"),
    "transfer_to_agent": ("customer_id", "queue"), "close_call": ("customer_id", "outcome", "reason"),
}
_RESULTS = {"verify_identity": ("ok", "verified", "wrong_answers_left", "hint", "error"),
            "get_account": ("ok", "products", "error"),
            "create_promise": ("ok", "promise_id", "error"),
            "add_note": ("ok", "error"), "schedule_callback": ("ok", "error"),
            "transfer_to_agent": ("ok", "error"), "close_call": ("ok", "error")}
_REQUIRED = {
    "verify_identity": {"identity_fields", "identity_wrong_limit"},
    "get_account": {"identity_fields", "identity_wrong_limit", "product_order", "resolve_all_products"},
    "stage_promise": {"identity_fields", "identity_wrong_limit", "band_ranges", "deadline_days",
                      "floor_fields", "holidays", "business_weekdays", "require_confirmation",
                      "promise_failure_limit", "product_order", "resolve_all_products"},
    # Absent API nonpersistence guarantees authorize no retry. An absent failure
    # disposition stays uncertain/quarantined; it never supplies a bank reason.
    "commit_promises": {"require_confirmation", "confirmation_scope", "promise_failure_limit", "promise_readback",
                        "product_order", "resolve_all_products"},
    "stage_note": {"note_reasons", "record_notes", "product_order", "resolve_all_products"},
    "finalize_notes": {"note_reasons", "record_notes", "product_order", "resolve_all_products"},
    "stage_transfer": {"transfer_reasons", "transfer_prerequisites"}, "commit_transfer": {"transfer_reasons", "transfer_prerequisites"},
    "schedule_callback": {"callback_window", "holidays", "business_weekdays"},
    "close_call": {"close_reasons", "record_notes"},
}

# Interface facts about the sealed kernel, never policy permissions or defaults.
# Accepted preparation is distinct from reconciled positive API evidence.
_CONTROLLER_STATE_EFFECTS = {
    "verify_identity": {
        "accepted_staging": ["Keeps the requested factor pending; sending an answer does not verify it."],
        "positive_recording": ["Validated ok=true identity results update cumulative wrong answers to max(existing wrong_answers, budget - returned wrong_answers_left), capped at the call-date-selected identity_wrong_limit, where budget is the API's declared wrong_answers_left maximum when declared, otherwise that limit; reaching the limit, or zero remaining, exhausts identity; ok=true alone does not verify a factor.",
                               "Only a validated verified=true result advances the submitted factor; verified=false leaves passed factors unchanged, except that under identity_factor_state latest_check a re-check of a passed factor withdraws its pass before sending and only verified=true restores it.",
                               "Under identity_counter_movement exact, any returned wrong_answers_left other than the expected remaining (unchanged on a match, one less on a mismatch) is uncertain verification, checked before exhaustion and success."],
        "rejections": ["Uncertain verification evidence, or an identity_failure_classes fault or failed exact repeat, ends verification: only the identity_close_choices row requiring that evidence can close, otherwise the call is quarantined.",
                       "An identity_failure_classes sealed receipt forbids every later bank call, including close_call; no disposition is recorded."]},
    "get_account": {
        "accepted_staging": ["Sending the account request does not establish loaded products."],
        "positive_recording": ["Validated positive account evidence loads the returned products once."]},
    "stage_promise": {
        "accepted_staging": ["Stores a pending promise for the selected product and removes that product's pending local disposition.",
                             "Clears the pending confirmation snapshot; staging does not record a bank promise."],
        "rejections": ["An already recorded promise or recorded note for that product prevents staging.",
                       "Under a resolve_all_products overdue scope, a product outside that scope cannot be staged.",
                       "An amount that is not positive, is below the product's floor_fields amount or exceeds its past_due amount is refused.",
                       "A staged or recorded transfer handoff refuses this action; after a handoff only commit_transfer and close_call are accepted.",
                       "A call whose sealed initial goal code is listed by a scoped goal_close refuses this action."]},
    "commit_promises": {
        "accepted_staging": ["Prepares the frozen staged promise batch; preparation and confirmation do not establish recording."],
        "positive_recording": ["Each validated positive result with its required persisted-record identity moves that product's pending promise into recorded promises."],
        "rejections": ["A staged or recorded transfer handoff refuses this action; after a handoff only commit_transfer and close_call are accepted.",
                       "A call whose sealed initial goal code is listed by a scoped goal_close refuses this action."]},
    "stage_note": {
        "accepted_staging": ["Stores the selected product's pending disposition and removes its pending promise.",
                             "Clears the pending confirmation snapshot; staging does not record a bank note."],
        "rejections": ["An already recorded promise or recorded note for that product prevents staging.",
                       "Under a resolve_all_products overdue scope, a product outside that scope cannot be staged.",
                       "A staged or recorded transfer handoff refuses this action; after a handoff only commit_transfer and close_call are accepted.",
                       "A call whose sealed initial goal code is listed by a scoped goal_close refuses this action."]},
    "finalize_notes": {
        "accepted_staging": ["Requires every product in the sealed resolve_all_products set (every loaded product for the boolean forms) resolved by a recorded promise or staged disposition, with no pending promise; an empty set with no staged disposition is refused.",
                             "Seals the disposition phase. When record_notes is false, dispositions remain local and no note API is called."],
        "positive_recording": ["When record_notes is true, each validated positive note result adds that product's recorded note; partial successes remain recorded."],
        "rejections": ["A staged or recorded transfer handoff refuses this action; after a handoff only commit_transfer and close_call are accepted.",
                       "A call whose sealed initial goal code is listed by a scoped goal_close refuses this action."]},
    "stage_transfer": {
        "accepted_staging": ["Stores a pending handoff, discards all pending promises and pending dispositions, and resets notes_finalized.",
                             "Preserves recorded promises; no persisted record is deleted and staging does not record a transfer.",
                             "Afterwards only commit_transfer and close_call are accepted."],
        "rejections": ["Any recorded note, existing handoff or queue without a transfer_prerequisites entry prevents staging; supplied queue prerequisites remain mandatory.",
                       "A call whose sealed initial goal code is listed by a scoped goal_close refuses this action."]},
    "commit_transfer": {
        "accepted_staging": ["Prepares the already staged handoff; sending it does not establish transfer success."],
        "positive_recording": ["Validated positive transfer evidence marks that handoff recorded."],
        "rejections": ["A call whose sealed initial goal code is listed by a scoped goal_close refuses this action."]},
    "schedule_callback": {
        "accepted_staging": ["Prepares the validated callback request; sending or confirming it does not establish a callback record."],
        "positive_recording": ["Validated positive callback evidence appends the submitted callback to recorded callbacks."]},
    "close_call": {
        "accepted_staging": ["Selects an eligible frozen disposition using protected state, then seals one close attempt; an attempted close is not a recorded close.",
                             "Before verification, with no recorded callback or verification evidence, an empty outcome and reason proposal selects the recorded identity phase's identity_close row when that row is one eligible declared choice; any other proposal must match a declared choice."],
        "positive_recording": ["Validated positive close evidence marks the close recorded."],
        "rejections": ["Every close result is terminal; negative or uncertain evidence cannot authorize another close attempt.",
                       "A refused close sets the quarantine flag and ends the call without a close; it never returns to dialogue."]},
}


class GuardCompileError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = tuple(sorted(set(errors)))
        super().__init__("Guard compilation failed: " + "; ".join(self.errors))


def execution_action_catalogue() -> dict[str, dict[str, Any]]:
    """Return detached interface metadata; this does not authorize an action.

    Staging uses the same semantic operation role as its eventual write. A
    caller still has to check the frozen API permissions and local-note mode.
    """
    operation_roles = {**_OPERATIONS, "stage_promise": "create_promise",
                       "stage_note": "add_note", "stage_transfer": "transfer_to_agent"}
    return {action: {"operation_role": operation_roles[action],
                     "input_fields": sorted(_INPUTS[action]),
                     "optional_input_fields": sorted(_OPTIONAL_INPUTS.get(action, set())),
                     "required_kinds": sorted(_REQUIRED[action]),
                     "controller_state_effects": copy.deepcopy(_CONTROLLER_STATE_EFFECTS[action])}
            for action in sorted(_INPUTS)}


def required_execution_kinds(plan: Mapping[str, Any]) -> list[str]:
    """Tell the controller which typed kinds are needed; supply no defaults."""
    try:
        errors = _plan_errors(plan)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise GuardCompileError(["malformed action plan for execution-kind selection"]) from None
    if errors:
        raise GuardCompileError(errors)
    kinds = set()
    for node in plan.get("nodes", {}).values():
        if isinstance(node, dict) and node.get("node_type") == "guard_action":
            kinds.update(_REQUIRED.get(node.get("action"), set()))
    return sorted(kinds)


def _hash_checked(value: Any, label: str) -> None:
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") != 1:
        raise GuardCompileError([label + " requires supported integer version 1"])
    if not isinstance(value, dict) or value.get("sha256") != digest({k: v for k, v in value.items() if k != "sha256"}):
        raise GuardCompileError([label + " has a missing or stale deterministic hash"])


def _plan_errors(plan: Any, operation_names: tuple[str, ...] | list[str] = (),
                 call_variables: tuple[str, ...] = ()) -> list[str]:
    errors, entry_errors = [], []
    if not isinstance(plan, dict) or set(plan) - {"initial_node", "nodes", "system_prompt"}:
        return ["plan supports only initial_node, nodes and system_prompt"]
    nodes = plan.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return ["plan.nodes must be a nonempty object"]
    if not isinstance(plan.get("initial_node"), str) or plan.get("initial_node") not in nodes:
        errors.append("plan.initial_node is unknown")
    protected_operations = set(_FIELDS) | set(operation_names)
    readonly_inputs = set(call_variables)
    def protected_read(name: Any) -> bool:
        return isinstance(name, str) and (name == "customer_id"
            or name.startswith(PREFIX) and name != "bg_facts"
            or any(name.startswith(operation + "_") for operation in protected_operations))
    def templates(value: Any, path: str, spoken: bool = False) -> None:
        if isinstance(value, str):
            for match in re.finditer(r"\{\{([^{}]+)\}\}", value):
                binding = match.group(1)
                if binding.split(".")[0] in readonly_inputs and binding not in readonly_inputs:
                    errors.append(path + ": call-input template must reference one declared variable exactly")
                names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", match.group(1))
                if any(protected_read(name) for name in names) or any(
                       re.search(r"(?<![A-Za-z0-9_])" + re.escape(op) + r"_[A-Za-z0-9_]", match.group(1))
                       for op in protected_operations):
                    errors.append(path + ": authored templates cannot read raw protected engine values")
                if spoken and "bg_facts" in names:
                    errors.append(path + ": authored speech cannot template facts containing internal action references")
        elif isinstance(value, dict):
            for key, item in value.items():
                if key == "variable" and protected_read(item):
                    errors.append(path + ".variable: authored conditions cannot read raw protected engine values")
                templates(item, path + "." + str(key), spoken or key == "on_enter")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                templates(item, path + "[" + str(index) + "]", spoken)
    templates(plan, "plan")
    def variable(name: Any) -> bool:
        return (isinstance(name, str) and bool(_NAME.fullmatch(name)) and not name.startswith(PREFIX)
                and name not in {"customer_id", "now"} | readonly_inputs
                and not any(name.startswith(operation + "_") for operation in protected_operations))
    for name, node in nodes.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name.startswith(PREFIX):
            errors.append("plan node names must be public identifiers")
        if not isinstance(node, dict) or node.get("name") != name:
            errors.append("plan node names must match their keys")
            continue
        kind = node.get("node_type")
        if kind == "guard_action":
            if set(node) - {"name", "node_type", "action", "inputs", "success_node", "blocked_node", "failure_node"}:
                errors.append(name + ": unsupported action fields")
            action = node.get("action")
            if not isinstance(action, str) or action not in _INPUTS:
                errors.append(name + ": unsupported action")
                continue
            inputs = node.get("inputs", {})
            required, optional = _INPUTS[action], _OPTIONAL_INPUTS.get(action, set())
            given = set(inputs) if isinstance(inputs, dict) else set()
            if not isinstance(inputs, dict) or not required <= given or given - required - optional:
                errors.append(name + ": wrong semantic input fields for " + action + ": inputs must be an object with "
                              + ("required " + ", ".join(sorted(required)) if required else "no required fields")
                              + (" and optional " + ", ".join(sorted(optional)) if optional else "")
                              + "; missing: " + (", ".join(sorted(required - given)) or "none")
                              + "; unsupported: " + (", ".join(sorted(given - required - optional)) or "none")
                              + ". Each value names a public extracted variable holding the proposal;"
                              " the controller validates it and does not choose it")
            elif not all(variable(value) for value in inputs.values()):
                errors.append(name + ": action inputs must reference untrusted public variables")
            for field in ("success_node", "blocked_node", "failure_node"):
                if node.get(field) not in nodes:
                    errors.append(name + ": unknown " + field)
        elif kind == "conversation":
            allowed = {"name", "node_type", "task_message", "routing_prompt", "on_enter", "on_enter_first_entry_only",
                       "extraction_schema", "transitions", "max_node_turns", "max_turn_transition",
                       "passthrough", "passthrough_default_transition"}
            if set(node) - allowed:
                errors.append(name + ": dialogue nodes cannot contain executable or unsupported fields")
            if not isinstance(node.get("task_message", ""), str):
                errors.append(name + ": task_message must be text")
            if "routing_prompt" in node and not isinstance(node["routing_prompt"], str):
                errors.append(name + ": routing_prompt must be text")
            extraction = node.get("extraction_schema", [])
            transitions = node.get("transitions", [])
            if not isinstance(extraction, list) or not isinstance(transitions, list):
                errors.append(name + ": extraction_schema and transitions must be arrays")
                continue
            if "passthrough" in node and type(node["passthrough"]) is not bool:
                errors.append(name + ": passthrough must be boolean")
            if "passthrough_default_transition" in node:
                default = node["passthrough_default_transition"]
                if not isinstance(default, str) or default not in nodes:
                    errors.append(name + ": passthrough default must target a declared public node")
            if node.get("passthrough") is True:
                default = node.get("passthrough_default_transition")
                fallback = transitions[-1] if transitions and isinstance(transitions[-1], dict) else {}
                # Entry routing is explicit. The default and unconditional
                # fallback must agree before the compiler seals public targets.
                if (not isinstance(default, str) or default not in nodes
                        or fallback.get("condition") != {"operator": "always"}
                        or fallback.get("is_tool_call", False) is not False
                        or fallback.get("target_node") != default):
                    errors.append(name + ": passthrough requires a public default and matching ordinary final always fallback")
            for index, entry in enumerate(extraction):
                if not isinstance(entry, dict) or not variable(entry.get("name")):
                    errors.append(name + ": extraction cannot write protected or engine variables")
                elif set(entry) - {"name", "type", "description", "enum_values", "validation_pattern", "max_retries", "min_confidence", "extraction_prompt"}:
                    errors.append(name + ": unsupported extraction fields")
                entry_errors.extend(extraction_entry_errors(entry, "node." + name + ".extraction_schema[" + str(index) + "]"))
            if any(not isinstance(t, dict) or not isinstance(t.get("condition"), dict) or set(t) - {"target_node", "description", "condition", "is_tool_call"} for t in transitions):
                errors.append(name + ": transitions must be documented transition objects")
            for target in [t.get("target_node") for t in transitions if isinstance(t, dict)] + [node.get("max_turn_transition")]:
                if target is not None and target not in nodes:
                    errors.append(name + ": routing may target public nodes only")
        else:
            errors.append(name + ": authored nodes must be conversation or guard_action")
    if not any(isinstance(n, dict) and n.get("action") == "close_call" for n in nodes.values()):
        errors.append("plan requires a guarded close_call; authored terminals are forbidden")
    # Final validation applies the same entry rules to the verbatim compiled
    # entries, so alone they do not stop compilation and hide its other errors.
    return errors + entry_errors if errors else []


def _dated_variants(contract: dict, profile: dict, kind: str) -> dict:
    """Bind a frozen date selector to its two retained sourced values.

    No clock is read here; the kernel compares the sealed call date at runtime.
    """
    selection = profile[kind]["effective_date"]
    refs = (selection["before_rule"], selection["on_or_after"]["rule"])
    retained = contract["active_rules"][kind].get("referenced_rule_values")
    if not isinstance(retained, dict) or set(retained) != set(refs) or refs[0] == refs[1]:
        raise GuardCompileError(["effective_date selector lacks its retained sourced variants"])
    bands = {band["name"] for band in profile.get("band_ranges", [])}
    for ref in refs:
        if (_scoped_value_errors(kind, retained[ref]) is not None or _profile_errors(kind, retained[ref])
                or kind == "deadline_days" and "band_ranges" in profile and set(retained[ref]) != bands):
            raise GuardCompileError(["effective_date variants must be primitive values of their kind covering the declared bands"])
    return {"effective_date": {"date": selection["on_or_after"]["date"],
                               "before": copy.deepcopy(retained[refs[0]]),
                               "on_or_after": copy.deepcopy(retained[refs[1]])}}


def _profile(contract: dict, execution_policy: dict, plan: dict, bindings: dict | None) -> dict:
    _hash_checked(contract, "contract")
    _hash_checked(execution_policy, "execution policy")
    # The conditional-policy library can inspect these rules, but this kernel
    # has no protected consumer for them. Inspection is never authorization,
    # including a descriptor that claims it is clear or already enforced.
    if "conditional_conflicts" in contract or "conditional_conflicts" in contract.get("active_rules", {}):
        raise GuardCompileError(["conditional policy conflicts lack a protected runtime guard; inspection cannot authorize execution"])
    if "bindings_sha256" in execution_policy and execution_policy["bindings_sha256"] != (bindings or {}).get("sha256"):
        raise GuardCompileError(["execution policy is bound to different action bindings"])
    profile = execution_policy.get("profile")
    if not isinstance(profile, dict):
        raise GuardCompileError(["execution policy requires a typed profile"])
    errors = []
    # An optional frozen execution rule cannot be silently omitted by a caller
    # supplying a narrower profile (for example, transfer confirmation).
    for kind in set(contract.get("active_rules", {})) & (_PROFILE_KINDS | _EXECUTION_ONLY_KINDS):
        if kind not in profile:
            errors.append("active execution rule omitted from profile: " + kind)
    singleton = profile.get("policy_applicability") == {"products": "single"}
    count_bounds = profile.get("product_count_bounds", {})
    loads_account = any(isinstance(node, dict) and node.get("action") == "get_account"
                        for node in plan.get("nodes", {}).values())
    if (loads_account and singleton and isinstance(count_bounds, dict)
            and type(count_bounds.get("min")) is int and count_bounds["min"] > 1):
        errors.append("product_count_bounds minimum exceeds supplied single-product scope")
    for kind in required_execution_kinds(plan):
        if kind not in profile and not (singleton and kind in {"product_order", "resolve_all_products"}):
            errors.append("missing typed execution kind: " + kind)
    for kind, value in profile.items():
        active = contract.get("active_rules", {}).get(kind)
        if not isinstance(active, dict) or canonical_json(value) != canonical_json(active.get("value")):
            errors.append("execution profile does not match active frozen rule: " + kind)
        errors.extend(_profile_errors(kind, value))
        if _scoped_value_errors(kind, value) is not None:
            continue
        if kind == "record_notes" and type(value) is not bool:
            errors.append(kind + " must be boolean")
        elif kind == "confirmation_scope" and value not in {"each", "batch"}:
            errors.append("confirmation_scope must be each or batch")
        elif kind in {"confirmation_actions", "close_precedence"} and (not isinstance(value, list) or kind == "close_precedence" and not value or any(not isinstance(x, str) or not x for x in value) or len(set(value)) != len(value)):
            errors.append(kind + " must be a unique list of semantic names (precedence nonempty)")
        elif kind == "product_order" and (not isinstance(value, dict) or
                value != {"mode": "account_order"} and (set(value) != {"field", "direction", "tie_breaker"} or not isinstance(value.get("field"), str) or not value["field"] or value.get("direction") not in {"ascending", "descending"} or value.get("tie_breaker") != "account_order")):
            errors.append("product_order requires explicit account_order mode or field/direction/account_order-ties")
        elif kind == "promise_failure_disposition" and (not isinstance(value, dict) or set(value) != {"reason", "clear_pending"} or not isinstance(value.get("reason"), str) or type(value.get("clear_pending")) is not bool):
            errors.append("promise_failure_disposition requires reason and clear_pending")
        elif kind == "nonpersistent_failures":
            if not isinstance(value, dict) or any(not isinstance(op, str) or not isinstance(cases, list) for op, cases in value.items()):
                errors.append("nonpersistent_failures requires operation-to-classifications mapping")
            else:
                for op, cases in value.items():
                    for case in cases:
                        if (not isinstance(case, dict) or set(case) - {"http_status", "error", "api_source"}
                                or type(case.get("http_status")) is not int or not 100 <= case["http_status"] <= 599
                                or ("error" in case and (not isinstance(case["error"], str) or not case["error"]))
                                or ("api_source" in case and
                                    (not isinstance(case["api_source"], dict)
                                     or set(case["api_source"]) != {"pointer", "quote"}
                                     or any(not isinstance(case["api_source"][key], str) or not case["api_source"][key]
                                            for key in ("pointer", "quote"))))):
                            errors.append("nonpersistent failure classifications need HTTP status100..599, optional exact error and optional pointer/quote API witness")
        elif kind == "identity_close":
            if not isinstance(value, dict) or set(value) != {"failed", "incomplete", "unreached"} or any(not isinstance(p, dict) or set(p) - {"outcome", "reason"} or not isinstance(p.get("outcome"), str) or ("reason" in p and not isinstance(p["reason"], str)) for p in value.values()):
                errors.append("identity_close requires failed, incomplete and unreached outcome/reason records")
        elif kind == "identity_close_choices":
            if not isinstance(value, dict) or not value or set(value) - {"unreached", "incomplete"}:
                errors.append("identity_close_choices requires a nonempty subset of unreached/incomplete scopes")
            else:
                for scope, choices in value.items():
                    if not isinstance(choices, list) or not choices:
                        errors.append("identity_close_choices scopes require nonempty choice arrays")
                        continue
                    for choice in choices:
                        if (not isinstance(choice, dict) or set(choice) - {"outcome", "reason", "requires"}
                                or not isinstance(choice.get("outcome"), str) or not choice["outcome"]
                                or choice.get("requires") not in ("none", "recorded_callback", "no_recorded_callback",
                                                                  "uncertain_verification", "definite_verification_failure")
                                or "reason" in choice and (not isinstance(choice["reason"], str) or not choice["reason"])):
                            errors.append("identity_close_choices require outcome, optional nonempty reason and supported evidence")
                evidence = [choice.get("requires") for choices in value.values() if isinstance(choices, list)
                            for choice in choices if isinstance(choice, dict)]
                if any(evidence.count(name) > 1 for name in ("uncertain_verification", "definite_verification_failure")):
                    errors.append("identity_close_choices allow at most one row per verification evidence")
        elif kind == "goal_close":
            errors.extend(_execution_errors(kind, value))
        elif kind == "transfer_prerequisites" and (not isinstance(value, dict) or not value or any(not isinstance(k, str) or v not in {"none", "identity", "account", "unstated"} for k, v in value.items())):
            errors.append("transfer_prerequisites requires queue-to-none/identity/account/unstated mapping")
        elif kind == "promise_readback" and value not in ("product_type", "none"):
            if (not isinstance(value, dict) or set(value) - {"product_fields", "value_labels"}
                    or not isinstance(value.get("product_fields"), list) or not value["product_fields"]
                    or any(not isinstance(f, str) or not f or f in {"customer_id", "product_id"} for f in value["product_fields"])
                    or not isinstance(value.get("value_labels", {}), dict)
                    or any(not isinstance(labels, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in labels.items()) for labels in value.get("value_labels", {}).values())):
                errors.append("promise_readback needs human product fields and optional field value labels, excluding internal IDs")
    selected_actions = {node.get("action") for node in plan.get("nodes", {}).values()
                        if isinstance(node, dict) and node.get("node_type") == "guard_action"}
    scopes = profile.get("attempt_scopes", {})
    limits = {"commit_promises": "promise_failure_limit", "finalize_notes": "note_failure_limit",
              "schedule_callback": "callback_failure_limit", "commit_transfer": "transfer_failure_limit"}
    if isinstance(scopes, dict):
        for action in selected_actions & set(scopes) & set(limits):
            limit = profile.get(limits[action])
            if type(limit) is not int or limit < 1:
                errors.append("selected attempt_scopes action requires its explicit positive " + limits[action])
        resume = profile.get("definite_failure_resume", [])
        if isinstance(resume, list) and all(isinstance(action, str) for action in resume):
            for action in set(resume) & selected_actions:
                if action not in scopes:
                    errors.append("selected definite_failure_resume action requires an explicit attempt_scopes entry")
    if ("confirmation_question" in profile) != ("confirmation_question_actions" in profile):
        errors.append("prescribed confirmation wording requires its explicit source-backed action scope")
    wording = profile.get("confirmation_readback")
    if "confirmation_readback" in profile and not _profile_errors("confirmation_readback", wording):
        provenance = contract.get("active_rules", {}).get("confirmation_readback", {}).get("provenance", [])
        if not provenance or any(not isinstance(source.get("quote"), str) or any(
                not re.search(r"(?<!\w)" + re.escape(item) + r"(?!\w)", source["quote"]) for item in wording["clauses"]) for source in provenance):
            errors.append("confirmation_readback wording must occur literally in its own sourced quotation")
        confirmed = set(profile.get("confirmation_actions", [])) | ({"commit_promises"} if profile.get("require_confirmation") is True else set())
        if selected_actions & set(wording["actions"]) - confirmed:
            errors.append("confirmation_readback names a selected action that requires no confirmation")
    if errors:
        raise GuardCompileError(errors)
    result = copy.deepcopy(profile)
    for kind in ("identity_wrong_limit", "deadline_days"):
        if _scoped_value_errors(kind, result.get(kind)) == []:
            result[kind] = _dated_variants(contract, result, kind)
    # Keep unused disclosure metadata frozen and shape-checked above, but do
    # not approve account narration or bind its schema/type codes for a goal
    # that never loads an account. This detached runtime selection grants no
    # fields; it is not a replacement policy rule or an added API permission.
    if not loads_account and "account_disclosure_fields" in result:
        result["account_disclosure_fields"] = []
    # Absence grants no priority: the kernel accepts only a unique protected
    # fact or a sourced identity disposition, and rejects ambiguous facts.
    # This detached runtime representation is not a frozen policy rule.
    result.setdefault("close_precedence", [])
    # No sourced identity disposition grants no bank closing choice. The
    # existing close handler can still use protected account/transfer facts;
    # otherwise it refuses the request and ends silently without a bank call.
    result.setdefault("identity_close", {})
    if result.get("policy_applicability") == {"products": "single"}:
        # Conservative kernel invariant: the sole product must be resolved.
        # This is not an invented cross-product policy rule or sort order;
        # explicitly supplied resolution rules still take precedence.
        result.setdefault("resolve_all_products", True)
    readback = result.get("promise_readback")
    if readback == "none":
        # A sourced absence of any commitment readback, never a way around a
        # required confirmation; no product field is read back at all.
        if result.get("require_confirmation") is True or "commit_promises" in result.get("confirmation_actions", []):
            raise GuardCompileError(["promise_readback none conflicts with a required commitment confirmation"])
        del result["promise_readback"]
    elif readback == "product_type":
        # Only the protected product type that already selects floors.
        result["promise_readback"] = {"product_fields": ["type"]}
    resolution = result.get("resolve_all_products")
    formats = result.get("call_input_formats")
    if formats is not None:
        if set(formats) - set(declared_call_variables(contract)):
            raise GuardCompileError(["formatted call inputs require source-declared read-only names"])
        provenance = contract["active_rules"]["call_input_formats"].get("provenance", [])
        if not provenance or any(not isinstance(source.get("quote"), str) or any(
                not re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", source["quote"]) for name in formats)
                for source in provenance):
            raise GuardCompileError(["formatted call-input names must occur literally in their own sourced rule"])
    if isinstance(resolution, dict) and "selector" in resolution:
        selector = resolution["selector"]
        if selector not in declared_call_variables(contract):
            raise GuardCompileError(["product-resolution selector requires a source-declared read-only call input"])
        provenance = contract["active_rules"]["resolve_all_products"].get("provenance", [])
        if not provenance or any(not isinstance(source.get("quote"), str) or
                not re.search(r"(?<!\w)" + re.escape(selector) + r"(?!\w)", source["quote"]) for source in provenance):
            raise GuardCompileError(["product-resolution selector must occur literally in its own sourced rule"])
        # Categories with other names carry no selector meaning. In particular
        # a generic goals category cannot silently reinterpret a call input.
        codes = result.get("wire_codes", {}).get(selector, {})
        selected = resolution["by_value"]
        if any(key in codes.values() and codes.get(key, key) != key for key in selected):
            raise GuardCompileError(["product-resolution key is ambiguous between semantic and wire selector codes"])
        normalized = {codes.get(key, key): required for key, required in selected.items()}
        if len(normalized) != len(selected):
            raise GuardCompileError(["product-resolution selector keys collide through frozen code bindings"])
        resolution["by_value"] = normalized
    scope = result.get("goal_close", {})
    if "selector" in scope:
        # Rows close only calls whose sealed initial goal code is listed; the
        # kernel keeps those calls from every promise, note and transfer role.
        selector = scope["selector"]
        provenance = contract["active_rules"]["goal_close"].get("provenance", [])
        if selector not in declared_call_variables(contract) or not provenance or any(
                not isinstance(source.get("quote"), str)
                or not re.search(r"(?<!\w)" + re.escape(selector) + r"(?!\w)", source["quote"]) for source in provenance):
            raise GuardCompileError(["per-goal close selector must be a source-declared read-only call input named literally in its own sourced rule"])
        codes = result.get("wire_codes", {}).get(selector, {})
        if any(key in codes.values() and codes.get(key, key) != key for key in scope["goals"]):
            raise GuardCompileError(["per-goal close code is ambiguous between semantic and wire selector codes"])
        scope["goals"] = [codes.get(key, key) for key in scope["goals"]]
        if len(set(scope["goals"])) != len(scope["goals"]):
            raise GuardCompileError(["per-goal close codes collide through frozen code bindings"])
        if codes and set(scope["goals"]) - set(codes.values()):
            raise GuardCompileError(["per-goal close goals must be keys or codes of the wire_codes category named by selector"])
        # A goal that must resolve products is a collecting goal: it closes on
        # its protected fact, never on a goal row.
        rule = result.get("resolve_all_products", False) if loads_account else False
        if rule is not False and not (isinstance(rule, dict) and rule.get("selector") == selector
                                      and all(rule["by_value"].get(code) is False for code in scope["goals"])):
            raise GuardCompileError(["per-goal close goals must map to false in the same-selector resolve_all_products rule"])
    # Decode controller-verified wire categories only for internal semantics.
    categories = (bindings or {}).get("categories", {})
    def decode(group: str, value: str) -> str:
        return categories.get(group, {}).get("wire_to_semantic", {}).get(value, value)
    if "note_reasons" in result:
        result["note_reasons"] = [decode("reasons", decode("notes", x)) for x in result["note_reasons"]]
    if "close_reasons" in result:
        result["close_reasons"] = {decode("outcomes", k): [decode("reasons", x) for x in v] for k, v in result["close_reasons"].items()}
    if "transfer_reasons" in result:
        result["transfer_reasons"] = {decode("queues", k): decode("reasons", v) for k, v in result["transfer_reasons"].items()}
    if "transfer_prerequisites" in result:
        result["transfer_prerequisites"] = {decode("queues", k): v for k, v in result["transfer_prerequisites"].items()}
    if "close_precedence" in result:
        result["close_precedence"] = [decode("outcomes", x) for x in result["close_precedence"]]
    if "identity_close" in result:
        result["identity_close"] = {k: {field: decode("outcomes" if field == "outcome" else "reasons", value)
                                      for field, value in proposal.items()} for k, proposal in result["identity_close"].items()}
    if "identity_close_choices" in result:
        result["identity_close_choices"] = {scope: [
            {key: decode("outcomes" if key == "outcome" else "reasons", value)
             if key in {"outcome", "reason"} else value for key, value in choice.items()}
            for choice in choices] for scope, choices in result["identity_close_choices"].items()}
    if "goal_close" in result:
        result["goal_close"] = {phase: rows if phase in ("selector", "goals") else [
            {key: decode("outcomes" if key == "outcome" else "reasons", value)
             if key in {"outcome", "reason"} else value for key, value in row.items()}
            for row in rows] for phase, rows in result["goal_close"].items()}
    disclosure = result.get("account_disclosure_fields")
    if isinstance(disclosure, dict):
        # This is a frozen sourced code map, not a guessed product taxonomy.
        # The disclosure compiler additionally binds every selected wire code
        # to the exact get_account product-type schema.
        codes = result.get("wire_codes", {}).get("product_types", {})
        if any(kind in codes.values() and codes.get(kind, kind) != kind for kind in disclosure["by_product_type"]):
            raise GuardCompileError(["account disclosure key is ambiguous between semantic and wire product types"])
        selected = {codes.get(kind, kind): fields for kind, fields in disclosure["by_product_type"].items()}
        if len(selected) != len(disclosure["by_product_type"]):
            raise GuardCompileError(["account disclosure product-type keys collide through frozen code bindings"])
        result["account_disclosure_fields"]["by_product_type"] = selected
    if "promise_failure_disposition" in result:
        result["promise_failure_disposition"]["reason"] = decode("reasons", decode("notes", result["promise_failure_disposition"]["reason"]))
        commits = any(node.get("action") == "commit_promises" for node in plan["nodes"].values())
        if commits and result["promise_failure_disposition"]["reason"] not in result.get("note_reasons", []):
            raise GuardCompileError(["supplied promise failure disposition requires its explicit note_reasons/local-disposition binding"])
    if set(result.get("confirmation_actions", [])) - {"commit_promises", "commit_transfer", "schedule_callback"}:
        raise GuardCompileError(["confirmation_actions includes an unsupported customer-write action"])
    # A permitted closing queue without a supplied prerequisite entry is never
    # dispatched; closing vocabulary alone grants no transfer.
    if set(result.get("transfer_prerequisites", {})) - set(result.get("transfer_reasons", {})):
        raise GuardCompileError(["transfer prerequisite queues must belong to permitted transfer queues"])
    if set(result.get("close_precedence", [])) - {"transferred", "promise", "paid", "no_promise"}:
        raise GuardCompileError(["close_precedence requires supported semantic outcomes or explicit wire bindings"])
    for scope, proposal in result.get("identity_close", {}).items():
        if proposal["outcome"] == "callback":
            # Every row of a phase shares its outcome, and the phase also closes without a callback.
            raise GuardCompileError(["identity_close outcomes cannot be bound to callback: that phase also closes without "
                                     "a recorded callback; a callback reason in identity_close_choices requiring "
                                     "recorded_callback can report one"])
        allowed = result.get("close_reasons", {}).get(proposal["outcome"])
        if allowed and "reason" not in proposal:
            # A reasonless phase row refined by sourced choices only supplies
            # the phase outcome: the kernel closes through one declared choice,
            # each checked below, and never on this row. No reason is picked.
            if scope in result.get("identity_close_choices", {}):
                continue
            raise GuardCompileError(["identity_close scope omits the reason its outcome requires; supply its sourced reason, "
                                     "or list each sourced reason as identity_close_choices for an unreached/incomplete scope"])
        if allowed is None or (allowed and proposal.get("reason") not in allowed) or (not allowed and proposal.get("reason")):
            raise GuardCompileError(["identity_close disposition conflicts with allowed closing outcomes/reasons"])
    for scope, choices in result.get("identity_close_choices", {}).items():
        fixed = result.get("identity_close", {}).get(scope)
        seen = set()
        for choice in choices:
            if fixed is None or choice["outcome"] != fixed["outcome"]:
                raise GuardCompileError(["identity closing choice outcome must match its fixed scope"])
            allowed = result.get("close_reasons", {}).get(choice["outcome"])
            reason = choice.get("reason", "")
            if allowed is None or (allowed and reason not in allowed) or (not allowed and reason):
                raise GuardCompileError(["identity closing choice conflicts with allowed closing reasons"])
            if reason in seen:
                raise GuardCompileError(["identity closing choices have duplicate decoded reasons"])
            seen.add(reason)
            if "callback" in (choice["outcome"], reason) and choice["requires"] != "recorded_callback":
                raise GuardCompileError(["callback-success closing choices require recorded_callback evidence"])
            if "no_callback" in (choice["outcome"], reason) and choice["requires"] != "no_recorded_callback":
                raise GuardCompileError(["no_callback closing choices require no_recorded_callback evidence"])
    # A protected outcome is a recorded fact; no sourced identity row stands in
    # for it. Rows are decoded above, so a code bound to that meaning counts.
    # Checked after the row loops, so shapes they refuse keep their messages.
    identity_rows = [*result.get("identity_close", {}).values(),
                     *(row for rows in result.get("identity_close_choices", {}).values() for row in rows)]
    if any(row["outcome"] in {"transferred", "promise", "paid", "no_promise"} for row in identity_rows):
        raise GuardCompileError(["identity closing rows cannot use a protected-fact outcome (promise, paid, transferred, "
                                 "no_promise or a code bound to one): only its recorded fact closes with it; a source "
                                 "requiring otherwise is an unsupported constraint"])
    # Goal rows close only phases with no protected collections fact. Payment,
    # note and transfer goals keep their evidence-only closing semantics.
    goal_close = result.get("goal_close", {})
    if goal_close and "selector" not in goal_close and selected_actions & COLLECTION_ROLES:
        raise GuardCompileError(["goal_close cannot be combined with promise, note or transfer roles "
                                 "unless selector and goals scope it to goals that use none of them"])
    for phase in set(goal_close) - {"selector", "goals"}:
        seen = set()
        for row in goal_close[phase]:
            allowed = result.get("close_reasons", {}).get(row["outcome"])
            reason = row.get("reason", "")
            if (row["outcome"] in {"transferred", "promise", "paid", "no_promise"} or allowed is None
                    or (allowed and reason not in allowed) or (not allowed and reason)):
                raise GuardCompileError(["goal closing rows require sourced non-collection outcomes and reasons"])
            if ("callback" in (row["outcome"], reason) and row["requires"] != "recorded_callback"
                    or "no_callback" in (row["outcome"], reason) and row["requires"] != "no_recorded_callback"):
                raise GuardCompileError(["goal closing callback reasons require matching callback evidence"])
            if (row["outcome"], reason) in seen:
                raise GuardCompileError(["goal closing rows are duplicated"])
            seen.add((row["outcome"], reason))
    return result


def _catalogue(contract: dict, openapi: dict, bindings: dict | None) -> dict:
    actual_catalogue = _operation_catalogue(openapi, [op["name"] for op in contract["permitted_operations"]])
    if canonical_json(actual_catalogue) != canonical_json(contract["permitted_operations"]):
        raise GuardCompileError(["OpenAPI schema/security differs from frozen contract"])
    tools = {t["function"]["name"]: t for t in derive_tools(openapi)}
    catalog = {}
    rules = contract["active_rules"]
    action_fields = rules.get("action_fields", {}).get("value", {})
    result_fields = rules.get("result_fields", {}).get("value", {})
    product_fields = rules.get("product_fields", {}).get("value", {})
    aliases = rules.get("tool_aliases", {}).get("value", {})
    if (not isinstance(aliases, dict) or set(aliases) - set(_FIELDS)
            or any(not isinstance(actual, str) or not actual for actual in aliases.values())):
        raise GuardCompileError(["tool_aliases must map supported canonical roles to actual API operation IDs"])
    permitted = {op["name"]: op for op in contract["permitted_operations"]}
    if set(aliases.values()) - set(permitted):
        raise GuardCompileError(["tool_aliases references an unknown or nonpermitted API operation"])
    resolved = {role: aliases.get(role, role) for role in _FIELDS
                if aliases.get(role, role) in permitted}
    if len(set(resolved.values())) != len(resolved):
        raise GuardCompileError(["tool_aliases must resolve semantic roles injectively, including canonical defaults"])
    if set(permitted) - set(resolved.values()):
        raise GuardCompileError(["renamed API operations require explicit unambiguous tool_aliases"])
    role_for_actual = {actual: role for role, actual in resolved.items()}
    for op in contract["permitted_operations"]:
        actual_name = op["name"]
        name = role_for_actual[actual_name]
        if actual_name not in tools or tools[actual_name]["function"]["parameters"]["properties"]["url"]["default"] != op["url"]:
            raise GuardCompileError(["OpenAPI differs from frozen operation catalogue"])
        request = op["request_schema"]
        response = next((s for media in op["response_schemas"].values() for s in media.values()
                         if isinstance(s, dict) and "properties" in s), {})
        fields = {}
        for semantic in _FIELDS[name]:
            actual = action_fields.get(name, {}).get(semantic, semantic)
            if semantic == "reason" and name == "close_call" and actual not in request.get("properties", {}):
                continue
            if actual not in request.get("properties", {}):
                raise GuardCompileError([name + ": missing unambiguous request field for " + semantic])
            fields[semantic] = actual
        if set(request.get("required", [])) - set(fields.values()):
            raise GuardCompileError([name + ": unbound required request fields"])
        if len(set(fields.values())) != len(fields):
            raise GuardCompileError([name + ": semantic request fields cannot share an API field"])
        for semantic, actual in fields.items():
            allowed_types = {"number", "integer"} if semantic == "amount" else {"string"}
            if request["properties"][actual].get("type") not in allowed_types:
                raise GuardCompileError([name + ": unsupported request type for " + semantic])
        results = {}
        for semantic in _RESULTS[name]:
            actual = result_fields.get(name, {}).get(semantic, semantic)
            if actual in response.get("properties", {}):
                results[semantic] = actual
            elif semantic in {"ok", "verified", "products", "promise_id", "wrong_answers_left"}:
                raise GuardCompileError([name + ": missing unambiguous trusted result field for " + semantic])
        if len(set(results.values())) != len(results):
            raise GuardCompileError([name + ": trusted result roles cannot share an API field"])
        if {"bg_http_status", "bg_result_types"} & set(results.values()):
            raise GuardCompileError([name + ": bank result field collides with reserved envelope status transport"])
        for semantic, actual in results.items():
            leaf = response["properties"][actual]
            kind = leaf.get("type")
            if semantic in {"ok", "verified"}:
                valid = kind == "boolean" or (kind == "string" and bool(leaf.get("enum"))
                    and all(type(v) is str and v in {"true", "false", "True", "False"} for v in leaf["enum"]))
            elif semantic == "products":
                valid = kind == "array" and leaf.get("items", {}).get("type") == "object"
            elif semantic == "wrong_answers_left":
                valid = kind == "integer"
            else:
                valid = kind == "string"
            if not valid:
                raise GuardCompileError([name + ": unsupported trusted result schema for " + semantic])
        # Raw JSON types each trusted result field may take in any declared
        # response schema. A field that some schema leaves untyped is unchecked.
        declared = [s["properties"] for media in op["response_schemas"].values() for s in media.values()
                    if isinstance(s, dict) and isinstance(s.get("properties"), dict)]
        result_types = {}
        for semantic, actual in results.items():
            found = [schema[actual].get("type") if isinstance(schema[actual], dict) else None
                     for schema in declared if actual in schema]
            if all(type(kind) is str for kind in found):
                result_types[semantic] = sorted(set(found))
        catalog[name] = {"name": actual_name, "path": op["path"], "method": op["method"],
                         "request_schema": copy.deepcopy(request), "fields": fields,
                         "results": results, "result_types": result_types,
                         "tool": copy.deepcopy(tools[actual_name])}
        if name == "verify_identity":
            budget = response["properties"][results["wrong_answers_left"]].get("maximum")
            if type(budget) is int and budget >= 1:
                catalog[name]["wrong_answers_budget"] = budget
        if name == "get_account":
            product_schema = response["properties"][results["products"]].get("items", {})
            keys = product_schema.get("properties", {})
            mapping = {s: product_fields.get(s, s) for s in ("product_id", "type", "days_late", "past_due")}
            if any(field not in keys for field in mapping.values()):
                raise GuardCompileError(["get_account: product field aliases require explicit active product_fields"])
            catalog[name]["product_fields"] = mapping
            catalog[name]["product_schema"] = copy.deepcopy(product_schema)
            # Declared non-2xx responses whose success flag can be false: the
            # only shapes of definite negative account evidence, checked by
            # guard_body against that exact status schema.
            receipts = {}
            for status, media in op["response_schemas"].items():
                schema = next((s for s in media.values() if isinstance(s, dict) and "properties" in s), {})
                properties = schema.get("properties", {})
                flag = properties.get(results["ok"], {})
                falsy = lambda v: v is False or v in ("false", "False")  # const is a one-value enum
                if (re.fullmatch(r"[45][0-9]{2}", status) and flag.get("type") in ("boolean", "string")
                        and any(map(falsy, flag.get("enum", [False]))) and falsy(flag.get("const", False))):
                    roles = {s: a for s, a in results.items() if a in properties}
                    receipts[status] = {"fields": dict(roles, _http_status="_http_status"), "request_schema": {
                        "properties": dict({a: {} if s == "ok" else copy.deepcopy(properties[a]) for s, a in roles.items()},
                                           _http_status={}),
                        "required": [a for a in roles.values() if a in schema.get("required", [])]}}
            catalog[name]["failure_receipts"] = receipts
    if bindings:
        _hash_checked(bindings, "action bindings")
        category_roles = {"outcomes": ("close_call", "outcome"), "reasons": ("close_call", "reason"),
                          "notes": ("add_note", "reason"), "queues": ("transfer_to_agent", "queue")}
        for category_name, category in bindings.get("categories", {}).items():
            if category_name == "notes" and rules.get("record_notes", {}).get("value") is False:
                if (not isinstance(category, dict) or set(category) != {"binding_kind", "semantic_to_wire", "wire_to_semantic"}
                        or category.get("binding_kind") != "local_disposition"):
                    raise GuardCompileError(["local-only notes require exact policy disposition binding shape"])
                forward = category["semantic_to_wire"]
                reverse = category["wire_to_semantic"]
                allowed_meanings = {"refused", "hardship", "dispute", "paid", "service_error"}
                declared = rules.get("wire_codes", {}).get("value", {}).get("notes")
                values = rules.get("note_reasons", {}).get("value", [])
                if declared is None:
                    declared = {value: value for value in values} if set(values) <= allowed_meanings else {}
                if (forward != declared or not forward or set(forward) - allowed_meanings
                        or len(set(forward.values())) != len(forward) or {v: k for k, v in forward.items()} != reverse
                        or not set(values) <= set(forward.values())):
                    raise GuardCompileError(["local disposition meanings must match the exact frozen injective policy mapping"])
                continue
            if (not isinstance(category, dict) or set(category) - {"unbound_codes"} != {"semantic_to_wire", "wire_to_semantic", "operation", "field", "api_enum", "api_enum_constrained"}
                    or "unbound_codes" in category and category_name not in ("outcomes", "reasons")):
                raise GuardCompileError(["API action bindings require exact enum constraint descriptor shape"])
            actual_op = category.get("operation", {}).get("name")
            op = role_for_actual.get(actual_op)
            field = category.get("field")
            if op not in catalog or field not in catalog[op]["request_schema"].get("properties", {}):
                raise GuardCompileError(["action binding references an unknown operation or request field"])
            if (category_name not in category_roles or op != category_roles[category_name][0]
                    or field != catalog[op]["fields"].get(category_roles[category_name][1])):
                raise GuardCompileError(["action binding category does not match its canonical semantic operation/field"])
            expected_operation = permitted[actual_op]
            if category.get("operation") != {key: expected_operation[key] for key in ("name", "method", "url")}:
                raise GuardCompileError(["action binding operation differs from exact frozen API identity"])
            leaf = catalog[op]["request_schema"]["properties"][field]
            constrained = "enum" in leaf
            enum = leaf.get("enum", [])
            if (type(category.get("api_enum_constrained")) is not bool
                    or category["api_enum_constrained"] != constrained or category.get("api_enum") != enum
                    or constrained and (not isinstance(enum, list) or not enum or any(not isinstance(v, str) or not v for v in enum) or len(set(enum)) != len(enum))):
                raise GuardCompileError(["action binding enum constraint differs from exact frozen string schema"])
            forward = category.get("semantic_to_wire", {})
            reverse = category.get("wire_to_semantic", {})
            unbound = category.get("unbound_codes", [])
            if ({v: k for k, v in forward.items()} != reverse or len(forward) != len(reverse) or constrained and any(v not in enum for v in forward.values())
                    or not isinstance(unbound, list) or any(not isinstance(v, str) or not v or v in reverse for v in unbound)
                    or constrained and any(v not in enum for v in unbound)):
                raise GuardCompileError(["action binding vocabulary differs from supplied API enum"])
    return catalog


def _disclosure_schema(contract: dict, profile: dict, catalog: dict) -> dict:
    """Approve only explicitly sourced scalar account fields for dialogue."""
    selected = profile.get("account_disclosure_fields")
    if selected is None or isinstance(selected, list) and not selected:
        return {}
    conditional = isinstance(selected, dict)
    if _profile_errors("account_disclosure_fields", selected):
        raise GuardCompileError(["account_disclosure_fields requires exact unconditional or common/by_product_type field lists"])
    account = catalog.get("get_account")
    if not isinstance(account, dict):
        raise GuardCompileError(["account disclosure requires a permitted get_account operation"])
    schema = account.get("product_schema")
    if schema is None:
        # Older catalogues omit the raw item schema. Resolve it from the same
        # frozen operation/result aliases, never from caller-proposed names.
        aliases = contract["active_rules"].get("tool_aliases", {}).get("value", {})
        operation = next((op for op in contract["permitted_operations"]
                          if op["name"] == aliases.get("get_account", "get_account")), None)
        if operation is None:
            raise GuardCompileError(["account disclosure lacks a frozen account schema"])
        response = next((value for media in operation["response_schemas"].values() for value in media.values()
                         if isinstance(value, dict) and "properties" in value), {})
        schema = response.get("properties", {}).get(account["results"]["products"], {}).get("items", {})
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    forbidden = {"customer_id", "product_id", account["product_fields"]["product_id"]}
    forbidden.update(op["fields"].get("customer_id", "") for op in catalog.values())
    fields = (selected["common"] + [field for branch in selected["by_product_type"].values() for field in branch]
              if conditional else selected)
    if any(field in forbidden for field in fields):
        raise GuardCompileError(["account_disclosure_fields cannot expose internal/customer identifier aliases"])
    approved = {}
    for field in fields:
        leaf = properties.get(field)
        if not isinstance(leaf, dict) or leaf.get("type") not in {"string", "number", "integer", "boolean"}:
            raise GuardCompileError(["account disclosure field is missing or is not a supported scalar: " + field])
        approved[field] = copy.deepcopy(leaf)
    if conditional:
        type_field = account["product_fields"]["type"]
        if type_field in forbidden:
            raise GuardCompileError(["conditional account disclosure cannot classify products through an internal/customer identifier alias"])
        type_schema = properties.get(type_field)
        if not isinstance(type_schema, dict) or type_schema.get("type") != "string":
            raise GuardCompileError(["conditional account disclosure requires an exact string product-type schema"])
        enum = type_schema.get("enum")
        sourced = contract["active_rules"]["account_disclosure_fields"]["value"]["by_product_type"]
        codes = profile.get("wire_codes", {}).get("product_types", {})
        if enum is not None and any(kind in enum and codes.get(kind, kind) != kind for kind in sourced):
            raise GuardCompileError(["account disclosure semantic product-type key shadows a supplied API code"])
        if enum is not None and any(kind not in enum for kind in selected["by_product_type"]):
            raise GuardCompileError(["account disclosure product-type key is outside the supplied API enum"])
        return {"mode": "by_product_type", "type_schema": copy.deepcopy(type_schema),
                "common": {field: approved[field] for field in selected["common"]},
                "by_product_type": {kind: {field: approved[field] for field in branch}
                                    for kind, branch in selected["by_product_type"].items()}}
    return approved


# This same source runs in local tests and is embedded verbatim in Freya code
# nodes. It uses only the three standard modules verified by the organizer kit.
_KERNEL = r'''
import json
import re
import datetime

def guard_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def guard_true(value):
    return value is True or value in ('true', 'True')

def guard_cents(value):
    if isinstance(value, bool):
        raise ValueError('invalid amount')
    text = str(value).strip()
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', text):
        raise ValueError('amount must have at most two decimal places')
    parts = text.split('.')
    return int(parts[0]) * 100 + int((parts[1] if len(parts) == 2 else '').ljust(2, '0'))

def guard_date(value):
    text = str(value)
    day = datetime.date.fromisoformat(text)
    if day.isoformat() != text:
        raise ValueError('expected an ISO date')
    return day

def guard_dated(state, profile, kind):
    # A sourced dated rule is chosen by the sealed call date, never a host clock.
    value = profile[kind]
    dated = value.get('effective_date') if type(value) is dict else None
    if type(dated) is not dict:
        return value
    if set(dated) != set(('date', 'before', 'on_or_after')):
        raise ValueError('dated policy variants are malformed')
    return dated['on_or_after'] if guard_date(state['now']) >= guard_date(dated['date']) else dated['before']

GUARD_IDENTITY_EVIDENCE = dict(uncertain='uncertain_verification', fault='definite_verification_failure')

def guard_identity_end(state, profile, status):
    # A protected verification terminal latches identity; only its sourced
    # evidence row can close. Without that row the call stays quarantined.
    evidence = GUARD_IDENTITY_EVIDENCE.get(status)
    if evidence is not None and not any(choice.get('requires') == evidence for choices in profile.get('identity_close_choices', dict()).values() for choice in choices):
        state['quarantined'] = True
    else:
        state['inflight'] = dict()
        state['identity_status'] = status
    return state, 'failure', dict(), ''

def guard_business(day, profile):
    return day.weekday() in profile['business_weekdays'] and day.isoformat() not in profile['holidays']

def guard_deadline(now, days, profile):
    day = now
    remaining = days
    for unused in range(days * 10 + len(profile['holidays']) * 7 + 370):
        if remaining == 0:
            return day
        day += datetime.timedelta(days=1)
        if guard_business(day, profile):
            remaining -= 1
    raise ValueError('calendar has no bounded deadline')

def guard_product(state, pid):
    found = [p for p in state['products'] if p['product_id'] == pid]
    if len(found) != 1:
        raise ValueError('product id must come from the account result')
    return found[0]

def guard_order(state, profile):
    products = state['products']
    singleton = profile.get('policy_applicability') == dict(products='single')
    if singleton and len(products) > 1:
        raise ValueError('account cardinality exceeds supplied single-product scope')
    bounds = profile.get('product_count_bounds', dict())
    if 'max' in bounds and len(products) > bounds['max']:
        raise ValueError('account cardinality exceeds supplied product-count maximum')
    if state.get('account_loaded') is True and 'min' in bounds and len(products) < bounds['min']:
        raise ValueError('account cardinality is below supplied product-count minimum')
    order = profile.get('product_order')
    if order is None:
        if singleton:
            return list(products)
        raise ValueError('product ordering requires an explicit policy rule or single-product scope')
    if order.get('mode') == 'account_order':
        return list(products)
    return sorted(products, key=lambda p: p[order['field']], reverse=order['direction'] == 'descending')

def guard_product_turn(state, pid, profile):
    if pid in state['pending_promises'] or pid in state['pending_notes']:
        return
    # A sourced overdue scope neither waits on nor writes for other products.
    rule = profile.get('resolve_all_products')
    overdue = isinstance(rule, dict) and rule.get('products') == 'overdue'
    remaining = [p['product_id'] for p in guard_order(state, profile) if (not overdue or p['days_late'] >= 1) and p['product_id'] not in state['recorded_promises'] and p['product_id'] not in state['recorded_notes'] and p['product_id'] not in state['pending_promises'] and p['product_id'] not in state['pending_notes']]
    if not remaining or remaining[0] != pid:
        raise ValueError('product proposals must follow supplied policy order')

def guard_display(product, profile):
    display = profile['promise_readback']
    parts = list()
    for field in display['product_fields']:
        value = product.get(field)
        if value is None or value == '':
            continue
        if type(value) not in (str, int):
            raise ValueError('product display fields must be plain labels')
        text = display.get('value_labels', dict()).get(field, dict()).get(str(value), str(value).replace('_', ' '))
        parts.append(text)
    if not parts:
        raise ValueError('no human product label in trusted account result')
    return ' '.join(parts)

def guard_readback(state, action, items, profile, maps):
    if action == 'commit_promises':
        labels = [guard_display(p, profile) for p in state['products']]
        if len(set(labels)) != len(labels):
            raise ValueError('approved product labels do not identify products uniquely')
        return '; '.join('Payment for ' + guard_display(guard_product(state, item['product_id']), profile) + ': ' + format(item['amount'], '.2f') + ' on ' + item['date'] for item in items)
    if action == 'schedule_callback':
        return '; '.join('Callback on ' + item['date'] + ' at ' + item['time'] for item in items)
    if action == 'commit_transfer':
        return 'Transfer to ' + state['transfer']['queue'].replace('_', ' ')
    return ''

def guard_decode(group, value, maps):
    return maps.get(group, {}).get('wire_to_semantic', dict()).get(value, value)

def guard_encode(group, value, maps):
    return maps.get(group, {}).get('semantic_to_wire', dict()).get(value, value)

def guard_schema(value, spec):
    kind = spec.get('type')
    if kind == 'string' and not isinstance(value, str):
        raise ValueError('request string required')
    if kind == 'integer' and type(value) is not int:
        raise ValueError('request integer required')
    if kind == 'number' and (type(value) not in (int, float) or str(value) in ('nan', 'inf', '-inf')):
        raise ValueError('request finite number required')
    if kind == 'boolean' and type(value) is not bool:
        raise ValueError('request boolean required')
    if 'enum' in spec and not any(type(value) is type(v) and value == v for v in spec['enum']):
        raise ValueError('request value outside supplied enum')
    if kind in ('number', 'integer'):
        if 'minimum' in spec and value < spec['minimum'] or 'maximum' in spec and value > spec['maximum']:
            raise ValueError('request numeric bound')
    if kind == 'string':
        if len(value) < spec.get('minLength', 0) or len(value) > spec.get('maxLength', 100000):
            raise ValueError('request string length')
        if 'pattern' in spec and re.search(spec['pattern'], value) is None:
            raise ValueError('request string pattern')
        if spec.get('format') == 'date':
            guard_date(value)

def guard_body(semantic, operation, catalog):
    spec = catalog[operation]
    body = dict()
    for name, value in semantic.items():
        if name not in spec['fields']:
            raise ValueError('unbound semantic request field')
        actual = spec['fields'][name]
        guard_schema(value, spec['request_schema']['properties'][actual])
        body[actual] = value
    if not set(spec['request_schema'].get('required', list())) <= set(body):
        raise ValueError('missing required request value')
    return body

def guard_new(customer_id, now, call_inputs=None):
    if not isinstance(customer_id, str) or not customer_id:
        raise ValueError('missing engine customer id')
    text = str(now)
    day = guard_date(text[:10]).isoformat()
    clock = datetime.datetime.fromisoformat(text.replace('Z', '+00:00')).strftime('%H:%M') if len(text) > 10 else ''
    return dict(customer_id=customer_id, now=day, now_time=clock, verified_fields=list(), wrong_answers=0,
                identity_status='pending', products=list(), account_loaded=False, account_failed=False,
                pending_promises=dict(), recorded_promises=dict(), pending_notes=dict(),
                recorded_notes=dict(), promise_failures=dict(), attempts=dict(),
                attempt_counts=dict(),
                transfer=dict(), callbacks=list(), notes_finalized=False, closed=False, close_attempted=False,
                quarantined=False, inflight=dict(), batch=dict(), consent='', close=dict(),
                call_inputs=json.loads(guard_json(call_inputs if call_inputs is not None else dict())))

def guard_call_inputs(state, profile):
    captured = state['call_inputs']
    names = profile.get('call_variables', list())
    if type(captured) is not dict or set(captured) != set(names):
        state['quarantined'] = True
        raise ValueError('protected call-input snapshot is malformed')
    if any(value is None or type(value) is str and value == '' for value in captured.values()):
        state['quarantined'] = True
        raise ValueError('protected call-input snapshot has an absent value')
    guard_json(captured)
    for name, format_name in profile.get('call_input_formats', dict()).items():
        value = captured.get(name)
        if format_name != 'offset_datetime' or type(value) is not str:
            state['quarantined'] = True
            raise ValueError('protected call-input format is malformed')
        match = re.fullmatch(r'([0-9]{4}-[0-9]{2}-[0-9]{2})[Tt ]([0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:[.,][0-9]+)?)?)(Z|[+-][0-9]{2}:[0-9]{2})', value)
        if match is None:
            state['quarantined'] = True
            raise ValueError('protected call input requires a date, time and explicit offset')
        offset = match.group(3)
        if offset != 'Z' and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59):
            state['quarantined'] = True
            raise ValueError('protected call-input offset is malformed')
        try:
            parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.utcoffset() is None:
                raise ValueError('missing offset')
        except Exception:
            state['quarantined'] = True
            raise ValueError('protected call-input datetime is malformed')
    rule = profile.get('resolve_all_products')
    if isinstance(rule, dict) and 'selector' in rule:
        value = captured.get(rule['selector'])
        if type(value) is not str or value not in rule['by_value']:
            state['quarantined'] = True
            raise ValueError('protected product-resolution selector is missing or unknown')

def guard_resolve_all_products(state, profile, finalizing=False):
    # Products that must be resolved; legacy booleans keep note finalization
    # over every loaded product.
    rule = profile['resolve_all_products']
    if type(rule) is bool:
        return list(state['products']) if rule or finalizing else list()
    if 'selector' in rule:
        guard_call_inputs(state, profile)
        if not rule['by_value'][state['call_inputs'][rule['selector']]]:
            return list()
    if rule.get('products') == 'overdue':
        return [p for p in state['products'] if p['days_late'] >= 1]
    return list(state['products'])

def guard_goal_close(state, profile):
    # Per-goal rows serve only calls whose sealed initial goal code is listed.
    table = profile.get('goal_close', dict())
    if 'selector' in table and state['call_inputs'][table['selector']] not in table['goals']:
        return dict()
    return table

def guard_close(state, proposed, profile, maps):
    transfer = state['transfer']
    facts = dict()
    if transfer.get('status') == 'recorded':
        facts['transferred'] = dict(outcome='transferred', reason=transfer['reason'])
    if state['recorded_promises']:
        facts['promise'] = dict(outcome='promise')
    dispositions = state['recorded_notes'] if profile['record_notes'] else state['pending_notes']
    if state['notes_finalized'] and state['products'] and all(dispositions.get(p['product_id']) == 'paid' for p in state['products']):
        facts['paid'] = dict(outcome='paid')
    if state['notes_finalized'] and dispositions:
        reason = next((dispositions.get(p['product_id']) for p in guard_order(state, profile) if dispositions.get(p['product_id']) not in (None, 'paid')), None)
        if reason is not None:
            facts['no_promise'] = dict(outcome='no_promise', reason=reason)
    proposal = next((facts[outcome] for outcome in profile['close_precedence'] if outcome in facts), None)
    if proposal is None and len(facts) == 1:
        # A source may rank only overlapping outcomes. One protected fact
        # needs no invented precedence; ordinary policy eligibility still
        # validates the selected outcome and reason below.
        proposal = list(facts.values())[0]
    elif proposal is None and len(facts) > 1:
        raise ValueError('closing has ambiguous unranked protected facts')
    if proposal is None:
        table = profile['identity_close']
        if state['identity_status'] == 'failed' and not state['account_loaded']:
            proposal = dict(table['failed'])
        elif state['identity_status'] in GUARD_IDENTITY_EVIDENCE:
            evidence = GUARD_IDENTITY_EVIDENCE[state['identity_status']]
            matching = [choice for choices in profile.get('identity_close_choices', dict()).values() for choice in choices if choice['requires'] == evidence]
            # A recorded callback is separate protected evidence with no sourced rank here.
            if len(matching) != 1 or state['callbacks']:
                raise ValueError('verification evidence lacks one declared closing choice')
            proposal = dict((key, value) for key, value in matching[0].items() if key != 'requires')
        elif state['identity_status'] != 'verified' and not state['account_loaded']:
            # A recorded callback is protected evidence, independent of free
            # dialogue proposals. Only the actual identity phase determines
            # which frozen callback choices can represent that evidence.
            unreached = (state['identity_status'] == 'pending' and not state['verified_fields']
                         and state['wrong_answers'] == 0)
            # Without a recorded callback, each scope a proposal could still
            # reach keeps its rows needing no evidence but that absence (a
            # fixed row is phase evidence). If all leave the same single row
            # that the absent callback decides, dialogue cannot change it; a
            # `none` row stays language-derived and needs its proposal.
            rows = [[row for row in profile.get('identity_close_choices', dict()).get(scope, [dict(table[scope], requires='no_recorded_callback')])
                     if row.get('requires', 'none') in ('none', 'no_recorded_callback')]
                    for scope in ('unreached', 'incomplete')[bool(state['verified_fields']):]]
            if state['callbacks']:
                scope = 'unreached' if unreached else 'incomplete'
                choices = profile.get('identity_close_choices', dict()).get(scope)
                # A fixed phase row is already one source-declared disposition;
                # no callback reason is invented when that row omits one. Its
                # supplied reason still passes the evidence/vocabulary checks.
                if choices is None:
                    matching = [table[scope]]
                else:
                    matching = [choice for choice in choices if choice['requires'] == 'recorded_callback']
                if len(matching) != 1:
                    raise ValueError('recorded callback lacks one declared closing choice for the actual identity phase')
                proposal = dict((key, value) for key, value in matching[0].items() if key != 'requires')
            elif all(len(item) == 1 and item == rows[0] for item in rows) and rows[0][0].get('requires') == 'no_recorded_callback':
                proposal = dict((key, value) for key, value in rows[0][0].items() if key != 'requires')
            elif proposed.get('outcome', '') == '' and proposed.get('reason', '') == '':
                # Nothing was proposed (no extracted value, or a close step
                # without proposal inputs): the recorded identity phase selects
                # its fixed row, only where that row is one eligible choice.
                proposal = dict(table['unreached' if unreached else 'incomplete'])
                if [dict((key, value) for key, value in row.items() if key != 'requires') for row in rows[0 if unreached else -1]].count(proposal) != 1:
                    raise ValueError('identity closing proposal lacks one declared eligible choice')
            else:
                if not isinstance(proposed.get('outcome', ''), str):
                    raise ValueError('identity closing outcome proposal must be text')
                outcome = guard_decode('outcomes', proposed.get('outcome', ''), maps)
                scope = 'unreached' if outcome == table['unreached']['outcome'] and not state['verified_fields'] else 'incomplete'
                choices = profile.get('identity_close_choices', dict()).get(scope)
                if choices is None:
                    proposal = dict(table[scope])
                else:
                    if not isinstance(proposed.get('reason', ''), str):
                        raise ValueError('identity closing reason proposal must be text')
                    reason = guard_decode('reasons', proposed.get('reason', ''), maps)
                    matching = [choice for choice in choices if choice['outcome'] == outcome and choice.get('reason', '') == reason]
                    if len(matching) != 1:
                        raise ValueError('identity closing proposal lacks one declared eligible choice')
                    choice = matching[0]
                    requirement = choice['requires']
                    if requirement in GUARD_IDENTITY_EVIDENCE.values():
                        raise ValueError('identity closing proposal lacks required verification evidence')
                    if requirement == 'recorded_callback':
                        # No callback is recorded on this path, so the evidence decides
                        # a callback claim: the scope's one row requiring that absence.
                        matching = [row for row in choices if row['requires'] == 'no_recorded_callback']
                        if len(matching) != 1:
                            raise ValueError('identity closing proposal lacks required callback evidence')
                        choice = matching[0]
                    proposal = dict((key, value) for key, value in choice.items() if key != 'requires')
        else:
            # Identity is verified (and possibly the account loaded) without a
            # protected collections fact: only a sourced goal row may close.
            # A recorded definite negative account receipt is final: its rows
            # alone apply, whatever is said later.
            phase = 'account_loaded' if state['account_loaded'] else 'account_failed' if state['account_failed'] else 'verified'
            # A recorded callback closes only through a recorded_callback row.
            rows = [row for row in guard_goal_close(state, profile).get(phase, list())
                    if (row['requires'] == 'recorded_callback') == bool(state['callbacks'])]
            if not rows:
                raise ValueError('closing lacks protected evidence')
            if len(rows) > 1:
                if not isinstance(proposed.get('outcome', ''), str) or not isinstance(proposed.get('reason', ''), str):
                    raise ValueError('goal closing proposal must be text')
                outcome = guard_decode('outcomes', proposed.get('outcome', ''), maps)
                reason = guard_decode('reasons', proposed.get('reason', ''), maps)
                claim = [row['requires'] for row in guard_goal_close(state, profile)[phase] if row['outcome'] == outcome and row.get('reason', '') == reason]
                # Without a recorded callback, a callback claim closes through the phase's one row requiring that absence.
                rows = [row for row in rows if row['outcome'] == outcome and row.get('reason', '') == reason
                        or claim == ['recorded_callback'] and not state['callbacks'] and row['requires'] == 'no_recorded_callback']
            if len(rows) != 1:
                raise ValueError('goal closing proposal lacks one declared eligible choice')
            proposal = dict((key, value) for key, value in rows[0].items() if key != 'requires')
    reasons = profile['close_reasons']
    if proposal['outcome'] not in reasons:
        raise ValueError('closing outcome outside policy')
    allowed = reasons[proposal['outcome']]
    if (allowed and proposal.get('reason') not in allowed) or (not allowed and proposal.get('reason')):
        raise ValueError('closing reason outside policy')
    # An outcome or reason code bound to a callback meaning claims that evidence.
    if 'callback' in (proposal['outcome'], proposal.get('reason')) and not state['callbacks']:
        raise ValueError('callback-success closing requires a recorded callback')
    if 'no_callback' in (proposal['outcome'], proposal.get('reason')) and state['callbacks']:
        raise ValueError('no_callback closing cannot hide a recorded callback')
    # Whatever row selected it, a protected outcome closes only as its recorded fact.
    if proposal['outcome'] in ('transferred', 'promise', 'paid', 'no_promise') and facts.get(proposal['outcome']) != proposal:
        raise ValueError('protected closing outcome lacks its recorded fact')
    return proposal

def guard_known_counts(state):
    if (not isinstance(state.get('recorded_promises'), dict) or not isinstance(state.get('recorded_notes'), dict)
            or not isinstance(state.get('callbacks'), list) or not isinstance(state.get('transfer'), dict)
            or type(state.get('closed')) is not bool):
        raise ValueError('recorded fact evidence is malformed')
    return dict(promises=len(state['recorded_promises']), notes=len(state['recorded_notes']),
                callbacks=len(state['callbacks']), transfer=state['transfer'].get('status') == 'recorded',
                close=state['closed'])

def guard_failed_facts(state, action, route):
    return dict(version=1, route=route, call_status='quarantined', recorded_counts=guard_known_counts(state),
                latest_action=dict(action=action, status='fact_projection_failed'))

# A policy refusal keeps the runtime view with one sanitized reason code; its
# text is never projected. A blank or unparseable caller value is asked for
# again. Any other unlisted fault keeps the degraded view.
GUARD_REFUSALS = dict(
    closed=('call is closed, uncertain or has an unresolved operation', 'identity verification has ended',
            'the disposition phase is sealed', 'a staged or recorded transfer permits only handoff and final close',
            'identity is complete or exhausted', 'transfer takes precedence over commitments and notes',
            'product is already recorded', 'this product exhausted its policy write budget',
            'duplicate or unresolved bank write'),
    not_verified=('account requires verified identity and is read once', 'action requires trusted identity and account results',
                  'transfer lacks the policy-required identity/account evidence'),
    order=('identity factors must follow policy order', 'product proposals must follow supplied policy order',
           'no valid staged promises', 'policy requires separate product confirmation',
           'all products need resolved promises or staged dispositions', 'no requested staged transfer'),
    missing_input=('invalid amount', 'amount must have at most two decimal places', 'expected an ISO date',
                   'missing required request value'),
    out_of_policy=('promise amount outside policy floor or past due', 'promise date outside policy calendar',
                   'product id must come from the account result', 'invalid or already recorded disposition',
                   'transfer not permitted in current state', 'a goal closed by goal rows uses no promise, note or transfer role',
                   'callback time outside policy window', 'callback date outside policy window',
                   'today callback requires a supplied clock and later time', 'request value outside supplied enum',
                   'request numeric bound', 'request string length', 'request string pattern'))

def guard_refusal(state, action, inputs, message):
    reason = next((code for code, messages in GUARD_REFUSALS.items() if message in messages), '')
    if reason == 'not_verified' and state['identity_status'] == 'verified':
        reason = 'closed' if action == 'get_account' else 'order'
    if reason in ('', 'order', 'out_of_policy'):
        try:
            if any(value is None or str(value).strip() == '' for value in inputs.values()):
                raise ValueError('blank caller value')
            if guard_date(inputs.get('date', state['now'])) < guard_date(state['now']):
                raise ValueError('proposed date precedes the call date')
            datetime.time.fromisoformat(str(inputs.get('time', '00:00')))
        except ValueError:
            reason = 'missing_input'
    return reason

def guard_projection_quarantine(state):
    # Raw account items may themselves contain a nonfinite/unserializable value.
    # Remove that untrusted display material while retaining reconciled records,
    # attempted-write evidence and any uncertain pending transaction.
    out = dict(state)
    out['quarantined'] = True
    out['products'] = list()
    out['account_loaded'] = False
    return out

def guard_facts(state, phase, action, route, data, profile, catalog, previous=None):
    # Explicit view only. The protected state and raw result are never copied.
    facts = dict(version=1, route=route, call_status='active')
    counts = guard_known_counts(state)
    facts['recorded_counts'] = counts
    identifiers = [state.get('customer_id', '')] + [p.get('product_id', '') for p in state.get('products', list())]
    def readable(value):
        return not isinstance(value, str) or not any(identifier and identifier in value for identifier in identifiers)
    if state.get('quarantined') is True:
        facts['call_status'] = 'quarantined'
        facts['latest_action'] = dict(action=action, status='unknown_or_malformed_evidence')
        if phase == 'result' and (data.get('ok') is False or type(data.get('ok')) is str and data['ok'] in ('false', 'False')):
            facts['latest_action']['response_status'] = 'rejected_persistence_unresolved'
        error = data.get('error')
        if phase == 'result' and isinstance(error, str) and error and readable(error):
            facts['latest_action']['error'] = error
        guard_json(facts)
        return facts
    if state.get('closed') is True:
        facts['call_status'] = 'closed'
    fields = profile.get('identity_fields', list())
    verified = state.get('verified_fields', list())
    wrong = state.get('wrong_answers', 0)
    limit = guard_dated(state, profile, 'identity_wrong_limit') if 'identity_wrong_limit' in profile else None
    if (not isinstance(verified, list) or any(field not in fields for field in verified)
            or len(set(verified)) != len(verified) or type(wrong) is not int or wrong < 0
            or type(limit) is int and wrong > limit):
        raise ValueError('identity fact state is malformed')
    identity = dict(status=state.get('identity_status', 'pending'), verified_factor_count=len(verified),
                    wrong_answers=wrong, next_factor='')
    if type(limit) is int:
        identity['wrong_answers_left'] = max(0, min(limit, catalog.get('verify_identity', dict()).get('wrong_answers_budget', limit)) - wrong)
    if identity['status'] == 'pending':
        identity['next_factor'] = next((field for field in fields if field not in verified), '')
    facts['identity'] = identity
    status = 'initialized' if phase == 'init' else 'sent' if route == 'allow' else 'awaiting_confirmation' if route == 'confirm' else 'blocked' if route == 'blocked' else 'completed_local_step'
    if phase == 'result':
        status = 'recorded' if action in ('commit_promises', 'finalize_notes', 'commit_transfer', 'schedule_callback', 'close_call') and route in ('done', 'closed') else 'loaded' if action == 'get_account' and state.get('account_loaded') else 'identity_checked' if action == 'verify_identity' and route == 'done' else 'rejected'
        if route == 'allow':
            status = 'continuing_batch' if guard_true(data.get('ok')) else 'retrying_nonpersistent_rejection'
    facts['latest_action'] = dict(action=action, status=status)
    if phase == 'result':
        for key in ('error', 'hint'):
            value = data.get(key, '')
            if value is not None and not isinstance(value, str):
                raise ValueError('fact result text has an unsupported type')
            if value and readable(value):
                if key == 'hint' and action == 'verify_identity' and route == 'done' and not guard_true(data.get('verified')):
                    identity['latest_hint'] = value
                elif key == 'error':
                    facts['latest_action']['error'] = value
        if action == 'verify_identity' and route == 'done':
            identity['latest_check'] = dict(field=(previous or dict()).get('inflight', dict()).get('semantic', dict()).get('field', ''),
                                            verified=guard_true(data.get('verified')))
    account = dict(loaded=state.get('account_loaded') is True,
                   narration_status='blocked_until_policy_declares_fields', products=list())
    facts['account'] = account
    if account['loaded'] and state.get('identity_status') != 'verified':
        raise ValueError('account facts lack protected verified identity')
    references = list()
    product_states = list()
    ordered = guard_order(state, profile) if account['loaded'] else list()
    disclosed = catalog.get('get_account', dict()).get('disclosure_fields', dict())
    for position, product in enumerate(ordered, 1):
        pid = product['product_id']
        returned_position = next(i for i, item in enumerate(state['products'], 1) if item['product_id'] == pid)
        promise = 'recorded' if pid in state['recorded_promises'] else 'staged' if pid in state['pending_promises'] else 'none'
        note = 'recorded' if pid in state['recorded_notes'] else 'staged' if pid in state['pending_notes'] else 'none'
        resolution = 'recorded_promise' if promise == 'recorded' else 'recorded_note' if note == 'recorded' else 'local_disposition_finalized' if note == 'staged' and state['notes_finalized'] and profile.get('record_notes') is False else 'pending' if promise == 'staged' or note == 'staged' else 'unresolved'
        reference = dict(position=position, returned_position=returned_position, product_ref=pid,
                         promise_status=promise, note_status=note, resolution_status=resolution,
                         proposal_constraints_status='unavailable')
        limit = profile.get('promise_failure_limit')
        failures = state['promise_failures'].get(pid, 0)
        if type(limit) is int and type(failures) is int and 0 <= failures <= limit:
            reference['remaining_failure_attempts'] = limit - failures
        try:
            product_type = product['type']
            if not isinstance(product_type, str) or not product_type or type(product['days_late']) is not int:
                raise ValueError('proposal product type or band is unavailable')
            bands = [band['name'] for band in profile['band_ranges'] if band['min'] <= product['days_late'] <= band['max']]
            if len(bands) != 1:
                raise ValueError('proposal band is unavailable')
            floor = guard_cents(product[profile['floor_fields'][product_type]])
            cap = guard_cents(product['past_due'])
            deadline = guard_deadline(guard_date(state['now']), guard_dated(state, profile, 'deadline_days')[bands[0]], profile).isoformat()
            if floor < 0 or cap <= 0 or floor > cap or type(limit) is not int or type(failures) is not int or not 0 <= failures <= limit:
                raise ValueError('proposal amount or failure budget is unavailable')
            reference['proposal_constraints'] = dict(product_type=product_type, band=bands[0],
                floor_amount_cents=floor, past_due_cap_cents=cap, business_deadline=deadline,
                positive_amount_required=True)
            try:
                guard_product_turn(state, pid, profile)
                reference['proposal_turn_available'] = True
            except ValueError:
                reference['proposal_turn_available'] = False
            sealed = state['closed'] or state['notes_finalized'] or bool(state['transfer']) or bool(state['inflight']) or promise == 'recorded' or note == 'recorded' or failures >= limit or not reference['proposal_turn_available']
            reference['proposal_constraints_status'] = 'blocked' if sealed else 'available'
        except (KeyError, ValueError, TypeError):
            pass
        references.append(reference)
        product_states.append(dict(position=position, promise=promise, note=note,
                                   confirmed_write_failures=state['promise_failures'].get(pid, 0)))
        if disclosed:
            display = dict()
            selected = disclosed
            if disclosed.get('mode') == 'by_product_type':
                kind = product['type']
                if not isinstance(kind, str) or not kind:
                    raise ValueError('conditional account fact lacks an exact product type')
                guard_schema(kind, disclosed['type_schema'])
                selected = dict(disclosed['common'])
                selected.update(disclosed['by_product_type'].get(kind, dict()))
            for field, schema in selected.items():
                if field not in product or not readable(product[field]):
                    raise ValueError('approved account fact is missing or contains an internal identifier')
                value = product[field]
                guard_schema(value, schema)
                if type(value) not in (str, int, float, bool):
                    raise ValueError('approved account fact is not scalar')
                display[field] = value
            account['products'].append(dict(position=position, fields=display))
    if account['loaded'] and disclosed:
        account['narration_status'] = 'approved_fields_only'
    facts['internal_action_references'] = dict(products=references)
    facts['product_states'] = product_states
    transfer = state.get('transfer', dict())
    facts['transfer'] = dict(status=transfer.get('status', 'none'))
    facts['callbacks'] = [dict(date=value['date'], time=value['time']) for value in state.get('callbacks', list())]
    facts['close'] = dict(status='recorded' if state.get('closed') is True else 'not_recorded')
    guard_json(facts)  # Finite strict JSON; unknown selected values fail closed.
    return facts

def guard_prepare(state, action, inputs, profile, catalog, maps):
    state = json.loads(guard_json(state))
    if state['closed'] or state.get('close_attempted') or state['quarantined'] or state['inflight']:
        raise ValueError('call is closed, uncertain or has an unresolved operation')
    # Recorded verification evidence leaves only its sourced close; a bank
    # session reported sealed leaves no bank call at all.
    if state['identity_status'] == 'sealed' or state['identity_status'] in GUARD_IDENTITY_EVIDENCE and action != 'close_call':
        raise ValueError('identity verification has ended')
    if any(type(key) is not str or not key or type(count) is not int or count < 0
           for key, count in state['attempt_counts'].items()):
        state['quarantined'] = True
        return state, 'failure', dict(), ''
    local_handoff = profile.get('record_notes') is False and action in ('stage_transfer', 'commit_transfer')
    if state['notes_finalized'] and action not in ('finalize_notes', 'close_call') and not local_handoff:
        raise ValueError('the disposition phase is sealed')
    if state['transfer'] and action not in ('commit_transfer', 'close_call'):
        raise ValueError('a staged or recorded transfer permits only handoff and final close')
    if action == 'verify_identity':
        fields = profile['identity_fields']
        if state['identity_status'] in ('verified', 'failed') or state['wrong_answers'] >= guard_dated(state, profile, 'identity_wrong_limit'):
            raise ValueError('identity is complete or exhausted')
        expected = next(f for f in fields if f not in state['verified_fields'])
        # The controller checks the next factor in policy order itself.
        # Extracted values persist for the call, so a proposal only selects a
        # latest_check correction of a passed factor (its pass is withdrawn
        # first); an absent or passed proposal cannot block the next check,
        # and a proposed later factor is refused.
        proposed = str(inputs.get('field') or '')
        recheck = profile.get('identity_factor_state') == 'latest_check' and proposed in state['verified_fields']
        field = proposed if recheck else expected
        if proposed in fields and proposed not in state['verified_fields'] and proposed != expected or not str(inputs['answer']).strip():
            raise ValueError('identity factors must follow policy order')
        if recheck:
            state['verified_fields'].remove(field)
        operation = action
        semantic = dict(customer_id=state['customer_id'], field=field, answer=str(inputs['answer']))
    elif action == 'get_account':
        if state['identity_status'] != 'verified' or state['account_loaded'] or state['account_failed']:
            raise ValueError('account requires verified identity and is read once')
        operation = action
        semantic = dict(customer_id=state['customer_id'])
    else:
        if action not in ('close_call', 'schedule_callback', 'stage_transfer', 'commit_transfer') and (state['identity_status'] != 'verified' or not state['account_loaded']):
            raise ValueError('action requires trusted identity and account results')
        if action not in ('close_call', 'schedule_callback') and guard_goal_close(state, profile):
            raise ValueError('a goal closed by goal rows uses no promise, note or transfer role')
        if state['transfer'] and action in ('stage_promise', 'commit_promises', 'stage_note', 'finalize_notes'):
            raise ValueError('transfer takes precedence over commitments and notes')
        if action == 'stage_promise':
            pid = inputs['product_id']
            product = guard_product(state, pid)
            if pid in state['recorded_promises'] or pid in state['recorded_notes']:
                raise ValueError('product is already recorded')
            if state['promise_failures'].get(pid, 0) >= profile['promise_failure_limit']:
                raise ValueError('this product exhausted its policy write budget')
            guard_product_turn(state, pid, profile)
            cents = guard_cents(inputs['amount'])
            floor_field = profile['floor_fields'][product['type']]
            floor = guard_cents(product[floor_field])
            past_due = guard_cents(product['past_due'])
            if cents <= 0 or cents < floor or cents > past_due:
                raise ValueError('promise amount outside policy floor or past due')
            bands = [b['name'] for b in profile['band_ranges'] if b['min'] <= product['days_late'] <= b['max']]
            if len(bands) != 1:
                raise ValueError('product has no unique policy band')
            today = guard_date(state['now'])
            day = guard_date(inputs['date'])
            deadline = guard_deadline(today, guard_dated(state, profile, 'deadline_days')[bands[0]], profile)
            if day < today or day > deadline or not guard_business(day, profile):
                raise ValueError('promise date outside policy calendar')
            state['pending_notes'].pop(pid, None)
            state['pending_promises'][pid] = dict(amount_cents=cents, date=day.isoformat())
            state['consent'] = ''
            state['batch'] = dict()
            return state, 'done', dict(), ''
        if action == 'stage_note':
            pid = inputs['product_id']
            guard_product(state, pid)
            guard_product_turn(state, pid, profile)
            reason = guard_decode('reasons', guard_decode('notes', inputs['reason'], maps), maps)
            if reason not in profile['note_reasons'] or pid in state['recorded_promises'] or pid in state['recorded_notes']:
                raise ValueError('invalid or already recorded disposition')
            state['pending_promises'].pop(pid, None)
            state['pending_notes'][pid] = reason
            state['consent'] = ''
            state['batch'] = dict()
            return state, 'done', dict(), ''
        if action == 'stage_transfer':
            queue = guard_decode('queues', inputs['queue'], maps)
            if queue not in profile['transfer_reasons'] or queue not in profile['transfer_prerequisites'] or state['recorded_notes'] or state['transfer']:
                raise ValueError('transfer not permitted in current state')
            prerequisite = profile['transfer_prerequisites'][queue]
            if prerequisite in ('identity', 'account') and state['identity_status'] != 'verified' or prerequisite == 'account' and not state['account_loaded']:
                raise ValueError('transfer lacks the policy-required identity/account evidence')
            state['transfer'] = dict(queue=queue, reason=profile['transfer_reasons'][queue], status='pending')
            state['pending_notes'] = dict()
            state['pending_promises'] = dict()
            state['notes_finalized'] = False
            state['consent'] = ''
            state['batch'] = dict()
            return state, 'done', dict(), ''
        if action == 'commit_promises':
            selected = inputs.get('product_id')
            promises = state['pending_promises']
            if not promises or selected is not None and selected not in promises:
                raise ValueError('no valid staged promises')
            if profile.get('confirmation_scope') == 'each' and selected is None and len(promises) > 1:
                raise ValueError('policy requires separate product confirmation')
            items = list()
            for pid in [p['product_id'] for p in guard_order(state, profile) if p['product_id'] in promises]:
                if selected is None or pid == selected:
                    if state['promise_failures'].get(pid, 0) >= profile['promise_failure_limit']:
                        raise ValueError('this product exhausted its policy write budget')
                    item = promises[pid]
                    amount = json.loads(str(item['amount_cents'] // 100) + '.' + str(item['amount_cents'] % 100).zfill(2))
                    if guard_cents(amount) != item['amount_cents']:
                        raise ValueError('amount loses integer-cent precision in API number representation')
                    items.append(dict(customer_id=state['customer_id'], product_id=pid, amount=amount, date=item['date']))
            operation = 'create_promise'
        elif action == 'finalize_notes':
            # An empty required set with nothing staged has nothing to finalize;
            # sealing it would leave no closing fact and no way to add one.
            required = guard_resolve_all_products(state, profile, True)
            if state['pending_promises'] or not state['products'] or not required and not state['pending_notes'] or any(p['product_id'] not in state['recorded_promises'] and p['product_id'] not in state['pending_notes'] for p in required):
                raise ValueError('all products need resolved promises or staged dispositions')
            state['notes_finalized'] = True
            if not profile['record_notes']:
                return state, 'done', dict(), ''
            items = [dict(customer_id=state['customer_id'], product_id=p['product_id'], reason=guard_encode('notes', state['pending_notes'][p['product_id']], maps)) for p in guard_order(state, profile) if p['product_id'] in state['pending_notes'] and p['product_id'] not in state['recorded_notes']]
            if not items:
                return state, 'done', dict(), ''
            operation = 'add_note'
        elif action == 'commit_transfer':
            if state['transfer'].get('status') != 'pending':
                raise ValueError('no requested staged transfer')
            prerequisite = profile['transfer_prerequisites'][state['transfer']['queue']]
            if prerequisite in ('identity', 'account') and state['identity_status'] != 'verified' or prerequisite == 'account' and not state['account_loaded']:
                raise ValueError('transfer lacks the policy-required identity/account evidence')
            operation = 'transfer_to_agent'
            items = [dict(customer_id=state['customer_id'], queue=guard_encode('queues', state['transfer']['queue'], maps))]
        elif action == 'schedule_callback':
            window = profile['callback_window']
            today = guard_date(state['now'])
            day = guard_date(inputs['date'])
            at = str(inputs['time'])
            datetime.time.fromisoformat(at)
            if not re.fullmatch(r'[0-9]{2}:[0-9]{2}', at) or not window['start'] <= at <= window['end']:
                raise ValueError('callback time outside policy window')
            first = today if window.get('allow_today', False) else guard_deadline(today, 1, profile)
            last = guard_deadline(today, window.get('next_business_days', 0), profile)
            today_exception = day == today and window.get('allow_today', False)
            if day < first or day > last or not today_exception and not guard_business(day, profile):
                raise ValueError('callback date outside policy window')
            if day == today and (not state['now_time'] or at <= state['now_time']):
                raise ValueError('today callback requires a supplied clock and later time')
            operation = 'schedule_callback'
            items = [dict(customer_id=state['customer_id'], date=day.isoformat(), time=at)]
        elif action == 'close_call':
            if state['pending_promises'] or state['pending_notes'] and not state['notes_finalized'] or state['transfer'].get('status') in ('pending', 'failed'):
                raise ValueError('closing has unresolved commitments, dispositions or transfer')
            required = guard_resolve_all_products(state, profile) if state['account_loaded'] else list()
            if required and state['transfer'].get('status') != 'recorded':
                dispositions = state['recorded_notes'] if profile['record_notes'] else state['pending_notes'] if state['notes_finalized'] else dict()
                if any(p['product_id'] not in state['recorded_promises'] and p['product_id'] not in dispositions for p in required):
                    raise ValueError('policy requires every product resolved before close')
            proposal = guard_close(state, inputs, profile, maps)
            operation = 'close_call'
            semantic = dict(customer_id=state['customer_id'], outcome=guard_encode('outcomes', proposal['outcome'], maps))
            if proposal.get('reason'):
                semantic['reason'] = guard_encode('reasons', proposal['reason'], maps)
            state['close'] = proposal
            items = [semantic]
        else:
            raise ValueError('unsupported action')
        # Validate the entire frozen batch before the first side effect. A
        # later item's API bounds must not be discovered after an earlier write.
        for item in items:
            guard_body(item, operation, catalog)
        batch = dict(action=action, operation=operation, items=items, index=0)
        scope = profile.get('attempt_scopes', dict()).get(action)
        if scope is not None:
            limit_kind = dict(commit_promises='promise_failure_limit', finalize_notes='note_failure_limit',
                              schedule_callback='callback_failure_limit', commit_transfer='transfer_failure_limit')
            batch['attempt_scope'] = scope
            batch['attempt_limit'] = 1 if action == 'close_call' else profile[limit_kind[action]]
            # Validate callback budgets before asking for new confirmation;
            # proposals/corrections cannot reset the dispatch counter.
            if action == 'schedule_callback':
                guard_attempt_slot(state, batch, items[0], catalog)
        state['batch'] = batch
        state['consent'] = ''
        confirmation = action == 'commit_promises' and profile['require_confirmation'] or action in profile.get('confirmation_actions', list())
        summary = guard_readback(state, action, items, profile, maps) if confirmation else ''
        if confirmation:
            return state, 'confirm', dict(), summary
        return guard_send(state, catalog)
    body = guard_body(semantic, operation, catalog)
    state['inflight'] = dict(action=action, operation=operation, body=body, semantic=semantic)
    return state, 'allow', body, ''

def guard_attempt_slot(state, batch, semantic, catalog, exhausted_ok=False):
    scope = batch.get('attempt_scope')
    if scope is None:
        return '', 0
    action = batch['action']
    limit = batch.get('attempt_limit')
    if type(limit) is not int or limit < 1:
        raise ValueError('malformed policy attempt budget')
    if scope == 'call':
        key = action + ':call'
    elif scope == 'product' and action in ('commit_promises', 'finalize_notes'):
        product = semantic.get('product_id')
        if type(product) is not str or not product:
            raise ValueError('product-scoped attempt lacks a trusted product')
        key = action + ':product:' + product
    elif scope == 'exact_request':
        key = action + ':request:' + batch['operation'] + ':' + guard_json(guard_body(semantic, batch['operation'], catalog))
    else:
        raise ValueError('unsupported policy attempt scope')
    count = state['attempt_counts'].get(key, 0)
    if type(count) is not int or count < 0 or count > limit or count == limit and not exhausted_ok:
        raise ValueError('policy dispatch attempt budget exhausted or malformed')
    # The separate counter must agree with the already protected dispatch
    # history. A removed/reset but superficially valid zero is not a new call.
    prefix = batch['operation'] + ':'
    actual = 0
    for attempt_key, attempt in state['attempts'].items():
        if not attempt_key.startswith(prefix):
            continue
        if scope == 'exact_request' and attempt_key != batch['operation'] + ':' + guard_json(guard_body(semantic, batch['operation'], catalog)):
            continue
        if scope == 'product':
            request = json.loads(attempt_key[len(prefix):])
            if request.get(catalog[batch['operation']]['fields']['product_id']) != semantic['product_id']:
                continue
        recorded_count = attempt.get('count')
        if type(recorded_count) is not int or recorded_count < 1:
            raise ValueError('malformed protected dispatch history')
        actual += recorded_count
    if count != actual:
        raise ValueError('policy attempt counter differs from protected dispatch history')
    return key, count

def guard_send(state, catalog):
    batch = state['batch']
    if state['consent']:
        snapshot = json.loads(state['consent'])
        if any(snapshot[key] != batch[key] for key in ('action', 'operation', 'items')):
            raise ValueError('confirmed snapshot changed')
    semantic = batch['items'][batch['index']]
    operation = batch['operation']
    body = guard_body(semantic, operation, catalog)
    key = operation + ':' + guard_json(body)
    previous = state['attempts'].get(key, dict())
    if previous and previous.get('status') != 'nonpersistent_failed':
        raise ValueError('duplicate or unresolved bank write')
    count_key, count = guard_attempt_slot(state, batch, semantic, catalog)
    if count_key:
        state['attempt_counts'][count_key] = count + 1
    state['attempts'][key] = dict(status='sent', count=previous.get('count', 0) + 1)
    state['inflight'] = dict(action=batch['action'], operation=operation, body=body, semantic=semantic, key=key)
    if batch['action'] == 'close_call':
        state['close_attempted'] = True
    return state, 'allow', body, ''

def guard_confirm(state, consent, catalog, expected_action):
    state = json.loads(guard_json(state))
    if state['closed'] or state.get('close_attempted') or state['quarantined'] or not state['batch'] or state['inflight'] or state['batch']['action'] != expected_action:
        raise ValueError('fresh explicit confirmation is absent')
    if consent != 'yes':
        state['batch'] = dict()
        state['consent'] = ''
        return state, 'blocked', dict(), ''
    state['consent'] = guard_json(state['batch'])
    return guard_send(state, catalog)

def guard_continue(state, catalog):
    # Keep the result just reconciled even if preparation of the next request
    # fails. The outer code handler only has the state before that result.
    try:
        return guard_send(state, catalog)
    except Exception:
        state['quarantined'] = True
        state['batch'] = dict()
        state['consent'] = ''
        return state, 'failure', dict(), ''

def guard_mistyped(data, spec):
    # The API hook records each trusted result field's raw JSON type, in sorted
    # semantic order, before the string mapping erases it. A non-string type
    # that no declared response schema allows is malformed evidence. A string
    # is never type evidence, so a host that hands the hook strings keeps the
    # earlier behaviour. Absent and null keep the missing-value rules; no
    # record at all is no type evidence.
    kinds = str(data.get('_types') or '').split(',')
    if kinds == ['']:
        return False
    names = sorted(spec['results'])
    allowed = spec.get('result_types', dict())
    return len(kinds) != len(names) or any(
        name in allowed and kind != 'string' and kind not in allowed[name] + ['absent', 'null']
        and not (kind == 'integer' and 'number' in allowed[name]) for name, kind in zip(names, kinds))

def guard_result(state, data, profile, catalog, expected_action):
    state = json.loads(guard_json(state))
    tx = state['inflight']
    if not tx or state['closed'] or state['quarantined'] or tx['action'] != expected_action:
        raise ValueError('no trusted pending result')
    action = tx['action']
    ok = data.get('ok')
    status = data.get('_http_status')
    # Mapped runtime envelopes always carry this field, including an empty
    # value when transport evidence is missing. Local semantic harnesses may
    # omit it; a supplied status must be an exact HTTP status, never guessed.
    if type(status) is str and re.fullmatch(r'[1-5][0-9]{2}', status):
        status = int(status)
    status_valid = '_http_status' not in data or type(status) is int and 100 <= status <= 599
    success_status = '_http_status' not in data or status_valid and 200 <= status <= 299
    # A positive flag and an error cannot jointly prove success. Preserve the
    # pending request and every earlier record; this never grants a replay.
    contradictory_error = guard_true(ok) and 'error' in data and data['error'] != ''
    if (not status_valid or guard_true(ok) and not success_status or contradictory_error
            or guard_mistyped(data, catalog[tx['operation']])):
        if action == 'verify_identity':
            return guard_identity_end(state, profile, 'uncertain')
        state['quarantined'] = True
        if tx.get('key'):
            state['attempts'][tx['key']]['status'] = 'unknown_persistence'
        if action == 'close_call':
            state['batch'] = dict()
            state['consent'] = ''
            return state, 'closed', dict(), ''
        return state, 'failure', dict(), ''
    matching_failures = [case for case in profile.get('nonpersistent_failures', dict()).get(tx['operation'], list())
                        if type(status) is int and status == case['http_status'] and
                        ('error' not in case or data.get('error') == case['error'])]
    nonpersistent = bool(matching_failures)
    negative_ok = ok is False or type(ok) is str and ok in ('false', 'False')
    # The controller has already checked the literal API quotation. Bodyless
    # failures additionally require that witness's exact operation response
    # scope; the kernel never interprets prose or guesses from a URL suffix.
    sourced_failure = False
    for case in matching_failures:
        witness = case.get('api_source')
        if (not isinstance(witness, dict) or set(witness) != set(('pointer', 'quote'))
                or type(witness.get('pointer')) is not str or type(witness.get('quote')) is not str
                or not witness['quote'].strip()):
            continue
        pointer = witness['pointer']
        if pointer.startswith('#/'):
            pointer = pointer[1:]
        if not pointer.startswith('/'):
            continue
        encoded = pointer[1:].split('/')
        if any('~' in part.replace('~0', '').replace('~1', '') for part in encoded):
            continue
        parts = [part.replace('~1', '/').replace('~0', '~') for part in encoded]
        operation = catalog.get(tx['operation'], dict())
        if (len(parts) >= 6 and parts[0] == 'paths' and parts[1] == operation.get('path')
                and parts[2].upper() == operation.get('method') and parts[3] == 'responses'
                and parts[4] == str(status)):
            sourced_failure = True
    bodyless_failure = (type(status) is int and 400 <= status <= 599 and sourced_failure
                        and (ok is None or type(ok) is str and ok == ''))
    classes = profile.get('identity_failure_classes')
    if action == 'verify_identity' and classes is not None and not guard_true(ok):
        definite = sourced_failure and (negative_ok or bodyless_failure)
        def matched(name):
            return definite and any(status == case['http_status'] and ('error' not in case or data.get('error') == case['error'])
                                    for case in classes.get(name, list()))
        if matched('sealed'):
            return guard_identity_end(state, profile, 'sealed')
        if matched('fault') or definite and tx.get('repeat'):
            return guard_identity_end(state, profile, 'fault')
        if matched('repeat_once'):
            # One immediate exact repeat, so no new answer can intervene.
            state['inflight'] = dict(tx, repeat=True)
            return state, 'allow', tx['body'], ''
        if not definite:
            return guard_identity_end(state, profile, 'uncertain')
        state['inflight'] = dict()
        return state, 'failure', dict(), ''
    # Every close result is terminal, including malformed or negative evidence.
    # Attempting a close does not establish that the bank recorded it.
    if action == 'close_call':
        state['inflight'] = dict()
        state['batch'] = dict()
        state['consent'] = ''
        state['closed'] = guard_true(ok)
        state['quarantined'] = not state['closed']
        state['attempts'][tx['key']]['status'] = 'recorded' if state['closed'] else 'unknown_persistence'
        return state, 'closed', dict(), ''
    if (action == 'commit_promises' and not guard_true(ok) and 'promise_id' in data
            and not (type(data['promise_id']) is str and data['promise_id'] == ''
                     or data['promise_id'] is None and type(status) is int
                     and 400 <= status <= 599 and sourced_failure)):
        # A record identity contradicts rejection/no-body evidence. Nonempty or
        # malformed IDs are uncertain, rather than permission to replay.
        state['quarantined'] = True
        state['attempts'][tx['key']]['status'] = 'unknown_persistence'
        return state, 'failure', dict(), ''
    if action == 'commit_promises' and nonpersistent and (negative_ok or bodyless_failure):
        state['inflight'] = dict()
        state['attempts'][tx['key']]['status'] = 'nonpersistent_failed'
        pid = tx['semantic']['product_id']
        failures = state['promise_failures'].get(pid, 0) + 1
        state['promise_failures'][pid] = failures
        if failures < profile['promise_failure_limit']:
            return guard_continue(state, catalog)
        disposition = profile.get('promise_failure_disposition', dict())
        if disposition.get('clear_pending') is True and disposition.get('reason') in profile.get('note_reasons', list()):
            state['pending_promises'].pop(pid, None)
            state['pending_notes'][pid] = disposition['reason']
            state['batch']['index'] += 1
            if state['batch']['index'] < len(state['batch']['items']):
                return guard_continue(state, catalog)
            state['batch'] = dict()
            state['consent'] = ''
            return state, 'failure', dict(), ''
        state['quarantined'] = True
        return state, 'failure', dict(), ''
    if (action in profile.get('definite_failure_resume', list()) and sourced_failure
            and nonpersistent and (negative_ok or bodyless_failure)):
        state['inflight'] = dict()
        state['attempts'][tx['key']]['status'] = 'nonpersistent_failed'
        batch = state['batch']
        try:
            limit_kind = dict(finalize_notes='note_failure_limit', schedule_callback='callback_failure_limit',
                              commit_transfer='transfer_failure_limit')
            if (batch.get('attempt_scope') != profile['attempt_scopes'][action]
                    or batch.get('attempt_limit') != profile[limit_kind[action]]):
                raise ValueError('pending attempt budget differs from frozen policy')
            count_key, count = guard_attempt_slot(state, batch, tx['semantic'], catalog, True)
            if not count_key:
                raise ValueError('resume lacks a policy attempt scope')
        except Exception:
            state['quarantined'] = True
            return state, 'failure', dict(), ''
        if count == batch['attempt_limit']:
            # The request is known not to have persisted, and the source
            # explicitly permits continuation. No successful fact is created.
            state['batch'] = dict()
            state['consent'] = ''
            if action == 'commit_transfer' and state['transfer'].get('status') != 'recorded':
                state['transfer'] = dict()
            return state, 'failure', dict(), ''
        return guard_continue(state, catalog)
    if not (type(ok) is bool or type(ok) is str and ok in ('true', 'false', 'True', 'False')):
        if action == 'verify_identity':
            return guard_identity_end(state, profile, 'uncertain')
        state['quarantined'] = True
        return state, 'failure', dict(), ''
    if action == 'commit_promises' and guard_true(ok) and (not isinstance(data.get('promise_id'), str) or not data['promise_id'].strip()):
        # An affirmative flag without the required persisted-record identity is
        # uncertain evidence. Retain the exact pending request and never retry.
        state['quarantined'] = True
        state['attempts'][tx['key']]['status'] = 'unknown_persistence'
        return state, 'failure', dict(), ''
    state['inflight'] = dict()
    if action == 'verify_identity':
        if guard_true(ok):
            verified = data.get('verified')
            remaining = data.get('wrong_answers_left')
            if isinstance(remaining, str) and remaining.isdigit():
                remaining = int(remaining)
            limit = guard_dated(state, profile, 'identity_wrong_limit')
            # A declared API maximum is the counter's own budget, above or below
            # the policy limit; zero remaining also exhausts identity.
            budget = catalog['verify_identity'].get('wrong_answers_budget', limit)
            if not (type(verified) is bool or type(verified) is str and verified in ('true', 'false', 'True', 'False')) or type(remaining) is not int or not 0 <= remaining <= budget:
                return guard_identity_end(state, profile, 'uncertain')
            # Exact movement is checked before exhaustion and success.
            if profile.get('identity_counter_movement') == 'exact' and remaining != budget - state['wrong_answers'] - (0 if guard_true(verified) else 1):
                return guard_identity_end(state, profile, 'uncertain')
            if guard_true(verified):
                state['verified_fields'].append(tx['semantic']['field'])
            # Use the engine counter, not guessed answer/hint interpretation.
            # It includes errors on all factors and excludes API-qualified
            # noncounting retries such as unreadable answers or hints.
            state['wrong_answers'] = min(limit, max(state['wrong_answers'], budget - remaining))
            state['identity_status'] = 'failed' if state['wrong_answers'] >= limit or remaining == 0 else 'verified' if all(f in state['verified_fields'] for f in profile['identity_fields']) else 'pending'
        return state, 'done' if guard_true(ok) else 'failure', dict(), ''
    if action == 'get_account':
        if not guard_true(ok):
            if 'account_failed' in guard_goal_close(state, profile):
                # Only an exact declared non-2xx receipt valid for that status
                # schema is definite; any other negative receipt raises, and
                # the result handler quarantines it.
                guard_body(dict((k, v) for k, v in data.items() if v not in ('', None) and k != '_types'), str(status), catalog['get_account']['failure_receipts'])
                state['account_failed'] = True
            return state, 'failure', dict(), ''
        products = data.get('products')
        if isinstance(products, str):
            products = json.loads(products)
        if not isinstance(products, list) or not products:
            raise ValueError('account result has no product list')
        aliases = catalog['get_account']['product_fields']
        normalized = list()
        for raw in products:
            if not isinstance(raw, dict):
                raise ValueError('account product is malformed')
            product = dict(raw)
            for semantic, actual in aliases.items():
                product[semantic] = raw[actual]
            if not isinstance(product['product_id'], str) or not product['product_id'] or type(product['days_late']) is not int:
                raise ValueError('account product identity or band is malformed')
            normalized.append(product)
        if len(set(p['product_id'] for p in normalized)) != len(normalized):
            raise ValueError('duplicate account product ids')
        state['products'] = normalized
        state['account_loaded'] = True
        return state, 'done', dict(), ''
    key = tx['key']
    if not guard_true(ok):
        # Explicit failure is not successful evidence. Without an API-qualified
        # no-persistence retry classification, quarantine instead of resending.
        state['attempts'][key]['status'] = 'failed'
        state['quarantined'] = True
        if action == 'commit_promises':
            pid = tx['semantic']['product_id']
            state['promise_failures'][pid] = state['promise_failures'].get(pid, 0) + 1
        return state, 'failure', dict(), ''
    state['attempts'][key]['status'] = 'recorded'
    if action == 'commit_promises':
        pid = tx['semantic']['product_id']
        state['recorded_promises'][pid] = state['pending_promises'].pop(pid)
    elif action == 'finalize_notes':
        pid = tx['semantic']['product_id']
        state['recorded_notes'][pid] = state['pending_notes'][pid]
    elif action == 'commit_transfer':
        state['transfer']['status'] = 'recorded'
    elif action == 'schedule_callback':
        state['callbacks'].append(tx['semantic'])
    batch = state['batch']
    batch['index'] += 1
    if batch['index'] < len(batch['items']):
        return guard_continue(state, catalog)
    state['batch'] = dict()
    state['consent'] = ''
    return state, 'done', dict(), ''
'''


def _literal(value: Any) -> str:
    # Freya source templating must never see adjacent braces, including nested
    # JSON endings. Python reconstructs them only at execution time.
    return repr(canonical_json(value)).replace("{", "\\x7b").replace("}", "\\x7d")


def _code(phase: str, action: str, inputs: dict, profile: dict, catalog: dict,
          maps: dict, result_vars: dict) -> str:
    spec = {"phase": phase, "action": action, "inputs": inputs, "profile": profile,
            "catalog": {k: {n: v for n, v in op.items() if n != "tool"} for k, op in catalog.items()},
            "maps": maps, "result_vars": result_vars}
    tail = "\nvars['bg_route'] = 'blocked'\nvars['bg_body'] = ''\nvars['bg_readback'] = ''\nvars['bg_facts'] = ''\nvars['bg_quarantined'] = ''\n"
    tail += "try:\n    spec = json.loads(" + _literal(spec) + ")\n"
    if phase == "init":
        tail += "    vars['bg_control_entries'] = '0'\n"
        tail += "    previous_state = None\n    call_inputs = dict()\n    for name in spec['profile'].get('call_variables', list()):\n        if name not in vars or vars[name] is None or type(vars[name]) is str and vars[name] == '':\n            raise ValueError('required call input is absent')\n        call_inputs[name] = vars[name]\n    state = guard_new(vars.get('customer_id'), vars.get('now'), call_inputs)\n    vars['bg_state'] = guard_json(state)\n    guard_call_inputs(state, spec['profile'])\n    route, body, readback = 'done', dict(), ''\n"
    else:
        tail += "    vars['bg_quarantined'] = 'yes'\n    state = json.loads(str(vars.get('bg_state') or ''))\n    expected = guard_new(state.get('customer_id'), state.get('now'))\n    if set(state) != set(expected) or any(type(state[key]) is not type(value) for key, value in expected.items()):\n        raise ValueError('protected state shape is malformed')\n    guard_call_inputs(state, spec['profile'])\n    vars['bg_quarantined'] = ''\n    previous_state = state\n"
        tail += "    entry_count = vars.get('bg_control_entries')\n"
        if phase == "result":
            # A bookkeeping fault cannot erase an already-dispatched receipt.
            tail += "    if not isinstance(entry_count, str) or not entry_count.isascii() or not entry_count.isdigit() or len(entry_count) > 7:\n        entry_count = " + str(CONTROLLED_ENTRY_LIMIT + 1) + "\n    else:\n        entry_count = int(entry_count) + 1\n    vars['bg_control_entries'] = str(entry_count)\n"
        else:
            tail += "    if not isinstance(entry_count, str) or not entry_count.isascii() or not entry_count.isdigit() or len(entry_count) > 7:\n        vars['bg_quarantined'] = 'yes'\n        raise ValueError('controller entry count is unavailable')\n    entry_count = int(entry_count) + 1\n    vars['bg_control_entries'] = str(entry_count)\n"
        if phase != "result":
            tail += "    if entry_count > " + str(CONTROLLED_ENTRY_LIMIT) + ":\n        vars['bg_quarantined'] = 'yes'\n        raise ValueError('controller entry budget exhausted')\n"
        if phase == "prepare":
            tail += "    if state['inflight']:\n        state['quarantined'] = True\n        vars['bg_state'] = guard_json(state)\n"
            tail += "    if not state.get('inflight'):\n        state['batch'] = dict()\n        state['consent'] = ''\n        vars['bg_state'] = guard_json(state)\n    inputs = dict((key, vars.get(name, '')) for key, name in spec['inputs'].items())\n"
            call = "state, route, body, readback = guard_prepare(state, spec['action'], inputs, spec['profile'], spec['catalog'], spec['maps'])\n"
            # A policy refusal keeps the unchanged state for the view below; a
            # refused close keeps its own termination path.
            tail += ("    " + call if action == "close_call" else
                     "    refusal = ''\n    try:\n        " + call + "    except ValueError as error:\n        refusal = guard_refusal(state, spec['action'], inputs, str(error))\n        if not refusal:\n            raise\n        route, body, readback = 'blocked', dict(), ''\n")
        elif phase == "confirm":
            tail += "    state, route, body, readback = guard_confirm(state, vars.get('bg_consent'), spec['catalog'], spec['action'])\n"
        else:
            # Reconciliation may otherwise prepare the next batch member or
            # qualified retry internally. Stop that preparation before it
            # marks an unsent request as inflight or increments bank counters.
            tail += "    if entry_count > " + str(CONTROLLED_ENTRY_LIMIT) + ":\n        def guard_continue(reconciled_state, reconciled_catalog):\n            return guard_projection_quarantine(reconciled_state), 'blocked', dict(), ''\n"
            tail += "    data = dict((key, vars.get(name, '')) for key, name in spec['result_vars'].items())\n    state, route, body, readback = guard_result(state, data, spec['profile'], spec['catalog'], spec['action'])\n"
            # Reconcile a dispatched request before administrative termination.
            # Known recorded successes and uncertain persistence survive it.
            tail += "    if entry_count > " + str(CONTROLLED_ENTRY_LIMIT) + " and state.get('closed') is not True:\n        state = guard_projection_quarantine(state)\n        route, body, readback = 'blocked', dict(), ''\n"
    # Consent resets write the declared enum value 'unclear', never '': the
    # platform drops code writes that a variable's extraction schema refuses.
    tail += "    try:\n        facts = guard_facts(state, spec['phase'], spec['action'], route, data if spec['phase'] == 'result' else dict(), spec['profile'], spec['catalog'], previous_state)\n    except Exception:\n        state = guard_projection_quarantine(state)\n        route = 'closed' if state.get('close_attempted') is True else 'blocked'\n        body, readback = dict(), ''\n        facts = guard_failed_facts(state, spec['action'], route)\n    if state.get('quarantined') is True:\n        body, readback = dict(), ''\n        vars['bg_quarantined'] = 'yes'\n    vars['bg_state'] = guard_json(state)\n    vars['bg_route'] = route\n    vars['bg_body'] = guard_json(body) if body else ''\n    vars['bg_readback'] = readback\n    vars['bg_consent'] = 'unclear'\n    vars['bg_facts'] = guard_json(facts)\n"
    body_fields = sorted({key for op in catalog.values() for key in op['request_schema'].get('properties', {})})
    for name in body_fields:
        tail += "    vars[" + repr("bg_arg_" + name) + "] = body.get(" + repr(name) + ", '')\n"
    if phase == "prepare" and action != "close_call":
        tail += "    if refusal and facts['call_status'] != 'quarantined':\n        facts['latest_action'] = dict(action=spec['action'], status='refused', reason=refusal)\n        vars['bg_facts'] = guard_json(facts)\n"
    for name in sorted(result_vars.values()):
        tail += "    vars[" + repr(name) + "] = ''\n"
    tail += "except Exception:\n    vars['bg_route'] = 'blocked'\n    vars['bg_control_entries'] = str(vars.get('bg_control_entries') or '')\n    vars['bg_body'] = ''\n    vars['bg_readback'] = ''\n    vars['bg_consent'] = 'unclear'\n    vars['bg_facts'] = json.dumps(dict(version=1, route='blocked', call_status='unavailable', latest_action=dict(action=" + repr(action) + ", status='blocked_or_malformed')))\n"
    for name in body_fields:
        tail += "    vars[" + repr("bg_arg_" + name) + "] = ''\n"
    # A refused close has no eligible close left to prove, so it is flagged, never a silent unflagged end.
    if phase != "prepare" or action == "close_call":
        tail += "    try:\n        retained = json.loads(str(vars.get('bg_state') or ''))\n        retained['quarantined'] = True\n        retained['batch'] = dict()\n        retained['consent'] = ''\n        vars['bg_state'] = guard_json(retained)\n    except Exception:\n        vars['bg_state'] = json.dumps(dict(quarantined=True))\n"
    else:
        tail += "    vars['bg_state'] = str(vars.get('bg_state') or '')\n"
    # Preserve the action/result route for evidence, but seal an independent
    # controller-owned priority signal. Invalid state has no recoverable bank
    # evidence; ordinary invalid non-close inputs retain their valid state.
    tail += "    try:\n        retained = json.loads(str(vars.get('bg_state') or ''))\n        guard_known_counts(retained)\n        if retained.get('quarantined') is True:\n            vars['bg_quarantined'] = 'yes'\n    except Exception:\n        vars['bg_quarantined'] = 'yes'\n"
    for name in sorted(result_vars.values()):
        tail += "    vars[" + repr(name) + "] = ''\n"
    return _KERNEL + tail


def _transition(target: str, route: str | None = None) -> dict:
    condition = {"operator": "always"} if route is None else {"logic": "or", "conditions": [{"variable": "bg_route", "operator": "equals", "operand": route}]}
    return {"target_node": target, "description": route or "always", "condition": condition}


def _code_node(name: str, code: str, transitions: list[dict]) -> dict:
    return {"name": name, "node_type": "code", "on_enter": {"type": "silent"},
            "passthrough": True, "passthrough_default_transition": transitions[-1]["target_node"],
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": code},
                    "timeout_ms": {"type": "number", "default": 10000}}}}}], "transitions": transitions}


def _confirmation_speech(profile: dict, action: str) -> dict:
    """Compiler-owned confirmation entry; sourced clauses are spoken verbatim."""
    wait = ("Wait for a clear yes or no about the exact terms just read. "
            "Do not change terms, call tools, or treat another topic as agreement.")
    wording = profile.get("confirmation_readback", {})
    terms = "{{bg_readback}}" + "".join("; " + clause for clause in (wording["clauses"] if action in wording.get("actions", []) else []))
    question = profile.get("confirmation_question") if action in profile.get("confirmation_question_actions", []) else None
    text = terms + ". " + question if question else "Please confirm these exact terms: " + terms + ". Do you agree?"
    return {"on_enter": {"type": "tts", "text": text}, "task_message": wait}


def _terminal() -> dict:
    return {"name": "bg_terminal", "node_type": "end_call", "on_enter": {"type": "silent"},
            "disable_inactivity_monitor": True, "terminal": {"type": "function", "function": {
                "name": "end_call", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {
                    "message_mode": {"type": "string", "default": "none"}}}}}, "transitions": []}


def _entry_budget_code(*, abort: bool = False) -> str:
    """Small controller-only gate; never changes a permitted bank action."""
    source = ("import json\nvars['bg_budget_route'] = 'stop'\ntry:\n"
              "    count = vars.get('bg_control_entries')\n"
              "    if not isinstance(count, str) or not count.isascii() or not count.isdigit() or len(count) > 7:\n"
              "        raise ValueError('controller entry count is unavailable')\n"
              "    count = int(count) + 1\n"
              "    vars['bg_control_entries'] = str(count)\n")
    source += ("    raise ValueError('conversation turn budget exhausted')\n" if abort else
               "    if count > " + str(CONTROLLED_ENTRY_LIMIT) + ":\n"
               "        raise ValueError('controller entry budget exhausted')\n"
               "    vars['bg_budget_route'] = 'continue'\n")
    source += ("except Exception:\n"
               "    vars['bg_budget_route'] = 'stop'\n"
               "    vars['bg_control_entries'] = str(vars.get('bg_control_entries') or '')\n"
               "    vars['bg_budget_exhausted'] = 'yes'\n"
               "    vars['bg_quarantined'] = 'yes'\n"
               "    vars['bg_body'] = ''\n    vars['bg_readback'] = ''\n"
               "    vars['bg_consent'] = 'unclear'\n"
               "    try:\n"
               "        state = json.loads(str(vars.get('bg_state') or ''))\n"
               "        state['quarantined'] = True\n"
               "        vars['bg_state'] = json.dumps(state, allow_nan=False)\n"
               "    except Exception:\n"
               "        pass\n")
    return source


def _bound_entries(nodes: dict, public_names: list[str]) -> None:
    """Bound both unattended-node waits and cycles through multiple nodes."""
    gates = {name: PREFIX + "gate_" + str(index)
             for index, name in enumerate(sorted(public_names))}
    needs_turn_stop = False
    for node in nodes.values():
        for transition in node.get("transitions", []):
            target = transition.get("target_node")
            if target in gates:
                transition["target_node"] = gates[target]
        for field in ("passthrough_default_transition", "max_turn_transition"):
            if node.get(field) in gates:
                node[field] = gates[node[field]]
        if node.get("node_type") == "conversation":
            if "max_node_turns" not in node:
                node["max_node_turns"] = DEFAULT_CONVERSATION_TURNS
            # Omitted source caps use the kit's documented default, with a
            # definite abort rather than the engine's unbounded normal route.
            if not node.get("max_turn_transition"):
                node["max_turn_transition"] = "bg_turn_budget_stop"
                needs_turn_stop = True
    if needs_turn_stop:
        nodes["bg_turn_budget_stop"] = _code_node(
            "bg_turn_budget_stop", _entry_budget_code(abort=True), [_transition("bg_terminal")])
    for name, gate in gates.items():
        if gate in nodes:
            raise GuardCompileError(["controller entry-gate name collision"])
        continue_route = {"target_node": name, "description": "within controller budget",
                          "condition": {"logic": "or", "conditions": [
                              {"variable": "bg_budget_route", "operator": "equals", "operand": "continue"}]}}
        nodes[gate] = _code_node(gate, _entry_budget_code(), [continue_route, _transition("bg_terminal")])


_TOOL_RULES = ("No tool call is allowed in this turn.",
               "The only tool call allowed in this turn is one offered completion. Call at most one tool per message and "
               "speak after every tool result; never end a turn with neither words nor a tool call.")
_SPEECH_RULE = " Say only words meant for the caller, never a tool call, a variable name or your reasoning."
_COMPLETION_ONLY_RULE = (" Nothing in this step can capture a caller answer, so never ask a question and wait here: when the "
                         "caller must be asked something, invoke in this same message the completion to the step that asks it. "
                         "State only account facts and action results that the runtime view shows.")


def _node_discipline(nodes: dict) -> None:
    """Append the kit's per-node tool rules to every authored speaking node.

    A completion-only step (LLM entry, no extraction, only completions) captures
    no answer; when exactly one completion target is a speaking caller-answer
    step, a silent relay sends a caller reply that arrives here to that step.
    """
    for name, node in list(nodes.items()):
        if node.get("passthrough"):
            continue
        routes = node.setdefault("transitions", [])
        completions = [route["target_node"] for route in routes if route.get("is_tool_call") is True]
        text = _TOOL_RULES[bool(completions)] + _SPEECH_RULE
        if (len(completions) == len(routes) > 0 and not node.get("extraction_schema")
                and node.get("on_enter", {"type": "llm"}).get("type") == "llm"):
            text += _COMPLETION_ONLY_RULE
            answers = {target for target in completions if nodes.get(target, {}).get("extraction_schema")
                       and not nodes[target].get("passthrough")
                       and nodes[target].get("on_enter", {"type": "llm"}).get("type") != "silent"}
            if len(answers) == 1:
                target, relay = answers.pop(), PREFIX + name + "_reply"
                nodes[relay] = {"name": relay, "node_type": "conversation", "on_enter": {"type": "silent"},
                                "task_message": "Say nothing.", "passthrough": True,
                                "passthrough_default_transition": target, "transitions": [_transition(target)]}
                routes.append({"target_node": relay, "description": "caller replied in a step that captures no answer",
                               "condition": {"operator": "always"}})
        node["task_message"] = (node.get("task_message", "") + "\n\n" + text).lstrip()


def _exclusive(first: dict, second: dict) -> bool:
    """Whether two rows are single equals tests on one variable with different operands."""
    tests = []
    for row in (first, second):
        terms = row["condition"].get("conditions")
        term = terms[0] if isinstance(terms, list) and len(terms) == 1 and isinstance(terms[0], dict) else {}
        tests.append((term.get("variable"), str(term.get("operand")).casefold()) if term.get("operator") == "equals" else None)
    return bool(tests[0] and tests[1] and tests[0][0] == tests[1][0] and tests[0][1] != tests[1][1])


def _one_route_per_target(nodes: dict) -> list[dict]:
    """Keep one row per target before a node's final always row.

    The kit merges same-target rows at load time into one row, unconditional
    if any is, which can shadow the rows between them. An earlier row to the
    fallback's target is dropped when every intervening row to another target
    excludes it (equals on the same variable, another operand), so it never
    decides differently from the fallback. Any other such row moves to a fresh
    copy of its target, the same entry gate code, so list-order routing and
    the existing route-order checks are unchanged.
    """
    changes = []
    for name, node in sorted(nodes.items()):
        rows = node.get("transitions", [])
        if not rows or rows[-1]["condition"] != {"operator": "always"} or rows[-1].get("is_tool_call"):
            continue
        target, kept = rows[-1]["target_node"], []
        for index, row in enumerate(rows[:-1]):
            if row["target_node"] != target or row.get("is_tool_call"):
                kept.append(row)
            elif all(_exclusive(row, other) for other in rows[index + 1:-1] if other["target_node"] != target):
                changes.append({"node": name, "row": index, "target": target, "change": "dropped"})
            else:
                relay = PREFIX + "relay_" + str(sum(change["change"] == "relayed" for change in changes))
                nodes[relay] = {**copy.deepcopy(nodes[target]), "name": relay}
                kept.append({**row, "target_node": relay})
                changes.append({"node": name, "row": index, "target": target, "change": "relayed", "relay": relay})
        node["transitions"] = kept + rows[-1:]
    return changes


def _prompt_enforced(contract: dict, plan: dict, catalog: dict, nodes: dict) -> str:
    """Compile a relaxed contract's prompt-enforced limitations into dialogue text.

    Returns the system_prompt block (each frozen requirement with its cited active
    source quotes) and appends each requirement to the task_message of every
    conversation node that routes into an action the entry concerns: one whose
    required kinds a cited rule has, or that the requirement names. Strict
    contracts have no entries and change nothing. Braces become parentheses so no
    text reads as a workflow template.
    """
    entries = contract.get("prompt_enforced_limitations") or []
    lines = ["Bank rules that no system check enforces. Follow each of them yourself in the conversation:"]
    roles = {action: spec["operation_role"] for action, spec in execution_action_catalogue().items()}
    for entry in entries:
        quotes = "; ".join('"' + source["quote"] + '"' for source in entry["sources"] if isinstance(source.get("quote"), str))
        lines.append("- " + entry["requirement"].strip() + (" Source: " + quotes if quotes else ""))
        kinds = {kind for kind, rule in contract["active_rules"].items() if set(rule["rule_ids"]) & set(entry["rule_ids"])}
        named = set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", entry["requirement"]))
        concerned = {action for action, role in roles.items() if _REQUIRED[action] & kinds
                     or {action, role, catalog.get(role, {}).get("name")} & named}
        reminder = ("\n\nBank rule for this step that no system check enforces: "
                    + entry["requirement"].strip().replace("{", "(").replace("}", ")"))
        for name, node in plan["nodes"].items():
            if node["node_type"] == "conversation" and any(plan["nodes"].get(route.get("target_node"), {}).get("action")
                                                           in concerned for route in node.get("transitions", [])):
                nodes[name]["task_message"] = nodes[name].get("task_message", "") + reminder
    return "\n".join(lines).replace("{", "(").replace("}", ")") if entries else ""


def _compile_guarded_workflow(plan: dict, contract: dict, openapi: dict, execution_policy: dict,
                              bindings: dict | None = None) -> dict:
    """Return detached ``workflow`` and ``guard_manifest``; perform no I/O.

    Inputs are controller-owned compilation context. Descriptive untyped policy
    is deliberately unsupported rather than converted into invented rules.
    """
    errors = _plan_errors(plan, [operation["name"] for operation in contract.get("permitted_operations", [])],
                          declared_call_variables(contract))
    if errors:
        raise GuardCompileError(errors)
    profile = _profile(contract, execution_policy, plan, bindings)
    catalog = _catalogue(contract, openapi, bindings)
    if "nonpersistent_failures" in profile:
        failure_rules = profile["nonpersistent_failures"]
        actual_operations = {op["name"] for op in catalog.values()}
        if set(failure_rules) - actual_operations:
            raise GuardCompileError(["nonpersistent_failures must name exact permitted API operation IDs"])
        # The kernel uses semantic roles; source guarantees are bound to actual
        # API IDs. Resolve through the same frozen injective alias map as calls.
        profile["nonpersistent_failures"] = {
            role: copy.deepcopy(failure_rules[op["name"]]) for role, op in catalog.items()
            if op["name"] in failure_rules}
    if "account_failed" in profile.get("goal_close", {}) and not catalog.get("get_account", {}).get("failure_receipts"):
        raise GuardCompileError(["goal_close account_failed needs get_account with a declared non-2xx response whose success flag can be false"])
    # A failure class only narrows a witnessed definite no-processing receipt.
    witnessed = [case for case in profile.get("nonpersistent_failures", {}).get("verify_identity", []) if case.get("api_source")]
    if any(not any(case["http_status"] == matcher["http_status"] and case.get("error") == matcher.get("error") for case in witnessed)
           for matchers in profile.get("identity_failure_classes", {}).values() for matcher in matchers):
        raise GuardCompileError(["identity_failure_classes matchers require an equal witnessed verify_identity nonpersistent_failures entry"])
    disclosure = _disclosure_schema(contract, profile, catalog)
    if "get_account" in catalog:
        catalog["get_account"]["disclosure_fields"] = disclosure
    display = profile.get("promise_readback")
    if display is not None:
        products = catalog.get("get_account", {}).get("product_fields", {})
        forbidden = {"product_id", "customer_id", products.get("product_id", "")}
        forbidden.update(op["fields"].get("customer_id", "") for op in catalog.values())
        if any(field in forbidden for field in display["product_fields"]):
            raise GuardCompileError(["promise_readback cannot expose renamed internal or customer IDs"])
        if set(display.get("value_labels", {})) - set(display["product_fields"]):
            raise GuardCompileError(["promise_readback value labels must belong to selected display fields"])
    maps = copy.deepcopy((bindings or {}).get("categories", {}))
    nodes = {name: copy.deepcopy(node) for name, node in plan["nodes"].items() if node["node_type"] == "conversation"}
    bound = {value for node in plan["nodes"].values() if node["node_type"] == "guard_action"
             for value in node.get("inputs", {}).values()}
    for node in nodes.values():
        for entry in node.get("extraction_schema", []):
            if entry["name"] in bound and "enum_values" not in entry and type(entry.get("min_confidence")) in (int, float):
                entry["min_confidence"] = min(entry["min_confidence"], PROPOSAL_MIN_CONFIDENCE)
    _node_discipline(nodes)
    # On the host a routing_prompt replaces the router's intent instruction and
    # every intent row then matched, so the first row won; without it the
    # default router chose correctly. Intent nodes never keep one.
    routing_changes = [{"node": name, "change": "routing_prompt_removed"} for name, node in sorted(nodes.items())
                       if "routing_prompt" in node and any(_router_judged(row["condition"]) for row in node.get("transitions", []))]
    for change in routing_changes:
        del nodes[change["node"]]["routing_prompt"]
    rules = _prompt_enforced(contract, plan, catalog, nodes)
    nodes["bg_terminal"] = _terminal()
    # Initialization failures have no safe bank disposition or valid customer id.
    # A quarantine terminal never fabricates a successful bank close. It is
    # silent unless the source requires an explanation; that line is speech
    # only, so it stays out of the profile every sealed code node embeds.
    nodes["bg_quarantine"] = {**_terminal(), "name": "bg_quarantine"}
    explanation = profile.pop("uncertain_stop_explanation", None)
    if explanation is not None:
        quotes = [source.get("quote") for source in contract["active_rules"]["uncertain_stop_explanation"]["provenance"]]
        if explanation is not True and any(not isinstance(quote, str) or not _sourced_stop_words(explanation, quote) for quote in quotes):
            raise GuardCompileError(["uncertain_stop_explanation wording must occur literally as whole words in its own sourced quotation, not as words it forbids saying"])
        # The kit's verbatim farewell: no model writes, extends or follows the line.
        nodes["bg_quarantine"]["terminal"]["function"]["parameters"]["properties"] = {
            "message_mode": {"type": "string", "default": "custom"},
            "spoken_message": {"type": "string", "default": "the result of this request could not be confirmed" if explanation is True else explanation}}
    nodes["bg_init"] = _code_node("bg_init", _code("init", "", {}, profile, catalog, maps, {}),
        [_transition(plan["initial_node"], "done"), _transition("bg_quarantine")])
    quarantine_route = {"target_node": "bg_quarantine", "description": "sealed quarantine",
                        "condition": {"logic": "or", "conditions": [{"variable": "bg_quarantined",
                            "operator": "equals", "operand": "yes"}]}}
    entries = []
    for name, node in sorted(plan["nodes"].items()):
        if node["node_type"] != "guard_action":
            continue
        action = node["action"]
        success, blocked, failure = node["success_node"], node["blocked_node"], node["failure_node"]
        operation = _OPERATIONS.get(action)
        if action == "finalize_notes" and not profile.get("record_notes"):
            operation = None
        if operation and operation not in catalog:
            raise GuardCompileError([action + ": bank operation is not permitted by frozen contract"])
        api = PREFIX + name + "_api"
        reconcile = PREFIX + name + "_result"
        confirm = PREFIX + name + "_confirm"
        accept = PREFIX + name + "_accept"
        result_vars = {semantic: PREFIX + name + "_result_" + semantic for semantic in catalog[operation]["results"]} if operation else {}
        if operation:
            result_vars['_http_status'] = PREFIX + name + '_result_http_status'
            result_vars['_types'] = PREFIX + name + '_result_types'
        needs_confirmation = (action == "commit_promises" and profile.get("require_confirmation") is True
                              or action in profile.get("confirmation_actions", []))
        routes = [copy.deepcopy(quarantine_route), _transition(success, "done")]
        if operation:
            routes += [_transition(api, "allow")]
            if needs_confirmation:
                routes += [_transition(confirm, "confirm")]
        routes += [_transition(blocked)]
        if action == "close_call":
            # A turn-limit exit must not return to the same public recovery
            # loop when no authorized bank disposition can be formed. A refused
            # close is quarantined above and ends at the quarantine terminal,
            # without fabricating a bank close or persistence claim.
            routes = [copy.deepcopy(quarantine_route), _transition(api, "allow"), _transition("bg_terminal")]
        nodes[name] = _code_node(name, _code("prepare", action, node.get("inputs", {}), profile, catalog, maps, result_vars), routes)
        entries.append(name)
        if not operation:
            continue
        tool = copy.deepcopy(catalog[operation]["tool"])
        body = tool["function"]["parameters"]["properties"]["body"]
        for key, leaf in body["properties"].items():
            leaf["default"] = "{{bg_arg_" + key + "}}"
        # This hook only transports the already validated immutable JSON body.
        # It never decides whether an action is authorized or rewrites its URL.
        tool["pre_process_code"] = "try:\n    config['body'] = str(vars.get('bg_body') or '')\nexcept Exception:\n    pass\n"
        # Transport only the actual immutable HTTP envelope status and the raw
        # JSON type of each trusted result field, which the string mapping
        # erases. The kit documents hooks before response_mapping; API
        # pre_action automatic result-key naming is insufficiently explicit.
        names = [actual for _, actual in sorted(catalog[operation]["results"].items())]
        tool["post_process_code"] = ("try:\n    if isinstance(result, dict):\n        if not isinstance(result.get('data'), dict):\n            result['data'] = dict()\n        result['data'].pop('bg_http_status', None)\n        if 'status_code' in result:\n            result['data']['bg_http_status'] = result['status_code']\n"
            "        kinds = list()\n        for name in " + repr(names) + ":\n            value = result['data'].get(name)\n"
            "            kinds.append('absent' if name not in result['data'] else 'null' if value is None else 'boolean' if value is True or value is False"
            " else 'integer' if isinstance(value, int) else 'number' if isinstance(value, float) else 'string' if isinstance(value, str)"
            " else 'array' if isinstance(value, list) else 'object' if isinstance(value, dict) else 'other')\n"
            "        result['data']['bg_result_types'] = ','.join(kinds)\nexcept Exception:\n    pass\n")
        nodes[api] = {"name": api, "node_type": "api_request", "on_enter": {"type": "silent"},
            "passthrough": True, "passthrough_default_transition": reconcile, "pre_actions": [tool],
            "response_mapping": [{"variable": result_vars[semantic], "path": "data." + actual}
                                 for semantic, actual in sorted(catalog[operation]["results"].items())]
                                 + [{"variable": result_vars['_http_status'], "path": "data.bg_http_status"},
                                    {"variable": result_vars['_types'], "path": "data.bg_result_types"}],
            "transitions": [_transition(reconcile)]}
        if needs_confirmation:
            nodes[confirm] = {"name": confirm, "node_type": "conversation", **_confirmation_speech(profile, action),
                "extraction_schema": [{"name": "bg_consent", "type": "string", "enum_values": ["yes", "no", "unclear"], "description": "Only a fresh unconditional affirmative answer to all exact terms just read is yes. A refusal is no. Any changed term, condition, question or other topic is unclear.", "min_confidence": 0.95, "max_retries": 0}],
                "max_node_turns": 1, "max_turn_transition": accept,
                "transitions": [{"target_node": accept, "description": "evaluate fresh answer", "condition": {"logic": "or", "conditions": [
                    {"variable": "bg_consent", "operator": "equals", "operand": value} for value in ("yes", "no", "unclear")]}}]}
            nodes[accept] = _code_node(accept, _code("confirm", action, {}, profile, catalog, maps, result_vars),
                [copy.deepcopy(quarantine_route), _transition(api, "allow"), _transition(blocked)])
        nodes[reconcile] = _code_node(reconcile, _code("result", action, {}, profile, catalog, maps, result_vars),
            ([_transition("bg_terminal")] if action == "close_call" else
             [copy.deepcopy(quarantine_route), _transition("bg_terminal", "closed"), _transition(api, "allow"), _transition(success, "done"),
              _transition(failure, "failure"), _transition(failure)]))
    # A lone intent row into an LLM-entry decision that can send the caller
    # back costs a routing-model call per answer that the decision judges
    # anyway. The kit evaluates always without a model call, after the
    # caller's turn. An earlier row to the decision would merge with it.
    for name, node in nodes.items():
        rows = node.get("transitions", [])
        target = nodes.get(rows[-1].get("target_node"), {}) if rows and not node.get("passthrough") else {}
        # The node-discipline reply relay row runs only after a caller turn in the decision itself.
        exits = [row for row in target.get("transitions", []) if row["target_node"] != PREFIX + rows[-1]["target_node"] + "_reply"]
        if (rows[-1:] and rows[-1]["condition"].get("operator") == "matches_intent" and exits
                and not any(row.get("is_tool_call") or row.get("target_node") == rows[-1]["target_node"]
                            or "matches_intent" in canonical_json(row["condition"]) for row in rows[:-1])
                and not rows[-1].get("is_tool_call") and (target.get("on_enter") or {}).get("type") == "llm"
                and not target.get("extraction_schema") and not target.get("passthrough")
                and all(row.get("is_tool_call") is True for row in exits) and any(row.get("target_node") == name for row in exits)):
            rows[-1]["condition"] = {"operator": "always"}
            node.pop("routing_prompt", None)
    # The kit date types anchor the extractor on the platform's real date; the
    # kernel judges dates by the call's supplied now (hosted round 1, R8).
    clocked = {value for node in plan["nodes"].values() if node["node_type"] == "guard_action"
               for key, value in node.get("inputs", {}).items() if key in ("date", "time")}
    for node in nodes.values():
        for entry in node.get("extraction_schema", []):
            if entry.get("type") in ("date", "datetime") or entry["name"] in clocked:
                entry["description"] = (entry.get("description", "") + " This call's current date and time is {{now}}:"
                                        " resolve today, tomorrow, weekdays and every other relative date or time"
                                        " from it, never from any other current date.").strip()
    _bound_entries(nodes, list(plan["nodes"]))
    routing_changes += _one_route_per_target(nodes)
    workflow = {"initial_node": "bg_init", "nodes": nodes}
    if "system_prompt" in plan:
        workflow["system_prompt"] = copy.deepcopy(plan["system_prompt"])
    if rules:
        authored = workflow.get("system_prompt", "")
        if not isinstance(authored, str):
            raise GuardCompileError(["plan system_prompt must be text"])
        workflow["system_prompt"] = (authored + "\n\n" if authored else "") + rules
    manifest = {"version": 1, "plan_sha256": digest(plan), "contract_sha256": contract["sha256"],
                "openapi_sha256": digest(openapi), "execution_policy_sha256": execution_policy["sha256"],
                "bindings_sha256": (bindings or {}).get("sha256", ""), "workflow_sha256": digest(workflow),
                "public_action_entries": entries, "protected_variable_prefix": PREFIX,
                "protected_fact_variable": "bg_facts", "fact_schema_version": 1,
                "controller_entry_limit": CONTROLLED_ENTRY_LIMIT,
                "default_conversation_turn_limit": DEFAULT_CONVERSATION_TURNS,
                "routing_normalisation": routing_changes,
                "host_behavior_verified": False, "conversation_compliance_proven": False}
    manifest["sha256"] = digest(manifest)
    return {"workflow": workflow, "guard_manifest": manifest}


def compile_guarded_workflow(plan: dict, contract: dict, openapi: dict, execution_policy: dict,
                             bindings: dict | None = None) -> dict:
    """Compile strict JSON inputs or return a bounded, nonsecret error."""
    try:
        canonical_json([plan, contract, openapi, execution_policy, bindings])
        return _compile_guarded_workflow(plan, contract, openapi, execution_policy, bindings)
    except GuardCompileError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise GuardCompileError(["malformed or unsupported guard compilation input"]) from None


def validate_guarded_workflow(workflow: Any, plan: dict, contract: dict, openapi: dict,
                              execution_policy: dict, bindings: dict | None = None) -> list[str]:
    """Recompile trusted context and compare executable content, not a flag."""
    try:
        expected = compile_guarded_workflow(plan, contract, openapi, execution_policy, bindings)["workflow"]
        if canonical_json(workflow) != canonical_json(expected):
            return ["guarded workflow differs from exact controller compilation (code, bodies, mappings, routing or protected writers)"]
    except GuardCompileError as exc:
        return list(exc.errors)
    except (TypeError, ValueError, KeyError, RecursionError):
        return ["guard context or workflow is malformed"]
    return []


def run_guard_step(phase: str, state: dict | None, action: str = "", inputs: dict | None = None,
                   result: dict | None = None, *, profile: dict, catalog: dict,
                   maps: dict | None = None, customer_id: str = "", now: str = "") -> tuple:
    """Local execution of the identical portable kernel; no API or model calls."""
    namespace: dict[str, Any] = {}
    exec(_KERNEL, namespace)
    if phase == "init":
        return namespace["guard_new"](customer_id, now), "done", {}, ""
    if phase == "prepare":
        return namespace["guard_prepare"](state, action, inputs or {}, profile, catalog, maps or {})
    if phase == "confirm":
        return namespace["guard_confirm"](state, (inputs or {}).get("consent"), catalog, action or (state or {}).get('batch', {}).get('action', ''))
    if phase == "result":
        return namespace["guard_result"](state, result or {}, profile, catalog, action or (state or {}).get('inflight', {}).get('action', ''))
    raise ValueError("unknown local guard phase")

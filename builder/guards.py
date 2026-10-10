"""Compile declarative banking actions into sealed engine-owned subgraphs.

The compiler accepts a frozen contract and a typed execution profile, never a
model assertion that a guard exists. Its validator recompiles the entire graph.
Speech interpretation and the Freya sandbox still require hosted verification.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from .policy import _profile_errors
from .contract import _operation_catalogue
from .safety import canonical_json, digest
from .toolbindings import derive_tools

PREFIX = "bg_"
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_INPUTS = {
    "verify_identity": {"field", "answer"}, "get_account": set(),
    "stage_promise": {"product_id", "amount", "date"},
    "commit_promises": set(), "stage_note": {"product_id", "reason"},
    "finalize_notes": set(), "stage_transfer": {"queue"},
    "commit_transfer": set(), "schedule_callback": {"date", "time"},
    "close_call": set(),
}
_OPTIONAL_INPUTS = {"commit_promises": {"product_id"}, "close_call": {"outcome", "reason"}}
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
    "get_account": {"identity_fields", "identity_wrong_limit"},
    "stage_promise": {"identity_fields", "identity_wrong_limit", "band_ranges", "deadline_days",
                      "floor_fields", "holidays", "business_weekdays", "require_confirmation",
                      "promise_failure_limit"},
    "commit_promises": {"require_confirmation", "confirmation_scope", "promise_failure_limit", "nonpersistent_failures", "promise_failure_disposition", "promise_readback"},
    "stage_note": {"note_reasons", "record_notes"}, "finalize_notes": {"note_reasons", "record_notes"},
    "stage_transfer": {"transfer_reasons", "transfer_prerequisites"}, "commit_transfer": {"transfer_reasons", "transfer_prerequisites"},
    "schedule_callback": {"callback_window", "holidays", "business_weekdays"},
    "close_call": {"close_reasons", "record_notes", "close_precedence", "identity_close", "product_order", "resolve_all_products"},
}


class GuardCompileError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = tuple(sorted(set(errors)))
        super().__init__("Guard compilation failed: " + "; ".join(self.errors))


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


def _plan_errors(plan: Any) -> list[str]:
    errors = []
    if not isinstance(plan, dict) or set(plan) - {"initial_node", "nodes", "system_prompt"}:
        return ["plan supports only initial_node, nodes and system_prompt"]
    nodes = plan.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return ["plan.nodes must be a nonempty object"]
    if not isinstance(plan.get("initial_node"), str) or plan.get("initial_node") not in nodes:
        errors.append("plan.initial_node is unknown")
    def variable(name: Any) -> bool:
        return (isinstance(name, str) and bool(_NAME.fullmatch(name)) and not name.startswith(PREFIX)
                and name not in {"customer_id", "now"}
                and not any(name.startswith(operation + "_") for operation in _FIELDS))
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
            if not isinstance(inputs, dict) or not _INPUTS[action] <= set(inputs) or set(inputs) - _INPUTS[action] - _OPTIONAL_INPUTS.get(action, set()):
                errors.append(name + ": wrong semantic input fields")
            elif not all(variable(value) for value in inputs.values()):
                errors.append(name + ": action inputs must reference untrusted public variables")
            for field in ("success_node", "blocked_node", "failure_node"):
                if node.get(field) not in nodes:
                    errors.append(name + ": unknown " + field)
        elif kind == "conversation":
            allowed = {"name", "node_type", "task_message", "on_enter", "on_enter_first_entry_only",
                       "extraction_schema", "transitions", "max_node_turns", "max_turn_transition"}
            if set(node) - allowed:
                errors.append(name + ": dialogue nodes cannot contain executable or unsupported fields")
            if not isinstance(node.get("task_message", ""), str):
                errors.append(name + ": task_message must be text")
            extraction = node.get("extraction_schema", [])
            transitions = node.get("transitions", [])
            if not isinstance(extraction, list) or not isinstance(transitions, list):
                errors.append(name + ": extraction_schema and transitions must be arrays")
                continue
            for entry in extraction:
                if not isinstance(entry, dict) or not variable(entry.get("name")):
                    errors.append(name + ": extraction cannot write protected or engine variables")
                elif set(entry) - {"name", "type", "description", "enum_values", "validation_pattern", "max_retries", "min_confidence", "extraction_prompt"}:
                    errors.append(name + ": unsupported extraction fields")
            if any(not isinstance(t, dict) or not isinstance(t.get("condition"), dict) or set(t) - {"target_node", "description", "condition", "is_tool_call"} for t in transitions):
                errors.append(name + ": transitions must be documented transition objects")
            for target in [t.get("target_node") for t in transitions if isinstance(t, dict)] + [node.get("max_turn_transition")]:
                if target is not None and target not in nodes:
                    errors.append(name + ": routing may target public nodes only")
        else:
            errors.append(name + ": authored nodes must be conversation or guard_action")
    if not any(isinstance(n, dict) and n.get("action") == "close_call" for n in nodes.values()):
        errors.append("plan requires a guarded close_call; authored terminals are forbidden")
    return errors


def _profile(contract: dict, execution_policy: dict, plan: dict, bindings: dict | None) -> dict:
    _hash_checked(contract, "contract")
    _hash_checked(execution_policy, "execution policy")
    if "bindings_sha256" in execution_policy and execution_policy["bindings_sha256"] != (bindings or {}).get("sha256"):
        raise GuardCompileError(["execution policy is bound to different action bindings"])
    profile = execution_policy.get("profile")
    if not isinstance(profile, dict):
        raise GuardCompileError(["execution policy requires a typed profile"])
    errors = []
    for kind in required_execution_kinds(plan):
        if kind not in profile:
            errors.append("missing typed execution kind: " + kind)
    for kind, value in profile.items():
        active = contract.get("active_rules", {}).get(kind)
        if not isinstance(active, dict) or canonical_json(value) != canonical_json(active.get("value")):
            errors.append("execution profile does not match active frozen rule: " + kind)
        errors.extend(_profile_errors(kind, value))
        if kind in {"record_notes", "resolve_all_products"} and type(value) is not bool:
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
                        if not isinstance(case, dict) or set(case) - {"http_status", "error"} or type(case.get("http_status")) is not int or not 100 <= case["http_status"] <= 599 or ("error" in case and not isinstance(case["error"], str)):
                            errors.append("nonpersistent failure classifications need HTTP status100..599 and optional exact error")
        elif kind == "identity_close":
            if not isinstance(value, dict) or set(value) != {"failed", "incomplete", "unreached"} or any(not isinstance(p, dict) or set(p) - {"outcome", "reason"} or not isinstance(p.get("outcome"), str) or ("reason" in p and not isinstance(p["reason"], str)) for p in value.values()):
                errors.append("identity_close requires failed, incomplete and unreached outcome/reason records")
        elif kind == "transfer_prerequisites" and (not isinstance(value, dict) or not value or any(not isinstance(k, str) or v not in {"none", "identity", "account"} for k, v in value.items())):
            errors.append("transfer_prerequisites requires queue-to-none/identity/account mapping")
        elif kind == "promise_readback":
            if (not isinstance(value, dict) or set(value) - {"product_fields", "value_labels"}
                    or not isinstance(value.get("product_fields"), list) or not value["product_fields"]
                    or any(not isinstance(f, str) or not f or f in {"customer_id", "product_id"} for f in value["product_fields"])
                    or not isinstance(value.get("value_labels", {}), dict)
                    or any(not isinstance(labels, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in labels.items()) for labels in value.get("value_labels", {}).values())):
                errors.append("promise_readback needs human product fields and optional field value labels, excluding internal IDs")
    if errors:
        raise GuardCompileError(errors)
    result = copy.deepcopy(profile)
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
    if "promise_failure_disposition" in result:
        result["promise_failure_disposition"]["reason"] = decode("reasons", decode("notes", result["promise_failure_disposition"]["reason"]))
    if set(result.get("confirmation_actions", [])) - {"commit_promises", "commit_transfer", "schedule_callback"}:
        raise GuardCompileError(["confirmation_actions includes an unsupported customer-write action"])
    if set(result.get("transfer_prerequisites", {})) != set(result.get("transfer_reasons", {})):
        raise GuardCompileError(["transfer prerequisite queues must exactly match permitted transfer queues"])
    if set(result.get("close_precedence", [])) - {"transferred", "promise", "paid", "no_promise"}:
        raise GuardCompileError(["close_precedence requires supported semantic outcomes or explicit wire bindings"])
    for proposal in result.get("identity_close", {}).values():
        allowed = result.get("close_reasons", {}).get(proposal["outcome"])
        if allowed is None or (allowed and proposal.get("reason") not in allowed) or (not allowed and proposal.get("reason")):
            raise GuardCompileError(["identity_close disposition conflicts with allowed closing outcomes/reasons"])
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
        if "bg_http_status" in results.values():
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
        catalog[name] = {"name": actual_name, "request_schema": copy.deepcopy(request), "fields": fields,
                         "results": results, "tool": copy.deepcopy(tools[actual_name])}
        if name == "get_account":
            product_schema = response["properties"][results["products"]].get("items", {})
            keys = product_schema.get("properties", {})
            mapping = {s: product_fields.get(s, s) for s in ("product_id", "type", "days_late", "past_due")}
            if any(field not in keys for field in mapping.values()):
                raise GuardCompileError(["get_account: product field aliases require explicit active product_fields"])
            catalog[name]["product_fields"] = mapping
            catalog[name]["product_schema"] = copy.deepcopy(product_schema)
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
            if not isinstance(category, dict) or set(category) != {"semantic_to_wire", "wire_to_semantic", "operation", "field", "api_enum", "api_enum_constrained"}:
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
            if {v: k for k, v in forward.items()} != reverse or len(forward) != len(reverse) or constrained and any(v not in enum for v in forward.values()):
                raise GuardCompileError(["action binding vocabulary differs from supplied API enum"])
    return catalog


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
    order = profile['product_order']
    if order.get('mode') == 'account_order':
        return list(state['products'])
    return sorted(state['products'], key=lambda p: p[order['field']], reverse=order['direction'] == 'descending')

def guard_product_turn(state, pid, profile):
    if pid in state['pending_promises'] or pid in state['pending_notes']:
        return
    remaining = [p['product_id'] for p in guard_order(state, profile) if p['product_id'] not in state['recorded_promises'] and p['product_id'] not in state['recorded_notes'] and p['product_id'] not in state['pending_promises'] and p['product_id'] not in state['pending_notes']]
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

def guard_new(customer_id, now):
    if not isinstance(customer_id, str) or not customer_id:
        raise ValueError('missing engine customer id')
    text = str(now)
    day = guard_date(text[:10]).isoformat()
    clock = datetime.datetime.fromisoformat(text.replace('Z', '+00:00')).strftime('%H:%M') if len(text) > 10 else ''
    return dict(customer_id=customer_id, now=day, now_time=clock, verified_fields=list(), wrong_answers=0,
                identity_status='pending', products=list(), account_loaded=False,
                pending_promises=dict(), recorded_promises=dict(), pending_notes=dict(),
                recorded_notes=dict(), promise_failures=dict(), attempts=dict(),
                transfer=dict(), callbacks=list(), notes_finalized=False, closed=False,
                quarantined=False, inflight=dict(), batch=dict(), consent='', close=dict())

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
    if proposal is None:
        outcome = guard_decode('outcomes', proposed.get('outcome', ''), maps)
        table = profile['identity_close']
        if state['identity_status'] == 'failed' and not state['account_loaded']:
            proposal = dict(table['failed'])
        elif state['identity_status'] != 'verified' and not state['account_loaded']:
            proposal = dict(table['unreached'] if outcome == table['unreached']['outcome'] and not state['verified_fields'] else table['incomplete'])
        else:
            raise ValueError('closing lacks protected evidence')
    reasons = profile['close_reasons']
    if proposal['outcome'] not in reasons:
        raise ValueError('closing outcome outside policy')
    allowed = reasons[proposal['outcome']]
    if (allowed and proposal.get('reason') not in allowed) or (not allowed and proposal.get('reason')):
        raise ValueError('closing reason outside policy')
    return proposal

def guard_prepare(state, action, inputs, profile, catalog, maps):
    state = json.loads(guard_json(state))
    if state['closed'] or state['quarantined'] or state['inflight']:
        raise ValueError('call is closed, uncertain or has an unresolved operation')
    local_handoff = profile.get('record_notes') is False and action in ('stage_transfer', 'commit_transfer')
    if state['notes_finalized'] and action not in ('finalize_notes', 'close_call') and not local_handoff:
        raise ValueError('the disposition phase is sealed')
    if state['transfer'] and action not in ('commit_transfer', 'close_call'):
        raise ValueError('a staged or recorded transfer permits only handoff and final close')
    if action == 'verify_identity':
        fields = profile['identity_fields']
        if state['identity_status'] in ('verified', 'failed') or state['wrong_answers'] >= profile['identity_wrong_limit']:
            raise ValueError('identity is complete or exhausted')
        field = inputs['field']
        expected = next(f for f in fields if f not in state['verified_fields'])
        if field != expected or not str(inputs['answer']).strip():
            raise ValueError('identity factors must follow policy order')
        operation = action
        semantic = dict(customer_id=state['customer_id'], field=field, answer=str(inputs['answer']))
    elif action == 'get_account':
        if state['identity_status'] != 'verified' or state['account_loaded']:
            raise ValueError('account requires verified identity and is read once')
        operation = action
        semantic = dict(customer_id=state['customer_id'])
    else:
        if action not in ('close_call', 'schedule_callback', 'stage_transfer', 'commit_transfer') and (state['identity_status'] != 'verified' or not state['account_loaded']):
            raise ValueError('action requires trusted identity and account results')
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
            deadline = guard_deadline(today, profile['deadline_days'][bands[0]], profile)
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
            if queue not in profile['transfer_reasons'] or state['recorded_notes'] or state['transfer']:
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
            if state['pending_promises'] or not state['products'] or any(p['product_id'] not in state['recorded_promises'] and p['product_id'] not in state['pending_notes'] for p in state['products']):
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
            if state['account_loaded'] and profile['resolve_all_products'] and state['transfer'].get('status') != 'recorded':
                dispositions = state['recorded_notes'] if profile['record_notes'] else state['pending_notes'] if state['notes_finalized'] else dict()
                if any(p['product_id'] not in state['recorded_promises'] and p['product_id'] not in dispositions for p in state['products']):
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
        batch = dict(action=action, operation=operation, items=items, index=0)
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

def guard_send(state, catalog):
    batch = state['batch']
    semantic = batch['items'][batch['index']]
    operation = batch['operation']
    body = guard_body(semantic, operation, catalog)
    key = operation + ':' + guard_json(body)
    previous = state['attempts'].get(key, dict())
    if previous and previous.get('status') != 'nonpersistent_failed':
        raise ValueError('duplicate or unresolved bank write')
    state['attempts'][key] = dict(status='sent', count=previous.get('count', 0) + 1)
    state['inflight'] = dict(action=batch['action'], operation=operation, body=body, semantic=semantic, key=key)
    return state, 'allow', body, ''

def guard_confirm(state, consent, catalog):
    state = json.loads(guard_json(state))
    if state['closed'] or state['quarantined'] or not state['batch'] or state['inflight'] or consent != 'yes':
        raise ValueError('fresh explicit confirmation is absent')
    state['consent'] = guard_json(state['batch'])
    return guard_send(state, catalog)

def guard_result(state, data, profile, catalog):
    state = json.loads(guard_json(state))
    tx = state['inflight']
    if not tx or state['closed'] or state['quarantined']:
        raise ValueError('no trusted pending result')
    action = tx['action']
    ok = data.get('ok')
    status = data.get('_http_status')
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    nonpersistent = any(type(status) is int and status == case['http_status'] and
                        ('error' not in case or data.get('error') == case['error'])
                        for case in profile.get('nonpersistent_failures', dict()).get(tx['operation'], list()))
    negative_ok = ok is False or type(ok) is str and ok in ('false', 'False')
    if action == 'commit_promises' and nonpersistent and negative_ok:
        state['inflight'] = dict()
        state['attempts'][tx['key']]['status'] = 'nonpersistent_failed'
        pid = tx['semantic']['product_id']
        failures = state['promise_failures'].get(pid, 0) + 1
        state['promise_failures'][pid] = failures
        if failures < profile['promise_failure_limit']:
            return guard_send(state, catalog)
        disposition = profile.get('promise_failure_disposition', dict())
        if disposition.get('clear_pending') is True and disposition.get('reason') in profile.get('note_reasons', list()):
            state['pending_promises'].pop(pid, None)
            state['pending_notes'][pid] = disposition['reason']
            state['batch']['index'] += 1
            if state['batch']['index'] < len(state['batch']['items']):
                return guard_send(state, catalog)
            state['batch'] = dict()
            state['consent'] = ''
            return state, 'failure', dict(), ''
        state['quarantined'] = True
        return state, 'failure', dict(), ''
    if not (type(ok) is bool or type(ok) is str and ok in ('true', 'false', 'True', 'False')):
        state['quarantined'] = True
        return state, 'failure', dict(), ''
    state['inflight'] = dict()
    if action == 'verify_identity':
        if guard_true(ok):
            verified = data.get('verified')
            remaining = data.get('wrong_answers_left')
            if isinstance(remaining, str) and remaining.isdigit():
                remaining = int(remaining)
            limit = profile['identity_wrong_limit']
            if not (type(verified) is bool or type(verified) is str and verified in ('true', 'false', 'True', 'False')) or type(remaining) is not int or not 0 <= remaining <= limit:
                state['quarantined'] = True
                return state, 'failure', dict(), ''
            if guard_true(verified):
                state['verified_fields'].append(tx['semantic']['field'])
            # Use the engine counter, not guessed answer/hint interpretation.
            # It includes errors on all factors and excludes API-qualified
            # noncounting retries such as unreadable answers or hints.
            state['wrong_answers'] = max(state['wrong_answers'], limit - remaining)
            state['identity_status'] = 'failed' if state['wrong_answers'] >= limit else 'verified' if all(f in state['verified_fields'] for f in profile['identity_fields']) else 'pending'
        return state, 'done' if guard_true(ok) else 'failure', dict(), ''
    if action == 'get_account':
        if not guard_true(ok):
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
        if not isinstance(data.get('promise_id'), str) or not data['promise_id']:
            state['quarantined'] = True
            return state, 'failure', dict(), ''
        state['recorded_promises'][pid] = state['pending_promises'].pop(pid)
    elif action == 'finalize_notes':
        pid = tx['semantic']['product_id']
        state['recorded_notes'][pid] = state['pending_notes'][pid]
    elif action == 'commit_transfer':
        state['transfer']['status'] = 'recorded'
    elif action == 'schedule_callback':
        state['callbacks'].append(tx['semantic'])
    elif action == 'close_call':
        state['closed'] = True
        return state, 'closed', dict(), ''
    batch = state['batch']
    batch['index'] += 1
    if batch['index'] < len(batch['items']):
        if state['consent'] and json.loads(state['consent'])['items'] != batch['items']:
            raise ValueError('confirmed snapshot changed')
        return guard_send(state, catalog)
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
    tail = "\nvars['bg_route'] = 'blocked'\nvars['bg_body'] = ''\nvars['bg_readback'] = ''\n"
    tail += "try:\n    spec = json.loads(" + _literal(spec) + ")\n"
    if phase == "init":
        tail += "    state = guard_new(vars.get('customer_id'), vars.get('now'))\n    route, body, readback = 'done', dict(), ''\n"
    else:
        tail += "    state = json.loads(str(vars.get('bg_state') or ''))\n"
        if phase == "prepare":
            tail += "    inputs = dict((key, vars.get(name, '')) for key, name in spec['inputs'].items())\n    state, route, body, readback = guard_prepare(state, spec['action'], inputs, spec['profile'], spec['catalog'], spec['maps'])\n"
        elif phase == "confirm":
            tail += "    state, route, body, readback = guard_confirm(state, vars.get('bg_consent'), spec['catalog'])\n"
        else:
            tail += "    data = dict((key, vars.get(name, '')) for key, name in spec['result_vars'].items())\n    state, route, body, readback = guard_result(state, data, spec['profile'], spec['catalog'])\n"
    tail += "    vars['bg_state'] = guard_json(state)\n    vars['bg_route'] = route\n    vars['bg_body'] = guard_json(body) if body else ''\n    vars['bg_readback'] = readback\n    vars['bg_consent'] = ''\n"
    body_fields = sorted({key for op in catalog.values() for key in op['request_schema'].get('properties', {})})
    for name in body_fields:
        tail += "    vars[" + repr("bg_arg_" + name) + "] = body.get(" + repr(name) + ", '')\n"
    for name in sorted(result_vars.values()):
        tail += "    vars[" + repr(name) + "] = ''\n"
    tail += "except Exception:\n    vars['bg_route'] = 'blocked'\n    vars['bg_body'] = ''\n    vars['bg_readback'] = ''\n    vars['bg_consent'] = ''\n"
    for name in body_fields:
        tail += "    vars[" + repr("bg_arg_" + name) + "] = ''\n"
    if phase != "prepare":
        tail += "    vars['bg_state'] = json.dumps(dict(quarantined=True))\n"
    else:
        tail += "    vars['bg_state'] = str(vars.get('bg_state') or '')\n"
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


def _terminal() -> dict:
    return {"name": "bg_terminal", "node_type": "end_call", "on_enter": {"type": "silent"},
            "disable_inactivity_monitor": True, "terminal": {"type": "function", "function": {
                "name": "end_call", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {
                    "message_mode": {"type": "string", "default": "none"}}}}}, "transitions": []}


def _compile_guarded_workflow(plan: dict, contract: dict, openapi: dict, execution_policy: dict,
                              bindings: dict | None = None) -> dict:
    """Return detached ``workflow`` and ``guard_manifest``; perform no I/O.

    Inputs are controller-owned compilation context. Descriptive untyped policy
    is deliberately unsupported rather than converted into invented rules.
    """
    errors = _plan_errors(plan)
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
    nodes["bg_terminal"] = _terminal()
    # Initialization failures have no safe bank disposition or valid customer id.
    # A silent quarantine terminal never fabricates a successful bank close.
    nodes["bg_quarantine"] = {**_terminal(), "name": "bg_quarantine"}
    nodes["bg_init"] = _code_node("bg_init", _code("init", "", {}, profile, catalog, maps, {}),
        [_transition(plan["initial_node"], "done"), _transition("bg_quarantine")])
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
        needs_confirmation = (action == "commit_promises" and profile.get("require_confirmation") is True
                              or action in profile.get("confirmation_actions", []))
        routes = [_transition(success, "done")]
        if operation:
            routes += [_transition(api, "allow")]
            if needs_confirmation:
                routes += [_transition(confirm, "confirm")]
        routes += [_transition(blocked)]
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
        # Transport only the actual immutable HTTP envelope status. The kit
        # documents hooks before response_mapping; API pre_action automatic
        # result-key naming is insufficiently explicit to use as evidence.
        tool["post_process_code"] = "try:\n    if isinstance(result, dict) and isinstance(result.get('data'), dict):\n        result['data'].pop('bg_http_status', None)\n        if 'status_code' in result:\n            result['data']['bg_http_status'] = result['status_code']\nexcept Exception:\n    pass\n"
        nodes[api] = {"name": api, "node_type": "api_request", "on_enter": {"type": "silent"},
            "passthrough": True, "passthrough_default_transition": reconcile, "pre_actions": [tool],
            "response_mapping": [{"variable": result_vars[semantic], "path": "data." + actual}
                                 for semantic, actual in sorted(catalog[operation]["results"].items())]
                                 + [{"variable": result_vars['_http_status'], "path": "data.bg_http_status"}],
            "transitions": [_transition(reconcile)]}
        if needs_confirmation:
            nodes[confirm] = {"name": confirm, "node_type": "conversation", "on_enter": {"type": "tts", "text": "Please confirm these exact terms: {{bg_readback}}. Do you agree?"},
                "task_message": "Wait for a clear yes or no about the exact terms just read. Do not change terms, call tools, or treat another topic as agreement.",
                "extraction_schema": [{"name": "bg_consent", "type": "string", "enum_values": ["yes", "no", "unclear"], "description": "Only a fresh unconditional affirmative answer to all exact terms just read is yes. A refusal is no. Any changed term, condition, question or other topic is unclear.", "min_confidence": 0.95, "max_retries": 0}],
                "max_node_turns": 1, "max_turn_transition": accept,
                "transitions": [{"target_node": accept, "description": "evaluate fresh answer", "condition": {"logic": "or", "conditions": [
                    {"variable": "bg_consent", "operator": "equals", "operand": value} for value in ("yes", "no", "unclear")]}}]}
            nodes[accept] = _code_node(accept, _code("confirm", action, {}, profile, catalog, maps, result_vars),
                [_transition(api, "allow"), _transition(blocked)])
        nodes[reconcile] = _code_node(reconcile, _code("result", action, {}, profile, catalog, maps, result_vars),
            [_transition("bg_terminal", "closed"), _transition(api, "allow"), _transition(success, "done"),
             _transition(failure, "failure"), _transition(failure)])
    workflow = {"initial_node": "bg_init", "nodes": nodes}
    if "system_prompt" in plan:
        workflow["system_prompt"] = copy.deepcopy(plan["system_prompt"])
    manifest = {"version": 1, "plan_sha256": digest(plan), "contract_sha256": contract["sha256"],
                "openapi_sha256": digest(openapi), "execution_policy_sha256": execution_policy["sha256"],
                "bindings_sha256": (bindings or {}).get("sha256", ""), "workflow_sha256": digest(workflow),
                "public_action_entries": entries, "protected_variable_prefix": PREFIX,
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
        return namespace["guard_confirm"](state, (inputs or {}).get("consent"), catalog)
    if phase == "result":
        return namespace["guard_result"](state, result or {}, profile, catalog)
    raise ValueError("unknown local guard phase")

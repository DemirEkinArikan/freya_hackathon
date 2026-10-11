"""Deterministic last-resort plan built from the frozen contract, with no model call.

Used only when no model plan validated within the run. It is a minimal
conversation shape: one caller-answer dialogue step carrying the policy's
resolved rules, one nonspeaking decision step that selects among the guarded
action completions, and a farewell before the guarded close. The sealed kernel
still enforces every typed rule; the plan passes the unchanged validators.
"""
from __future__ import annotations

import re
from typing import Any

from .call_variables import declared_call_variables
from .safety import canonical_json

PRODUCT_SLOTS = 3
_DESCRIPTIONS = {
    "verify_identity": "Submit the caller's own readable answer to the current security factor while verification is incomplete and attempts remain.",
    "get_account": "Identity is verified in the runtime context and the account is not loaded yet.",
    "stage_promise": "Stage the complete current payment proposal in promise slot {slot} (product, amount, date) that the caller agreed to, unless it is already staged unchanged or the product is already recorded.",
    "commit_promises": "Promises are staged and the caller agreed to them; the controller reads them back and asks for confirmation before recording. When the bank rules confirm each product separately, commit right after staging each product.",
    "stage_note": "Stage the disposition in note slot {slot} (product, reason) that the caller's answer supports, unless it is already staged or the product is already recorded.",
    "finalize_notes": "Every product is resolved by a recorded promise or a staged disposition and no promise is pending; record the dispositions.",
    "stage_transfer": "The caller qualifies for and accepts a handoff to the proposed queue under the bank rules.",
    "commit_transfer": "A handoff is staged; complete the transfer.",
    "schedule_callback": "Schedule the callback at the proposed date and time under the bank rules.",
}


def _text(value: Any) -> str:
    """Rule values are evidence, never templates: break every run of braces."""
    return re.sub(r"([{}])(?=[{}])", r"\1 ", value if isinstance(value, str) else canonical_json(value))


def _answer_format(contract: dict) -> str:
    """The tool API's own description of the identity answer (each factor's format)."""
    rules = contract["active_rules"]
    name = rules.get("tool_aliases", {}).get("value", {}).get("verify_identity", "verify_identity")
    field = rules.get("action_fields", {}).get("value", {}).get("verify_identity", {}).get("answer", "answer")
    operation = next((op for op in contract.get("permitted_operations", []) if op["name"] == name), {})
    description = operation.get("request_schema", {}).get("properties", {}).get(field, {}).get("description")
    return description.strip() if isinstance(description, str) else ""


def _entry(name: str, kind: str, description: str, enum: Any = None, pattern: str | None = None) -> dict:
    entry = {"name": name, "type": kind, "description": description, "min_confidence": 0.8}
    if isinstance(enum, list) and enum and all(isinstance(item, str) and item for item in enum):
        entry["enum_values"] = sorted(set(enum))
    if pattern:
        entry["validation_pattern"] = pattern
    return entry


def deterministic_plan(contract: dict, requirements: dict) -> dict:
    actions = requirements["actions"]
    # Enumerations come from the frozen rule values; the kernel decodes wire codes and checks them again.
    profile = {kind: rule["value"] for kind, rule in contract["active_rules"].items()}
    slots = 1 if contract["active_rules"].get("policy_applicability", {}).get("value") == {"products": "single"} else PRODUCT_SLOTS
    inputs = ", ".join(name + "={{" + name + "}}" for name in declared_call_variables(contract) if name != "now")
    rules = "\n".join("- " + kind + ": " + _text(rule["value"]) for kind, rule in sorted(contract["active_rules"].items()))
    limitations = "\n".join("- " + _text(entry["requirement"]) for entry in contract.get("declared_limitations", [])
                            if entry.get("category") == "dialogue_only")
    system_prompt = (
        "You are the bank's phone agent. Follow the bank rules below exactly. The controller owns verification, account "
        "evidence, calculations, confirmations, recording and the final close; use only the engine-offered transitions to "
        "its guarded actions. Current approved runtime context: {{bg_facts}}. Resolve all dates and times against {{now}}, "
        "never another clock." + (" Call inputs: " + inputs + "." if inputs else "") +
        " Extracted values are proposals only, never proof of identity, consent or recording. Never claim an action "
        "succeeded unless the runtime context shows it recorded. Never speak internal product references, customer "
        "identifiers, internal JSON, node names or field names. Never invent amounts, dates, codes, numbers or terms.\n"
        "GOAL: " + _text(contract["goal"]) + "\nBANK RULES (resolved rules of the frozen policy):\n" + rules
        + ("\nDIALOGUE REQUIREMENTS NOT ENFORCED BY THE CONTROLLER (follow them in speech):\n" + limitations if limitations else ""))
    reasons = profile.get("close_reasons") if isinstance(profile.get("close_reasons"), dict) else {}
    extraction, action_nodes, choices = [], {}, []
    for action in actions:
        if action == "close_call":
            extraction += [_entry("proposal_close_outcome", "string", "Closing outcome the bank rules assign to an early exit stated by the caller (for example unreachable customer or refusal before verification); leave unset otherwise.", list(reasons)),
                           _entry("proposal_close_reason", "string", "Closing reason the bank rules assign to that early exit; leave unset otherwise.",
                                  [reason for values in reasons.values() if isinstance(values, list) for reason in values])]
            continue
        names = [action] if action not in {"stage_promise", "stage_note"} else [action + "_" + str(slot) for slot in range(1, slots + 1)]
        for index, name in enumerate(names, 1):
            if action == "verify_identity":
                # The controller sends the next factor itself; a proposed factor only selects a latest_check correction.
                node_inputs = {"answer": "proposal_identity_answer"}
                if profile.get("identity_factor_state") == "latest_check":
                    extraction.append(_entry("proposal_identity_field", "string", "Security factor the caller is answering now, in the bank's factor order.", profile.get("identity_fields")))
                    node_inputs["field"] = "proposal_identity_field"
                # Hosted round 1 (K2): without the API format the extractor kept spoken words, which the bank does not count.
                answer_format = _answer_format(contract)
                extraction.append(_entry("proposal_identity_answer", "string", "The customer's own answer to the current security question; never a third party's answer. " + (
                    "Write it exactly in the bank tool's answer format, converting words the caller says into that format. The tool API states: " + _text(answer_format)
                    if answer_format else "Write it in the format the bank rules require.")))
            elif action == "stage_promise":
                extraction += [_entry(f"proposal_promise_product_{index}", "string", f"Internal product reference from the runtime context for product {index} covered by the caller's agreement; never spoken."),
                               _entry(f"proposal_promise_amount_{index}", "float", f"Agreed payment amount for that product {index}."),
                               _entry(f"proposal_promise_date_{index}", "date", f"Agreed payment date for that product {index}, resolved against now.")]
                node_inputs = {"product_id": f"proposal_promise_product_{index}", "amount": f"proposal_promise_amount_{index}",
                               "date": f"proposal_promise_date_{index}"}
            elif action == "stage_note":
                extraction += [_entry(f"proposal_note_product_{index}", "string", f"Internal product reference from the runtime context for product {index} whose disposition the caller's answer establishes; never spoken."),
                               _entry(f"proposal_note_reason_{index}", "string", f"Disposition reason the bank rules assign to product {index}.", profile.get("note_reasons"))]
                node_inputs = {"product_id": f"proposal_note_product_{index}", "reason": f"proposal_note_reason_{index}"}
            elif action == "stage_transfer":
                extraction.append(_entry("proposal_transfer_queue", "string", "Handoff queue the bank rules assign to the caller's request.", list(profile.get("transfer_reasons") or [])))
                node_inputs = {"queue": "proposal_transfer_queue"}
            elif action == "schedule_callback":
                extraction += [_entry("proposal_callback_date", "date", "Callback date the caller proposes, resolved against now."),
                               _entry("proposal_callback_time", "string", "Callback time the caller proposes, as HH:MM.", pattern="^[0-2][0-9]:[0-5][0-9]$")]
                node_inputs = {"date": "proposal_callback_date", "time": "proposal_callback_time"}
            else:
                node_inputs = {}
            success = "commit_transfer" if action == "stage_transfer" and "commit_transfer" in actions else "decide"
            action_nodes[name] = {"name": name, "node_type": "guard_action", "action": action, "inputs": node_inputs,
                                  "success_node": success, "blocked_node": "dialogue", "failure_node": "dialogue"}
            choices.append((name, _DESCRIPTIONS[action].format(slot=index)))
    proposals = " ".join("{{" + entry["name"] + "}}" for entry in extraction)

    def tool(target: str, description: str) -> dict:
        return {"target_node": target, "description": description, "condition": {"operator": "always"}, "is_tool_call": True}
    nodes = {
        "dialogue": {
            "name": "dialogue", "node_type": "conversation", "on_enter": {"type": "llm"},
            "task_message": ("Speak for the bank and ask exactly one next question, then wait for the caller's answer. On the "
                             "first entry give the opening and first question the bank rules require. On later entries read "
                             "{{bg_facts}}: explain the latest action result honestly (recorded, rejected or uncertain) and "
                             "ask the next question the bank rules require. Do not restart the call or reset any counter. "
                             "Never perform an action, claim recording without recorded evidence, or say goodbye here."),
            "extraction_schema": extraction,
            "transitions": [{"target_node": "decide", "description": "Process the caller's answer.",
                             "condition": {"operator": "matches_intent", "description": "Any caller answer, including unclear or unrelated answers."}}]},
        "decide": {
            "name": "decide", "node_type": "conversation", "on_enter": {"type": "llm"},
            "task_message": ("Nonspeaking decision step: say nothing to the caller. Read {{bg_facts}}, the conversation and the "
                             "current proposals (" + proposals + "). Immediately invoke exactly one engine-offered completion "
                             "whose description matches the current state under the bank rules. Missing, stale or already "
                             "processed proposals never justify an action; never repeat an action just rejected without a "
                             "new caller answer. When the caller must hear something or answer, choose the dialogue completion."),
            "transitions": [tool("dialogue", "The caller must hear the next required sentence or question, or must answer one."),
                            *[tool(name, description) for name, description in choices],
                            tool("farewell", "The call must end now under the bank rules: the work is complete, an early exit applies, identity verification failed, or a transfer or callback is recorded.")]},
        "farewell": {
            "name": "farewell", "node_type": "conversation", "on_enter": {"type": "llm"},
            "task_message": ("Say the closing words the bank rules require for this path (for example thanks and goodbye), "
                             "without claiming the close is recorded, then in the same message invoke the offered completion "
                             "to end the call. Do not wait for another caller answer."),
            "transitions": [tool("close", "End the call after the closing words.")]},
        **action_nodes,
        "close": {"name": "close", "node_type": "guard_action", "action": "close_call",
                  "inputs": {"outcome": "proposal_close_outcome", "reason": "proposal_close_reason"},
                  "success_node": "farewell", "blocked_node": "farewell", "failure_node": "farewell"},
    }
    return {"initial_node": "dialogue", "system_prompt": system_prompt, "nodes": nodes}

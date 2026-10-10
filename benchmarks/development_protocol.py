"""Authored synthetic development-v2 protocol; no customer facts or oracles."""
from copy import deepcopy


REQUEST_TYPES = {
    "verify_identity": {"customer_id": "string", "field": "string", "answer": "string"},
    "get_account": {"customer_id": "string"},
    "create_promise": {"customer_id": "string", "product_id": "string", "amount": "number", "date": "string"},
    "add_note": {"customer_id": "string", "product_id": "string", "reason": "string"},
    "schedule_callback": {"customer_id": "string", "date": "string", "time": "string"},
    "transfer_to_agent": {"customer_id": "string", "queue": "string"},
    "close_call": {"customer_id": "string", "outcome": "string", "reason": "string"},
}
RESULT_ROLES = {
    "verify_identity": ("ok", "verified", "wrong_answers_left", "hint", "error"),
    "get_account": ("ok", "products", "error"),
    "create_promise": ("ok", "promise_id", "error"),
    "add_note": ("ok", "error"), "schedule_callback": ("ok", "error"),
    "transfer_to_agent": ("ok", "error"), "close_call": ("ok", "error"),
}
RAW_RESULTS = {"ok": "accepted", "verified": "identity_match", "wrong_answers_left": "attempts_remaining",
               "hint": "guidance", "error": "failure_code", "products": "accounts", "promise_id": "record_ref"}


def result_mapping(raw=False):
    return {role: {name: RAW_RESULTS[name] if raw else name for name in names}
            for role, names in RESULT_ROLES.items()}


def response_definitions(role, product_properties, identity_limit, raw=False):
    """Declare a local protocol, without sample answers, records or bank data.

    Original development-v1 HTTP200 meanings remain: an exact false result is
    a definite nonpersistent failure. New status alternatives are explicitly
    authored here; they do not assert support on the organizer bank service.
    """
    boolean = {"type": "string", "enum": ["true", "false"]} if raw else {"type": "boolean"}
    fields = {
        "ok": {**boolean, "description": "Semantic result field: ok. Exact true means this operation completed; writes persisted. Exact false means no write persisted."},
        "verified": {**boolean, "description": "Semantic result field: verified. Match for this requested factor only; not a waiver of other factors."},
        "wrong_answers_left": {"type": "integer", "minimum": 0, "maximum": identity_limit,
                               "description": "Semantic result field: wrong_answers_left. Shared remaining wrong-answer budget across every factor."},
        "hint": {"type": "string", "description": "Semantic result field: hint. Optional bank-supplied format guidance."},
        "error": {"type": "string", "enum": ["temporary_unavailable", "invalid_request", "verification_denied"],
                  "description": "Semantic result field: error. Exact false and this declared code identify definite failure; missing/contradictory evidence is uncertain."},
        "products": {"type": "array", "items": {"type": "object", "properties": deepcopy(product_properties),
                      "required": ["product_id", "type", "past_due", "days_late"]},
                     "description": "Semantic result field: products. Account products in bank-returned order; required on a successful lookup."},
        "promise_id": {"type": "string", "minLength": 1,
                       "description": "Semantic result field: promise_id. Nonempty opaque persisted-record identity, required on a successful promise write."},
    }
    mapping = result_mapping(raw)[role]
    schema = {"type": "object", "properties": {mapping[name]: deepcopy(fields[name]) for name in RESULT_ROLES[role]},
              "required": [mapping["ok"]]}
    description = ("Synthetic development-v2 only. HTTP200 exact ok=false with a declared error is a definite nonpersistent failure; "
                   "ok=true means a completed operation, and persisted success for a write. An identity mismatch is ok=true, verified=false. "
                   "Successful identity checks also provide verified and wrong_answers_left; a successful account lookup provides products; "
                   "a successful promise write provides a nonempty promise_id. Missing/malformed evidence never implies success.")
    responses = {"200": {"description": description, "content": {"application/json": {"schema": schema}}}}
    if raw:
        failure = deepcopy(schema)
        failure["properties"][mapping["ok"]]["enum"] = ["false"]
        failure["properties"][mapping["error"]]["enum"] = ["temporary_unavailable"]
        failure["required"] = [mapping["ok"], mapping["error"]]
        responses["503"] = {"description": "Authored local alternative: exact accepted=false and failure_code=temporary_unavailable guarantee no persistence.",
                            "content": {"application/json": {"schema": failure}}}
        if role == "create_promise":
            created = deepcopy(schema)
            created["properties"][mapping["ok"]]["enum"] = ["true"]
            created["required"] = [mapping["ok"], mapping["promise_id"]]
            responses["201"] = {"description": "Authored local alternative: accepted=true with nonempty record_ref identifies a persisted promise.",
                                "content": {"application/json": {"schema": created}}}
    return responses

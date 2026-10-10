"""Independent evaluator-only local protocol samples; never export to a model."""
from copy import deepcopy


RAW_FIELDS = {"ok": "accepted", "verified": "identity_match", "wrong_answers_left": "attempts_remaining",
              "hint": "guidance", "error": "failure_code", "products": "accounts", "promise_id": "record_ref"}


def protocol_response(role, *, raw=False, ok=True, status=200, remaining=2, products=None):
    """Return an invented response envelope, with no dependency on an oracle.

    Consumers can replace products and remaining counts from their own isolated
    local bank state. No response builder implies a Freya-hosted bank simulator.
    """
    body = {"ok": ok}
    if role == "verify_identity":
        body.update(verified=ok, wrong_answers_left=remaining)
    elif role == "get_account" and ok:
        body["products"] = deepcopy(products or [])
    elif role == "create_promise" and ok:
        body["promise_id"] = "local-protocol-record"
    if not ok:
        body["error"] = "temporary_unavailable"
    if raw:
        body = {RAW_FIELDS.get(key, key): ("true" if value else "false") if type(value) is bool else value
                for key, value in body.items()}
    return {"status_code": status, "data": body}


def decode_protocol_response(envelope, *, raw=False):
    reverse = {wire: semantic for semantic, wire in RAW_FIELDS.items()}
    body = {reverse.get(key, key) if raw else key: deepcopy(value) for key, value in envelope["data"].items()}
    for key in ("ok", "verified"):
        if raw and key in body:
            body[key] = {"true": True, "false": False}[body[key]]
    return {"status_code": envelope["status_code"], "data": body}

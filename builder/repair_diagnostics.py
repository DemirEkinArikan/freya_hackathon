"""A closed, data-free boundary from acceptance evidence to plan repairs.

This runtime module deliberately has no evaluator dependencies. Evaluator text
is used only as a lookup key; it is never returned, interpolated, or trusted as
instructions. The policy argument must be the controller's frozen, approved
input policy. Only its provenanced rule identifiers may cross this boundary.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


DIAGNOSTICS = {
    "closing outcome/reason have not been recorded":
        "Ensure the completed conversation reaches its permitted final closing action, using the outcome and reason derived from the supplied brief and observed action state.",
    "outcome/reason do not match the independent oracle":
        "Choose the closing outcome and reason from the supplied brief and observed conversation state.",
    "promises do not match the independent oracle":
        "Recheck commitment eligibility, confirmation, write arguments, and successful-write state against the supplied brief.",
    "notes do not match the independent oracle":
        "Recheck whether a note is allowed or required, its scope, and its reason against the supplied brief.",
    "callbacks do not match the independent oracle":
        "Recheck callback eligibility and the agreed date and time against the supplied brief's calendar rules.",
    "transfers do not match the independent oracle":
        "Recheck transfer eligibility and queue selection against the supplied brief and tool contract.",
    "close_call must occur exactly once and be the final tool":
        "Close the call without duplication after all permitted actions; prevent further tool calls after closing.",
    "final close_call failed":
        "Prepare and validate closing terms under the supplied brief and tool contract before the single closing attempt; report a failed closing action truthfully.",
    "Brief A forbids add_note":
        "Remove note writes when the applicable supplied brief forbids them.",
    "a transfer forbids notes before or after it":
        "Enforce the applicable transfer and note exclusion before performing either write.",
    "identity/account tools used with a third party or unreached customer":
        "Gate identity checks and account access on reaching the intended customer.",
    "tool is outside the seven permitted banking capabilities":
        "Use only capabilities declared in the supplied tool contract.",
    "identity field is not permitted by the contract":
        "Select identity fields only from the supplied contract and applicable policy.",
    "an already verified identity factor was checked again":
        "Preserve successful identity checks and do not ask for a verified factor again.",
    "identity factors were asked out of order":
        "Ask identity factors in the order required by the supplied brief.",
    "identity check continued after total wrong-answer limit":
        "Stop identity checks when the supplied brief's wrong-answer limit is reached.",
    "account access or promise occurred before both identity factors passed":
        "Prevent account access and commitment writes until all required identity factors have passed.",
    "promise attempted before a successful account lookup":
        "Require a successful account lookup after identity verification before attempting a commitment write; use only the returned account state.",
    "promise attempted after a successful write for the product":
        "Stop commitment attempts for a product after its write succeeds; preserve that successful state across later conversation turns.",
    "promise retries exceeded one retry per product":
        "Enforce the supplied brief's retry limit separately for each product; distinguish explicit rejection from ambiguous persistence.",
    "more than one successful promise write for a product":
        "Prevent duplicate successful commitment writes for the same product.",
    "recorded-success claim precedes successful write":
        "Claim that an action was recorded only after the tool confirms the write succeeded.",
    "promise lacks exact readback and subsequent confirmation before tool start":
        "Read back the proposed commitment terms exactly and obtain the required confirmation after readback and before starting the write, under the supplied brief.",
    "promise used consent withdrawn or changed before tool start":
        "Discard consent when commitment terms change or consent is withdrawn; read back the current terms and obtain fresh confirmation before starting the write.",
    "products lack one collective readback and confirmation before writes":
        "Apply the supplied brief's collective confirmation requirement: read back all proposed product commitments together and obtain confirmation before starting their writes.",
    "farewell is absent before close_call":
        "Give the farewell required by the supplied brief before the single closing tool attempt.",
    "success claim lacks matching successful completion before speech":
        "Wait for matching successful tool completion before claiming that an action succeeded; keep spoken terms consistent with the confirmed write.",
    "banking action attempted after a successful transfer":
        "After a transfer succeeds, stop further banking actions and perform only the permitted final closing action under the supplied brief.",
    "note attempted before a successful verified account lookup":
        "Require completed identity verification and a successful account lookup before an allowed note write; use only the returned account state.",
    "recorded-success claim terms differ from successful write":
        "Describe a recorded commitment using only the exact terms confirmed by its successful tool result; do not substitute proposed or earlier terms.",
}

# Typed rule-kind names are interface labels, not values from a bank brief.
# Ordering selects one useful source reference when the scorer supplies none.
RULE_KINDS = {
    "closing outcome/reason have not been recorded": ("close_precedence", "close_reasons", "identity_close"),
    "outcome/reason do not match the independent oracle": ("close_precedence", "close_reasons", "identity_close"),
    "promises do not match the independent oracle": ("require_confirmation", "confirmation_scope", "floor_fields", "deadline_days"),
    "notes do not match the independent oracle": ("record_notes", "note_reasons"),
    "callbacks do not match the independent oracle": ("callback_window", "business_weekdays", "holidays"),
    "transfers do not match the independent oracle": ("transfer_prerequisites", "transfer_reasons"),
    "close_call must occur exactly once and be the final tool": ("close_precedence", "close_reasons"),
    "final close_call failed": ("close_reasons", "close_precedence", "nonpersistent_failures"),
    "Brief A forbids add_note": ("record_notes", "note_reasons"),
    "a transfer forbids notes before or after it": ("record_notes", "transfer_reasons"),
    "identity/account tools used with a third party or unreached customer": ("identity_close", "identity_fields"),
    "tool is outside the seven permitted banking capabilities": ("tool_aliases", "action_fields"),
    "identity field is not permitted by the contract": ("identity_fields",),
    "an already verified identity factor was checked again": ("identity_fields",),
    "identity factors were asked out of order": ("identity_fields",),
    "identity check continued after total wrong-answer limit": ("identity_wrong_limit",),
    "account access or promise occurred before both identity factors passed": ("identity_fields",),
    "promise attempted before a successful account lookup": ("identity_fields", "product_fields"),
    "promise attempted after a successful write for the product": ("promise_failure_limit", "resolve_all_products"),
    "promise retries exceeded one retry per product": ("promise_failure_limit", "write_retryable_errors", "nonpersistent_failures"),
    "more than one successful promise write for a product": ("promise_failure_limit", "resolve_all_products"),
    "recorded-success claim precedes successful write": ("promise_readback", "require_confirmation"),
    "promise lacks exact readback and subsequent confirmation before tool start": ("promise_readback", "require_confirmation", "confirmation_scope"),
    "promise used consent withdrawn or changed before tool start": ("require_confirmation", "promise_readback", "confirmation_scope"),
    "products lack one collective readback and confirmation before writes": ("confirmation_scope", "require_confirmation", "promise_readback"),
    "farewell is absent before close_call": ("close_precedence", "close_reasons"),
    "success claim lacks matching successful completion before speech": ("promise_readback", "require_confirmation", "result_fields"),
    "banking action attempted after a successful transfer": ("transfer_prerequisites", "transfer_reasons", "close_precedence"),
    "note attempted before a successful verified account lookup": ("identity_fields", "record_notes"),
    "recorded-success claim terms differ from successful write": ("promise_readback", "result_fields"),
}

_FALLBACKS = {
    "behavior": "Recheck the applicable banking actions and their ordering against the supplied brief and tool contract.",
    "conversation": "Recheck the conversation instructions and required confirmations in the supplied brief.",
    "honesty": "Keep claims about account state and completed actions consistent with observed successful tool results.",
    "policy": "Recheck the applicable constraints in the supplied brief and their enforcement before tool calls.",
}
MAX_DIAGNOSTICS = 32

# A known evidence defect cannot become a plan failure by changing its category.
_NONBEHAVIORAL_MESSAGES = {
    "incomplete ledger", "invalid scoring contract", "malformed outcome/reason record",
    "invalid or ambiguous outcome semantic bindings", "malformed tool call record",
    "malformed tool arguments", "conflicting tool argument aliases", "malformed tool name",
    "malformed tool result", "closing tool terms differ from the ledger",
    "malformed identity action result", "malformed identity verification result",
    "malformed identity field", "identity success contradicts failed tool action",
    "malformed identity wrong-answer counter", "identity wrong-answer counter contradicts recorded checks",
    "malformed identity completion flag", "identity completion flag contradicts recorded checks",
    "malformed claim-order evidence", "unsupported claim-order evidence",
    "recorded-success claim has invalid tool position",
    "malformed or unbound conversation evidence", "malformed tool action result",
    "malformed identity hint/error result", "recorded-success claim terms absent or invalid",
    "promise ledger differs from successful tool writes",
    *(f"invalid {collection} record" for collection in ("promises", "notes", "callbacks", "transfers")),
    *(f"{collection} ledger differs from successful tool writes" for collection in ("notes", "callbacks", "transfers")),
}


def _valid_failures(failures: Any) -> bool:
    return isinstance(failures, list) and all(
        isinstance(failure, dict)
        and isinstance(failure.get("category"), str)
        and failure["category"] in _FALLBACKS
        and isinstance(failure.get("message"), str)
        and bool(failure["message"].strip())
        and failure["message"] not in _NONBEHAVIORAL_MESSAGES
        for failure in failures)


def _evidence_gap(value: Any, active: set[int] | None = None,
                  depth: int = 0, nodes: list[int] | None = None) -> bool:
    """Inspect evidence flags without interpreting or returning private data.

    A cyclic/non-JSON evidence graph cannot establish complete acceptance.
    Nested judge/customer status strings are intentionally not run statuses.
    """
    nodes = [0] if nodes is None else nodes
    nodes[0] += 1
    if depth > 64 or nodes[0] > 50000:
        return True
    if not isinstance(value, (dict, list)):
        return False
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        return True
    active.add(identity)
    try:
        if isinstance(value, dict):
            if "missing_evidence" in value and (not isinstance(value["missing_evidence"], list) or value["missing_evidence"]):
                return True
            if value.get("category") in ("infrastructure", "evidence"):
                return True
            return any(_evidence_gap(child, active, depth + 1, nodes) for child in value.values())
        return any(_evidence_gap(child, active, depth + 1, nodes) for child in value)
    finally:
        active.remove(identity)


def _failure_counts(failures: list[dict]) -> Counter:
    return Counter((row["category"], row["message"]) for row in failures)


def test_infrastructure_failed(result: Any) -> bool:
    """Fail closed on incomplete, inconsistent, or nonbehavioral evidence.

    Legacy local runners may omit hosted rows. When present, rows must contain
    complete verdicts, and the aggregate must include their failures. Hosted
    repetitions count fixtures in ``total``, so row count is not that counter.
    """
    if not isinstance(result, dict):
        return True
    total, passed = result.get("total"), result.get("passed")
    if type(total) is not int or total <= 0 or type(passed) is not int or not 0 <= passed <= total:
        return True
    failures, status = result.get("failures"), result.get("status")
    if not _valid_failures(failures) or not isinstance(status, str):
        return True
    if status not in {"passed", "completed", "failed", "behavioral_failure"}:
        return True
    if (passed < total and not failures) or (passed == total and failures):
        return True
    if status == "passed" and passed != total or status in {"failed", "behavioral_failure"} and passed == total:
        return True
    try:
        evidence_gap = _evidence_gap(result)
    except RecursionError:
        evidence_gap = True
    if evidence_gap:
        return True
    if "deployment_pin" in result:
        pin = result["deployment_pin"]
        if (not isinstance(pin, dict) or pin.get("verified") is not True
                or pin.get("per_call_version_verified") is not True):
            return True
    if "reps" in result and (type(result["reps"]) is not int or result["reps"] <= 0):
        return True
    if "simulations" in result:
        simulations = result["simulations"]
        if not isinstance(simulations, list) or not simulations:
            return True
        repetitions = set()
        for simulation in simulations:
            if (not isinstance(simulation, dict) or simulation.get("status") != "completed"
                    or "interrupted" in simulation and simulation["interrupted"] is not False):
                return True
            if "reps" in result:
                repetition = simulation.get("repetition")
                if (type(repetition) is not int or not 1 <= repetition <= result["reps"]
                        or repetition in repetitions):
                    return True
                repetitions.add(repetition)
        if "reps" in result and len(repetitions) != result["reps"]:
            return True
    if "final" in result:
        final = result["final"]
        if not isinstance(final, list) or len(final) != total:
            return True
        for row in final:
            if not isinstance(row, dict) or type(row.get("pass")) is not bool:
                return True
            count, runs, required = row.get("passes"), row.get("runs"), row.get("required_runs")
            if (type(count) is not int or type(runs) is not int or type(required) is not int
                    or required <= 0 or runs != required or not 0 <= count <= runs
                    or row["pass"] != (count == required)
                    or "reps" in result and required != result["reps"]):
                return True
        if sum(row["pass"] for row in final) != passed:
            return True
    if "rows" in result:
        rows = result["rows"]
        if not isinstance(rows, list) or not rows:
            return True
        if len(rows) < total or "reps" in result and len(rows) != total * result["reps"]:
            return True
        nested_failures = []
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("pass")) is not bool
                    or not _valid_failures(row.get("failures"))
                    or row["pass"] != (not row["failures"])):
                return True
            nested_failures.extend(row["failures"])
        if _failure_counts(nested_failures) != _failure_counts(failures):
            return True
        if passed == total and not all(row["pass"] for row in rows):
            return True
        if passed < total and all(row["pass"] for row in rows):
            return True
        if "final" in result:
            final = result["final"]
            if (sum(row["passes"] for row in final) != sum(row["pass"] for row in rows)
                    or sum(row["runs"] for row in final) != len(rows)):
                return True
            if "reps" in result:
                # A complete hosted bundle must establish every fixture's own
                # verdict. Aggregate sums alone cannot detect swapped counts
                # or a duplicate repetition replacing a missing execution.
                fixtures = {}
                for row in final:
                    fixture = row.get("fixture_id")
                    if not isinstance(fixture, str) or not fixture.strip() or fixture in fixtures:
                        return True
                    fixtures[fixture] = row
                repetitions = {fixture: set() for fixture in fixtures}
                pass_counts = Counter()
                for row in rows:
                    fixture, repetition = row.get("fixture_id"), row.get("repetition")
                    if (not isinstance(fixture, str) or fixture not in fixtures
                            or type(repetition) is not int or not 1 <= repetition <= result["reps"]
                            or repetition in repetitions[fixture]):
                        return True
                    repetitions[fixture].add(repetition)
                    pass_counts[fixture] += row["pass"]
                if any(len(repetitions[fixture]) != result["reps"]
                       or pass_counts[fixture] != row["passes"]
                       for fixture, row in fixtures.items()):
                    return True
    return False


def _approved_rules(policy: Any) -> dict[str, str]:
    """Accept typed IDs only when their input source reference is present.

    Content provenance was checked against approved source bytes before freeze;
    this standalone boundary rechecks its shape without importing a compiler or
    carrying source quotes, filenames, hashes, or rule values into diagnostics.
    """
    if not isinstance(policy, dict) or not isinstance(policy.get("sources"), list) or not isinstance(policy.get("rules"), list):
        return {}
    sources = {}
    for source in policy["sources"]:
        if not isinstance(source, dict):
            continue
        ident, file = source.get("id"), source.get("file")
        if not isinstance(ident, str) or not ident.strip() or not isinstance(file, str) or not file.strip():
            continue
        fingerprint = source.get("sha256")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            continue
        sources[ident] = sources.get(ident, 0) + 1
    counts = Counter(rule.get("id") for rule in policy["rules"] if isinstance(rule, dict) and isinstance(rule.get("id"), str))
    approved = {}
    for rule in policy["rules"]:
        if not isinstance(rule, dict):
            continue
        ident, kind, source = rule.get("id"), rule.get("kind"), rule.get("source")
        if (not isinstance(ident, str) or not ident or len(ident) > 128
                or any(character.isspace() or ord(character) < 32 for character in ident)
                or counts[ident] != 1 or not isinstance(kind, str) or not kind.strip()
                or "value" not in rule or not isinstance(source, dict)):
            continue
        source_id, quote = source.get("id"), source.get("quote")
        if isinstance(source_id, str) and sources.get(source_id) == 1 and isinstance(quote, str) and quote.strip():
            approved[ident] = kind
    return approved


def repair_test_summary(result: dict, policy: dict | None = None) -> dict:
    """Return only fixed actionable guidance for a complete behavioral failure.

    Direct callers cannot bypass the infrastructure gate. Unknown free-form
    messages get fixed category guidance; no message fragments survive.
    """
    if test_infrastructure_failed(result) or result["passed"] == result["total"]:
        raise ValueError("Complete behavioral failure evidence is required for plan repair")
    approved = _approved_rules(policy)
    failures, seen = [], set()
    for failure in result["failures"]:
        category, message = failure["category"], failure["message"]
        diagnostic = DIAGNOSTICS.get(message, _FALLBACKS[category])
        rule_id = failure.get("rule_id")
        if isinstance(rule_id, str) and rule_id in approved:
            reference = rule_id
        elif "rule_id" not in failure:
            reference = next((ident for kind in RULE_KINDS.get(message, ())
                              for ident, actual_kind in approved.items() if actual_kind == kind), None)
        else:
            reference = None
        key = (category, diagnostic, reference)
        if key in seen:
            continue
        seen.add(key)
        row = {"category": category, "diagnostic": diagnostic}
        if reference is not None:
            row["rule_id"] = reference
        failures.append(row)
        if len(failures) == MAX_DIAGNOSTICS:
            break
    return {"status": "behavioral_failure", "passed": result["passed"], "total": result["total"], "failures": failures}

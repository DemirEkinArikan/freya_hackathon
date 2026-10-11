"""Provenance-aware policy IR and pure, policy-driven banking primitives.

These helpers validate proposed actions and ledger state. They do not interpret
speech, prove compliance of an LLM, or replace platform conversation tests.
Amounts are represented in integer minor units; all clocks come from ``now``.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, TypedDict

from .safety import canonical_json, digest
from .call_variables import CallVariablesError, check_call_variables


class RuleSource(TypedDict):
    id: str
    quote: str


class PolicyRule(TypedDict, total=False):
    id: str
    kind: str
    value: Any
    source: RuleSource
    overrides: list[str]


class ProductCountBounds(TypedDict, total=False):
    min: int
    max: int


class EffectiveDateBranch(TypedDict):
    date: str
    rule: str


class EffectiveDateSelection(TypedDict):
    before_rule: str
    on_or_after: EffectiveDateBranch


class ScopedDateValue(TypedDict):
    effective_date: EffectiveDateSelection


class PolicyError(ValueError):
    pass


class MissingExecutionRules(PolicyError):
    """Required policy inputs are absent; changing dialogue cannot supply them."""
    def __init__(self, kinds):
        self.missing_kinds = tuple(sorted(set(kinds)))
        super().__init__("required execution rule is missing: " + ", ".join(self.missing_kinds))


_PROFILE_KINDS = {"identity_fields", "identity_wrong_limit", "holidays", "business_weekdays",
                  "band_ranges", "deadline_days", "floor_fields", "callback_window",
                  "note_reasons", "close_reasons", "transfer_reasons", "require_confirmation",
                  "promise_failure_limit", "tool_aliases", "persuasion_limits", "identity_hint_exemptions",
                  "write_retryable_errors", "confirmation_scope", "note_failure_limit", "callback_failure_limit",
                  "transfer_failure_limit", "action_fields", "result_fields", "product_fields"}
_PROFILE_KINDS.update({"wire_codes", "policy_applicability", "product_count_bounds"})
_EXECUTION_ONLY_KINDS = {"nonpersistent_failures", "confirmation_actions", "confirmation_readback", "product_order",
                         "close_precedence", "identity_close", "promise_failure_disposition",
                         "record_notes", "resolve_all_products", "transfer_prerequisites",
                         "promise_readback", "account_disclosure_fields", "identity_close_choices", "goal_close", "call_variables", "call_input_formats",
                         "attempt_scopes", "definite_failure_resume",
                         "confirmation_question", "confirmation_question_actions",
                         "identity_factor_state", "identity_counter_movement", "identity_failure_classes",
                         "uncertain_stop_explanation"}

# Interface descriptions contain no bank values. They explain an existing
# rejection; the predicates below remain the authority for accepted shapes.
_DESCRIPTIVE_EXTENSION_GUIDANCE = {
    "promise_failure_resolution": "descriptive extension only: operation is the selected actual API ID; optional api_annotations is either {document: openapi, HTTP-status: {pointer, quote}} with three-digit response-status keys, or a nonempty array of exact {document: openapi, operation: actual API ID, http_status: integer100..599, pointer, quote} rows; statuses unique, witnesses literal and operation/status-scoped, every annotation has a matching operative nonpersistent_failures row; no executable rule or guarantee is inferred from this extension",
}
_SHAPE_GUIDANCE = {
    "call_variables": "an optional array of unique ordinary initial call-input variable names, or a nonempty mapping from those names to nonempty text descriptions; each name occurs literally in its source quotation; no target values or defaults; read-only; controller/credential namespaces forbidden; an empty array is allowed. Every declared name is a required initial input: when one is absent, null or exactly empty text, the sealed init ends the call silently in quarantine before any dialogue, bank request or close. Whitespace-only text and other present values such as 0 or false pass this presence check; call_input_formats and a resolve_all_products selector add their own value checks. The init ends the call the same way when the engine customer_id is not nonempty text, or when now does not start with a valid YYYY-MM-DD date (a longer now must parse as an ISO date-time). Every later guarded action step re-checks the sealed snapshot of declared inputs and quarantines if it is malformed. A source requirement limited to this presence check is enforced and needs no declaration",
    "call_input_formats": "optional nonempty object mapping source-declared read-only initial call-variable names to offset_datetime only. Each name occurs literally in this rule's quotation and is also declared in call_variables. Use only when the source expressly requires a date+time with explicit UTC offset, and then always, including for the engine clock now; never infer formats from API schemas or add defaults. offset_datetime validates a full YYYY-MM-DD local calendar date, HH:MM with optional seconds/fraction, and explicit Z or +/-HH:MM offset on the raw sealed initial string before any bank request. Values/date/offset are retained without conversion; malformed/date-only/offset-less values quarantine and cannot be repaired through ordinary dialogue variables. Absence preserves legacy behavior; this grants no action or permission",
    "policy_applicability": 'exactly {products: "single"} or {products: "multiple"}; scope must be supplied by the policy source. A sourced single-product scope may omit product_order and resolve_all_products; do not invent either rule to satisfy multiple-product dependencies. Independently supplied rules remain operative',
    "product_count_bounds": "a nonempty object containing only optional min and max, each a nonnegative integer (not a boolean); min <= max when both are supplied. Encode explicit source-backed account product-count bounds here, not only in descriptive extensions. No count default; min applies after account loading. This does not supply ordering or replace policy_applicability",
    "identity_fields": "a nonempty array of unique nonempty strings in factor order",
    "identity_wrong_limit": "a positive integer (booleans are not integers), or exactly {effective_date: {before_rule: primitive same-kind rule ID, on_or_after: {date: source ISO YYYY-MM-DD, rule: primitive same-kind rule ID}}}. Use it when a source makes a value effective from a date. The wrapper explicitly overrides both distinct referenced rules, which keep their own sourced values; no assumed clock or unconditional after-date replacement. The contract retains both values and the sealed runtime applies the on_or_after rule when the supplied call date is on or after date, otherwise before_rule",
    "holidays": "an array of ISO YYYY-MM-DD strings; an empty array is allowed",
    "business_weekdays": "a nonempty array of integers from 0 through 6",
    "band_ranges": "a nonempty array of objects with nonempty name and integer min/max; min <= max, names unique, intervals nonoverlapping. There is no interval without an integer max (no open-ended band), and a promise for a product whose days_late is in no band is refused",
    "deadline_days": "a nonempty object mapping band names to nonnegative integers, or exactly {effective_date: {before_rule: primitive same-kind rule ID, on_or_after: {date: source ISO YYYY-MM-DD, rule: primitive same-kind rule ID}}}. Preserve both dated source values and their provenance, not just the current value: the wrapper explicitly overrides both distinct referenced rules, each covering exactly the declared bands. The contract retains both values and the sealed runtime applies the on_or_after rule when the supplied call date is on or after date, otherwise before_rule",
    "floor_fields": "an object mapping nonempty product-type names to nonempty field names",
    "callback_window": "an object with start/end local HH:MM strings, start <= end; optional allow_today is boolean and next_business_days is a nonnegative integer",
    "note_reasons": "a nonempty array of unique nonempty strings",
    "close_reasons": "a nonempty object mapping exact API close outcome codes (wire_codes.outcomes values) to arrays of exact API reason codes; arrays may be empty. A declared wire_codes semantic key may stand for its code. An API close code that the source gives no supported meaning stays out of wire_codes (unbound); this is accepted only in goals whose execution_actions are limited to verify_identity, get_account and close_call (never with schedule_callback or a promise, note or transfer role), and an unbound code is never itself a builder meaning name",
    "transfer_reasons": "an object mapping nonempty queue names to nonempty reason strings",
    "require_confirmation": "a boolean, not a string or integer",
    "promise_failure_limit": "a positive integer; booleans are not integers",
    "tool_aliases": "an object mapping nonempty semantic operation names to nonempty API operation names",
    "persuasion_limits": "a nonempty object mapping band names to nonnegative integers",
    "identity_hint_exemptions": "an array of unique nonempty strings; an empty array is allowed",
    "write_retryable_errors": "an array of unique nonempty strings; an empty array is allowed",
    "confirmation_scope": 'the string "each" or "batch"',
    "confirmation_question": "optional nonempty literal text containing the exact question prescribed by the source, present verbatim in its cited source quotation; preserve spelling/language/punctuation; no variable templates, control characters or private engine/credential prefixes (the compiler prepends its protected readback). Requires source-backed confirmation_question_actions to state exactly where the wording applies, never infer a global scope. Omit both when no wording is prescribed. This changes text only, not confirmation requirements or guarded consent semantics; different questions for different actions are not representable by this scalar interface",
    "confirmation_question_actions": "paired with confirmation_question: a nonempty unique array containing only commit_promises, commit_transfer, schedule_callback, with literal source provenance establishing the question's action scope. No default/inferred scope. A common source question can explicitly include multiple roles. This grants neither actions nor confirmation requirements; other confirmation nodes retain their generic fallback",
    "confirmation_readback": "optional exactly {actions: nonempty unique array of commit_promises/commit_transfer/schedule_callback, clauses: nonempty unique array of facts the source requires the confirmation read-back to state}. Each clause is copied verbatim, as whole words, from this rule's own source quotation and contains no digits or other numerals, braces, control or format characters or private engine/credential prefixes. The compiler checks only these literal properties, not meaning: a clause states a fixed fact, never a date, time, amount or other value (the protected terms carry those) and never words the source forbids saying. Requests keep only their sealed fields. Clauses are spoken verbatim after the protected terms. A selected named action must require confirmation. Use confirmation_question for a prescribed literal question; omit this rule when the source prescribes no read-back fact",
    "note_failure_limit": "a positive integer; booleans are not integers",
    "callback_failure_limit": "a positive integer; booleans are not integers",
    "transfer_failure_limit": "a positive integer; booleans are not integers",
    "action_fields": "an object mapping API operation roles to objects that map the role's semantic request keys to actual request field names; a key left out means the request field has exactly that name. Roles and semantic keys: verify_identity: customer_id, field, answer; get_account: customer_id; create_promise: customer_id, product_id, amount, date; add_note: customer_id, product_id, reason; schedule_callback: customer_id, date, time; transfer_to_agent: customer_id, queue; close_call: customer_id, outcome, reason (reason only when the request has it). Other roles and keys are not read",
    "result_fields": "an object mapping API operation roles to objects that map the role's semantic result keys to actual response field names; a key left out means the response field has exactly that name. Roles and semantic keys: verify_identity: ok, verified, wrong_answers_left, hint, error; get_account: ok, products, error; create_promise: ok, promise_id, error; add_note: ok, error; schedule_callback: ok, error; transfer_to_agent: ok, error; close_call: ok, error. ok, verified, wrong_answers_left, products and promise_id are required; hint and error are optional. Other roles and keys are not read",
    "product_fields": "an object mapping the semantic product keys product_id, type, days_late and past_due to the actual field names of each get_account product; a key left out means the product field has exactly that name, and a get_account product schema lacking any of the four is refused. Other keys are not read",
    "wire_codes": "an object mapping category names to nonempty string-to-string maps; wire values within each category must be unique. In outcomes/reasons/notes/queues the keys are builder meanings only, each mapped to its exact API code: outcomes promise/paid/transferred/no_promise/not_verified/not_reached; reasons support/human/restructuring/refused/hardship/dispute/service_error/failed/declined/callback/no_callback/wrong_number/deceased/unclear; notes refused/hardship/dispute/paid/service_error; queues support/general/restructuring. Bind only meanings the source supplies; never rebind a code to a collections meaning it does not have. Other categories, such as product_types or a declared selector, keep their source names",
    "nonpersistent_failures": "an object mapping actual operation names to arrays of unique objects containing http_status (integer 100..599), optional error (nonempty string), and optional api_source containing exactly pointer and quote (nonempty strings); controller execution requires a literal OpenAPI witness scoped to that operation's exact response status",
    "confirmation_actions": 'a unique string array containing only "commit_promises", "commit_transfer", "schedule_callback"; an empty array is allowed',
    "attempt_scopes": 'an optional nonempty object mapping sealed write-action roles commit_promises/finalize_notes/schedule_callback/commit_transfer/close_call to call/product/exact_request; product is allowed only for commit_promises/finalize_notes. Selected scoped actions require their explicitly supplied positive promise_failure_limit/note_failure_limit/callback_failure_limit/transfer_failure_limit; close_call is always at most once. The limit counts actual dispatches, not proposals, and no scope or limit is inferred when absent. A per-product attempt rule scopes only commit_promises/finalize_notes, whose requests name a product; it gives schedule_callback/commit_transfer no scope, so they are never replayed unless a source rule scopes them by call or exact_request with their own limit and definite_failure_resume. No retry permission authorizes a second close_call: a negative or uncertain close stays terminal and is never reported as recorded',
    "definite_failure_resume": 'an optional unique string array containing only finalize_notes/schedule_callback/commit_transfer; an empty array is allowed. For selected roles an explicit attempt_scopes entry and matching positive limit are required. Only source-authorized continuation after an exact API-qualified nonpersistent negative receipt is enabled: retry the same frozen request below its cap, or resume without claiming success at the cap. This supplies no bank disposition, never resumes uncertainty, and never replays close_call or changes promise exhaustion handling',
    "product_order": 'exactly {mode: "account_order"}, or exactly {field: nonempty string, direction: "ascending"/"descending", tie_breaker: "account_order"}',
    "close_precedence": "a nonempty array of unique protected execution outcome meanings (transferred, promise, paid, no_promise) or their explicitly bound wire values, ordered only as supplied by policy. Identity closing outcomes belong in identity_close/identity_close_choices, not this array; preserve their sourced outcomes and reasons there. Do not invent a priority, outcome or account prerequisite for a permitted transfer. Rank only protected facts that can coexist. Recorded promises and notes are never removed, and the controller refuses a later promise or local disposition for a product that already has a recorded promise or note, so a later claim about that same product adds no competing closing fact and needs no declaration. Facts from different products, or a recorded transfer after a recorded promise, can coexist and need a sourced ranking; without one the controller refuses the ambiguous close",
    "identity_close": "an optional object containing exactly failed, incomplete, unreached; each scope is an object containing outcome (nonempty string) and optional reason (nonempty string). Include only source-supplied dispositions; do not invent identity outcomes/reasons from permitted API vocabulary. Absence authorizes no identity closing choice: the protected handler may close on an eligible recorded account/transfer fact, otherwise it safely refuses without a bank close. This safety fallback does not satisfy an explicit policy requirement to record a close",
    "identity_close_choices": 'a nonempty object with only unreached/incomplete scopes; each scope is a nonempty array of objects containing outcome (nonempty string), optional reason (nonempty string), and requires ("none"/"recorded_callback"/"no_recorded_callback"/"uncertain_verification"/"definite_verification_failure"). A choice whose outcome differs from identity_close[scope].outcome, or whose reason is outside that outcome\'s sourced close_reasons vocabulary, is refused. uncertain_verification and definite_verification_failure rows stand for protected verify_identity evidence, at most one of each across scopes: once the controller records that evidence (see identity_counter_movement and identity_failure_classes) the call closes only through that row, whatever is proposed, and the row is never eligible otherwise; without the row that evidence quarantines without a close. Exhausted identity keeps identity_close.failed. A condition resting only on what is said in the conversation (contact, wrong party, refusal, clarification, silence or reply limits) has no protected evidence: list it as a requires "none" row, have the workflow establish it and propose the matching pair; protected identity evidence takes precedence over such rows, and this is not an unsupported constraint. When several such conditions hold at once, the workflow proposes one eligible requires "none" row, following the source\'s order when it gives one; the controller accepts it, and the overlap needs no declaration. Successful protected transfer evidence is handled separately; do not invent an identity choice or outcome to stand in for a transfer',
    "identity_factor_state": 'optional, exactly "latest_check" when the source makes the latest completed check of each identity factor control its passed flag. While identity is incomplete a correction of an already passed factor may be checked again: the controller withdraws that pass before sending it, only verified=true restores it, and the next factor is the first in identity_fields order without a pass. Wrong-answer counting is unchanged and no answer is replayed automatically. Absence keeps passes permanent and checks only the next factor',
    "identity_counter_movement": 'optional, exactly "exact" when the source requires the returned wrong_answers_left to start at the API\'s declared maximum (otherwise identity_wrong_limit), never increase, fall by exactly one on a processed mismatch and stay unchanged on a match. Any other movement, including a match reaching zero, is uncertain verification: it is checked before exhaustion and success, no further check is sent, and the call closes only through the identity_close_choices row requiring uncertain_verification. Absence keeps the cumulative counter update',
    "identity_failure_classes": 'optional nonempty object with only repeat_once, fault and/or sealed keys, each a nonempty array of unique {http_status: integer 100..599, error?: nonempty string} verify_identity matchers. Each matcher appears in one class only and equals the http_status/error of an operative verify_identity nonpersistent_failures entry with its api_source (a definite no-processing receipt). repeat_once: the controller sends that exact request once more, immediately and without a new answer; a further definite failure of that repeat is a definite verification failure. fault: a definite verification failure. Either ends verification, which then closes only through the identity_close_choices row requiring definite_verification_failure. sealed: the bank session is already closed; the controller makes no further bank call, including close_call, and records no disposition. A witnessed matcher without a class leaves identity unchanged for a new complete answer. With this rule any other verify_identity failure is uncertain verification, and nothing is retried after uncertain processing',
    "goal_close": 'an optional object with only verified, account_loaded and/or account_failed keys, for goals that select no promise, note or transfer role. If other goals of the source select such roles, add selector (a source-declared read-only call input named in this rule\'s quotation) and goals (its actual supplied codes, or semantic keys of a wire_codes category named exactly selector): rows then apply only to calls whose sealed initial value is listed, those calls cannot use promise, note or transfer roles, and other calls keep evidence-only closes. A listed goal must have product resolution false. verified applies once identity is verified without account evidence; account_loaded after positive account evidence; account_failed after a definite negative account receipt (declared non-2xx get_account status, success flag false, mapped fields valid). With account_failed declared, other negative account receipts quarantine without a close; a recorded one is final: only account_failed rows apply, so omit there any pair the source says cannot override the receipt. Each is a nonempty array of objects containing outcome, optional reason and requires ("none"/"recorded_callback"/"no_recorded_callback"), using sourced close codes from close_reasons, each outcome/reason pair once per phase; collections meanings (promise, paid, transferred, no_promise) are never permitted. With schedule_callback every close code must be a bound wire_codes meaning, and after a recorded callback only recorded_callback rows are eligible. One eligible row is fixed; several rows require a matching closing proposal. A condition resting only on what is said in the conversation has no protected evidence: list its pair in each phase where it can apply, for the workflow to establish and propose; this is not an unsupported constraint. When several such conditions hold at once, the workflow proposes one eligible requires "none" row, following the source\'s order when it gives one; the controller accepts it, and the overlap needs no declaration. Other evidence conditions are unsupported constraints',
    "promise_failure_disposition": "an object containing exactly reason (nonempty string) and clear_pending (boolean). For selected commit_promises, encode any supplied supported exhaustion consequence here with its sourced note_reasons vocabulary; descriptive extensions alone are not executable. If no consequence is supplied, omit this optional rule rather than invent a disposition; absence remains conservative",
    "record_notes": "a boolean, not a string or integer",
    "resolve_all_products": 'a boolean, or an object with {selector: initial call-input name, by_value: {supplied string code: boolean}} when the source explicitly gives goal-dependent product resolution, and/or products: "overdue" when the source scopes resolution to overdue products (protected integer days_late >= 1) instead of every returned product; {products: "overdue"} alone applies to every call. selector occurs literally in this rule\'s quotation and is declared by the source-backed read-only call_variables rule. Use actual supplied selector codes, never invented goal labels, defaults, or an inferred wire category. An explicitly supplied wire_codes category named exactly selector may bind semantic keys to actual codes; other categories are unrelated. Missing, non-string or unknown initial selector values quarantine; later dialogue variables cannot relax the sealed initial scope. This changes only which products must be resolved before the final close and, for the object form, before note finalization (a selected false requires none); never allowed outcomes or actions',
    "transfer_prerequisites": 'a nonempty object mapping permitted transfer queue names to "none", "identity", "account" or "unstated". Use "unstated" when the source authorizes dispatching that queue but states no identity/account prerequisite: no evidence gate is added and nothing else is granted; never add identity or account from omission. Omit a queue the source names only in closing vocabulary without authorizing its dispatch; the controller refuses staging an omitted queue',
    "promise_readback": 'an object with a nonempty unique product_fields string array excluding customer_id/product_id, and optional value_labels mapping selected fields to string-to-string label maps; an absent or empty selected field is left out of that product\'s label. Use exactly "product_type" when the source requires confirming the product but prescribes no product fields: each product is named only by its protected product type, the type already used for floors; labels shared by two loaded products block the confirmation and no other field is read back. Use exactly "none" only when the source requires no confirmation for commit_promises (require_confirmation false and commit_promises not in confirmation_actions)',
    "account_disclosure_fields": "an array of unique nonempty actual API scalar field names required on every product (empty allowed), or exactly {common: such a field array, by_product_type: a nonempty object mapping supplied product-type codes to such arrays}. Common fields are required on every product; conditional fields only on their selected supplied type. Exclude customer_id/product_id and their aliases. Preserve source conditions instead of requiring type-specific fields globally; no invented defaults. Keys use exact API type codes or source semantic names explicitly mapped by frozen wire_codes.product_types",
    "uncertain_stop_explanation": "optional: what the source requires the caller to be told when the call stops without a close because a write's persistence is unknown or its evidence is missing, malformed or contradictory. Either the exact words to say to the caller, copied verbatim as whole words from this rule's own source quotation (never words the source forbids saying; no digits, braces, control characters or private engine/credential prefixes), or true when the source requires an explanation there but prescribes no words (a fixed neutral line is then used). The controller's single sealed quarantine end speaks exactly these words, verbatim and once, at every quarantine stop (unknown persistence, input failure, controller fault) and adds no write, close, retry or other words; turn-budget and entry-budget stops stay silent. Omit when the source requires no speech at that stop; different words for different stops are not representable",
}


def execution_rule_guidance() -> dict[str, str]:
    """Describe typed interfaces and identified descriptive formats, not values."""
    guidance = {**_SHAPE_GUIDANCE, **_DESCRIPTIVE_EXTENSION_GUIDANCE}
    return {kind: guidance[kind] for kind in sorted(guidance)}


_OFFSET_WORDING = re.compile(r"\b(?:utc|gmt|time[- ]?zone)\s+offsets?\b"
                             r"|\boffsets?\s+(?:from|to)\s+(?:utc|gmt)\b|\bexplicit\s+time[- ]?zones?\b", re.I)
_NOT_REQUIRED = re.compile(r"\b(?:no|not|never|without|optional|optionally|may|if|unless|ignore[sd]?|omit(?:s|ted)?)\b|n['’]t\b", re.I)
_STATED_OFFSET = re.compile(r"(?<![\d:])(?<!\d\s)[+-]\d{2}:\d{2}(?!\d)"
                            r"|\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?![\d:]|[.,]\d|\s*(?:[Zz]\b|[+-]\d))")
_DATE_TIME = re.compile(r"\bdate\b.*\btime\b|\bdatetime\b|\btimestamp\b", re.I | re.S)


def offset_datetime_inputs(texts: Any, names: Any) -> set[str]:
    """Input names a source statement requires to carry an explicit UTC offset.

    Literal wording only: a statement (sentence, list item or table row) that
    names the input beside UTC/time-zone offset wording, with no negation or
    optionality cue, no stated offset value and no offset-less example
    date-time; a name other than the engine clock now also needs date and time
    wording. Unclear wording is left to extraction review.
    """
    found = set()
    for text in texts:
        for statement in re.split(r"\n\s*\n|\n(?=\s*(?:[|#*-]|\d+[.)]\s))|(?<=[.!?])\s+", text):
            if _OFFSET_WORDING.search(statement) and not (_NOT_REQUIRED.search(statement) or _STATED_OFFSET.search(statement)):
                found.update(name for name in names if (name == "now" or _DATE_TIME.search(statement))
                             and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", statement))
    return found


def _json_type(value: Any) -> str:
    """A fixed type label, never a supplied value or native class name."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "unsupported native value"


def _structured_error(kind: str, value: Any, domain: str) -> str:
    message = (f"{domain} rule {kind} has an invalid structured value: "
               f"{kind}.value expected {_SHAPE_GUIDANCE[kind]}; received {_json_type(value)}")
    if kind == "identity_close" and isinstance(value, dict):
        scopes = {"failed", "incomplete", "unreached"}
        missing = sorted(scopes - set(value))
        if missing:
            message += "; missing required scopes: " + ", ".join(missing)
        if set(value) - scopes:
            message += "; unsupported scope fields are present"
        for scope in ("failed", "incomplete", "unreached"):
            if scope not in value:
                continue
            proposal = value[scope]
            location = "identity_close.value." + scope
            if not isinstance(proposal, dict):
                message += f"; {location} must be an object, received {_json_type(proposal)}"
                continue
            if not isinstance(proposal.get("outcome"), str) or not proposal["outcome"]:
                message += f"; {location}.outcome is required and must be a nonempty string"
            if "reason" in proposal and (not isinstance(proposal["reason"], str) or not proposal["reason"]):
                message += f"; {location}.reason must be a nonempty string when supplied"
            if set(proposal) - {"outcome", "reason"}:
                message += f"; {location} permits only outcome and optional reason"
        message += "; identity_close_choices does not replace the required fixed scope entries"
    # All fragments above are fixed interface labels; supplied keys and values
    # are omitted, keeping repairs bounded and free of fixture/secret literals.
    return message


def _scoped_value_errors(kind: str, value: Any) -> list[str] | None:
    """Inspect closed selector syntax; never resolve references or a clock.

    None identifies the unchanged primitive form. Valid selector syntax grants
    no execution support by itself: the contract compiler validates and retains
    the referenced values, and the guarded runtime selects by the call date.
    """
    def text(item: Any) -> bool:
        return isinstance(item, str) and bool(item.strip())

    if (kind in {"identity_wrong_limit", "deadline_days"} and isinstance(value, dict)
            and "effective_date" in value
            and (kind == "identity_wrong_limit" or isinstance(value["effective_date"], dict))):
        selection = value["effective_date"]
        valid = (set(value) == {"effective_date"} and isinstance(selection, dict)
                 and set(selection) == {"before_rule", "on_or_after"}
                 and text(selection["before_rule"]) and isinstance(selection["on_or_after"], dict))
        if valid:
            branch = selection["on_or_after"]
            valid = set(branch) == {"date", "rule"} and text(branch["rule"]) and text(branch["date"])
            if valid:
                try:
                    valid = date.fromisoformat(branch["date"]).isoformat() == branch["date"]
                except ValueError:
                    valid = False
    else:
        return None
    return [] if valid else [_structured_error(kind, value, "scoped execution")]


def _profile_errors(kind: str, value: Any) -> list[str]:
    scoped = _scoped_value_errors(kind, value)
    if scoped is not None:
        return scoped
    def strings(item: Any, *, empty: bool = False) -> bool:
        return isinstance(item, list) and (empty or bool(item)) and all(isinstance(x, str) and x for x in item)
    valid = True
    if kind == "policy_applicability":
        valid = (isinstance(value, dict) and set(value) == {"products"}
                 and isinstance(value["products"], str) and value["products"] in {"single", "multiple"})
    elif kind == "product_count_bounds":
        valid = (isinstance(value, dict) and bool(value) and not set(value) - {"min", "max"}
                 and all(type(bound) is int and bound >= 0 for bound in value.values())
                 and (not {"min", "max"} <= set(value) or value["min"] <= value["max"]))
    elif kind in {"account_disclosure_fields", "confirmation_question", "confirmation_question_actions", "resolve_all_products", "call_input_formats",
                  "identity_factor_state", "identity_counter_movement", "identity_failure_classes", "uncertain_stop_explanation"}:
        return _execution_errors(kind, value)
    elif kind in {"identity_wrong_limit", "promise_failure_limit", "note_failure_limit", "callback_failure_limit", "transfer_failure_limit"}:
        valid = type(value) is int and value > 0
    elif kind in {"identity_fields", "note_reasons"}:
        valid = strings(value) and len(set(value)) == len(value)
    elif kind == "holidays":
        valid = strings(value, empty=True)
        if valid:
            try:
                valid = all(date.fromisoformat(x).isoformat() == x for x in value)
            except ValueError:
                valid = False
    elif kind == "business_weekdays":
        valid = isinstance(value, list) and bool(value) and all(type(x) is int and 0 <= x <= 6 for x in value)
    elif kind == "band_ranges":
        valid = isinstance(value, list) and bool(value)
        if valid:
            valid = all(isinstance(b, dict) and isinstance(b.get("name"), str) and b["name"]
                        and type(b.get("min")) is int and type(b.get("max")) is int and b["min"] <= b["max"] for b in value)
        if valid:
            valid = len({b["name"] for b in value}) == len(value) and all(
                a["max"] < b["min"] or b["max"] < a["min"]
                for i, a in enumerate(value) for b in value[i+1:])
    elif kind == "deadline_days":
        valid = isinstance(value, dict) and bool(value) and all(isinstance(k, str) and k and type(v) is int and v >= 0 for k, v in value.items())
    elif kind in {"floor_fields", "transfer_reasons", "tool_aliases"}:
        valid = isinstance(value, dict) and all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items())
    elif kind == "close_reasons":
        valid = isinstance(value, dict) and bool(value) and all(isinstance(k, str) and k and strings(v, empty=True) for k, v in value.items())
    elif kind == "require_confirmation":
        valid = type(value) is bool
    elif kind == "persuasion_limits":
        valid = isinstance(value, dict) and bool(value) and all(isinstance(k, str) and k and type(v) is int and v >= 0 for k, v in value.items())
    elif kind in {"identity_hint_exemptions", "write_retryable_errors"}:
        valid = strings(value, empty=True) and len(set(value)) == len(value)
    elif kind == "confirmation_scope":
        valid = isinstance(value, str) and value in {"each", "batch"}
    elif kind == "confirmation_readback":
        return _execution_errors(kind, value)
    elif kind in {"action_fields", "result_fields", "product_fields"}:
        valid = isinstance(value, dict) and all(isinstance(k, str) and k and
                    ((isinstance(v, str) and bool(v)) or (isinstance(v, dict) and bool(v) and
                     all(isinstance(x, str) and x and isinstance(y, str) and y for x, y in v.items()))) for k, v in value.items())
    elif kind == "wire_codes":
        # Other bank goals may add typed code categories. Preserve them as data;
        # the action-binding compiler interprets only its four known categories.
        valid = isinstance(value, dict) and all(isinstance(k, str) and k
                    and isinstance(v, dict) and bool(v) and all(isinstance(x, str) and x and
                    isinstance(y, str) and y for x, y in v.items()) and len(set(v.values())) == len(v)
                    for k, v in value.items())
    elif kind == "callback_window":
        valid = isinstance(value, dict) and isinstance(value.get("start"), str) and isinstance(value.get("end"), str)
        if valid:
            try:
                first, last = time.fromisoformat(value["start"]), time.fromisoformat(value["end"])
                valid = first <= last and first.strftime("%H:%M") == value["start"] and last.strftime("%H:%M") == value["end"]
            except ValueError:
                valid = False
        if valid:
            valid = type(value.get("allow_today", False)) is bool and type(value.get("next_business_days", 0)) is int and value.get("next_business_days", 0) >= 0
    return [] if valid else [_structured_error(kind, value, "policy")]


def _execution_errors(kind: str, value: Any) -> list[str]:
    """Additional guard inputs are typed here; legacy extension IR remains data."""
    scoped = _scoped_value_errors(kind, value)
    if scoped is not None:
        return scoped
    if kind not in _EXECUTION_ONLY_KINDS:
        return _profile_errors(kind, value)
    def text(item: Any) -> bool:
        return isinstance(item, str) and bool(item)
    if kind == "call_variables":
        try:
            check_call_variables(value)
            valid = True
        except CallVariablesError:
            valid = False
    elif kind == "call_input_formats":
        valid = isinstance(value, dict) and bool(value)
        if valid:
            try:
                check_call_variables(list(value))
                valid = all(format_name == "offset_datetime" for format_name in value.values())
            except CallVariablesError:
                valid = False
    elif kind == "confirmation_question":
        valid = (text(value) and bool(value.strip()) and "{{" not in value and "}}" not in value
                 and not any(ord(char) < 32 or ord(char) == 127 for char in value)
                 and not any(prefix in value.casefold() for prefix in ("bg_", "auth_", "bank_token")))
    elif kind == "confirmation_question_actions":
        valid = (isinstance(value, list) and bool(value) and all(text(item) for item in value)
                 and len(set(value)) == len(value)
                 and not set(value) - {"commit_promises", "commit_transfer", "schedule_callback"})
    elif kind == "confirmation_readback":
        # Literal sourced words only: no numerals, templates, hidden characters
        # or engine names. Meaning is not checked; requests stay sealed.
        clauses = value.get("clauses") if isinstance(value, dict) else None
        valid = (isinstance(value, dict) and set(value) == {"actions", "clauses"}
                 and not _execution_errors("confirmation_question_actions", value["actions"])
                 and isinstance(clauses, list) and bool(clauses)
                 and all(text(item) and bool(item.strip())
                         and all(char.isprintable() and char not in "{}" and not char.isnumeric() for char in item)
                         and not any(prefix in item.casefold() for prefix in ("bg_", "auth_", "bank_token")) for item in clauses)
                 and len(set(clauses)) == len(clauses))
    elif kind == "account_disclosure_fields":
        def fields(items):
            return (isinstance(items, list) and all(text(item) for item in items)
                    and len(set(items)) == len(items) and not set(items) & {"customer_id", "product_id"})
        valid = fields(value) or (isinstance(value, dict) and set(value) == {"common", "by_product_type"}
                 and fields(value["common"]) and isinstance(value["by_product_type"], dict)
                 and bool(value["by_product_type"])
                 and all(text(kind) and fields(items) for kind, items in value["by_product_type"].items()))
    elif kind == "record_notes":
        valid = type(value) is bool
    elif kind == "attempt_scopes":
        valid = (isinstance(value, dict) and bool(value)
                 and set(value) <= {"commit_promises", "finalize_notes", "schedule_callback", "commit_transfer", "close_call"}
                 and all(scope in ("call", "product", "exact_request") for scope in value.values())
                 and all(scope != "product" or action in {"commit_promises", "finalize_notes"}
                         for action, scope in value.items()))
    elif kind == "definite_failure_resume":
        valid = (isinstance(value, list) and all(text(action) for action in value)
                 and len(set(value)) == len(value)
                 and set(value) <= {"finalize_notes", "schedule_callback", "commit_transfer"})
    elif kind == "resolve_all_products":
        valid = type(value) is bool
        if (isinstance(value, dict) and value and set(value) <= {"selector", "by_value", "products"}
                and ("selector" in value) == ("by_value" in value) and value.get("products", "overdue") == "overdue"):
            valid = True
            if "selector" in value:
                try:
                    check_call_variables([value["selector"]])
                    valid = (isinstance(value["by_value"], dict) and bool(value["by_value"])
                             and all(text(code) and type(required) is bool for code, required in value["by_value"].items()))
                except CallVariablesError:
                    valid = False
    elif kind in {"confirmation_actions", "close_precedence"}:
        valid = (isinstance(value, list) and (kind == "confirmation_actions" or bool(value))
                 and all(text(item) for item in value) and len(set(value)) == len(value))
        if valid and kind == "confirmation_actions":
            valid = set(value) <= {"commit_promises", "commit_transfer", "schedule_callback"}
    elif kind == "product_order":
        valid = (isinstance(value, dict) and (value == {"mode": "account_order"}
                 or (set(value) == {"field", "direction", "tie_breaker"}
                     and text(value["field"]) and value["direction"] in ("ascending", "descending")
                     and value["tie_breaker"] == "account_order")))
    elif kind == "identity_close_choices":
        valid = (isinstance(value, dict) and bool(value) and set(value) <= {"unreached", "incomplete"}
                 and all(isinstance(choices, list) and bool(choices) for choices in value.values()))
        if valid:
            valid = all(isinstance(choice, dict) and set(choice) <= {"outcome", "reason", "requires"}
                        and text(choice.get("outcome"))
                        and choice.get("requires") in ("none", "recorded_callback", "no_recorded_callback",
                                                       "uncertain_verification", "definite_verification_failure")
                        and ("reason" not in choice or text(choice["reason"]))
                        for choices in value.values() for choice in choices)
    elif kind == "identity_factor_state":
        valid = value == "latest_check"
    elif kind == "identity_counter_movement":
        valid = value == "exact"
    elif kind == "uncertain_stop_explanation":
        # Sourced words only: no digits, templates or engine names can add a
        # value, binding or field to the stop line.
        valid = value is True or (text(value) and bool(value.strip())
                 and not any(char in "{}" or char.isdigit() or ord(char) < 32 or ord(char) == 127 for char in value)
                 and not any(prefix in value.casefold() for prefix in ("bg_", "auth_", "bank_token")))
    elif kind == "identity_failure_classes":
        matchers = [canonical_json(case) for cases in value.values() for case in cases] if (
            isinstance(value, dict) and value and set(value) <= {"repeat_once", "fault", "sealed"}
            and all(isinstance(cases, list) and cases for cases in value.values())) else []
        valid = bool(matchers) and len(set(matchers)) == len(matchers) and all(
            isinstance(case, dict) and set(case) - {"error"} == {"http_status"}
            and type(case["http_status"]) is int and 100 <= case["http_status"] <= 599
            and ("error" not in case or text(case["error"])) for cases in value.values() for case in cases)
    elif kind == "goal_close":
        phases = {key: rows for key, rows in value.items() if key not in ("selector", "goals")} if isinstance(value, dict) else {}
        valid = (bool(phases) and set(phases) <= {"verified", "account_loaded", "account_failed"}
                 and ("selector" in value) == ("goals" in value)
                 and all(isinstance(rows, list) and bool(rows) and all(
                     isinstance(row, dict) and set(row) <= {"outcome", "reason", "requires"} and text(row.get("outcome"))
                     and row.get("requires") in ("none", "recorded_callback", "no_recorded_callback")
                     and ("reason" not in row or text(row["reason"])) for row in rows) for rows in phases.values()))
        if valid and "selector" in value:
            try:
                check_call_variables([value["selector"]])
                valid = (isinstance(value["goals"], list) and bool(value["goals"])
                         and all(text(code) for code in value["goals"]) and len(set(value["goals"])) == len(value["goals"]))
            except CallVariablesError:
                valid = False
    elif kind == "identity_close":
        valid = (isinstance(value, dict) and set(value) == {"failed", "incomplete", "unreached"}
                 and all(isinstance(proposal, dict) and set(proposal) <= {"outcome", "reason"}
                         and text(proposal.get("outcome")) and ("reason" not in proposal or text(proposal["reason"]))
                         for proposal in value.values()))
    elif kind == "promise_failure_disposition":
        valid = (isinstance(value, dict) and set(value) == {"reason", "clear_pending"}
                 and text(value["reason"]) and type(value["clear_pending"]) is bool)
    elif kind == "transfer_prerequisites":
        valid = (isinstance(value, dict) and bool(value)
                 and all(text(queue) and prerequisite in ("none", "identity", "account", "unstated")
                         for queue, prerequisite in value.items()))
    elif kind == "promise_readback":
        valid = value in ("product_type", "none") or (isinstance(value, dict) and set(value) <= {"product_fields", "value_labels"}
                 and isinstance(value.get("product_fields"), list) and bool(value["product_fields"])
                 and all(text(field) and field not in {"customer_id", "product_id"}
                         for field in value["product_fields"])
                 and len(set(value["product_fields"])) == len(value["product_fields"])
                 and isinstance(value.get("value_labels", {}), dict))
        if valid and isinstance(value, dict):
            valid = all(text(field) and field in value["product_fields"] and isinstance(labels, dict)
                        and all(text(wire) and text(label) for wire, label in labels.items())
                        for field, labels in value.get("value_labels", {}).items())
    elif kind == "nonpersistent_failures":  # Exact trusted pairs; no free prose predicates.
        valid = isinstance(value, dict)
        if valid:
            for operation, failures in value.items():
                if (not text(operation) or not isinstance(failures, list)
                        or any(not isinstance(failure, dict) or set(failure) - {"http_status", "error", "api_source"}
                               or type(failure.get("http_status")) is not int or not 100 <= failure["http_status"] <= 599
                               or ("error" in failure and not text(failure["error"]))
                               or ("api_source" in failure and
                                   (not isinstance(failure["api_source"], dict)
                                    or set(failure["api_source"]) != {"pointer", "quote"}
                                    or not text(failure["api_source"]["pointer"])
                                    or not text(failure["api_source"]["quote"]))) for failure in failures)
                        or len({canonical_json(failure) for failure in failures}) != len(failures)):
                    valid = False
                    break
    return [] if valid else [_structured_error(kind, value, "execution")]


# A stop line is sourced only as whole words of its quotation, and not where the
# quotation forbids saying them ("never tell the caller that ...").
_NEGATED_SPEECH = re.compile(r"(?:\bnot|\bnever|\bcannot|n['\u2019]t)\s+(?:say|tell|claim|state|mention|imply|suggest|promise|report|inform|announce|confirm)"
                             r"(?:\s+(?:to\s+)?(?:the\s+)?(?:caller|customer|user|them|him|her))?(?:\s+that)?[\s:\"'\u201c\u2018]*$", re.I)


def _sourced_stop_words(words: str, quote: str) -> bool:
    return bool(words.strip()) and any(not _NEGATED_SPEECH.search(quote, 0, found.start())
        for found in re.finditer(r"(?<!\w)" + re.escape(words.strip()) + r"(?!\w)", quote))


def validate_policy(policy: Any, source_documents: Mapping[str, str] | None = None,
                    *, strict: bool = False) -> list[str]:
    """Check IR/provenance, never semantic correctness of quoted rules.

    ``strict`` rejects legacy/untyped shapes and top-level rule-value bypasses.
    Full controller source inventory and coverage belong to contract compilation.
    Pure helper profiles are intentionally supported by ``rule_value`` separately.
    """
    errors: list[str] = []
    if not isinstance(policy, dict):
        return ["policy must be an object"]
    if type(policy.get("version", 1)) is not int or policy.get("version", 1) != 1 or (strict and "version" not in policy):
        errors.append("policy.version must be 1")
    if strict and set(policy) - {"version", "goal", "allowed_actions", "sources", "rules", "unsupported_constraints"}:
        errors.append("policy contains unsupported top-level fields; rule values belong in rules")
    if not isinstance(policy.get("goal"), str) or not policy["goal"].strip():
        errors.append("policy.goal must be a nonempty string")
    actions = policy.get("allowed_actions")
    if not isinstance(actions, list) or any(not isinstance(a, str) or not a for a in actions):
        errors.append("policy.allowed_actions must be a list of action names")
    elif len(set(actions)) != len(actions):
        errors.append("policy.allowed_actions must be unique")
    sources = policy.get("sources", [])
    source_ids: set[str] = set()
    source_files: set[str] = set()
    if not isinstance(sources, list) or (strict and not sources):
        errors.append("policy.sources must be a list")
        sources = []
    for i, source in enumerate(sources):
        if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not source.get("id"):
            errors.append(f"policy.sources[{i}] requires id")
            continue
        if source["id"] in source_ids:
            errors.append(f"policy.sources[{i}] has duplicate id")
        source_ids.add(source["id"])
        if not isinstance(source.get("file"), str) or not source["file"]:
            errors.append(f"policy.sources[{i}] requires file")
        else:
            source_files.add(source["file"])
        if strict and "sha256" not in source:
            errors.append(f"policy.sources[{i}] requires sha256")
        if "sha256" in source and (not isinstance(source["sha256"], str)
                                    or len(source["sha256"]) != 64
                                    or any(c not in "0123456789abcdef" for c in source["sha256"])):
            errors.append(f"policy.sources[{i}].sha256 must be lowercase SHA-256")
        elif "sha256" in source and source_documents is not None and source["id"] in source_documents:
            # Match the controller's canonical JSON fingerprint convention.
            actual = digest(source_documents[source["id"]])
            if source["sha256"] != actual:
                errors.append(f"policy.sources[{i}].sha256 differs from the supplied source")
    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        return errors + ["policy.rules must be a nonempty list"]
    ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for i, rule in enumerate(rules):
        prefix = f"policy.rules[{i}]"
        if not isinstance(rule, dict):
            errors.append(prefix + " must be an object")
            continue
        ident = rule.get("id")
        if not isinstance(ident, str) or not ident:
            errors.append(prefix + " requires id")
        elif ident in ids:
            errors.append(prefix + " has duplicate id")
        else:
            ids.add(ident)
            by_id[ident] = rule
        if strict and set(rule) - {"id", "kind", "value", "source", "overrides"}:
            errors.append(prefix + " contains unsupported rule fields")
        if "kind" in rule:
            if not isinstance(rule["kind"], str) or not rule["kind"]:
                errors.append(prefix + ".kind must be nonempty")
            if "value" not in rule:
                errors.append(prefix + " requires value")
        elif strict or not isinstance(rule.get("text"), str) or not rule.get("text"):
            errors.append(prefix + " requires kind/value or text")
        if strict and "source" not in rule:
            errors.append(prefix + " requires typed source provenance")
        provenance = [rule["source"]] if "source" in rule else rule.get("sources", [])
        if not isinstance(provenance, list) or not provenance:
            errors.append(prefix + " requires source provenance")
            continue
        for source in provenance:
            if not isinstance(source, dict):
                errors.append(prefix + " has malformed source provenance")
                continue
            sid = source.get("id", source.get("file"))
            if not isinstance(sid, str) or not sid or ("source" in rule and sid not in source_ids) or (source_ids and sid not in source_ids and sid not in source_files):
                errors.append(prefix + " refers to an unknown source")
            quote = source.get("quote")
            if not isinstance(quote, str) or not quote.strip():
                errors.append(prefix + " requires a literal source quote")
            else:
                question = rule.get("value")
                if rule.get("kind") == "confirmation_question" and isinstance(question, str) and question not in quote:
                    errors.append(prefix + " confirmation_question must appear literally in its cited source quote")
                if rule.get("kind") == "confirmation_readback" and isinstance(question, dict) and isinstance(question.get("clauses"), list):
                    if any(isinstance(item, str) and not re.search(r"(?<!\w)" + re.escape(item) + r"(?!\w)", quote) for item in question["clauses"]):
                        errors.append(prefix + " confirmation_readback wording must appear literally as whole words in its cited source quote")
                if rule.get("kind") == "resolve_all_products" and isinstance(question, dict):
                    selector = question.get("selector")
                    if isinstance(selector, str) and not re.search(r"(?<!\w)" + re.escape(selector) + r"(?!\w)", quote):
                        errors.append(prefix + " product-resolution selector must appear literally in its cited source quote")
                if rule.get("kind") == "call_input_formats" and isinstance(question, dict):
                    for name in question:
                        if isinstance(name, str) and not re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", quote):
                            errors.append(prefix + " formatted call-input name must appear literally in its cited source quote")
                if rule.get("kind") == "uncertain_stop_explanation" and isinstance(question, str) and not _sourced_stop_words(question, quote):
                    errors.append(prefix + " uncertain_stop_explanation must appear literally as whole words in its cited source quote, not as words it forbids saying")
                if source_documents is not None:
                    document = source_documents.get(sid) if isinstance(sid, str) else None
                    if document is None or quote not in document:
                        errors.append(prefix + " quote is not present in the supplied source")
    for ident, rule in by_id.items():
        overrides = rule.get("overrides", [])
        if not isinstance(overrides, list) or any(not isinstance(x, str) for x in overrides):
            errors.append(f"rule {ident} overrides must be a list of rule ids")
            continue
        if any(x not in ids or x == ident for x in overrides):
            errors.append(f"rule {ident} has an unknown or self override")
        if len(set(overrides)) != len(overrides):
            errors.append(f"rule {ident} repeats an override")
        if any(x in by_id and by_id[x].get("kind") != rule.get("kind") for x in overrides):
            errors.append(f"rule {ident} has a cross-kind override; semantic replacement is unsupported")
    successors = {ident: [child for child in rule.get("overrides", [])
                          if isinstance(child, str) and child in by_id]
                  if isinstance(rule.get("overrides", []), list) else []
                  for ident, rule in by_id.items()}
    indegree = {ident: 0 for ident in by_id}
    for children in successors.values():
        for child in children:
            indegree[child] += 1
    ready = [ident for ident, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        ident = ready.pop()
        visited += 1
        for child in successors[ident]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(by_id):
        errors.append("policy override graph contains a cycle")
    kinds = {rule["kind"] for rule in by_id.values() if isinstance(rule.get("kind"), str) and rule.get("kind")}
    if kinds & set(policy):
        errors.append("policy top-level values cannot shadow quoted rule kinds")
    # Resolve every extension kind, not only built-in helper concepts. JSON typed
    # equality distinguishes true/1, null/missing, and other Python coercions.
    active_by_kind = _active_rules(policy)
    for kind in sorted(kinds):
        try:
            active = active_by_kind.get(kind, [])
            values = [canonical_json(rule["value"]) for rule in active if "value" in rule]
            if len(set(values)) > 1:
                errors.append(f"policy rules for {kind} have conflicting active values")
            if active and kind in _PROFILE_KINDS:
                value = active[0].get("value")
                errors.extend(_profile_errors(kind, value))
        except (PolicyError, TypeError, ValueError):
            errors.append(f"policy rules for {kind} are conflicting or malformed")
    unsupported = policy.get("unsupported_constraints", [])
    if not isinstance(unsupported, list) or any(not isinstance(x, (str, dict)) for x in unsupported):
        errors.append("policy.unsupported_constraints must be a list of declaration objects")
    try:
        canonical_json(policy)
    except (TypeError, ValueError):
        errors.append("policy must contain finite JSON values")
    return errors


_QUOTE_MARKS = str.maketrans("‘’‚‛“”„‟", "''''\"\"\"\"")
_QUOTE_EDGE = ".,;:!?…\"'‘’‚‛“”„‟"
_READ_RULE_FIELDS = {"id", "kind", "value", "source", "overrides", "text", "sources"}


def _canonical(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Straight quotation marks and one space per whitespace run, with each character's source span."""
    pieces = list(re.finditer(r"\s+|.", text, re.S))
    return ("".join(" " if piece.group().isspace() else piece.group().translate(_QUOTE_MARKS) for piece in pieces),
            [piece.span() for piece in pieces])


def _source_span(quote: str, document: str, canonical: tuple[str, list[tuple[int, int]]]) -> tuple[int, int, str] | None:
    """Return (start, end, exact source text) for the least-trimmed quote equal after canonicalisation."""
    text, spans = canonical
    edge = [character.isspace() or character in _QUOTE_EDGE for character in quote]
    if all(edge):
        return None
    lead, tail = edge.index(False), edge[::-1].index(False)
    starts = {lead} | {i for i in range(min(lead, 8)) if not quote[i].isspace()}
    ends = {len(quote) - tail} | {len(quote) - k for k in range(min(tail, 8)) if not quote[-k - 1].isspace()}
    for start, end in sorted(((s, e) for s in starts for e in ends), key=lambda c: (c[0] + len(quote) - c[1], c[0])):
        needle = _canonical(quote[start:end])[0]
        # A trimmed edge never cuts a source word: "rest." does not match inside "restrictions".
        found = re.search(("(?<!\\w)" if start and re.match(r"\w", needle) else "") + re.escape(needle)
                          + ("(?!\\w)" if end < len(quote) and re.match(r"\w", needle[-1]) else ""), text)
        if found:
            return start, end, document[spans[found.start()][0]:spans[found.start() + len(needle) - 1][1]]
    return None


def _descriptive_annotation(value: Any, rule_ids: set, whole: bool = True) -> bool:
    """Only nonempty text: no flag, number or null (typed or written as text), no single bare word as the
    whole field value (a state such as "withdrawn"), and no ISO date or rule id anywhere in the value."""
    if isinstance(value, str):
        return (bool(value.strip()) and not re.fullmatch(r"(?i)\s*(true|false|null|none|yes|no|on|off|[-+]?\d+(\.\d+)?)\s*", value)
                and not (whole and len(re.findall(r"[^\W_]+", value)) < 2) and not re.search(r"\d{4}-\d{2}-\d{2}", value)
                and not any(re.search(r"(?<!\w)" + re.escape(ident) + r"(?!\w)", value) for ident in rule_ids))
    if isinstance(value, dict):
        return bool(value) and all(_descriptive_annotation(key, rule_ids, False) and _descriptive_annotation(item, rule_ids, False)
                                   for key, item in value.items())
    return isinstance(value, list) and bool(value) and all(_descriptive_annotation(item, rule_ids, False) for item in value)


def normalise_policy(policy: Any, source_documents: Mapping[str, str]) -> tuple[Any, list[dict[str, Any]]]:
    """Apply recorded, deterministic rewrites that spare a repair; validation itself stays strict.

    A rule quote absent from its cited source becomes the exact source text it equals once edge
    punctuation and whitespace are trimmed (least trimming first), curly quotation marks are
    straightened and whitespace runs collapse. Words must still match exactly; nothing fuzzy.
    A rule field that no builder code reads is dropped only when it holds descriptive text alone
    and the rule is on neither side of an override; a flag, number, ISO date or rule id keeps the
    strict rejection. One record per change.
    """
    if not isinstance(policy, dict) or not isinstance(policy.get("rules"), list):
        return policy, []
    policy, records, canonical = copy.deepcopy(policy), [], {}
    rules = [rule for rule in policy["rules"] if isinstance(rule, dict)]
    rule_ids = {rule["id"] for rule in rules if isinstance(rule.get("id"), str)}
    # Either side of an override may carry the qualifier (a date in words, say) deciding when each value applies.
    overridden = {ident for rule in rules if isinstance(rule.get("overrides"), list) for ident in rule["overrides"] if isinstance(ident, str)}
    for index, rule in enumerate(policy["rules"]):
        if not isinstance(rule, dict):
            continue
        related = bool(rule.get("overrides")) or (isinstance(rule.get("id"), str) and rule["id"] in overridden)
        dropped = {field: rule[field] for field in sorted(set(rule) - _READ_RULE_FIELDS)
                   if not related and _descriptive_annotation(rule[field], rule_ids)}
        if dropped:
            for field in dropped:
                del rule[field]
            records.append({"rule": index, "rule_id": rule.get("id"), "change": "dropped_unread_fields", "fields": dropped})
        source = rule.get("source")
        sid = source.get("id") if isinstance(source, dict) else None
        quote = source.get("quote") if isinstance(source, dict) else None
        document = source_documents.get(sid) if isinstance(sid, str) else None
        if not isinstance(quote, str) or not isinstance(document, str) or quote in document:
            continue
        if sid not in canonical:
            canonical[sid] = _canonical(document)
        match = _source_span(quote, document, canonical[sid])
        if match is None:
            continue
        start, end, exact = match
        kept = quote[start:end]
        steps = (["trimmed_edges"] if (start, end) != (0, len(quote)) else []) + \
                (["quotation_marks"] if re.sub(r"\s+", " ", kept) != re.sub(r"\s+", " ", exact) else []) + \
                (["whitespace_runs"] if kept.translate(_QUOTE_MARKS) != exact.translate(_QUOTE_MARKS) else [])
        source["quote"] = exact
        records.append({"rule": index, "rule_id": rule.get("id"), "change": "normalised_quote", "source": sid,
                        "steps": steps, "quote": quote, "exact_source_text": exact})
    return policy, records


def _active_rules(policy: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Select retained active rules; callers validate override graphs first."""
    rules = [rule for rule in policy.get("rules", []) if isinstance(rule, dict)]
    overridden = {ident for rule in rules for ident in rule.get("overrides", [])
                  if isinstance(ident, str)} if all(isinstance(rule.get("overrides", []), list) for rule in rules) else set()
    active: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        if isinstance(rule.get("kind"), str) and isinstance(rule.get("id"), str) and rule["id"] not in overridden:
            active.setdefault(rule["kind"], []).append(rule)
    return active


def rule_value(policy: Mapping[str, Any], kind: str, default: Any = None) -> Any:
    """Resolve structured rules after explicit overrides; profiles can use keys."""
    if kind in policy:
        return policy[kind]
    rules = [r for r in policy.get("rules", []) if isinstance(r, dict)]
    overridden = {ident for r in rules for ident in r.get("overrides", [])
                  if isinstance(ident, str)} if all(isinstance(r.get("overrides", []), list) for r in rules) else set()
    values = [r.get("value") for r in rules if r.get("kind") == kind and r.get("id") not in overridden]
    if not values:
        return default
    if any(canonical_json(value) != canonical_json(values[0]) for value in values[1:]):
        raise PolicyError(f"conflicting active policy rules for {kind}")
    return values[0]


def money_to_cents(value: Any) -> int:
    if isinstance(value, bool):
        raise PolicyError("amount must be a finite decimal number")
    try:
        amount = Decimal(str(value))
        cents = amount * 100
    except (InvalidOperation, ValueError, TypeError):
        raise PolicyError("amount must be a finite decimal number") from None
    if not amount.is_finite() or cents != cents.to_integral_value():
        raise PolicyError("amount must have at most two decimal places")
    return int(cents)


def _date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            raise PolicyError("date/now must be ISO formatted") from None


def is_business_day(day: date | str, holidays: Any = (), weekdays: Any = (0, 1, 2, 3, 4)) -> bool:
    return _date(day).weekday() in weekdays and _date(day) not in {_date(x) for x in holidays}


def business_deadline(now: date | datetime | str, days: int, holidays: Any = (),
                      weekdays: Any = (0, 1, 2, 3, 4)) -> date:
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise PolicyError("business-day count must be a nonnegative integer")
    if not weekdays or any(type(x) is not int or not 0 <= x <= 6 for x in weekdays):
        raise PolicyError("business_weekdays must include at least one valid weekday")
    current = _date(now)
    remaining = days
    holiday_dates = {_date(x) for x in holidays}
    while remaining:
        current += timedelta(days=1)
        if current.weekday() in weekdays and current not in holiday_dates:
            remaining -= 1
    return current


def next_business_day(day: date | str, holidays: Any = (), weekdays: Any = (0, 1, 2, 3, 4)) -> date:
    return business_deadline(day, 1, holidays, weekdays)


def named_weekday(now: date | datetime | str, weekday: int, *, next_week: bool) -> date:
    if type(weekday) is not int or not 0 <= weekday <= 6:
        raise PolicyError("weekday must be 0..6")
    today = _date(now)
    return today - timedelta(days=today.weekday()) + timedelta(days=weekday + (7 if next_week else 0))


def product_by_id(products: list[dict[str, Any]], product_id: str) -> dict[str, Any]:
    matches = [product for product in products if product.get("product_id") == product_id]
    if len(matches) != 1:
        raise PolicyError("product_id must identify exactly one returned product")
    return matches[0]


def product_band(product: Mapping[str, Any], policy: Mapping[str, Any]) -> str:
    days = product.get("days_late")
    if type(days) is not int:
        raise PolicyError("days_late must be an integer")
    bands = rule_value(policy, "band_ranges", [])
    matches = [band["name"] for band in bands if band["min"] <= days <= band["max"]]
    if len(matches) != 1:
        raise PolicyError("product does not match exactly one policy band")
    return matches[0]


def product_floor_cents(product: Mapping[str, Any], policy: Mapping[str, Any]) -> int:
    fields = rule_value(policy, "floor_fields", {})
    field = fields.get(product.get("type"))
    if not field or field not in product:
        raise PolicyError("policy has no floor field for this product")
    floor = money_to_cents(product[field])
    if floor < 0:
        raise PolicyError("product floor must not be negative")
    return floor


def product_deadline(product: Mapping[str, Any], now: Any, policy: Mapping[str, Any]) -> date:
    days = rule_value(policy, "deadline_days", {}).get(product_band(product, policy))
    if days is None:
        raise PolicyError("policy has no deadline for this band")
    return business_deadline(now, days, rule_value(policy, "holidays", []),
                             rule_value(policy, "business_weekdays", (0, 1, 2, 3, 4)))


def validate_commitment(product: Mapping[str, Any], amount: Any, day: Any, now: Any,
                        policy: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        cents = money_to_cents(amount)
        if not product_floor_cents(product, policy) <= cents <= money_to_cents(product["past_due"]):
            errors.append("amount is outside this product's permitted range")
        promise_day = _date(day)
        if promise_day < _date(now):
            errors.append("promise date is before supplied now")
        if not is_business_day(promise_day, rule_value(policy, "holidays", []),
                               rule_value(policy, "business_weekdays", (0, 1, 2, 3, 4))):
            errors.append("promise date is not a business day")
        if promise_day > product_deadline(product, now, policy):
            errors.append("promise date exceeds this product's deadline")
    except (PolicyError, KeyError, TypeError, ValueError):
        errors.append("commitment or policy inputs are invalid/incomplete")
    return errors


def validate_callback(day: Any, at: Any, now: Any, policy: Mapping[str, Any]) -> list[str]:
    try:
        current = datetime.fromisoformat(str(now).replace("Z", "+00:00")) if not isinstance(now, datetime) else now
        wanted_day = date.fromisoformat(str(day)) if not isinstance(day, date) else day
        wanted_time = time.fromisoformat(str(at))
        if wanted_time.strftime("%H:%M") != str(at) or wanted_time.tzinfo:
            return ["callback time must be a local HH:MM"]
        window = rule_value(policy, "callback_window")
        if not isinstance(window, dict):
            return ["policy has no callback window"]
        days = {current.date()} if window.get("allow_today", False) else set()
        days.update(business_deadline(current, n, rule_value(policy, "holidays", []),
                                     rule_value(policy, "business_weekdays", (0, 1, 2, 3, 4)))
                    for n in range(1, int(window.get("next_business_days", 0)) + 1))
        errors = []
        if wanted_day not in days:
            errors.append("callback date is outside the policy window")
        if not time.fromisoformat(window["start"]) <= wanted_time <= time.fromisoformat(window["end"]):
            errors.append("callback time is outside the policy window")
        proposed = datetime.combine(wanted_day, wanted_time, tzinfo=current.tzinfo)
        if proposed <= current:
            errors.append("callback must be later than supplied now")
        return errors
    except (TypeError, ValueError, KeyError, PolicyError):
        return ["callback or policy inputs are invalid/incomplete"]


def _true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value in ("true", "True"))


def _boolean(value: Any) -> bool:
    if value is True or (isinstance(value, str) and value in ("true", "True")):
        return True
    if value is False or (isinstance(value, str) and value in ("false", "False")):
        return False
    raise PolicyError("tool evidence must contain an explicit boolean")


def _result_data(result: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise PolicyError("tool result must be an object")
    data = result.get("data", result)
    if not isinstance(data, Mapping):
        raise PolicyError("tool result data must be an object")
    return data


def _write_result(result: Mapping[str, Any]) -> str:
    """An explicit rejection is distinct from a transport/unknown-persistence result."""
    try:
        data = _result_data(result)
        return "recorded" if _boolean(data.get("ok")) else "rejected"
    except PolicyError:
        return "unknown_persistence"


def _counter(state: Mapping[str, Any], group: str, product_id: str) -> int:
    counts = state.get(group, {})
    if not isinstance(counts, Mapping):
        raise PolicyError("invalid per-product counters")
    value = counts.get(product_id, 0)
    if type(value) is not int or value < 0:
        raise PolicyError("counter must be a nonnegative integer")
    return value


def _promise_unknown(state: Mapping[str, Any], product_id: str) -> bool:
    return _write_unknown(state, "promise_status", product_id)


def _write_unknown(state: Mapping[str, Any], group: str, product_id: str) -> bool:
    statuses = state.get(group, {})
    if not isinstance(statuses, Mapping):
        raise PolicyError("invalid write statuses")
    return statuses.get(product_id) == "unknown_persistence"


def _failure_limit(policy: Mapping[str, Any], kind: str) -> int:
    limit = rule_value(policy, kind)
    if type(limit) is not int or limit < 1:
        raise PolicyError("policy must supply a positive " + kind)
    return limit


def compile_execution_policy(policy: Mapping[str, Any], bindings: dict | None = None,
                             *, required_kinds: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """Freeze typed helper inputs, with no interpretation of extension prose.

    Only the caller's requested kinds are mandatory. Unknown extensions do not
    become executable rules; an unknown requested kind fails closed.
    """
    kinds = _PROFILE_KINDS | _EXECUTION_ONLY_KINDS
    if not isinstance(required_kinds, (list, tuple)) or any(not isinstance(kind, str) or kind not in kinds for kind in required_kinds):
        raise PolicyError("requested execution rule kind is unsupported")
    profile = {}
    missing = []
    absent = object()
    applicability = rule_value(policy, "policy_applicability", absent)
    singleton = applicability == {"products": "single"}
    for kind in sorted(kinds):
        value = rule_value(policy, kind, absent)
        if value is absent:
            if kind in required_kinds and not (singleton and kind in {"product_order", "resolve_all_products"}):
                missing.append(kind)
            continue
        errors = _execution_errors(kind, value)
        if errors:
            raise PolicyError("; ".join(errors))
        profile[kind] = copy.deepcopy(value)
    if missing:
        raise MissingExecutionRules(missing)
    if ("confirmation_question" in profile) != ("confirmation_question_actions" in profile):
        raise MissingExecutionRules(["confirmation_question_actions" if "confirmation_question" in profile else "confirmation_question"])
    if "band_ranges" in profile:
        bands = {band["name"] for band in profile["band_ranges"]}
        for kind in ("deadline_days", "persuasion_limits"):
            if kind == "deadline_days" and kind in profile and _scoped_value_errors(kind, profile[kind]) == []:
                # A selector carries references, not band keys. The guarded
                # profile checks each retained leaf's band coverage.
                continue
            if kind in profile and set(profile[kind]) != bands:
                raise PolicyError(kind + " must cover exactly the declared bands")
    out = {"version": 1, "policy_sha256": digest(policy), "profile": profile}
    if bindings is not None:
        from .action_bindings import checked_action_bindings
        out["bindings_sha256"] = checked_action_bindings(policy, bindings)["sha256"]
    out["sha256"] = digest(out)
    return out


def identity_record(state: Mapping[str, Any], field: str, result: Mapping[str, Any],
                    policy: Mapping[str, Any], *, wrong_answer_budget: int | None = None) -> dict[str, Any]:
    """Only successful API evidence advances identity; unreadable/errors don't count."""
    assert_action_allowed(state, "verify_identity")
    fields = rule_value(policy, "identity_fields", [])
    limit = rule_value(policy, "identity_wrong_limit")
    if _profile_errors("identity_fields", fields) or type(limit) is not int or limit < 1 or field not in fields:
        raise PolicyError("identity policy is missing or invalid")
    if wrong_answer_budget is not None and (type(wrong_answer_budget) is not int or wrong_answer_budget < 1):
        raise PolicyError("API wrong-answer budget must be a positive integer")
    # A declared API maximum is the counter's own budget, above or below the limit.
    budget = limit if wrong_answer_budget is None else wrong_answer_budget
    verified = state.get("verified_fields", [])
    wrong = state.get("wrong_answers", 0)
    if (not isinstance(verified, list) or verified != fields[:len(verified)]
            or type(wrong) is not int or wrong < 0):
        raise PolicyError("identity state is malformed")
    if state.get("identity_status") == "failed" or wrong >= limit:
        raise PolicyError("identity check is already exhausted")
    expected = next((name for name in fields if name not in verified), None)
    if field != expected:
        raise PolicyError("security questions must follow policy order")
    data = _result_data(result)
    ok = _boolean(data.get("ok"))
    passed = _boolean(data.get("verified")) if ok else False
    remaining = data.get("wrong_answers_left")
    if "wrong_answers_left" in data and (type(remaining) is not int or not 0 <= remaining <= budget):
        raise PolicyError("service identity counter is malformed")
    exemptions = rule_value(policy, "identity_hint_exemptions", ["day_month_swapped"])
    if _profile_errors("identity_hint_exemptions", exemptions):
        raise PolicyError("identity hint exemptions must be explicit strings")
    hint = data.get("hint")
    if hint is not None and (not isinstance(hint, str) or hint not in exemptions):
        raise PolicyError("identity hint has no declared counter meaning")
    verified = list(verified)
    if ok:
        if passed:
            verified.append(field)
        elif hint not in exemptions:
            wrong += 1
    # The service's counter is authoritative and may reflect earlier attempts.
    if ok and remaining is not None:
        wrong = max(wrong, budget - remaining)
    out = copy.deepcopy(dict(state))
    out.update(verified_fields=verified, wrong_answers=wrong,
               identity_status="failed" if wrong >= limit or ok and remaining == 0 else "verified" if all(f in verified for f in fields) else "pending")
    return out


def stage_commitment(state: Mapping[str, Any], products: list[dict[str, Any]], product_id: str,
                     amount: Any, day: Any, now: Any, policy: Mapping[str, Any]) -> dict[str, Any]:
    assert_action_allowed(state, "create_promise")
    product = product_by_id(products, product_id)
    errors = validate_commitment(product, amount, day, now, policy)
    if errors:
        raise PolicyError("; ".join(errors))
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id in out.get("recorded_promises", {}) or product_id in out.get("recorded_notes", {}):
        raise PolicyError("cannot stage this commitment in current call state")
    if _promise_unknown(state, product_id) or _write_unknown(state, "note_status", product_id):
        raise PolicyError("promise persistence must be reconciled before another disposition")
    failures = _counter(state, "promise_failures", product_id)
    if failures and failures >= _failure_limit(policy, "promise_failure_limit"):
        raise PolicyError("product promise failure budget is exhausted")
    out.get("pending_notes", {}).pop(product_id, None)
    out.setdefault("pending_promises", {})[product_id] = {"amount_cents": money_to_cents(amount), "date": _date(day).isoformat()}
    out["confirmation_status"] = "pending"
    return out


def confirm_commitments(state: Mapping[str, Any]) -> dict[str, Any]:
    """Called only by an application after it has established explicit consent."""
    out = copy.deepcopy(dict(state))
    if not out.get("pending_promises") or out.get("closed") or out.get("close_attempted") or out.get("close_status") == "unknown_persistence" or out.get("transfer"):
        raise PolicyError("no active commitments to confirm")
    out["confirmation_status"] = "confirmed"
    return out


def assert_commitment_attempt_allowed(state: Mapping[str, Any], product_id: str,
                                      policy: Mapping[str, Any]) -> None:
    """Check before execution, not after spending another write attempt."""
    assert_action_allowed(state, "create_promise")
    if state.get("closed") or state.get("transfer") or state.get("confirmation_status") != "confirmed":
        raise PolicyError("recording requires confirmed active commitments")
    if product_id not in state.get("pending_promises", {}) or product_id in state.get("recorded_promises", {}):
        raise PolicyError("unknown or already recorded commitment")
    if product_id in state.get("recorded_notes", {}) or _promise_unknown(state, product_id) or _write_unknown(state, "note_status", product_id):
        raise PolicyError("recorded notes or unknown persistence prohibit a promise write")
    if _counter(state, "promise_failures", product_id) >= _failure_limit(policy, "promise_failure_limit"):
        raise PolicyError("product promise failure budget is exhausted")


def record_commitment_result(state: Mapping[str, Any], product_id: str, result: Mapping[str, Any],
                             policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    status = _write_result(result)
    if policy is not None:
        assert_commitment_attempt_allowed(state, product_id, policy)
    else:
        assert_action_allowed(state, "create_promise")
        # Preserve the legacy first-positive-result call without silently
        # permitting failure retries when the bank's limit was not supplied.
        if (status != "recorded" or _counter(state, "promise_failures", product_id)
                or _promise_unknown(state, product_id) or _write_unknown(state, "note_status", product_id)):
            raise PolicyError("failure/unknown write handling requires the supplied policy")
        if (state.get("closed") or state.get("transfer") or state.get("confirmation_status") != "confirmed"
                or product_id not in state.get("pending_promises", {}) or product_id in state.get("recorded_promises", {})):
            raise PolicyError("recording requires an unrecorded confirmed active commitment")
        if product_id in state.get("recorded_notes", {}):
            raise PolicyError("a recorded note prohibits a promise write")
    out = copy.deepcopy(dict(state))
    out.setdefault("promise_status", {})[product_id] = status
    if status == "recorded":
        promise = out["pending_promises"].pop(product_id)
        out.setdefault("recorded_promises", {})[product_id] = promise
    elif status == "rejected":
        out.setdefault("promise_failures", {})[product_id] = _counter(state, "promise_failures", product_id) + 1
    return out


def record_persuasion_attempt(state: Mapping[str, Any], products: list[dict[str, Any]], product_id: str,
                              policy: Mapping[str, Any]) -> dict[str, Any]:
    """Count an application-classified persuasion action for this stable ID."""
    assert_action_allowed(state, "create_promise")
    product = product_by_id(products, product_id)
    limits = rule_value(policy, "persuasion_limits")
    if _profile_errors("persuasion_limits", limits):
        raise PolicyError("policy has no typed per-band persuasion limits")
    limit = limits.get(product_band(product, policy))
    if type(limit) is not int or limit < 0:
        raise PolicyError("persuasion limit is missing for the product band")
    if (state.get("closed") or state.get("transfer") or product_id in state.get("recorded_promises", {})
            or product_id in state.get("recorded_notes", {}) or _promise_unknown(state, product_id)
            or _write_unknown(state, "note_status", product_id)):
        raise PolicyError("persuasion is not permitted in this product state")
    count = _counter(state, "persuasion_attempts", product_id)
    if count >= limit:
        raise PolicyError("product persuasion budget is exhausted")
    out = copy.deepcopy(dict(state))
    out.setdefault("persuasion_attempts", {})[product_id] = count + 1
    return out


def stage_note(state: Mapping[str, Any], products: list[dict[str, Any]], product_id: str,
               reason: str, policy: Mapping[str, Any], *, bindings: dict | None = None) -> dict[str, Any]:
    assert_action_allowed(state, "add_note")
    from .action_bindings import checked_action_bindings, decode_action_value
    decode_action_value(checked_action_bindings(policy, bindings), "notes", reason)
    product_by_id(products, product_id)
    if reason not in rule_value(policy, "note_reasons", []):
        raise PolicyError("note reason is not permitted by policy")
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id in out.get("recorded_promises", {}) or product_id in out.get("recorded_notes", {}):
        raise PolicyError("cannot note this product in current call state")
    if _promise_unknown(state, product_id) or state.get("note_status", {}).get(product_id) == "unknown_persistence":
        raise PolicyError("write persistence must be reconciled before a note")
    out.get("pending_promises", {}).pop(product_id, None)
    out["confirmation_status"] = "pending" if out.get("pending_promises") else "none"
    out.setdefault("pending_notes", {})[product_id] = reason
    return out


def stage_transfer(state: Mapping[str, Any], queue: str, policy: Mapping[str, Any], *, bindings: dict | None = None) -> dict[str, Any]:
    assert_action_allowed(state, "transfer_to_agent")
    from .action_bindings import checked_action_bindings, decode_action_value
    decode_action_value(checked_action_bindings(policy, bindings), "queues", queue)
    reason = rule_value(policy, "transfer_reasons", {}).get(queue)
    if reason is None or state.get("closed") or state.get("transfer"):
        raise PolicyError("transfer is not permitted")
    out = copy.deepcopy(dict(state))
    if out.get("recorded_notes") or "unknown_persistence" in out.get("note_status", {}).values():
        raise PolicyError("already recorded notes cannot be undone for a transfer")
    out.update(transfer={"queue": queue, "reason": reason, "status": "pending"},
               pending_notes={}, pending_promises={}, confirmation_status="none")
    return out


def record_note_result(state: Mapping[str, Any], product_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Commit a previously staged disposition only after positive tool evidence."""
    assert_action_allowed(state, "add_note")
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id not in out.get("pending_notes", {}):
        raise PolicyError("no permitted staged note to record")
    if product_id in out.get("recorded_notes", {}):
        raise PolicyError("note is already recorded")
    if state.get("note_status", {}).get(product_id) == "unknown_persistence":
        raise PolicyError("unknown note persistence prohibits retries")
    status = _write_result(result)
    out.setdefault("note_status", {})[product_id] = status
    if status == "recorded":
        out.setdefault("recorded_notes", {})[product_id] = out["pending_notes"][product_id]
    elif status == "rejected":
        out.setdefault("note_failures", {})[product_id] = _counter(state, "note_failures", product_id) + 1
    return out


def record_transfer_result(state: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    assert_action_allowed(state, "transfer_to_agent")
    out = copy.deepcopy(dict(state))
    if out.get("closed") or not out.get("transfer") or out["transfer"].get("status") != "pending":
        raise PolicyError("no active transfer")
    status = _write_result(result)
    out["transfer"]["status"] = "failed" if status == "rejected" else status
    return out


def choose_close(state: Mapping[str, Any], products: list[dict[str, Any]], policy: Mapping[str, Any],
                 *, bindings: dict | None = None) -> dict[str, str]:
    """Choose from recorded facts; precedence and reason vocabulary are policy inputs."""
    from .action_bindings import checked_action_bindings, decode_action_value, encode_action_value
    binding = checked_action_bindings(policy, bindings)
    def proposal(meaning: str, reason: str | None = None) -> dict[str, str]:
        out = {"outcome": encode_action_value(binding, "outcomes", meaning)}
        if reason is not None:
            out["reason"] = reason
        validate_close(out, policy, bindings=bindings)
        return out
    if state.get("closed") or state.get("close_attempted") or state.get("close_status") == "unknown_persistence":
        raise PolicyError("call is already closed")
    transfer = state.get("transfer", {})
    if transfer.get("status") == "recorded":
        if rule_value(policy, "transfer_reasons", {}).get(transfer.get("queue")) != transfer.get("reason"):
            raise PolicyError("recorded transfer queue/reason evidence does not match policy")
        return proposal("transferred", transfer["reason"])
    if transfer:
        raise PolicyError("a transfer without confirmed success cannot establish a close")
    if state.get("recorded_promises"):
        return proposal("promise")
    if "unknown_persistence" in state.get("promise_status", {}).values() or "unknown_persistence" in state.get("note_status", {}).values():
        raise PolicyError("unreconciled persistence cannot establish a closing disposition")
    notes = state.get("pending_notes", {})
    meanings = [decode_action_value(binding, "notes", notes[p["product_id"]]) if p["product_id"] in notes else None for p in products]
    if products and all(meaning == "paid" for meaning in meanings):
        return proposal("paid")
    meaning = next((meaning for meaning in meanings if meaning not in (None, "paid")), None)
    if meaning:
        return proposal("no_promise", encode_action_value(binding, "reasons", meaning))
    fallback = state.get("close_proposal")
    if isinstance(fallback, dict):
        if decode_action_value(binding, "outcomes", fallback.get("outcome")) in {"promise", "transferred"}:
            raise PolicyError("closing proposal lacks successful tool evidence")
        validate_close(fallback, policy, bindings=bindings)
        return dict(fallback)
    raise PolicyError("recorded facts do not establish a closing outcome")


def validate_close(proposal: Mapping[str, Any], policy: Mapping[str, Any], *, bindings: dict | None = None) -> None:
    from .action_bindings import checked_action_bindings, decode_action_value
    if not isinstance(proposal, Mapping):
        raise PolicyError("closing proposal must be an object")
    binding = checked_action_bindings(policy, bindings)
    reasons = rule_value(policy, "close_reasons", {})
    outcome, reason = proposal.get("outcome"), proposal.get("reason")
    decode_action_value(binding, "outcomes", outcome)
    if reason not in (None, ""):
        decode_action_value(binding, "reasons", reason)
    if outcome not in reasons or (reasons[outcome] and reason not in reasons[outcome]) or (not reasons[outcome] and reason not in (None, "")):
        raise PolicyError("closing outcome/reason is not permitted by policy")


def record_close_result(state: Mapping[str, Any], proposal: Mapping[str, Any], result: Mapping[str, Any],
                        policy: Mapping[str, Any], *, bindings: dict | None = None) -> dict[str, Any]:
    from .action_bindings import checked_action_bindings, decode_action_value
    binding = checked_action_bindings(policy, bindings)
    validate_close(proposal, policy, bindings=bindings)
    if state.get("closed") or state.get("close_attempted") or state.get("close_status") == "unknown_persistence":
        raise PolicyError("close_call must be the last and only close attempt")
    semantic = decode_action_value(binding, "outcomes", proposal.get("outcome"))
    if semantic == "promise" and not state.get("recorded_promises"):
        raise PolicyError("promise outcome requires a successfully recorded promise")
    transfer = state.get("transfer", {})
    if semantic == "transferred" and (transfer.get("status") != "recorded"
            or transfer.get("reason") != proposal.get("reason")
            or rule_value(policy, "transfer_reasons", {}).get(transfer.get("queue")) != proposal.get("reason")):
        raise PolicyError("transferred outcome requires a successful matching transfer")
    if transfer and semantic != "transferred":
        raise PolicyError("an active transfer requires its matching successful closing outcome")
    if semantic != "transferred" and ("unknown_persistence" in state.get("promise_status", {}).values()
                                      or "unknown_persistence" in state.get("note_status", {}).values()):
        raise PolicyError("unknown persistence must be reconciled before closing")
    out = copy.deepcopy(dict(state))
    status = _write_result(result)
    out["close_attempted"] = True
    out["close_status"] = status
    if status == "recorded":
        out.update(closed=True, close=dict(proposal))
    return out


def assert_action_allowed(state: Mapping[str, Any], action: str) -> None:
    if state.get("closed") or state.get("close_attempted") or state.get("close_status") == "unknown_persistence":
        raise PolicyError("no tool call is permitted after any close_call attempt")
    if state.get("transfer") and action in {"add_note", "create_promise"}:
        raise PolicyError("staged or completed transfers prohibit notes and pending promises")

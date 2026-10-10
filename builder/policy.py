"""Provenance-aware policy IR and pure, policy-driven banking primitives.

These helpers validate proposed actions and ledger state. They do not interpret
speech, prove compliance of an LLM, or replace platform conversation tests.
Amounts are represented in integer minor units; all clocks come from ``now``.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, TypedDict

from .safety import canonical_json, digest


class RuleSource(TypedDict):
    id: str
    quote: str


class PolicyRule(TypedDict, total=False):
    id: str
    kind: str
    value: Any
    source: RuleSource
    overrides: list[str]


class PolicyError(ValueError):
    pass


_PROFILE_KINDS = {"identity_fields", "identity_wrong_limit", "holidays", "business_weekdays",
                  "band_ranges", "deadline_days", "floor_fields", "callback_window",
                  "note_reasons", "close_reasons", "transfer_reasons", "require_confirmation",
                  "promise_failure_limit", "tool_aliases"}


def _profile_errors(kind: str, value: Any) -> list[str]:
    def strings(item: Any, *, empty: bool = False) -> bool:
        return isinstance(item, list) and (empty or bool(item)) and all(isinstance(x, str) and x for x in item)
    valid = True
    if kind in {"identity_wrong_limit", "promise_failure_limit"}:
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
    return [] if valid else [f"policy rule {kind} has an invalid structured value"]


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
            elif source_documents is not None:
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
    if not isinstance(unsupported, list) or any(not isinstance(x, str) for x in unsupported):
        errors.append("policy.unsupported_constraints must be a string list")
    try:
        canonical_json(policy)
    except (TypeError, ValueError):
        errors.append("policy must contain finite JSON values")
    return errors


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
    return value is True or value in ("true", "True")


def _result_data(result: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise PolicyError("tool result must be an object")
    data = result.get("data", result)
    if not isinstance(data, Mapping):
        raise PolicyError("tool result data must be an object")
    return data


def identity_record(state: Mapping[str, Any], field: str, result: Mapping[str, Any],
                    policy: Mapping[str, Any]) -> dict[str, Any]:
    """Only successful API evidence advances identity; unreadable/errors don't count."""
    fields = rule_value(policy, "identity_fields", [])
    limit = rule_value(policy, "identity_wrong_limit")
    if not fields or type(limit) is not int or limit < 1 or field not in fields:
        raise PolicyError("identity policy is missing or invalid")
    out = copy.deepcopy(dict(state))
    verified = list(out.get("verified_fields", []))
    wrong = int(out.get("wrong_answers", 0))
    if out.get("identity_status") == "failed" or wrong >= limit:
        raise PolicyError("identity check is already exhausted")
    expected = next((name for name in fields if name not in verified), None)
    if field != expected:
        raise PolicyError("security questions must follow policy order")
    data = _result_data(result)
    if _true(data.get("ok")):
        if _true(data.get("verified")):
            verified.append(field)
        elif data.get("hint") != "day_month_swapped":
            wrong += 1
    # The service's counter is authoritative and may reflect earlier attempts.
    remaining = data.get("wrong_answers_left")
    if _true(data.get("ok")) and type(remaining) is int and 0 <= remaining <= limit:
        wrong = max(wrong, limit - remaining)
    out.update(verified_fields=verified, wrong_answers=wrong,
               identity_status="failed" if wrong >= limit else "verified" if all(f in verified for f in fields) else "pending")
    return out


def stage_commitment(state: Mapping[str, Any], products: list[dict[str, Any]], product_id: str,
                     amount: Any, day: Any, now: Any, policy: Mapping[str, Any]) -> dict[str, Any]:
    product = product_by_id(products, product_id)
    errors = validate_commitment(product, amount, day, now, policy)
    if errors:
        raise PolicyError("; ".join(errors))
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id in out.get("recorded_promises", {}) or product_id in out.get("recorded_notes", {}):
        raise PolicyError("cannot stage this commitment in current call state")
    out.get("pending_notes", {}).pop(product_id, None)
    out.setdefault("pending_promises", {})[product_id] = {"amount_cents": money_to_cents(amount), "date": _date(day).isoformat()}
    out["confirmation_status"] = "pending"
    return out


def confirm_commitments(state: Mapping[str, Any]) -> dict[str, Any]:
    """Called only by an application after it has established explicit consent."""
    out = copy.deepcopy(dict(state))
    if not out.get("pending_promises") or out.get("closed") or out.get("transfer"):
        raise PolicyError("no active commitments to confirm")
    out["confirmation_status"] = "confirmed"
    return out


def record_commitment_result(state: Mapping[str, Any], product_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or out.get("confirmation_status") != "confirmed":
        raise PolicyError("recording requires confirmed active commitments")
    if product_id not in out.get("pending_promises", {}) or product_id in out.get("recorded_promises", {}):
        raise PolicyError("unknown or already recorded commitment")
    data = _result_data(result)
    if _true(data.get("ok")):
        promise = out["pending_promises"].pop(product_id)
        out.setdefault("recorded_promises", {})[product_id] = promise
    else:
        counts = out.setdefault("promise_failures", {})
        counts[product_id] = counts.get(product_id, 0) + 1
    return out


def stage_note(state: Mapping[str, Any], products: list[dict[str, Any]], product_id: str,
               reason: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    product_by_id(products, product_id)
    if reason not in rule_value(policy, "note_reasons", []):
        raise PolicyError("note reason is not permitted by policy")
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id in out.get("recorded_promises", {}) or product_id in out.get("recorded_notes", {}):
        raise PolicyError("cannot note this product in current call state")
    out.get("pending_promises", {}).pop(product_id, None)
    out["confirmation_status"] = "pending" if out.get("pending_promises") else "none"
    out.setdefault("pending_notes", {})[product_id] = reason
    return out


def stage_transfer(state: Mapping[str, Any], queue: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    reason = rule_value(policy, "transfer_reasons", {}).get(queue)
    if reason is None or state.get("closed"):
        raise PolicyError("transfer is not permitted")
    out = copy.deepcopy(dict(state))
    if out.get("recorded_notes"):
        raise PolicyError("already recorded notes cannot be undone for a transfer")
    out.update(transfer={"queue": queue, "reason": reason, "status": "pending"},
               pending_notes={}, pending_promises={})
    return out


def record_note_result(state: Mapping[str, Any], product_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Commit a previously staged disposition only after positive tool evidence."""
    out = copy.deepcopy(dict(state))
    if out.get("closed") or out.get("transfer") or product_id not in out.get("pending_notes", {}):
        raise PolicyError("no permitted staged note to record")
    if product_id in out.get("recorded_notes", {}):
        raise PolicyError("note is already recorded")
    if _true(_result_data(result).get("ok")):
        out.setdefault("recorded_notes", {})[product_id] = out["pending_notes"][product_id]
    else:
        failures = out.setdefault("note_failures", {})
        failures[product_id] = failures.get(product_id, 0) + 1
    return out


def record_transfer_result(state: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(state))
    if out.get("closed") or not out.get("transfer"):
        raise PolicyError("no active transfer")
    out["transfer"]["status"] = "recorded" if _true(_result_data(result).get("ok")) else "failed"
    return out


def choose_close(state: Mapping[str, Any], products: list[dict[str, Any]], policy: Mapping[str, Any]) -> dict[str, str]:
    """Choose from recorded facts; precedence and reason vocabulary are policy inputs."""
    transfer = state.get("transfer", {})
    if transfer.get("status") == "recorded":
        return {"outcome": "transferred", "reason": transfer["reason"]}
    if state.get("recorded_promises"):
        return {"outcome": "promise"}
    notes = state.get("pending_notes", {})
    if products and all(notes.get(p["product_id"]) == "paid" for p in products):
        return {"outcome": "paid"}
    reason = next((notes[p["product_id"]] for p in products if notes.get(p["product_id"]) not in (None, "paid")), None)
    if reason:
        return {"outcome": "no_promise", "reason": reason}
    fallback = state.get("close_proposal")
    if isinstance(fallback, dict):
        if fallback.get("outcome") in {"promise", "transferred"}:
            raise PolicyError("closing proposal lacks successful tool evidence")
        validate_close(fallback, policy)
        return dict(fallback)
    raise PolicyError("recorded facts do not establish a closing outcome")


def validate_close(proposal: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    reasons = rule_value(policy, "close_reasons", {})
    outcome, reason = proposal.get("outcome"), proposal.get("reason")
    if outcome not in reasons or (reasons[outcome] and reason not in reasons[outcome]) or (not reasons[outcome] and reason not in (None, "")):
        raise PolicyError("closing outcome/reason is not permitted by policy")


def record_close_result(state: Mapping[str, Any], proposal: Mapping[str, Any], result: Mapping[str, Any],
                        policy: Mapping[str, Any]) -> dict[str, Any]:
    validate_close(proposal, policy)
    if state.get("closed"):
        raise PolicyError("close_call must be the last and only successful close")
    if proposal.get("outcome") == "promise" and not state.get("recorded_promises"):
        raise PolicyError("promise outcome requires a successfully recorded promise")
    if proposal.get("outcome") == "transferred" and (state.get("transfer", {}).get("status") != "recorded"
            or state.get("transfer", {}).get("reason") != proposal.get("reason")):
        raise PolicyError("transferred outcome requires a successful matching transfer")
    out = copy.deepcopy(dict(state))
    if _true(_result_data(result).get("ok")):
        out.update(closed=True, close=dict(proposal))
    return out


def assert_action_allowed(state: Mapping[str, Any], action: str) -> None:
    if state.get("closed"):
        raise PolicyError("no tool call is permitted after close_call")
    if state.get("transfer") and action in {"add_note", "create_promise"}:
        raise PolicyError("staged or completed transfers prohibit notes and pending promises")

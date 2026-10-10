"""Inspect source-bound conditional calendar conflicts without resolving them.

This module has no clock, I/O, speech, tool or disposition side effects. A clear
predicate is local to that condition; it does not prove conversation compliance.
Compiled descriptors explicitly remain unenforced until a runtime guard consumes
them before an affected offer is spoken or written.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
import re
from typing import Any

from .safety import canonical_json, digest

MAX_EXPRESSION_DEPTH = 8
MAX_EXPRESSION_NODES = 64
MAX_WEEK_OFFSET = 520
MAX_CONDITIONS = 64


class ConditionalConflictError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(sorted(set(errors)))
        super().__init__("Conditional policy conflict invalid: " + "; ".join(self.errors))


def _reject(message):
    raise ConditionalConflictError([message])


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _date(value):
    if type(value) is not str:
        raise ValueError("date is not an ISO string")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date is not a canonical ISO date")
    return parsed


def _clock(value):
    if type(value) is datetime:
        return value.date()
    if type(value) is date:
        return value
    if type(value) is not str:
        raise ValueError("call clock is missing or malformed")
    if len(value) == 10:
        return _date(value)
    if len(value) < 16 or value[10] != "T":
        raise ValueError("call clock is not an ISO date/time")
    _date(value[:10])
    return datetime.fromisoformat(value).date()


def _validate_expression(expression, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > MAX_EXPRESSION_DEPTH or count[0] > MAX_EXPRESSION_NODES:
        _reject("calendar expression exceeds the supported structural bound")
    if not isinstance(expression, dict):
        _reject("calendar expression must be a typed object")
    op = expression.get("op")
    if op == "now_date":
        valid = set(expression) == {"op"}
    elif op == "literal_date":
        valid = set(expression) == {"op", "value"}
        if valid:
            try:
                _date(expression["value"])
            except (TypeError, ValueError):
                valid = False
    elif op == "last_business_day_of_week":
        valid = (set(expression) == {"op", "week_offset", "week_start"}
                 and type(expression["week_offset"]) is int
                 and abs(expression["week_offset"]) <= MAX_WEEK_OFFSET
                 and type(expression["week_start"]) is int and 0 <= expression["week_start"] <= 6)
    elif op == "if_date_equals_now":
        valid = set(expression) == {"op", "date", "then", "else"}
        if valid:
            for key in ("date", "then", "else"):
                _validate_expression(expression[key], depth + 1, count)
    else:
        valid = False
    if not valid:
        _reject("calendar expression has an unsupported operator or shape")


def _source_map(sources):
    if not isinstance(sources, list) or not sources:
        _reject("conditional conflicts require a controller-bound source inventory")
    result = {}
    for source in sources:
        if (not isinstance(source, dict) or set(source) != {"id", "file", "sha256"}
                or not _text(source.get("id")) or not _text(source.get("file"))
                or not isinstance(source.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None
                or source["id"] in result):
            _reject("conditional conflict source inventory is malformed or ambiguous")
        result[source["id"]] = copy.deepcopy(source)
    return result


def _witnesses(entry, sources, selected=None):
    ids, provenance = entry.get("rule_ids"), entry.get("provenance")
    if (not isinstance(ids, list) or not ids or any(not _text(item) for item in ids)
            or len(set(ids)) != len(ids) or not isinstance(provenance, list)):
        _reject("conditional conflict requires actual active rule provenance")
    wanted = set(ids) if selected is None else {selected}
    if not wanted <= set(ids):
        _reject("conditional conflict references an inactive rule")
    result = []
    for rule_id in sorted(wanted):
        rows = [row for row in provenance if isinstance(row, dict) and row.get("rule_id") == rule_id]
        if (len(rows) != 1 or set(rows[0]) != {"rule_id", "id", "quote"}
                or not _text(rows[0]["id"]) or rows[0]["id"] not in sources or not _text(rows[0]["quote"])):
            _reject("conditional conflict rule witness is missing or ambiguous")
        result.append({**copy.deepcopy(rows[0]), "source": copy.deepcopy(sources[rows[0]["id"]])})
    return result


def _reference(active_rules, sources, reference):
    if (not isinstance(reference, dict) or set(reference) != {"rule_id", "value_path"}
            or not _text(reference.get("rule_id")) or not isinstance(reference.get("value_path"), list)
            or len(reference["value_path"]) > 16
            or any(not (_text(part) or (type(part) is int and part >= 0)) for part in reference["value_path"])):
        _reject("conditional conflict requires a closed active-rule/value-path reference")
    matches = [(kind, entry) for kind, entry in active_rules.items()
               if isinstance(entry, dict) and isinstance(entry.get("rule_ids"), list)
               and reference["rule_id"] in entry["rule_ids"]]
    if len(matches) != 1 or matches[0][0] == "conditional_conflicts":
        _reject("conditional conflict rule reference is inactive, recursive or ambiguous")
    kind, entry = matches[0]
    provenance = _witnesses(entry, sources, reference["rule_id"])
    if "value" not in entry:
        _reject("conditional conflict referenced rule has no value")
    value = entry["value"]
    for part in reference["value_path"]:
        if isinstance(value, dict) and isinstance(part, str) and part in value:
            value = value[part]
        elif isinstance(value, list) and type(part) is int and part < len(value):
            value = value[part]
        else:
            _reject("conditional conflict value path does not exist in its active rule")
    return copy.deepcopy(value), {"rule_id": reference["rule_id"], "kind": kind,
                                  "value_path": copy.deepcopy(reference["value_path"]),
                                  "value_sha256": digest(value), "provenance": provenance}


def compile_conditional_conflicts(active_rules: Any, sources: Any) -> dict | None:
    """Compile a detached inspection descriptor from validated active rules.

    References select actual active values and derive their source witnesses.
    Callers cannot supply alternative calendars, fallback dates or asserted
    provenance alongside those references. The outer compiler validates quotes
    against actual controller source text before calling this function.
    """
    if not isinstance(active_rules, dict):
        _reject("conditional conflict active-rule inventory must be an object")
    if "conditional_conflicts" not in active_rules:
        return None
    try:
        canonical_json(active_rules)
    except (TypeError, ValueError, RecursionError):
        _reject("conditional conflict values must be finite JSON")
    source_map = _source_map(sources)
    entry = active_rules["conditional_conflicts"]
    if not isinstance(entry, dict):
        _reject("conditional conflict rule must have a compiled active entry")
    origin = _witnesses(entry, source_map)
    records = entry.get("value")
    if not isinstance(records, list) or not records or len(records) > MAX_CONDITIONS:
        _reject("conditional_conflicts must be a bounded nonempty typed record list")
    compiled, seen = [], set()
    for record in records:
        if (not isinstance(record, dict) or set(record) != {"id", "phase", "effect", "predicate"}
                or not _text(record.get("id")) or record["id"] in seen
                or record.get("phase") != "date_offer" or record.get("effect") != "block_affected_route"):
            _reject("conditional conflict record has an unsupported scope, effect or identifier")
        seen.add(record["id"])
        predicate = record["predicate"]
        if (not isinstance(predicate, dict)
                or set(predicate) != {"operator", "required_date", "lower_bound", "calendar"}
                or predicate["operator"] != "lt" or not isinstance(predicate["calendar"], dict)
                or set(predicate["calendar"]) != {"weekdays", "holidays"}):
            _reject("conditional conflict predicate must compare a required date with its now-date lower bound")
        left, left_meta = _reference(active_rules, source_map, predicate["required_date"])
        right, right_meta = _reference(active_rules, source_map, predicate["lower_bound"])
        weekdays, weekday_meta = _reference(active_rules, source_map, predicate["calendar"]["weekdays"])
        holidays, holiday_meta = _reference(active_rules, source_map, predicate["calendar"]["holidays"])
        if left_meta["rule_id"] == right_meta["rule_id"]:
            _reject("conditional conflict must retain distinct required-date and lower-bound rules")
        _validate_expression(left)
        if right != {"op": "now_date"}:
            _reject("conditional conflict lower bound must be the source-owned now_date expression")
        if (weekday_meta["kind"] != "business_weekdays" or weekday_meta["value_path"]
                or not isinstance(weekdays, list) or not weekdays
                or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)
                or len(set(weekdays)) != len(weekdays)):
            _reject("conditional conflict weekdays must bind the active business_weekdays rule")
        if (holiday_meta["kind"] != "holidays" or holiday_meta["value_path"]
                or not isinstance(holidays, list) or any(type(day) is not str for day in holidays)
                or len(set(holidays)) != len(holidays)):
            _reject("conditional conflict holidays must bind the active holidays rule")
        try:
            for holiday in holidays:
                _date(holiday)
        except (TypeError, ValueError):
            _reject("conditional conflict holiday dates must be canonical ISO strings")
        result = {"id": record["id"], "phase": record["phase"], "effect": record["effect"],
                  "predicate": {"operator": "lt", "required_date": left, "lower_bound": right},
                  "calendar": {"weekdays": weekdays, "holidays": holidays},
                  "bindings": {"required_date": left_meta, "lower_bound": right_meta,
                               "weekdays": weekday_meta, "holidays": holiday_meta},
                  "origin": origin}
        result["sha256"] = digest(result)
        compiled.append(result)
    descriptor = {"version": 1, "runtime_enforced": False,
                  "conditions": sorted(compiled, key=lambda record: record["id"])}
    descriptor["sha256"] = digest(descriptor)
    return descriptor


def _evaluate_expression(expression, today, calendar):
    op = expression["op"]
    if op == "now_date":
        return today
    if op == "literal_date":
        return _date(expression["value"])
    if op == "last_business_day_of_week":
        start = today - timedelta(days=(today.weekday() - expression["week_start"]) % 7) + timedelta(weeks=expression["week_offset"])
        holidays = {_date(item) for item in calendar["holidays"]}
        days = [start + timedelta(days=offset) for offset in range(7)]
        available = [day for day in days if day.weekday() in calendar["weekdays"] and day not in holidays]
        if not available:
            raise ValueError("week has no declared business day")
        return available[-1]
    if op == "if_date_equals_now":
        compared = _evaluate_expression(expression["date"], today, calendar)
        branch = "then" if compared == today else "else"
        return _evaluate_expression(expression[branch], today, calendar)
    raise ValueError("unsupported calendar expression")


def evaluate_conditional_conflicts(contract: Any, now: Any, *, expected_contract_sha256: str | None = None) -> list[dict]:
    """Return clear/blocked/unknown observations without altering a date or state.

    Unknown is never a clear result. Runtime integration must block that affected
    route on both blocked and unknown, before speaking an offer or writing it.
    """
    try:
        if not isinstance(contract, dict) or "sha256" not in contract:
            raise ValueError("missing contract fingerprint")
        if expected_contract_sha256 is not None:
            if (not isinstance(expected_contract_sha256, str)
                    or re.fullmatch(r"[0-9a-f]{64}", expected_contract_sha256) is None
                    or contract["sha256"] != expected_contract_sha256):
                raise ValueError("contract differs from the independently pinned fingerprint")
        if (type(contract.get("version")) is not int or contract["version"] != 1
                or not isinstance(contract.get("active_rules"), dict)):
            raise ValueError("malformed contract header")
        _source_map(contract.get("sources"))
        if digest({key: value for key, value in contract.items() if key != "sha256"}) != contract["sha256"]:
            raise ValueError("changed contract")
        descriptor = compile_conditional_conflicts(contract.get("active_rules"), contract.get("sources"))
        if canonical_json(descriptor) != canonical_json(contract.get("conditional_conflicts")):
            raise ValueError("changed conditional descriptor")
    except (ConditionalConflictError, TypeError, ValueError, KeyError, OverflowError, RecursionError):
        return [{"id": None, "phase": "date_offer", "effect": "block_affected_route",
                 "status": "unknown", "reason": "invalid_conflict_contract"}]
    if descriptor is None:
        return []
    try:
        today = _clock(now)
    except (TypeError, ValueError, OverflowError):
        return [{"id": record["id"], "phase": record["phase"], "effect": record["effect"],
                 "status": "unknown", "reason": "invalid_call_clock"} for record in descriptor["conditions"]]
    results = []
    for record in descriptor["conditions"]:
        result = {"id": record["id"], "phase": record["phase"], "effect": record["effect"]}
        try:
            required = _evaluate_expression(record["predicate"]["required_date"], today, record["calendar"])
            blocked = required < today
            result.update(status="blocked" if blocked else "clear",
                          reason="required_date_before_now" if blocked else "required_date_not_before_now",
                          required_date=required.isoformat(), lower_bound=today.isoformat())
        except (TypeError, ValueError, OverflowError):
            result.update(status="unknown", reason="calendar_expression_unresolved")
        results.append(result)
    return results

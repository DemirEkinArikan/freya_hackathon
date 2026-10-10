"""Read-only call-input names from an approved, frozen policy contract."""
from __future__ import annotations

import re
from typing import Any

from .safety import digest


class CallVariablesError(ValueError):
    pass


def check_call_variables(value: Any) -> tuple[str, ...]:
    """Validate declarations, never infer names or supply customer values."""
    if isinstance(value, dict):
        if not value or any(not isinstance(description, str) or not description.strip()
                            for description in value.values()):
            raise CallVariablesError("call_variables mapping requires nonempty text descriptions, never target values or defaults")
        names = list(value)
    elif isinstance(value, list):
        names = value
    else:
        raise CallVariablesError("call_variables requires a name array or a name-to-description mapping")
    if (len(names) > 128
            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", name)
                   or name.casefold().startswith(("bg_", "auth_")) or name.casefold() == "bank_token" for name in names)
            or len(set(names)) != len(names)):
        raise CallVariablesError("call_variables requires unique ordinary variable names; protected controller names are forbidden")
    return tuple(names)


def declared_call_variables(contract: Any) -> tuple[str, ...]:
    """Require frozen controller provenance for every declared call-input name.

    Literal name/source binding does not certify that a natural-language quote
    describes an initial call input; independent interpretation remains needed.
    """
    if not isinstance(contract, dict):
        raise CallVariablesError("call_variables requires a frozen controller contract")
    active = contract.get("active_rules", {})
    if not isinstance(active, dict):
        raise CallVariablesError("call_variables requires frozen active rules")
    if "call_variables" not in active:
        return ()
    if (type(contract.get("version")) is not int or contract["version"] != 1
            or contract.get("sha256") != digest({key: value for key, value in contract.items() if key != "sha256"})):
        raise CallVariablesError("call_variables contract fingerprint is missing or stale")
    rule = active["call_variables"]
    if not isinstance(rule, dict):
        raise CallVariablesError("call_variables requires a source-backed active rule")
    names = check_call_variables(rule.get("value"))
    sources = contract.get("sources")
    provenance, rule_ids = rule.get("provenance"), rule.get("rule_ids")
    if (not isinstance(sources, list) or not isinstance(provenance, list) or not provenance
            or not isinstance(rule_ids, list) or not rule_ids
            or any(not isinstance(source, dict) for source in sources)):
        raise CallVariablesError("call_variables requires retained source and rule provenance")
    source_ids = {source.get("id") for source in sources if isinstance(source.get("id"), str)}
    quotes = []
    for reference in provenance:
        if (not isinstance(reference, dict) or reference.get("id") not in source_ids
                or reference.get("rule_id") not in rule_ids
                or not isinstance(reference.get("quote"), str) or not reference["quote"].strip()):
            raise CallVariablesError("call_variables requires retained source and rule provenance")
        quotes.append(reference["quote"])
    if any(not any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", quote) for quote in quotes)
           for name in names):
        raise CallVariablesError("each call_variables name must occur literally in its active source quotation")
    return names

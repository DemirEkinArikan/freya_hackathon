"""Fresh stdlib utilities for synthetic artifacts; no product code is imported.

Scrubbing is a publication aid, not a promise that arbitrary text is confidential.
Pass every known credential as ``secrets``/``forbidden_values`` for exact checks.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"^(?:authorization|proxyauthorization|xapikey|xteamkey|apikey|.*apikey|"
    r"token|sessiontoken|accesstoken|refreshtoken|authtoken|bearertoken|password|passwd|"
    r"clientsecret|privatekey|freya.*key|openai.*key|anthropic.*key)$", re.I
)
_TOKEN = re.compile(r"\b(?:fak_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{20,})\b")
_BEARER = re.compile(r"\bBearer\s+([^\s\"'<>;,]+)", re.I)
_ENV = re.compile(
    r"\b([A-Z][A-Z0-9_]*)[ \t]*=[ \t]*"
    r"(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;#]+)"
)


def canonical_json(value: Any) -> str:
    """Deterministic UTF-8 JSON representation; reject nonfinite numbers."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains a duplicate object key")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise ValueError("JSON contains a nonfinite number")


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=_pairs, parse_constant=_nonfinite)


def _placeholder(value: str) -> bool:
    v = value.strip().strip("\"'")
    if not v or v == REDACTED:
        return True
    if re.fullmatch(r"\{\{[\w.:-]+\}\}|\{[\w.:-]+\}|\$\{[\w.:-]+\}|\$[A-Z][A-Z0-9_]*|<[^<>]+>", v):
        return True
    if re.fullmatch(r"(?:fak_|sk-|github_pat_)(?:your[_ -].*|placeholder|example|\.\.\.)", v, re.I):
        return True
    return bool(re.fullmatch(r"(?:your[_ -].*|replace[_ -].*|pick[_ -](?:a|any)[_ -]secret"
                             r".*|changeme|placeholder)", v, re.I))


def _sensitive_key(key: str) -> bool:
    return bool(_SECRET_KEY.fullmatch(re.sub(r"[^A-Za-z0-9]", "", key)))


def _scrub_text(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        value = value.replace(secret, REDACTED)
    value = _TOKEN.sub(lambda m: m.group(0) if _placeholder(m.group(0)) else REDACTED, value)
    value = _BEARER.sub(
        lambda m: m.group(0) if _placeholder(m.group(1)) or m.group(1).lower() in
        {"http", "authentication", "scheme", "auth", "tokens", "tool"} else "Bearer " + REDACTED, value
    )
    value = _ENV.sub(
        lambda m: m.group(0) if not _sensitive_key(m.group(1)) or _placeholder(m.group(2))
        else m.group(1) + "=" + REDACTED, value
    )
    return value


def sanitize(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Copy a JSON value, preserving object/list shape while scrubbing secrets.

    Schema property declarations such as ``Authorization: {type: string}``
    remain intact; concrete ``default``/``const`` values are scrubbed instead.
    """
    known = tuple(str(s) for s in secrets if s)

    def visit(item: Any, sensitive: bool = False) -> Any:
        if isinstance(item, dict):
            out = {}
            for key, child in item.items():
                name = str(key)
                child_sensitive = _sensitive_key(name) or (
                    sensitive and name in {"default", "const", "value", "example", "examples", "enum"}
                )
                out[key] = visit(child, child_sensitive)
            return out
        if isinstance(item, list):
            return [visit(child, sensitive) for child in item]
        if isinstance(item, tuple):
            return tuple(visit(child, sensitive) for child in item)
        if isinstance(item, str):
            if sensitive and not _placeholder(item):
                # "Bearer {{token}}" is a safe workflow template, not a credential.
                match = _BEARER.fullmatch(item.strip())
                if not (match and _placeholder(match.group(1))):
                    return REDACTED
            return _scrub_text(item, known)
        if sensitive and item is not None:
            return REDACTED
        return item

    return visit(value)


def assert_no_secrets(value: Any, forbidden_values: Iterable[str] = ()) -> None:
    """Raise without disclosing the offending value or artifact contents."""
    known = tuple(str(s) for s in forbidden_values if s)
    def secret_key(item: Any) -> bool:
        if isinstance(item, dict):
            return any((isinstance(key, str) and _scrub_text(key, known) != key)
                       or secret_key(child) for key, child in item.items())
        if isinstance(item, (list, tuple)):
            return any(secret_key(child) for child in item)
        return False
    if sanitize(value, known) != value or secret_key(value):
        raise ValueError("Artifact contains credential-like or forbidden material")

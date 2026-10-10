"""Read this project's credential file as data, never as shell code."""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import MutableMapping

_KEYS = {"FREYA_API_KEY", "FREYA_API_URL", "FREYA_BANK_TEAM_KEY", "FREYA_WORKSPACE_ID",
         "BUILDER_MODEL", "FREYA_BUILDER_MODEL", "FREYA_KEY"}


def read_local_environment(path: Path) -> dict[str, str]:
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Local credential file cannot be a symlink")
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError("Local credential path must be a regular file")
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError(f"Invalid local environment declaration on line {number}")
        key, value = match.groups()
        if key not in _KEYS and not re.fullmatch(r"FREYA_TOOL_AUTH_[A-Z0-9_]+", key):
            raise ValueError(f"Unsupported local environment key on line {number}")
        if key in values:
            raise ValueError(f"Duplicate local environment key on line {number}")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Invalid quoted local value on line {number}")
            value = value[1:-1]
        if any(symbol in value for symbol in ("$", "`", "\x00")):
            raise ValueError(f"Shell expansion is forbidden on local environment line {number}")
        values[key] = value
    legacy = values.pop("FREYA_KEY", None)
    if legacy:
        if values.get("FREYA_API_KEY") and values["FREYA_API_KEY"] != legacy:
            raise ValueError("Conflicting local Freya key declarations")
        values.setdefault("FREYA_API_KEY", legacy)
    return values


def load_local_environment(path: Path, environ: MutableMapping[str, str] | None = None) -> tuple[str, ...]:
    """Existing process variables win; absent files are allowed for offline use."""
    target = os.environ if environ is None else environ
    values = read_local_environment(path)
    loaded = []
    for key, value in values.items():
        if key not in target:
            target[key] = value
            loaded.append(key)
    return tuple(loaded)

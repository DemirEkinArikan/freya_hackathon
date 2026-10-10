"""Strict authored-value comparison with scoped Freya serialization defaults.

These defaults are reviewed from the synthetic T03 staging readback, not learned
from a response at runtime. Unknown additions fail closed. This is a comparison
of validated workflows, not a replacement for guarded compilation/validation.
"""
from __future__ import annotations

from .platform import AGENT_LLM
from .safety import canonical_json

# Common node serialization fields. Values, types and location all matter.
_NODE_DEFAULTS = {
    "assertions": [], "extraction_schema": [], "tools": [], "pre_actions": [],
    "response_mapping": [], "role_message": "", "task_message": "",
    "interruptible": True, "max_node_turns": None, "max_turn_transition": None,
    "on_enter_first_entry_only": False, "passthrough": False,
    "passthrough_default_transition": None, "return_to_previous_on_interrupt": False,
    "suppress_tool_results": False, "disable_inactivity_monitor": False,
    "truncation_strategy": "keep_all", "truncation_window_size": 10,
}
# Only node types present in retained live evidence may acquire these defaults.
_OBSERVED_NODE_TYPES = {"conversation", "end_call"}


class ReadbackMismatch(ValueError):
    """Stored data is not the authenticated workflow plus reviewed defaults."""


def _defaults(path: tuple, expected: dict, authored: dict) -> dict:
    if not path:
        return {"global_nodes": {}, "test_variables": {}}
    if len(path) < 2 or path[0] != "nodes":
        return {}
    node = authored.get("nodes", {}).get(path[1], {})
    if node.get("node_type") not in _OBSERVED_NODE_TYPES:
        return {}
    if len(path) == 2:
        return _NODE_DEFAULTS
    if (len(path) == 5 and path[2] == "transitions" and type(path[3]) is int
            and path[4] == "condition" and expected.get("operator") == "matches_intent"):
        return {"mode": "exact"}
    if (path[2:] == ("terminal", "function") and node["node_type"] == "end_call"
            and expected.get("handler") == "__function__:end_call"):
        return {"description": ""}
    return {}


def verify_readback(authored: dict, stored: dict) -> int:
    """Raise on drift; return the number of accepted default additions.

    Neither input is mutated. Adapter-owned root agent_config retains its existing
    boundary: require the event model, but do not normalize voice/call settings.
    If authored explicitly includes agent_config, every authored value is checked.
    No workflow subtree is removed before comparison. Missing keys, list changes,
    nonfinite numbers and type changes (including bool/int) are always rejected.
    """
    if type(authored) is not dict or type(stored) is not dict:
        raise ReadbackMismatch("Deployed definition must be a workflow object")
    canonical_json(authored)
    canonical_json(stored)
    config = stored.get("agent_config")
    llm = config.get("llmConfig") if type(config) is dict else None
    if type(llm) is not dict or any(llm.get(k) != v for k, v in AGENT_LLM.items()):
        raise ReadbackMismatch("Deployed definition lacks the verified fixed event model")

    def fail(path, reason):
        # Values (especially controller-bound credentials/code) never enter errors.
        pointer = "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in path)
        raise ReadbackMismatch("Deployed definition differs from the controller-authenticated guarded workflow: "
                               + reason + " at " + pointer[:240])

    def compare(expected, actual, path=()):
        if type(expected) is not type(actual):
            fail(path, "type changed")
        count = 0
        if isinstance(expected, dict):
            for key, value in expected.items():
                if key not in actual:
                    fail((*path, key), "authored field missing")
                count += compare(value, actual[key], (*path, key))
            defaults = _defaults(path, expected, authored)
            for key in actual.keys() - expected.keys():
                if not path and key == "agent_config":
                    continue
                if key not in defaults or canonical_json(actual[key]) != canonical_json(defaults[key]):
                    fail((*path, key), "unreviewed addition")
                count += 1
        elif isinstance(expected, list):
            if len(expected) != len(actual):
                fail(path, "list length changed")
            for index, (a, b) in enumerate(zip(expected, actual)):
                count += compare(a, b, (*path, index))
        elif expected != actual:
            fail(path, "authored value changed")
        return count

    return compare(authored, stored)

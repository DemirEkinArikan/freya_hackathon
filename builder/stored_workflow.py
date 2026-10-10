"""Compare stored workflows without accepting arbitrary platform additions.

The live v3 serializer fills optional fields. Only known defaults at their exact
structural locations may be absent from the authored graph; authored values and
list order must survive unchanged. Model configuration is verified separately
by the platform adapter and must be removed by the caller before comparison.
"""
from __future__ import annotations

from .safety import canonical_json


_TOP_DEFAULTS = {"test_variables": {}, "global_nodes": {}, "tools": []}
_NODE_DEFAULTS = {
    "assertions": [], "extraction_schema": [], "tools": [], "pre_actions": [],
    "response_mapping": [], "role_message": "", "task_message": "",
    "on_enter_first_entry_only": False, "passthrough": False,
    "passthrough_default_transition": None, "max_turn_transition": None,
    "disable_inactivity_monitor": False, "interruptible": True,
    "return_to_previous_on_interrupt": False, "suppress_tool_results": False,
    "truncation_strategy": "keep_all", "truncation_window_size": 10,
}


def _default(path: tuple, key: str, parent: dict) -> tuple[bool, object]:
    if not path:
        return key in _TOP_DEFAULTS, _TOP_DEFAULTS.get(key)
    if len(path) == 2 and path[0] == "nodes":
        if key == "max_node_turns":
            if parent.get("node_type") in {"end_call", "code", "api_request"}:
                return True, None
            if (parent.get("node_type") == "conversation"
                    and parent.get("max_turn_transition") is None):
                # Observed live serialization of an omitted cap is null. The
                # kit documents an engine default of 10; null is not a claim
                # of unlimited turns, and must not erase an authored fallback.
                return True, None
        return key in _NODE_DEFAULTS, _NODE_DEFAULTS.get(key)
    if (len(path) == 5 and path[0] == "nodes" and path[2] == "transitions"
            and type(path[3]) is int and path[4] == "condition" and key == "mode"):
        return True, "exact"
    if (len(path) == 7 and path[0] == "nodes" and path[2] == "transitions"
            and type(path[3]) is int and path[4:6] == ("condition", "conditions")
            and type(path[6]) is int and key == "mode"
            and parent.get("operator") == "equals"
            and isinstance(parent.get("variable"), str) and parent["variable"]
            and "operand" in parent):
        # Observed on immediate equality leaves of transition groups. Keep
        # deeper groups, other operators and unrelated paths fail-closed.
        return True, "exact"
    if key == "description" and path[0] == "nodes":
        if len(path) == 4 and path[2] == "response_mapping" and type(path[3]) is int:
            return True, ""
        if len(path) == 4 and path[2:] == ("terminal", "function"):
            return True, ""
        if (len(path) == 5 and path[2] in {"tools", "pre_actions"}
                and type(path[3]) is int and path[4] == "function"):
            return True, ""
    return False, None


def assert_equivalent_stored_workflow(stored: dict, authored: dict) -> None:
    """Raise when readback changes an authored value or adds an unknown field."""
    def fail(path):
        # Retain field locations only, never values such as credentials or code.
        location = ".".join(str(part) for part in path) or "workflow"
        raise ValueError("Stored workflow differs from authored workflow at " + location)

    def compare(actual, expected, path=()):
        if type(actual) is not type(expected):
            fail(path)
        if isinstance(expected, dict):
            for key, value in expected.items():
                if key not in actual:
                    fail((*path, key))
                compare(actual[key], value, (*path, key))
            for key in actual.keys() - expected.keys():
                permitted, value = _default(path, key, expected)
                if not permitted or canonical_json(actual[key]) != canonical_json(value):
                    fail((*path, key))
        elif isinstance(expected, list):
            if len(actual) != len(expected):
                fail(path)
            for index, value in enumerate(expected):
                compare(actual[index], value, (*path, index))
        elif canonical_json(actual) != canonical_json(expected):
            fail(path)

    if not isinstance(stored, dict) or not isinstance(authored, dict):
        fail(())
    compare(stored, authored)

import copy
import json
from pathlib import Path
import unittest

from builder.stored_workflow import assert_equivalent_stored_workflow


class StoredWorkflowTests(unittest.TestCase):
    def setUp(self):
        fixtures = Path(__file__).parent / "fixtures/platform-readback"
        self.authored = json.loads((fixtures / "authored.json").read_text())
        self.stored = json.loads((fixtures / "stored.json").read_text())

    def test_live_serializer_defaults_do_not_change_the_authored_definition(self):
        before = copy.deepcopy(self.stored)
        assert_equivalent_stored_workflow(self.stored, self.authored)
        self.assertEqual(self.stored, before)
        assert_equivalent_stored_workflow(self.authored, self.authored)

    def test_live_code_and_api_serializer_defaults_preserve_actions(self):
        fixtures = Path(__file__).parent / "fixtures/platform-readback"
        authored = json.loads((fixtures / "actions-authored.json").read_text())
        stored = json.loads((fixtures / "actions-stored.json").read_text())
        assert_equivalent_stored_workflow(stored, authored)
        for name, node in stored["nodes"].items():
            if node["node_type"] in {"code", "api_request"}:
                mutated = copy.deepcopy(stored)
                mutated["nodes"][name]["max_node_turns"] = 10
                with self.assertRaises(ValueError):
                    assert_equivalent_stored_workflow(mutated, authored)
                action_mutation = copy.deepcopy(stored)
                action_mutation["nodes"][name]["pre_actions"] = []
                with self.assertRaises(ValueError):
                    assert_equivalent_stored_workflow(action_mutation, authored)

    def test_unset_conversation_cap_accepts_only_observed_null_without_fallback(self):
        authored = copy.deepcopy(self.authored)
        del authored["nodes"]["start"]["max_node_turns"]
        del authored["nodes"]["start"]["max_turn_transition"]
        stored = copy.deepcopy(self.stored)
        stored["nodes"]["start"]["max_node_turns"] = None
        stored["nodes"]["start"]["max_turn_transition"] = None
        assert_equivalent_stored_workflow(stored, authored)
        for value in (10, 0, False, "10", "", 10.0):
            with self.subTest(value=value):
                stored["nodes"]["start"]["max_node_turns"] = value
                with self.assertRaises(ValueError):
                    assert_equivalent_stored_workflow(stored, authored)

    def test_added_null_cap_cannot_supply_or_erase_authored_fallback(self):
        authored = copy.deepcopy(self.authored)
        del authored["nodes"]["start"]["max_node_turns"]
        stored = copy.deepcopy(self.stored)
        stored["nodes"]["start"]["max_node_turns"] = None
        with self.assertRaises(ValueError):
            assert_equivalent_stored_workflow(stored, authored)
        stored["nodes"]["start"]["max_turn_transition"] = None
        with self.assertRaises(ValueError):
            assert_equivalent_stored_workflow(stored, authored)

    def test_null_cap_at_unknown_node_type_or_wrong_path_is_rejected(self):
        authored = copy.deepcopy(self.authored)
        del authored["nodes"]["start"]["max_node_turns"]
        del authored["nodes"]["start"]["max_turn_transition"]
        for node_type in ("unknown", None, "conversation-like"):
            with self.subTest(node_type=node_type):
                authored["nodes"]["start"]["node_type"] = node_type
                stored = copy.deepcopy(authored)
                stored["nodes"]["start"]["max_node_turns"] = None
                with self.assertRaises(ValueError):
                    assert_equivalent_stored_workflow(stored, authored)
        authored["nodes"]["start"]["node_type"] = "conversation"
        for path in ((), ("nodes",), ("nodes", "start", "on_enter")):
            stored = copy.deepcopy(authored)
            target = stored
            for key in path:
                target = target[key]
            target["max_node_turns"] = None
            with self.subTest(path=path), self.assertRaises(ValueError):
                assert_equivalent_stored_workflow(stored, authored)

    def _grouped_authored(self):
        authored = copy.deepcopy(self.authored)
        authored["nodes"]["start"]["transitions"][0]["condition"] = {
            "logic": "or", "conditions": [
                {"variable": "synthetic_route", "operator": "equals", "operand": "ready"},
                {"variable": "synthetic_counter", "operator": "equals", "operand": 0},
            ]}
        return authored

    def test_immediate_grouped_equality_accepts_only_exact_default_immutably(self):
        authored = self._grouped_authored()
        stored = copy.deepcopy(authored)
        leaves = stored["nodes"]["start"]["transitions"][0]["condition"]["conditions"]
        for leaf in leaves:
            leaf["mode"] = "exact"
        before = copy.deepcopy(stored)
        assert_equivalent_stored_workflow(stored, authored)
        self.assertEqual(stored, before)
        for value in ("contains", "EXACT", None, False, 0, {}):
            with self.subTest(value=value):
                mutated = copy.deepcopy(stored)
                mutated["nodes"]["start"]["transitions"][0]["condition"]["conditions"][0]["mode"] = value
                with self.assertRaises(ValueError):
                    assert_equivalent_stored_workflow(mutated, authored)

    def test_grouped_default_preserves_authored_mode_operands_and_order(self):
        authored = self._grouped_authored()
        leaves = authored["nodes"]["start"]["transitions"][0]["condition"]["conditions"]
        leaves[0]["mode"] = "contains"
        stored = copy.deepcopy(authored)
        stored["nodes"]["start"]["transitions"][0]["condition"]["conditions"][1]["mode"] = "exact"
        assert_equivalent_stored_workflow(stored, authored)
        for mutate in (
                lambda leaves: leaves[0].update(mode="exact"),
                lambda leaves: leaves[0].update(operand="replacement"),
                lambda leaves: leaves[0].update(variable="replacement"),
                lambda leaves: leaves.reverse()):
            mutated = copy.deepcopy(stored)
            mutate(mutated["nodes"]["start"]["transitions"][0]["condition"]["conditions"])
            with self.assertRaises(ValueError):
                assert_equivalent_stored_workflow(mutated, authored)

    def test_grouped_default_requires_immediate_equality_leaf_at_transition(self):
        for leaf in ({"variable": "synthetic_route", "operator": "is_set"},
                     {"variable": "synthetic_counter", "operator": "greater_than", "operand": 0},
                     {"operator": "equals", "operand": "ready"},
                     {"variable": "synthetic_route", "operator": "equals"},
                     {"variable": "", "operator": "equals", "operand": "ready"}):
            authored = self._grouped_authored()
            authored["nodes"]["start"]["transitions"][0]["condition"]["conditions"] = [leaf]
            stored = copy.deepcopy(authored)
            stored["nodes"]["start"]["transitions"][0]["condition"]["conditions"][0]["mode"] = "exact"
            with self.subTest(leaf=leaf), self.assertRaises(ValueError):
                assert_equivalent_stored_workflow(stored, authored)
        authored = self._grouped_authored()
        group = authored["nodes"]["start"]["transitions"][0]["condition"]
        group["conditions"] = [{"logic": "and", "conditions": group["conditions"]}]
        stored = copy.deepcopy(authored)
        stored["nodes"]["start"]["transitions"][0]["condition"]["conditions"][0]["conditions"][0]["mode"] = "exact"
        with self.assertRaises(ValueError):
            assert_equivalent_stored_workflow(stored, authored)
        authored = copy.deepcopy(self.authored)
        authored["nodes"]["start"]["on_enter"]["conditions"] = [
            {"variable": "synthetic_route", "operator": "equals", "operand": "ready"}]
        stored = copy.deepcopy(authored)
        stored["nodes"]["start"]["on_enter"]["conditions"][0]["mode"] = "exact"
        with self.assertRaises(ValueError):
            assert_equivalent_stored_workflow(stored, authored)

    def test_unknown_addition_is_rejected_even_when_empty(self):
        for obj in (self.stored, self.stored["nodes"]["start"],
                    self.stored["nodes"]["start"]["on_enter"]):
            with self.subTest(obj=obj):
                obj["unexpected_field"] = []
                with self.assertRaises(ValueError):
                    assert_equivalent_stored_workflow(self.stored, self.authored)
                del obj["unexpected_field"]

    def test_added_action_or_variable_is_rejected(self):
        mutations = (
            lambda s: s["nodes"]["start"].update(pre_actions=[{"function": {"handler": "unsafe"}}]),
            lambda s: s["test_variables"].update(customer_id="unexpected"),
            lambda s: s["nodes"].update(injected={"name": "injected", "node_type": "end_call"}),
            lambda s: s["nodes"]["start"].update(extraction_schema=[{"name": "bg_state"}]),
        )
        for mutate in mutations:
            stored = copy.deepcopy(self.stored)
            mutate(stored)
            with self.assertRaises(ValueError):
                assert_equivalent_stored_workflow(stored, self.authored)

    def test_modified_prompt_route_entry_or_terminal_is_rejected(self):
        mutations = (
            lambda s: s.update(system_prompt="replacement"),
            lambda s: s["nodes"]["start"]["transitions"][0].update(target_node="start"),
            lambda s: s["nodes"]["start"]["on_enter"].update(text="replacement"),
            lambda s: s["nodes"]["finish"]["terminal"]["function"].update(handler="replacement"),
            lambda s: s["nodes"]["start"].update(max_node_turns=2),
        )
        for mutate in mutations:
            stored = copy.deepcopy(self.stored)
            mutate(stored)
            with self.assertRaises(ValueError):
                assert_equivalent_stored_workflow(stored, self.authored)

    def test_nondefault_extra_value_or_wrong_type_is_rejected(self):
        for key, value in (("passthrough", True), ("interruptible", 1),
                           ("truncation_window_size", 10.0), ("role_message", "override")):
            with self.subTest(key=key):
                stored = copy.deepcopy(self.stored)
                stored["nodes"]["start"][key] = value
                with self.assertRaises(ValueError):
                    assert_equivalent_stored_workflow(stored, self.authored)

    def test_integral_float_read_back_as_int_is_the_same_authored_number(self):
        authored = {"initial_node": "start", "nodes": {"start": {"name": "start", "min_confidence": 1.0, "retries": 2}}}
        for value, equivalent in ((1, True), (1.0, True), (2, False), (0.9, False), (True, False), ("1", False), (None, False)):
            stored = copy.deepcopy(authored)
            stored["nodes"]["start"]["min_confidence"] = value
            with self.subTest(value=value):
                if equivalent:
                    assert_equivalent_stored_workflow(stored, authored)
                else:
                    with self.assertRaises(ValueError):
                        assert_equivalent_stored_workflow(stored, authored)
        stored = copy.deepcopy(authored)
        stored["nodes"]["start"]["retries"] = True
        with self.assertRaises(ValueError):
            assert_equivalent_stored_workflow(stored, authored)

    def test_default_at_wrong_path_is_rejected(self):
        self.stored["nodes"]["start"]["on_enter"]["passthrough"] = False
        with self.assertRaises(ValueError):
            assert_equivalent_stored_workflow(self.stored, self.authored)

    def test_missing_authored_value_is_rejected_without_disclosing_values(self):
        del self.stored["nodes"]["start"]["task_message"]
        with self.assertRaises(ValueError) as result:
            assert_equivalent_stored_workflow(self.stored, self.authored)
        self.assertNotIn(self.authored["nodes"]["start"]["task_message"], str(result.exception))

    def test_list_order_and_nested_code_or_headers_cannot_change(self):
        authored = copy.deepcopy(self.authored)
        authored["nodes"]["start"]["pre_actions"] = [
            {"function": {"handler": "code_exec", "code": "vars['ready'] = True"}},
            {"function": {"handler": "api", "headers": {"Authorization": "synthetic-secret"}}},
        ]
        stored = copy.deepcopy(authored)
        stored["nodes"]["start"]["pre_actions"][0]["function"]["description"] = ""
        assert_equivalent_stored_workflow(stored, authored)
        stored["nodes"]["start"]["pre_actions"].reverse()
        with self.assertRaises(ValueError):
            assert_equivalent_stored_workflow(stored, authored)
        stored = copy.deepcopy(authored)
        stored["nodes"]["start"]["pre_actions"][1]["function"]["headers"]["Authorization"] = "replacement"
        with self.assertRaises(ValueError) as result:
            assert_equivalent_stored_workflow(stored, authored)
        self.assertNotIn("synthetic-secret", str(result.exception))


if __name__ == "__main__":
    unittest.main()

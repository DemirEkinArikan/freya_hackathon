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

    def test_conversation_turn_cap_defaults_require_explicit_authored_value(self):
        authored = copy.deepcopy(self.authored)
        del authored["nodes"]["start"]["max_node_turns"]
        stored = copy.deepcopy(self.stored)
        stored["nodes"]["start"]["max_node_turns"] = None
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

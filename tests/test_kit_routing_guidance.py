"""Kit declarations and sealed transport; no platform timing/speech claims."""
from copy import deepcopy
import json
import unittest

from builder.build import make_workflow_prompt
from builder.guards import GuardCompileError, compile_guarded_workflow, required_execution_kinds
from builder.validation import validate_workflow
from test_source_dialogue_guidance import source_context, SPEECH


def context():
    frozen, api, unused_policy, unused_sources = source_context()
    return frozen, api


def transition(target, *, tool=False):
    value = {"target_node": target, "description": "Complete the source-required ending and close.",
             "condition": {"operator": "matches_intent", "description": "The assistant completed its required ending."}
             if tool else {"operator": "always"}}
    if tool:
        value["is_tool_call"] = True
    return value


class KitRoutingGuidanceTests(unittest.TestCase):
    def test_approved_prompt_preserves_contract_and_documented_event_locations(self):
        frozen, api = context()
        before = deepcopy(frozen["contract"])
        header, data = make_workflow_prompt(frozen["contract"], api).split("\n\nAUTHORIZED INPUTS:\n", 1)
        payload = json.loads(data)
        self.assertEqual(payload["policy_contract_sha256"], before["sha256"])
        self.assertEqual(payload["active_rules"], {kind: rule["value"] for kind, rule in before["active_rules"].items()})
        self.assertEqual(frozen["contract"], before)
        self.assertIn("node-level passthrough:true", header)
        self.assertIn("transition-level is_tool_call:true", header)
        self.assertIn("do not hardcode", header)
        self.assertIn("caller-answer node", header)
        self.assertIn("passthrough_default_transition", payload["runtime_reference"])
        self.assertIn("not evaluate that tool transition", payload["runtime_reference"])

    def test_silent_decision_and_fixed_ending_declarations_survive_protected_gate_remapping(self):
        frozen, api = context()
        talk = frozen["plan"]["nodes"]["talk"]
        talk.update(on_enter={"type": "silent"}, passthrough=True,
            passthrough_default_transition="farewell", transitions=[transition("farewell")],
            max_turn_transition="farewell", routing_prompt="Use only the current approved view: {{bg_facts}}.")
        frozen["plan"]["nodes"]["farewell"] = {"name": "farewell", "node_type": "conversation",
            "task_message": "Complete the source-required ending.", "on_enter": {"type": "tts", "text": SPEECH["farewell"]},
            "passthrough": True, "passthrough_default_transition": "close", "transitions": [transition("close")]}
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, guard_context=frozen), [])
        for name, authored_target in (("talk", "farewell"), ("farewell", "close")):
            node = workflow["nodes"][name]
            self.assertIs(node["passthrough"], True)
            target = node["passthrough_default_transition"]
            self.assertEqual(target, node["transitions"][-1]["target_node"])
            self.assertEqual(node["transitions"][-1]["condition"], {"operator": "always"})
            self.assertEqual(workflow["nodes"][target]["node_type"], "code")
            self.assertTrue(any(edge["target_node"] == authored_target for edge in workflow["nodes"][target]["transitions"]))
        self.assertEqual(workflow["nodes"]["farewell"]["on_enter"]["text"], SPEECH["farewell"])
        self.assertEqual(workflow["nodes"]["close"]["node_type"], "code")

    def test_question_node_keeps_waiting_declaration_and_fresh_public_extraction(self):
        frozen, api = context()
        talk = frozen["plan"]["nodes"]["talk"]
        talk.update(passthrough=False, on_enter={"type": "tts", "text": "Are you available to speak?"},
            extraction_schema=[{"name": "availability_answer", "type": "string", "min_confidence": 0.95}],
            transitions=[{"target_node": "close", "description": "The caller has answered.",
                          "condition": {"operator": "matches_intent", "description": "A new availability answer was received."}}])
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, guard_context=frozen), [])
        self.assertIs(workflow["nodes"]["talk"]["passthrough"], False)
        self.assertNotIn("passthrough_default_transition", workflow["nodes"]["talk"])
        self.assertEqual(workflow["nodes"]["talk"]["extraction_schema"], talk["extraction_schema"])

    def test_llm_completion_tool_flag_is_transition_level_and_does_not_hardcode_pre_gate_name(self):
        frozen, api = context()
        talk = frozen["plan"]["nodes"]["talk"]
        talk.update(on_enter={"type": "llm"}, task_message="Say the source-required farewell, then use the offered completion transition identified by its description.",
                    transitions=[transition("close", tool=True)])
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, guard_context=frozen), [])
        node = workflow["nodes"]["talk"]
        edge = node["transitions"][0]
        self.assertIs(edge["is_tool_call"], True)
        self.assertNotIn("is_tool_call", edge["condition"])
        self.assertNotEqual(edge["target_node"], "close")
        self.assertTrue(any(item["target_node"] == "close" for item in workflow["nodes"][edge["target_node"]]["transitions"]))
        self.assertNotIn("transition_to_close", node["task_message"])
        self.assertFalse(node.get("passthrough", False))
        self.assertNotIn("tools", node)

    def test_undocumented_transition_alias_and_operator_cannot_replace_kit_flag(self):
        frozen, api = context()
        edge = frozen["plan"]["nodes"]["talk"]["transitions"][0]
        edge["is_tool_call_transition"] = True
        with self.assertRaises(GuardCompileError):
            required_execution_kinds(frozen["plan"])
        del edge["is_tool_call_transition"]
        edge["condition"] = {"operator": "is_tool_call_transition"}
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertTrue(any("unknown condition operator" in error for error in validate_workflow(workflow, api, guard_context=frozen)))


if __name__ == "__main__":
    unittest.main()

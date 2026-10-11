"""F8: a lone catch-all answer route into an LLM-entry decision compiles as always."""
import unittest

from builder.guards import compile_guarded_workflow
from builder.validation import validate_workflow
from test_kit_routing_guidance import context


def intent(target, text="Any caller answer, including unclear or unrelated responses."):
    return {"target_node": target, "description": "Process the fresh caller answer.",
            "condition": {"operator": "matches_intent", "description": text}}


def completion(target):
    return {"target_node": target, "description": "State that selects " + target + ".", "is_tool_call": True,
            "condition": {"operator": "always"}}


def decide_shape():
    frozen, api = context()
    frozen["plan"]["nodes"]["talk"].update(
        passthrough=False, on_enter={"type": "tts", "text": "Are you available to speak?"},
        extraction_schema=[{"name": "availability_answer", "type": "string", "min_confidence": 0.8}],
        transitions=[intent("decide")], routing_prompt="Route every fresh answer to decide using {{bg_facts}}.")
    frozen["plan"]["nodes"]["decide"] = {
        "name": "decide", "node_type": "conversation", "on_enter": {"type": "llm"},
        "task_message": "Nonspeaking decision: read {{bg_facts}} and take one offered completion.",
        "transitions": [completion("talk"), completion("close")]}
    return frozen, api


class HopLatencyTests(unittest.TestCase):
    def compiled(self, frozen, api):
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, guard_context=frozen), [])
        return workflow["nodes"]

    def test_lone_catch_all_into_decision_needs_no_routing_model(self):
        frozen, api = decide_shape()
        nodes = self.compiled(frozen, api)
        talk = nodes["talk"]
        self.assertEqual(talk["transitions"][-1]["condition"], {"operator": "always"})
        self.assertNotIn("is_tool_call", talk["transitions"][-1])
        self.assertNotIn("routing_prompt", talk)
        # Still the same waiting caller-answer step that extracts the fresh answer.
        self.assertIs(talk["passthrough"], False)
        self.assertEqual(talk["extraction_schema"], frozen["plan"]["nodes"]["talk"]["extraction_schema"])
        gate = talk["transitions"][-1]["target_node"]
        self.assertTrue(any(row["target_node"] == "decide" for row in nodes[gate]["transitions"]))
        self.assertTrue(all(row.get("is_tool_call") for row in nodes["decide"]["transitions"]))

    def test_other_shapes_keep_their_intent_routes(self):
        cases = {
            "no route back": lambda plan: plan["decide"].update(transitions=[completion("close")]),
            "second intent": lambda plan: plan["talk"]["transitions"].insert(0, intent("close", "The caller asks to end the call.")),
            "completion on answer": lambda plan: plan["talk"]["transitions"].insert(0, completion("close")),
            "target asks": lambda plan: plan["decide"].update(extraction_schema=[
                {"name": "decide_answer", "type": "string", "min_confidence": 0.8}]),
            "target not llm entry": lambda plan: plan["decide"].update(on_enter={"type": "silent"}),
            "target has a router row": lambda plan: plan["decide"].update(transitions=[completion("talk"), intent("close")]),
            "action target": lambda plan: plan["talk"].update(transitions=[intent("close")]) or plan.pop("decide"),
            # always would merge with this row into one unconditional row (N1).
            "earlier row to the decision": lambda plan: plan["talk"]["transitions"].insert(0, {
                "target_node": "decide", "description": "Caller is unsure.", "condition": {"logic": "or", "conditions": [
                    {"variable": "availability_answer", "operator": "equals", "operand": "maybe"}]}}),
        }
        for label, change in cases.items():
            with self.subTest(label):
                frozen, api = decide_shape()
                change(frozen["plan"]["nodes"])
                talk = self.compiled(frozen, api)["talk"]
                self.assertEqual(talk["transitions"][-1]["condition"]["operator"], "matches_intent")

    def test_deterministic_rows_before_the_catch_all_keep_their_order(self):
        frozen, api = decide_shape()
        first = {"target_node": "close", "description": "Caller declined.", "condition": {"logic": "or", "conditions": [
            {"variable": "availability_answer", "operator": "equals", "operand": "no"}]}}
        frozen["plan"]["nodes"]["talk"]["transitions"].insert(0, first)
        rows = self.compiled(frozen, api)["talk"]["transitions"]
        self.assertEqual(rows[0]["condition"], first["condition"])
        self.assertEqual(rows[1]["condition"], {"operator": "always"})


if __name__ == "__main__":
    unittest.main()

"""Hosted R3: the step that asks a question captures the caller's answer to it."""
import copy
import unittest

from test_guards import fixture
from builder.guards import compile_guarded_workflow, validate_guarded_workflow
from builder.validation import validate_workflow


def tool(target, description="completion"):
    return {"target_node": target, "description": description, "condition": {"operator": "always"}, "is_tool_call": True}


def decide_shaped():
    """An invented decide-shaped plan: a speaking question whose completion awaits a silent answer step."""
    context = fixture()
    nodes = context["plan"]["nodes"]
    listen = nodes.pop("talk")
    listen.update(name="listen", on_enter={"type": "silent"}, transitions=[{"target_node": "decide", "description": "any answer",
                  "condition": {"operator": "matches_intent", "description": "Any caller answer."}}])
    nodes["listen"] = listen
    nodes["ask"] = {"name": "ask", "node_type": "conversation", "task_message": "Ask the next question from {{bg_facts}}.",
                    "transitions": [tool("listen", "question asked; await the answer"), tool("close", "nothing to ask")]}
    nodes["opening"] = {"name": "opening", "node_type": "conversation", "on_enter": {"type": "tts", "text": "Hello."},
                        "passthrough": True, "passthrough_default_transition": "listen", "transitions": [
                            {"target_node": "listen", "description": "await", "condition": {"operator": "always"}}]}
    actions = ("identity", "account", "stage", "commit", "note", "finish", "transfer", "handoff", "callback", "close")
    nodes["decide"] = {"name": "decide", "node_type": "conversation", "task_message": "Take one completion.",
                       "transitions": [tool("ask")] + [tool(name) for name in actions]}
    for name in actions:
        nodes[name].update(success_node="decide", blocked_node="decide", failure_node="decide")
    context["plan"]["initial_node"] = "opening"
    return context


class AskAndCaptureTests(unittest.TestCase):
    def compile(self, context):
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(validate_guarded_workflow(workflow, **context), [])
        self.assertEqual(validate_workflow(workflow, context["openapi"]), [])
        nodes = workflow["nodes"]
        enters = lambda target: nodes[target]["transitions"][0]["target_node"] if target.startswith("bg_gate_") else target
        return nodes, enters

    def test_question_step_extracts_and_completes_to_the_answer_exit(self):
        context = decide_shaped()
        nodes, enters = self.compile(context)
        self.assertEqual(nodes["ask"]["extraction_schema"], nodes["listen"]["extraction_schema"])
        self.assertEqual([enters(route["target_node"]) for route in nodes["ask"]["transitions"] if route.get("is_tool_call")],
                         ["decide", "close"])
        # The answer step and the passthrough opening into it are unchanged.
        self.assertEqual(enters(nodes["opening"]["transitions"][-1]["target_node"]), "listen")
        self.assertEqual(enters(nodes["listen"]["transitions"][0]["target_node"]), "decide")
        self.assertNotIn("extraction_schema", context["plan"]["nodes"]["ask"])

    def test_no_reply_relay_into_a_step_that_asks_its_own_question(self):
        for authored, relayed in ((False, False), (True, True)):
            with self.subTest(authored_answer_step=authored):
                context = decide_shaped()
                nodes = context["plan"]["nodes"]
                if authored:
                    nodes["ask"]["extraction_schema"] = copy.deepcopy(nodes["listen"]["extraction_schema"])
                nodes["explain"] = {"name": "explain", "node_type": "conversation", "task_message": "Explain the batch.",
                                    "transitions": [tool("commit"), tool("ask")]}
                nodes["decide"]["transitions"].append(tool("explain"))
                compiled, _ = self.compile(context)
                self.assertEqual("bg_explain_reply" in compiled, relayed)

    def test_other_shapes_are_left_alone(self):
        def silent_answer_with_two_exits(nodes):
            nodes["listen"]["transitions"].insert(0, {"target_node": "close", "description": "stop", "condition": {
                "logic": "or", "conditions": [{"variable": "answer_text", "operator": "equals", "operand": "stop"}]}})

        def speaking_answer(nodes):
            nodes["listen"]["on_enter"] = {"type": "llm"}

        def asking_step_extracts(nodes):
            nodes["ask"]["extraction_schema"] = [{"name": "note_reason", "type": "string", "min_confidence": 0.9}]

        def fixed_question_line(nodes):
            nodes["ask"]["on_enter"] = {"type": "tts", "text": "Which one?"}

        def two_answer_steps(nodes):
            nodes["listen_too"] = {**copy.deepcopy(nodes["listen"]), "name": "listen_too"}
            nodes["ask"]["transitions"].append(tool("listen_too"))

        for change in (silent_answer_with_two_exits, speaking_answer, asking_step_extracts, fixed_question_line, two_answer_steps):
            with self.subTest(change.__name__):
                context = decide_shaped()
                change(context["plan"]["nodes"])
                nodes, enters = self.compile(context)
                names = lambda node: [entry["name"] for entry in node.get("extraction_schema", [])]
                self.assertEqual(names(nodes["ask"]), names(context["plan"]["nodes"]["ask"]))
                self.assertIn("listen", [enters(route["target_node"]) for route in nodes["ask"]["transitions"]])


if __name__ == "__main__":
    unittest.main()

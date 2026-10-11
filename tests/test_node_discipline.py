"""Hosted F5/N6: speaking nodes carry the kit's tool rules; completion-only steps capture no answer."""
from pathlib import Path
import unittest

from builder.guards import compile_guarded_workflow, validate_guarded_workflow
from builder.validation import validate_workflow
from test_guards import fixture

NO_TOOL = "No tool call is allowed in this turn."
COMPLETION = "The only tool call allowed in this turn is one offered completion."
NO_EMPTY_TURN = "never end a turn with neither words nor a tool call"
CAPTURES_NOTHING = "Nothing in this step can capture a caller answer"


def completion(target):
    return {"target_node": target, "description": "go to " + target, "condition": {"operator": "always"}, "is_tool_call": True}


class NodeDisciplineTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        nodes = self.context["plan"]["nodes"]
        # A recovery step shaped like the hosted public-B one: LLM entry, no extraction, only completions.
        nodes["recovery"] = {"name": "recovery", "node_type": "conversation", "on_enter": {"type": "llm"},
                             "task_message": "Explain the latest action result honestly.",
                             "transitions": [completion("talk"), completion("close")]}
        nodes["farewell"] = {"name": "farewell", "node_type": "conversation", "on_enter": {"type": "llm"},
                             "task_message": "Thank the caller and say goodbye.", "transitions": [completion("close")]}
        nodes["notice"] = {"name": "notice", "node_type": "conversation", "on_enter": {"type": "tts", "text": "Goodbye."},
                           "task_message": "Fixed line.", "passthrough": True, "passthrough_default_transition": "close",
                           "transitions": [{"target_node": "close", "description": "always", "condition": {"operator": "always"}}]}
        nodes["account"]["blocked_node"] = "recovery"
        nodes["finish"]["success_node"] = "farewell"
        nodes["callback"]["success_node"] = "notice"

    def compile(self):
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        self.assertEqual(validate_guarded_workflow(workflow, **self.context), [])
        self.assertEqual(validate_workflow(workflow, self.context["openapi"]), [])
        return workflow["nodes"]

    def test_every_speaking_node_carries_the_kit_tool_rule(self):
        nodes = self.compile()
        talk = nodes["talk"]["task_message"]
        self.assertTrue(talk.startswith("Discuss permitted choices.\n\n" + NO_TOOL))
        self.assertIn("never a tool call, a variable name or your reasoning", talk)
        self.assertNotIn(CAPTURES_NOTHING, talk)
        for name in ("recovery", "farewell"):
            text = nodes[name]["task_message"]
            self.assertIn(COMPLETION, text)
            self.assertIn(NO_EMPTY_TURN, text)  # N6: no turn without speech or a tool call
            self.assertIn(CAPTURES_NOTHING, text)
            self.assertNotIn(NO_TOOL, text)
        # A fixed passthrough line runs no dialogue model and stays unchanged.
        self.assertEqual(nodes["notice"]["task_message"], "Fixed line.")
        # Compiler-owned confirmation wording is not authored and stays unchanged.
        self.assertNotIn(NO_TOOL, nodes["bg_commit_confirm"]["task_message"])

    def test_reply_in_a_completion_only_step_returns_to_the_speaking_answer_step(self):
        nodes = self.compile()
        routes = nodes["recovery"]["transitions"]
        gate = routes[0]["target_node"]
        self.assertTrue(gate.startswith("bg_gate_"))
        self.assertEqual(nodes[gate]["transitions"][0]["target_node"], "talk")
        self.assertEqual([route.get("is_tool_call") for route in routes], [True, True, None])
        self.assertEqual(routes[-1], {"target_node": "bg_recovery_reply", "condition": {"operator": "always"},
                                      "description": "caller replied in a step that captures no answer"})
        relay = nodes["bg_recovery_reply"]
        self.assertEqual((relay["on_enter"], relay["passthrough"], relay["passthrough_default_transition"]),
                         ({"type": "silent"}, True, gate))
        self.assertEqual([route["target_node"] for route in relay["transitions"]], [gate])
        # The farewell has no caller-answer target, so nothing is added to its routes.
        self.assertNotIn("bg_farewell_reply", nodes)
        self.assertEqual(len(nodes["farewell"]["transitions"]), 1)

    def test_no_relay_without_exactly_one_speaking_answer_step(self):
        plan = self.context["plan"]["nodes"]
        plan["listen"] = {"name": "listen", "node_type": "conversation", "on_enter": {"type": "silent"},
                          "task_message": "Wait for the answer.",
                          "extraction_schema": [{"name": "answer_text", "type": "string", "min_confidence": 0.9}],
                          "transitions": [completion("close")]}
        plan["recovery"]["transitions"] = [completion("listen"), completion("close")]
        self.assertNotIn("bg_recovery_reply", self.compile())  # a silent answer step would wait for a reply already given
        plan["ask"] = {**plan["listen"], "name": "ask", "on_enter": {"type": "llm"}}
        plan["recovery"]["transitions"] = [completion("talk"), completion("ask"), completion("listen"), completion("close")]
        self.assertNotIn("bg_recovery_reply", self.compile())  # two answer steps: no guess

    def test_plan_prompt_keeps_step_procedure_out_of_the_system_prompt(self):
        text = " ".join((Path(__file__).resolve().parents[1] / "builder/prompts/workflow.txt").read_text().split())
        self.assertIn("carries the whole system_prompt while the caller waits, so keep it short", text)
        # Deceptive callers probe these in any step, so they stay in the shared prompt.
        self.assertIn("the honesty, privacy and identity-verification rules that hold in every step", text)
        self.assertIn("Put each step's procedure, counts, offers and choices in that step's task_message", text)
        self.assertIn("recording notice as its own sentence about the call, never beside a statement that an action was recorded", text)


if __name__ == "__main__":
    unittest.main()

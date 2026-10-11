"""Hosted F5/N6/K3: speaking nodes carry the kit's tool rules; a completion-only step decides a reply where it arrives."""
from pathlib import Path
import unittest

from builder.guards import compile_guarded_workflow, validate_guarded_workflow
from builder.validation import validate_workflow
from test_execution_requirements import packet
from test_guards import fixture
from test_plan_fallback import fallback_errors

NO_TOOL = "No tool call is allowed in this turn."
COMPLETION = "The only tool call allowed in this turn is one offered completion."
NO_EMPTY_TURN = "never end a turn with neither words nor a tool call"
EVERY_MESSAGE = "Invoke exactly one in every message of this step."
NO_WAIT = "Never ask a question and wait here"


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
        # A speaking answer step that also offers a completion keeps the kit's N6 line.
        nodes["query"] = {"name": "query", "node_type": "conversation", "on_enter": {"type": "llm"},
                          "task_message": "Ask for the missing detail.",
                          "extraction_schema": [{"name": "detail_text", "type": "string", "min_confidence": 0.9}],
                          "transitions": [completion("close"),
                                          {"target_node": "talk", "description": "other topic",
                                           "condition": {"operator": "matches_intent", "description": "Any other topic."}}]}
        nodes["notice"] = {"name": "notice", "node_type": "conversation", "on_enter": {"type": "tts", "text": "Goodbye."},
                           "task_message": "Fixed line.", "passthrough": True, "passthrough_default_transition": "close",
                           "transitions": [{"target_node": "close", "description": "always", "condition": {"operator": "always"}}]}
        nodes["account"]["blocked_node"] = "recovery"
        nodes["finish"]["success_node"] = "farewell"
        nodes["callback"]["success_node"] = "notice"
        nodes["note"]["blocked_node"] = "query"

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
        self.assertNotIn(NO_WAIT, talk)
        query = nodes["query"]["task_message"]
        self.assertIn(COMPLETION + " Call at most one tool per message", query)
        self.assertIn(NO_EMPTY_TURN, query)  # N6: no turn without speech or a tool call
        self.assertNotIn(NO_WAIT, query)
        for name in ("recovery", "farewell"):
            text = nodes[name]["task_message"]
            self.assertIn(COMPLETION + " " + EVERY_MESSAGE, text)
            # K3: a step that leaves on its completion never gets the speak-after-tool line.
            self.assertNotIn("speak after every tool result", text)
            self.assertNotIn(NO_EMPTY_TURN, text)
            self.assertIn(NO_WAIT, text)
            self.assertNotIn(NO_TOOL, text)
        # A fixed passthrough line runs no dialogue model and stays unchanged.
        self.assertEqual(nodes["notice"]["task_message"], "Fixed line.")
        # Compiler-owned confirmation wording is not authored and stays unchanged.
        self.assertNotIn(NO_TOOL, nodes["bg_commit_confirm"]["task_message"])

    def test_reply_in_a_completion_only_step_is_extracted_and_decided_there(self):
        nodes = self.compile()
        routes = nodes["recovery"]["transitions"]
        gate = routes[0]["target_node"]
        self.assertTrue(gate.startswith("bg_gate_"))
        self.assertEqual(nodes[gate]["transitions"][0]["target_node"], "talk")
        # K3: no relay returns the reply to talk unextracted. The step extracts it with
        # talk's schema (the kit runs extraction before routing and the dialogue model).
        self.assertEqual([route.get("is_tool_call") for route in routes], [True, True])
        self.assertEqual(nodes["recovery"]["extraction_schema"], nodes["talk"]["extraction_schema"])
        self.assertFalse([name for name in nodes if name.endswith("_reply")])
        # The farewell has no caller-answer target, so it captures nothing.
        self.assertNotIn("extraction_schema", nodes["farewell"])
        self.assertEqual(len(nodes["farewell"]["transitions"]), 1)

    def test_fallback_decision_captures_the_dialogue_answer_and_says_nothing(self):
        plan, errors, workflow = fallback_errors(*packet())
        self.assertEqual(errors, [])
        nodes = workflow["nodes"]
        decide, dialogue = nodes["decide"], nodes["dialogue"]
        self.assertEqual(decide["extraction_schema"], dialogue["extraction_schema"])
        self.assertIn("proposal_identity_answer", [entry["name"] for entry in decide["extraction_schema"]])
        self.assertTrue(all(route.get("is_tool_call") is True for route in decide["transitions"]))
        self.assertNotIn("bg_decide_reply", nodes)
        self.assertIn("say nothing to the caller", decide["task_message"])
        self.assertNotIn("speak after every tool result", decide["task_message"])
        self.assertIn(EVERY_MESSAGE, decide["task_message"])
        # F8 still sends every dialogue answer to the decision without a routing-model call.
        self.assertEqual(dialogue["transitions"][-1]["condition"], {"operator": "always"})
        gate = nodes[dialogue["transitions"][-1]["target_node"]]
        self.assertEqual(gate["transitions"][0]["target_node"], "decide")
        self.assertNotIn("extraction_schema", plan["nodes"]["decide"])  # the plan itself is unchanged

    def test_no_capture_without_exactly_one_speaking_answer_step(self):
        plan = self.context["plan"]["nodes"]
        plan["listen"] = {"name": "listen", "node_type": "conversation", "on_enter": {"type": "silent"},
                          "task_message": "Wait for the answer.",
                          "extraction_schema": [{"name": "answer_text", "type": "string", "min_confidence": 0.9}],
                          "transitions": [completion("close")]}
        plan["recovery"]["transitions"] = [completion("listen"), completion("close")]
        self.assertNotIn("extraction_schema", self.compile()["recovery"])  # a silent answer step would wait for a reply already given
        plan["ask"] = {**plan["listen"], "name": "ask", "on_enter": {"type": "llm"}}
        plan["recovery"]["transitions"] = [completion("talk"), completion("ask"), completion("listen"), completion("close")]
        self.assertNotIn("extraction_schema", self.compile()["recovery"])  # two answer steps: no guess

    def test_plan_prompt_keeps_step_procedure_out_of_the_system_prompt(self):
        text = " ".join((Path(__file__).resolve().parents[1] / "builder/prompts/workflow.txt").read_text().split())
        self.assertIn("carries the whole system_prompt while the caller waits, so keep it short", text)
        # Deceptive callers probe these in any step, so they stay in the shared prompt.
        self.assertIn("the honesty, privacy and identity-verification rules that hold in every step", text)
        self.assertIn("Put each step's procedure, counts, offers and choices in that step's task_message", text)
        self.assertIn("recording notice as its own sentence about the call, never beside a statement that an action was recorded", text)


if __name__ == "__main__":
    unittest.main()

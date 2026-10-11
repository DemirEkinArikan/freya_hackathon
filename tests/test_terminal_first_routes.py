"""A degraded hosted router must not turn an ordinary answer into a call-ending exit.

Hosted readiness (public-A, 11 Oct) matched ordinary contact answers to the first
of eight intent rows, a terminal exit. Synthetic nodes only; no model, platform,
brief or evaluator material is used.
"""
import json
import unittest

from builder.build import make_workflow_prompt
from test_kit_routing_guidance import context
from test_replay_scheduling import caller, compiled, conversation, intent, route, script
from builder.validation import validate_workflow
from tools.replay_guarded import ReplayError, replay

FIRST = "the first matches_intent route targets a call-ending exit"
GOODBYE = conversation("goodbye", [route("close")], entry="tts", passthrough="close")
LATER = conversation("later", [route("talk", intent())])


def errors_for(nodes):
    workflow, context_, api = compiled(nodes)
    return validate_workflow(workflow, api, guard_context=context_)


class TerminalFirstRouteTests(unittest.TestCase):
    def test_a_call_ending_first_intent_is_refused_and_not_replayed(self):
        nodes = {"talk": conversation("talk", [route("goodbye", intent("rare exit")), route("later", intent())]),
                 "goodbye": GOODBYE, "later": LATER}
        self.assertEqual([e.split(":", 1)[0] for e in errors_for(nodes) if FIRST in e], ["node.talk.transitions[0]"])
        workflow, context_, api = compiled(nodes)
        with self.assertRaisesRegex(ReplayError, FIRST):
            replay(workflow, context_, api, script([caller(selected=0)]))

    def test_ordinary_first_routes_a_degraded_first_row_match_to_the_ordinary_path(self):
        nodes = {"talk": conversation("talk", [route("later", intent()), route("goodbye", intent("rare exit"))]),
                 "goodbye": GOODBYE, "later": LATER}
        self.assertEqual(errors_for(nodes), [])
        workflow, context_, api = compiled(nodes)
        visited = [step["node"] for step in replay(workflow, context_, api, script([caller(selected=0)]))["steps"]]
        self.assertIn("later", visited)
        self.assertNotIn("goodbye", visited)
        # An empty or unmatched evaluation stays in the node; it selects nothing.
        report = replay(workflow, context_, api, script([caller()]))
        self.assertEqual(report["status"], "missing_conversation_frame")
        self.assertNotIn("goodbye", [step["node"] for step in report["steps"]])

    def test_catch_all_into_a_decision_step_tool_rows_and_all_ending_rows_pass(self):
        decide = conversation("decide", [route("later", tool=True), route("goodbye", tool=True)], entry="llm")
        for transitions in ([route("decide", intent("Any caller answer"))],
                            [route("goodbye", tool=True), route("later", intent())],
                            [route("goodbye", intent("rare exit")), route("close", intent())]):
            with self.subTest(transitions=transitions):
                nodes = {"talk": conversation("talk", transitions, entry="llm"), "decide": decide,
                         "goodbye": GOODBYE, "later": LATER}
                self.assertFalse([e for e in errors_for(nodes) if FIRST in e])

    def test_generator_prompt_and_reference_order_routes_for_the_hosted_router(self):
        frozen, api = context()
        header, data = make_workflow_prompt(frozen["contract"], api).split("\n\nAUTHORIZED INPUTS:\n", 1)
        reference = json.loads(data)["runtime_reference"]
        self.assertIn("never the first intent route or the", header)
        self.assertIn("nonspeaking LLM-entry decision step", header)
        self.assertIn("is never the first intent route", reference)
        self.assertIn("one catch-all intent into an LLM-entry decision step", reference)


if __name__ == "__main__":
    unittest.main()

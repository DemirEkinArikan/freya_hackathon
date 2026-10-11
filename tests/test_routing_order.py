"""Routing that list-order and deterministic-first hosts order alike.

Synthetic nodes only; no model, platform or evaluator material is used.
"""
import json
import os
import unittest
from pathlib import Path

from builder.safety import digest, read_json
from builder.validation import validate_workflow
from test_replay_scheduling import caller, compiled, conversation, equals, intent, route, script
from tools.replay_guarded import ReplayError, replay

ROOT = Path(__file__).resolve().parents[1]
RETAINED_RUN = os.environ.get("FREYA_ROUTING_ORDER_RUN", "")
ORDER = "follows matches_intent"


def errors_for(nodes):
    workflow, context, api = compiled(nodes)
    return validate_workflow(workflow, api, guard_context=context)


class RoutingOrderTests(unittest.TestCase):
    def assert_order_errors(self, nodes, expected):
        found = [error.split(":", 1)[0] for error in errors_for(nodes) if ORDER in error]
        self.assertEqual(found, expected)

    def test_intent_routes_followed_by_an_ordinary_always_are_refused(self):
        nodes = {"talk": conversation("talk", [route("later", intent()), route("close")]),
                 "later": conversation("later", [route("close")])}
        self.assert_order_errors(nodes, ["node.talk.transitions[1]"])
        workflow, context, api = compiled(nodes)
        with self.assertRaisesRegex(ReplayError, ORDER):
            replay(workflow, context, api, script([caller(selected=0)]))

    def test_deterministic_routes_listed_before_intents_route_alike(self):
        nodes = {"talk": conversation("talk", [route("close", equals("answer_text", "done")), route("later", intent())]),
                 "later": conversation("later", [route("close")])}
        self.assertEqual(errors_for(nodes), [])
        workflow, context, api = compiled(nodes)
        report = replay(workflow, context, api, script([caller(values={"answer_text": "done"}, selected=1)]))
        self.assertEqual(report["status"], "terminated")
        self.assertNotIn("later", [step["node"] for step in report["steps"]])
        report = replay(workflow, context, api, script([caller(selected=1)]))
        self.assertIn("later", [step["node"] for step in report["steps"]])
        report = replay(workflow, context, api, script([caller()]))
        self.assertEqual((report["status"], report["frames_consumed"]), ("missing_conversation_frame", 1))
        self.assertNotIn("later", [step["node"] for step in report["steps"]])

    def test_passthrough_nodes_route_deterministically(self):
        later = conversation("later", [route("close")])
        self.assert_order_errors({"talk": conversation("talk", [route("later", intent()), route("close")],
                                                       passthrough="close"), "later": later},
                                 ["node.talk.transitions[1]"])
        self.assert_order_errors({"talk": conversation("talk", [route("later", equals("now", "2031-05-02")), route("close")],
                                                       passthrough="close"), "later": later}, [])

    def test_mixed_or_after_an_intent_is_refused_but_mixed_and_is_not(self):
        later = conversation("later", [route("close")])
        mixed_or = {"logic": "or", "conditions": [equals("answer_text", "done"), intent()]}
        mixed_and = {"logic": "or", "conditions": [{"logic": "and", "conditions": [
            equals("answer_text", "done"), intent()]}]}
        self.assert_order_errors({"talk": conversation("talk", [route("later", intent()), route("close", mixed_or)]),
                                  "later": later}, ["node.talk.transitions[1]"])
        self.assert_order_errors({"talk": conversation("talk", [route("later", intent()), route("close", mixed_and)]),
                                  "later": later}, [])

    def test_same_target_routes_count_as_one_merged_route(self):
        later = conversation("later", [route("close")])
        split = [route("close", equals("answer_text", "done")), route("later", intent()), route("close", intent())]
        self.assert_order_errors({"talk": conversation("talk", split), "later": later}, ["node.talk.transitions[2]"])
        joined = [route("close", intent()), route("close", equals("answer_text", "done")), route("later", intent())]
        self.assert_order_errors({"talk": conversation("talk", joined), "later": later}, [])

    def test_tool_transitions_are_not_router_rows(self):
        later = conversation("later", [route("close")])
        nodes = {"talk": conversation("talk", [route("later", intent()), route("close", tool=True)], entry="llm"),
                 "later": later}
        self.assertEqual(errors_for(nodes), [])
        nodes["talk"]["transitions"] = [route("close", tool=True), route("later", intent())]
        self.assertEqual(errors_for(nodes), [])
        workflow, context, api = compiled(nodes)
        report = replay(workflow, context, api, script([{"node": "talk", "event": "agent_completion", "transition": 0}]))
        self.assertEqual(report["status"], "terminated")

    def test_ordinary_or_merged_unconditional_routes_still_shadow_later_routes(self):
        later = conversation("later", [route("close")])
        for transitions, error in (([route("close"), route("later", intent())], "unconditional route shadows later transitions"),
                                   ([route("close", tool=True), route("close", intent())], "is_tool_call always route shares its target")):
            with self.subTest(transitions=transitions):
                errors = errors_for({"talk": conversation("talk", transitions, entry="llm"), "later": later})
                self.assertTrue(any(e.startswith("node.talk.transitions[0]: " + error) for e in errors))


@unittest.skipUnless(RETAINED_RUN, "set FREYA_ROUTING_ORDER_RUN to a retained generated build run directory")
class RetainedCandidateRoutingReplayTests(unittest.TestCase):
    """No-model replay of a retained build's attempts; the artifacts stay untracked."""

    def test_retained_attempts_get_only_the_routing_order_rejection(self):
        run = Path(RETAINED_RUN)
        lines = (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        inputs = next(event for event in map(json.loads, lines) if event.get("event") == "inputs")
        openapi = read_json(ROOT / "build-the-builder" / "tools" / "openapi.json")
        self.assertEqual(digest(openapi), inputs["tool_api_sha256"])
        attempts = sorted((run / "attempts").glob("plan-*"))
        self.assertTrue(attempts)
        for attempt in attempts:
            with self.subTest(attempt=attempt.name):
                workflow = read_json(attempt / "workflow.json")
                # The current compiler may differ from the build source, so the
                # exact recompilation check is left to the build source.
                errors = validate_workflow(workflow, openapi)
                self.assertFalse([error for error in errors if "shadows" in error])
                flagged = {error.split(".")[1] for error in errors if ORDER in error}
                expected = set()
                for name, node in workflow["nodes"].items():
                    rows = [row for row in node.get("transitions", []) if row.get("is_tool_call") is not True]
                    if rows and rows[-1]["condition"] == {"operator": "always"} and any(
                            "matches_intent" in json.dumps(row["condition"]) for row in rows[:-1]):
                        expected.add(name)
                self.assertTrue(expected)
                self.assertEqual(flagged, expected)


if __name__ == "__main__":
    unittest.main()

"""Generated routing that the hosted router can execute (hosted readiness, issue 8).

On the platform an eight-intent node got no match on 25 of 34 caller turns and
matched the other 9 to its first row; a one-intent node matched 6 of 6. Kit rule:
same-target routes merge into one, unconditional if any is always. Synthetic
nodes only; no model, platform, brief or evaluator material is used.
"""
import copy
import json
import unittest

from builder.build import make_workflow_prompt
from builder.guards import compile_guarded_workflow
from builder.validation import plan_routing_errors, validate_workflow
from test_guards import fixture
from test_kit_routing_guidance import context
from test_replay_scheduling import conversation, equals, intent, route

MERGE = "non-adjacent routes share a target with an always route"


def plan(**nodes):
    return {"initial_node": "talk", "nodes": nodes}


class PlanRoutingShapeTests(unittest.TestCase):
    def test_several_intent_routes_and_routing_prompts_are_refused(self):
        errors = plan_routing_errors(plan(talk=conversation("talk", [route("a", intent()), route("b", intent())])))
        self.assertEqual(len(errors), 1)
        self.assertIn("plan node talk: 2 matches_intent routes at transitions[0, 1]", errors[0])
        talk = conversation("talk", [route("a", intent())])
        talk["routing_prompt"] = "Synthetic routing context."
        self.assertIn("routing_prompt replaces", plan_routing_errors(plan(talk=talk))[0])

    def test_catch_all_into_a_decision_step_and_an_enum_fork_pass(self):
        decide = conversation("decide", [route("a", tool=True), route("b", tool=True)], entry="llm")
        shapes = (plan(talk=conversation("talk", [route("decide", intent("Any caller answer"))]), decide=decide),
                  plan(talk=conversation("talk", [route("a", equals("route_choice", "a")),
                                                  route("b", equals("route_choice", "b"))])),
                  plan(talk=conversation("talk", [route("a", tool=True), route("b", intent())], entry="llm")))
        for shape in shapes:
            with self.subTest(shape=sorted(shape["nodes"])):
                self.assertEqual(plan_routing_errors(shape), [])


class SameTargetMergeTests(unittest.TestCase):
    def test_guard_routes_keep_a_shared_success_and_blocked_target_after_allow(self):
        source = fixture()  # every guard action routes success, blocked and failure to talk
        workflow = compile_guarded_workflow(**source)["workflow"]
        rows = workflow["nodes"]["identity"]["transitions"]
        routes = [json.dumps(row["condition"]) for row in rows]
        allow = next(i for i, text in enumerate(routes) if '"allow"' in text)
        done = next(i for i, text in enumerate(routes) if '"done"' in text)
        self.assertLess(allow, done)
        self.assertEqual((rows[done]["target_node"], rows[-1]["condition"]), (rows[-1]["target_node"], {"operator": "always"}))
        context_ = {key: value for key, value in source.items() if key != "openapi"}
        self.assertEqual(validate_workflow(workflow, source["openapi"], guard_context=context_), [])
        # The earlier order (done before allow) merges done with the fallback ahead of allow.
        shadowed = copy.deepcopy(workflow)
        old = shadowed["nodes"]["identity"]["transitions"]
        old.insert(allow, old.pop(done))
        self.assertTrue(any(error.startswith("node.identity.transitions[1, 3]: " + MERGE)
                            for error in validate_workflow(shadowed, source["openapi"])))

    def test_generator_prompt_and_reference_keep_one_intent_route_and_no_routing_prompt(self):
        frozen, api = context()
        header, data = make_workflow_prompt(frozen["contract"], api).split("\n\nAUTHORIZED INPUTS:\n", 1)
        reference = json.loads(data)["runtime_reference"]
        for text in ("most one matches_intent route", "nonspeaking LLM-entry decision step", "route-choice",
                     "Do not author routing_prompt"):
            self.assertIn(text, header)
        for text in ("At most one matches_intent route per node", "routing_prompt: it replaces the router"):
            self.assertIn(text, reference)
        self.assertNotIn("task_message,routing_prompt", reference)


if __name__ == "__main__":
    unittest.main()

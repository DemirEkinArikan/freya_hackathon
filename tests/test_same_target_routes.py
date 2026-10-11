"""The kit merges routes that share a target, so a completion and an ordinary
route to one target cannot coexist. Synthetic nodes, plus an env-gated no-model
recompile of a retained plan; no model, platform or evaluator material is used.
"""
import copy
import json
import os
import unittest
from pathlib import Path

from builder.action_bindings import compile_action_bindings
from builder.build import make_workflow_prompt
from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements, validate_plan_execution
from builder.guards import compile_guarded_workflow, required_execution_kinds
from builder.policy import compile_execution_policy
from builder.safety import digest, read_json
from builder.validation import validate_workflow
from test_kit_routing_guidance import context
from test_replay_scheduling import compiled, conversation, equals, intent, route

ROOT = Path(__file__).resolve().parents[1]


def merged(path, ordinary):
    return (f"{path}: is_tool_call route shares its target with ordinary transitions{ordinary}; the kit merges "
            "same-target routes into one route (the OR of their conditions, unconditional if any is always), which "
            "cannot be both a completion and a router row. Keep one route per target: drop one of them (a kept "
            "completion can also cover those caller answers, since an unmatched caller turn stays in the node) or "
            "send the ordinary route to a different node")


def errors_for(transitions):
    nodes = {"talk": conversation("talk", transitions, entry="llm")}
    for name in {row["target_node"] for row in transitions} - {"close"}:
        nodes[name] = conversation(name, [route("close")])
    workflow, context, api = compiled(nodes)
    return validate_workflow(workflow, api, guard_context=context)


class SameTargetRouteTests(unittest.TestCase):
    def test_completion_sharing_a_target_with_an_ordinary_route_is_refused_whatever_its_condition_or_position(self):
        for transitions, error in (
                ([route("close", tool=True), route("later", intent()), route("close", intent())],
                 merged("node.talk.transitions[0]", [2])),
                # Last in the list, the merged route still shadows the intent before it.
                ([route("later", intent()), route("close", intent()), route("close", tool=True)],
                 merged("node.talk.transitions[2]", [1])),
                # A conditional completion still merges into one route that cannot be both.
                ([route("later", intent()), route("close", intent(), tool=True), route("close", intent())],
                 merged("node.talk.transitions[1]", [2])),
                ([route("later", intent()), route("close", equals("answer_text", "done"), tool=True),
                  route("close", intent())],
                 merged("node.talk.transitions[1]", [2]))):
            with self.subTest(error=error):
                self.assertEqual(errors_for(transitions), [error])

    def test_one_route_per_target_remedies_validate(self):
        for transitions in ([route("close", tool=True), route("later", intent())],
                            [route("close", tool=True), route("later", intent()), route("other", intent())],
                            [route("close", tool=True), route("close", intent(), tool=True), route("later", intent())]):
            with self.subTest(transitions=transitions):
                self.assertEqual(errors_for(transitions), [])

    def test_reference_gives_one_route_per_target_and_the_completion_remedy(self):
        frozen, api = context()
        reference = json.loads(make_workflow_prompt(frozen["contract"], api).split(
            "\n\nAUTHORIZED INPUTS:\n", 1)[1])["runtime_reference"]
        for text in ("Give each target one route per node", "never share a target", "keep only the completion"):
            self.assertIn(text, reference)


@unittest.skipUnless(os.environ.get("FREYA_SAME_TARGET_RUN"), "retained run artifacts are local only")
class RetainedPlanRecompile(unittest.TestCase):
    """Recompiles a retained public-brief build's first plan read-only; no model call."""

    def test_retained_plan_gets_only_the_named_merge_error_and_either_drop_clears_it(self):
        run = Path(os.environ["FREYA_SAME_TARGET_RUN"])
        kit = ROOT / "build-the-builder"
        text = (kit / "briefs" / "brief-a.md").read_text(encoding="utf-8")
        sources = [{"id": "brief-1", "file": "brief-a.md", "sha256": digest(text), "text": text}]
        api = read_json(kit / "tools" / "openapi.json")
        recorded = next(json.loads(line) for line in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                        if '"event": "inputs"' in line)
        self.assertEqual([s["sha256"] for s in sources], [s["sha256"] for s in recorded["sources"]])
        self.assertEqual(digest(api), recorded["tool_api_sha256"])
        policy = read_json(run / "policy.json")
        plan = read_json(run / "attempts" / "plan-001" / "plan.json")
        contract = compile_policy_contract(policy, sources, api)
        requirements = compile_execution_requirements(policy, sources, api)

        def errors(candidate):
            self.assertEqual(validate_plan_execution(candidate, requirements, policy, sources, api), [])
            bindings = compile_action_bindings(policy, api)
            execution = compile_execution_policy(policy, bindings, required_kinds=required_execution_kinds(candidate))
            workflow = compile_guarded_workflow(candidate, contract, api, execution, bindings)["workflow"]
            return validate_workflow(workflow, api, policy, guard_context={
                "plan": candidate, "contract": contract, "execution_policy": execution, "bindings": bindings})

        self.assertEqual(errors(plan), [merged("node.recovery.transitions[1]", [8])])
        conditional = copy.deepcopy(plan)
        row = conditional["nodes"]["recovery"]["transitions"][1]
        row["condition"] = {"operator": "matches_intent", "description": row["description"]}
        self.assertEqual(errors(conditional), [merged("node.recovery.transitions[1]", [8])])
        for drop in (1, 8):
            candidate = copy.deepcopy(plan)
            del candidate["nodes"]["recovery"]["transitions"][drop]
            with self.subTest(drop=drop):
                self.assertEqual(errors(candidate), [])


if __name__ == "__main__":
    unittest.main()

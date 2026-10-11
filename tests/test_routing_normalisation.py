"""Intent nodes never keep a routing_prompt (on the host it made every intent row
match, so the first row won), and no node keeps two rows to one target where one
is always (the kit merges them into one unconditional row). Synthetic contracts,
plus an env-gated no-model recompile of a retained plan. Local compile evidence,
not hosted behaviour.
"""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock

from builder.action_bindings import compile_action_bindings
from builder.build import PROJECT, execute, make_workflow_prompt
from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements, validate_plan_execution
from builder.guards import compile_guarded_workflow, required_execution_kinds, validate_guarded_workflow
from builder.policy import compile_execution_policy
from builder.safety import digest, read_json
from builder.validation import _router_judged, validate_workflow
import test_builder
from test_guards import fixture
from test_kit_routing_guidance import context
from test_replay_scheduling import conversation, equals, intent, route
from tools.replay_guarded import _route

ROOT = Path(__file__).resolve().parents[1]


def intent_prompts(workflow):
    return [name for name, node in workflow["nodes"].items() if "routing_prompt" in node
            and any(_router_judged(row["condition"]) for row in node.get("transitions", []))]


def same_target_fallbacks(workflow):
    return [(name, row["target_node"]) for name, node in workflow["nodes"].items() if node.get("transitions")
            and node["transitions"][-1]["condition"] == {"operator": "always"}
            for row in node["transitions"][:-1] if row["target_node"] == node["transitions"][-1]["target_node"]]


def compiled(talk, actions=("close",)):
    source = fixture()
    nodes = source["plan"]["nodes"]
    source["plan"] = {"initial_node": "talk", "nodes": {"talk": talk, **{name: nodes[name] for name in actions}}}
    bundle = compile_guarded_workflow(**source)
    guard_context = {key: value for key, value in source.items() if key != "openapi"}
    return bundle, source, validate_workflow(bundle["workflow"], source["openapi"], guard_context=guard_context)


class RoutingNormalisationTests(unittest.TestCase):
    def test_intent_nodes_lose_routing_prompt_and_other_nodes_keep_it(self):
        source = fixture()
        talk = source["plan"]["nodes"]["talk"]
        talk["routing_prompt"] = "Route using current {{bg_facts}}."
        talk["transitions"].append(route("decide", intent()))
        decide = conversation("decide", [route("close")], passthrough="close")
        decide["routing_prompt"] = "No intent row reads this."
        source["plan"]["nodes"]["decide"] = decide
        bundle = compile_guarded_workflow(**source)
        nodes = bundle["workflow"]["nodes"]
        self.assertEqual(validate_guarded_workflow(bundle["workflow"], **source), [])
        self.assertNotIn("routing_prompt", nodes["talk"])
        self.assertEqual(nodes["decide"]["routing_prompt"], decide["routing_prompt"])
        self.assertEqual(intent_prompts(bundle["workflow"]), [])
        self.assertIn({"node": "talk", "change": "routing_prompt_removed"}, bundle["guard_manifest"]["routing_normalisation"])

    def test_guard_routes_keep_one_row_per_target_and_route_alike(self):
        bundle = compile_guarded_workflow(**fixture())
        nodes = bundle["workflow"]["nodes"]
        self.assertEqual(same_target_fallbacks(bundle["workflow"]), [])
        gate = nodes["identity"]["transitions"][-1]["target_node"]
        # The allow row is no longer between two rows the kit would merge into one unconditional row.
        self.assertEqual([row["target_node"] for row in nodes["identity"]["transitions"]],
                         ["bg_quarantine", "bg_identity_api", gate])
        for value, target in (("done", gate), ("allow", "bg_identity_api"), ("blocked", gate)):
            self.assertEqual(_route(nodes["identity"], {"bg_route": value}), (target, True))
        for value in ("done", "failure", "other"):
            self.assertEqual(_route(nodes["bg_identity_result"], {"bg_route": value}), (gate, True))
        self.assertIn({"node": "identity", "row": 1, "target": gate, "change": "dropped"},
                      bundle["guard_manifest"]["routing_normalisation"])

    def test_authored_rows_drop_only_when_they_never_fire_first_and_otherwise_relay(self):
        dead_intent = conversation("talk", [route("identity", intent()), route("identity")])
        excluded = conversation("talk", [route("identity", equals("answer_text", "a")),
                                         route("account", equals("answer_text", "b")), route("identity")],
                                passthrough="identity")
        for talk, actions in ((dead_intent, ("close", "identity")), (excluded, ("close", "identity", "account"))):
            with self.subTest(rows=len(talk["transitions"])):
                bundle, _, errors = compiled(talk, actions)
                self.assertEqual(errors, [])
                self.assertEqual(same_target_fallbacks(bundle["workflow"]), [])
                self.assertEqual(len(bundle["workflow"]["nodes"]["talk"]["transitions"]), len(talk["transitions"]) - 1)
                self.assertNotIn("bg_relay_0", bundle["workflow"]["nodes"])
        overlapping = conversation("talk", [route("identity", {"logic": "or", "conditions": [
            {"variable": "answer_text", "operator": "is_set"}]}), route("account", equals("answer_text", "b")),
            route("identity")], passthrough="identity")
        bundle, source, errors = compiled(overlapping, ("close", "identity", "account"))
        nodes = bundle["workflow"]["nodes"]
        self.assertEqual(errors, [])
        self.assertEqual(same_target_fallbacks(bundle["workflow"]), [])
        gate = nodes["talk"]["transitions"][-1]["target_node"]
        self.assertEqual({**nodes["bg_relay_0"], "name": gate}, nodes[gate])
        # The first row still wins when both conditions hold, through an identical entry gate.
        self.assertEqual(_route(nodes["talk"], {"answer_text": "b"}), ("bg_relay_0", True))
        self.assertEqual(nodes["bg_relay_0"]["transitions"][0]["target_node"], "identity")
        self.assertEqual(validate_guarded_workflow(bundle["workflow"], **source), [])

    def test_prompt_and_reference_never_ask_for_routing_prompt_on_intent_nodes(self):
        frozen, api = context()
        header, data = make_workflow_prompt(frozen["contract"], api).split("\n\nAUTHORIZED INPUTS:\n", 1)
        reference = json.loads(data)["runtime_reference"]
        self.assertIn("Never set routing_prompt on a node with matches_intent routes", header)
        self.assertIn("matches_intent\nroutes never sets routing_prompt", reference)
        self.assertNotIn("in routing_prompt", header + reference)


class RoutingTraceTests(unittest.TestCase):
    # Borrowed, not inherited, so test_builder's own tests do not run again here.
    setUp, tearDown, args = test_builder.BuilderTests.setUp, test_builder.BuilderTests.tearDown, test_builder.BuilderTests.args

    def test_generate_only_trace_records_the_removed_routing_prompt(self):
        generator = Mock()
        generator.generate.return_value = test_builder.fixture()

        def proposal(prompt, *, contract_sha256):
            proposed = test_builder.plan()
            proposed["nodes"]["start"]["routing_prompt"] = "Route every caller answer."
            return {"plan": proposed, "contract_sha256": contract_sha256}
        generator.generate_plan.side_effect = proposal
        result = execute(self.args("--generate-only"), generator=generator,
                         client_factory=lambda **kw: self.fail("platform touched"))
        self.assertEqual(result["status"], "generated_validated")
        run = PROJECT / result["run_directory"]
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        recorded = [event["changes"] for event in events if event["event"] == "routing_normalisation"]
        self.assertEqual(recorded, [[{"node": "start", "change": "routing_prompt_removed"}]])
        self.assertNotIn("routing_prompt", json.loads((run / "workflow.json").read_text())["nodes"]["start"])


@unittest.skipUnless(os.environ.get("FREYA_PUBLIC_A_RUN"), "retained run artifacts are local only")
class RetainedPublicAPlan(unittest.TestCase):
    """Recompiles a retained public-brief build's selected plan read-only; no model call."""

    def test_retained_plan_compiles_without_intent_routing_prompts_or_same_target_fallbacks(self):
        run = Path(os.environ["FREYA_PUBLIC_A_RUN"])
        kit = ROOT / "build-the-builder"
        text = (kit / "briefs" / "brief-a.md").read_text(encoding="utf-8")
        sources = [{"id": "brief-1", "file": "brief-a.md", "sha256": digest(text), "text": text}]
        api = read_json(kit / "tools" / "openapi.json")
        policy, plan = read_json(run / "policy.json"), read_json(run / "plan.json")
        self.assertTrue(intent_prompts({"nodes": plan["nodes"]}))
        requirements = compile_execution_requirements(policy, sources, api)
        self.assertEqual(validate_plan_execution(plan, requirements, policy, sources, api), [])
        contract = compile_policy_contract(policy, sources, api)
        bindings = compile_action_bindings(policy, api)
        execution = compile_execution_policy(policy, bindings, required_kinds=required_execution_kinds(plan))
        workflow = compile_guarded_workflow(plan, contract, api, execution, bindings)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, policy, guard_context={
            "plan": plan, "contract": contract, "execution_policy": execution, "bindings": bindings}), [])
        self.assertEqual(intent_prompts(workflow), [])
        self.assertEqual(same_target_fallbacks(workflow), [])


if __name__ == "__main__":
    unittest.main()

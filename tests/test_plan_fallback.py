"""Deterministic fallback plan: built without a model call, validated by the unchanged checks."""
import copy
import json
import unittest
from unittest.mock import patch

import test_builder
from builder.action_bindings import compile_action_bindings
from builder.build import FALLBACK_EXPORT_RESERVE, FALLBACK_MIN_SECONDS, PROJECT, execute
from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements, validate_plan_execution
from builder.fallback_plan import deterministic_plan
from builder.guards import compile_guarded_workflow, required_execution_kinds
from builder.policy import compile_execution_policy
from builder.runtime import Deadline, GenerationError
from builder.validation import validate_workflow
from test_execution_requirements import packet
from tools import package_submission as package
from tools.verify_submission_bundle import check_recompilation


def fallback_errors(policy, sources, openapi):
    """Run the same checks build.py applies to a model plan."""
    contract = compile_policy_contract(policy, sources, openapi)
    requirements = compile_execution_requirements(policy, sources, openapi)
    plan = deterministic_plan(contract, requirements)
    errors = validate_plan_execution(plan, requirements, policy, sources, openapi)
    if errors:
        return plan, errors
    bindings = compile_action_bindings(policy, openapi)
    execution = compile_execution_policy(policy, bindings, required_kinds=required_execution_kinds(plan))
    workflow = compile_guarded_workflow(plan, contract, openapi, execution, bindings)["workflow"]
    return plan, validate_workflow(workflow, openapi, policy, guard_context={
        "plan": plan, "contract": contract, "execution_policy": execution, "bindings": bindings})


class FallbackPlanTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def broken_generator(self):
        generator = self.generator()
        generator.generate.return_value = test_builder.fixture()
        broken = test_builder.plan()
        broken["initial_node"] = "missing"
        generator.generate_plan.side_effect = lambda prompt, *, contract_sha256: {
            "plan": copy.deepcopy(broken), "contract_sha256": contract_sha256}
        return generator

    def events(self, manifest_or_dir):
        run = PROJECT / manifest_or_dir["run_directory"] if isinstance(manifest_or_dir, dict) else manifest_or_dir
        return [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]

    def test_fallback_validates_for_every_action_shape(self):
        for actions, local in ((None, False), (None, True), (["schedule_callback", "close_call"], False),
                               (["verify_identity", "get_account", "close_call"], False)):
            with self.subTest(actions=actions, local=local):
                plan, errors = fallback_errors(*packet(actions, local=local, narrow=actions is not None))
                self.assertEqual(errors, [])
                self.assertEqual(plan["initial_node"], "dialogue")
                self.assertTrue(all(node["node_type"] in {"conversation", "guard_action"} for node in plan["nodes"].values()))
                decide = plan["nodes"]["decide"]["transitions"]
                self.assertTrue(all(t["is_tool_call"] and t["condition"] == {"operator": "always"} for t in decide))

    def test_failed_model_plan_exports_the_fallback_with_its_origin_in_the_trace(self):
        generator = self.broken_generator()
        client = []
        def factory(**kwargs):
            client.append(test_builder.FakeClient(**kwargs))
            return client[0]
        manifest = execute(self.args(), generator=generator, client_factory=factory)
        self.assertEqual((manifest["status"], manifest["plan_origin"]), ("exported", "deterministic_fallback"))
        self.assertIn("Candidate validation failed after bounded repairs", manifest["plan_fallback_reason"])
        self.assertEqual(generator.generate_plan.call_count, 3)  # no extra model call for the fallback
        self.assertEqual((client[0].saves, client[0].exports), (1, 1))
        events = self.events(manifest)
        self.assertIn(("plan_fallback_decision", True), [(e["event"], e.get("started")) for e in events])
        self.assertIn("plan_fallback_built", [e["event"] for e in events])
        passed = [e for e in events if e["event"] == "validation" and e["passed"]]
        self.assertEqual([e["plan_origin"] for e in passed], ["deterministic_fallback"])
        run = PROJECT / manifest["run_directory"]
        plan = json.loads((run / "plan.json").read_text())
        self.assertEqual(set(plan["nodes"]) >= {"dialogue", "decide", "farewell", "close"}, True)
        attempt = json.loads((run / manifest["selected_attempt_directory"] / "attempt.json").read_text())
        self.assertEqual((attempt["status"], attempt["plan_origin"]), ("exported", "deterministic_fallback"))
        # Three model attempts, then the fallback: the packaging recompilation check accepts plan-004.
        self.assertEqual(manifest["selected_attempt_directory"], "attempts/plan-004")
        export = json.loads((run / "submission.workflow.json").read_text())
        check_recompilation(run, manifest, export, [("brief.md", test_builder.BRIEF_TEXT)], test_builder.contract())

    def test_valid_model_plan_never_builds_the_fallback(self):
        generator = self.generator()
        generator.generate.return_value = test_builder.fixture()
        with patch("builder.build.deterministic_plan", side_effect=AssertionError("fallback built")):
            manifest = execute(self.args("--generate-only"), generator=generator,
                               client_factory=lambda **kw: self.fail("Freya touched"))
        self.assertEqual((manifest["status"], manifest["plan_origin"]), ("generated_validated", "model"))
        self.assertIsNone(manifest["plan_fallback_reason"])
        self.assertNotIn("plan_fallback_decision", [e["event"] for e in self.events(manifest)])

    def test_timed_out_plan_call_uses_fallback_only_inside_the_time_reserve(self):
        for left, exported in ((295, True), (FALLBACK_MIN_SECONDS - 5, False)):
            with self.subTest(left=left):
                now = [0.0]
                deadline = Deadline(1800, clock=lambda: now[0])
                generator = self.generator()
                generator.generate.return_value = test_builder.fixture()
                def timeout(prompt, *, contract_sha256):
                    now[0] = 1800 - left
                    raise GenerationError("Codex generation exceeded its allocated time")
                generator.generate_plan.side_effect = timeout
                clients, reserves = [], []
                def factory(**kwargs):
                    reserves.append(kwargs["reserve"])
                    clients.append(test_builder.FakeClient(**kwargs))
                    return clients[0]
                out = self.directory / f"out-{left}"
                args = self.args("--output-dir", str(out))
                if exported:
                    manifest = execute(args, generator=generator, client_factory=factory, deadline=deadline)
                    self.assertEqual((manifest["status"], manifest["plan_origin"]), ("exported", "deterministic_fallback"))
                    self.assertEqual((clients[0].exports, reserves), (1, [FALLBACK_EXPORT_RESERVE]))
                    self.assertEqual(generator.generate_plan.call_count, 1)
                else:
                    with self.assertRaisesRegex(GenerationError, "exceeded its allocated time"):
                        execute(args, generator=generator, client_factory=factory, deadline=deadline)
                    self.assertEqual(clients, [])
                    events = self.events(next(out.iterdir()))
                    self.assertIn(("plan_fallback_decision", False), [(e["event"], e.get("started")) for e in events])
        self.assertLess(FALLBACK_EXPORT_RESERVE, 300 - FALLBACK_MIN_SECONDS)

    def test_option_off_keeps_the_model_failure(self):
        generator = self.broken_generator()
        with patch("builder.build.deterministic_plan", side_effect=AssertionError("fallback built")):
            with self.assertRaisesRegex(ValueError, "Candidate validation failed after bounded repairs"):
                execute(self.args("--generate-only", "--no-plan-fallback"), generator=generator,
                        client_factory=lambda **kw: self.fail("Freya touched"))


class FallbackSelectionTests(unittest.TestCase):
    def test_model_plan_export_outranks_a_cleaner_earlier_fallback_export(self):
        def entry(number, fallback, diagnostics, finished):
            return {"attempt": number, "exported": True, "local_validation": True, "package_validation": True,
                    "deterministic_fallback": fallback, "diagnostics": diagnostics,
                    "finished_at": "2026-10-11T20:" + finished + ":00+00:00"}
        fallback, model = entry(1, True, 0, "10"), entry(2, False, 7, "20")
        self.assertIs(min([fallback, model], key=package.attempt_rank), model)
        self.assertIs(min([fallback, dict(model, exported=False)], key=package.attempt_rank), fallback)
        legacy = {key: value for key, value in model.items() if key != "deterministic_fallback"}
        self.assertIs(min([fallback, legacy], key=package.attempt_rank), legacy)


if __name__ == "__main__":
    unittest.main()

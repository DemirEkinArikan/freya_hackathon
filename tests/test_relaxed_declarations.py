"""Relaxed declaration mode (last-resort attempts): protected or unresolved declarations become
prompt-enforced dialogue rules, while strict mode and every sealed protection stay unchanged.

Invented policies only; no model, network or evaluator input.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import test_builder
from builder.action_bindings import ActionBindingError, compile_action_bindings
from builder.build import PROJECT, _extract_policy, execute, make_workflow_prompt
from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import compile_execution_requirements, validate_plan_execution
from builder.guards import (_OUTCOME_RULE, _catalogue, _profile, compile_guarded_workflow, required_execution_kinds,
                            run_guard_step, validate_guarded_workflow)
from builder.policy import compile_execution_policy
from builder.runtime import Deadline, Trace
from builder.safety import digest
from test_categorised_declarations import CLOSE_REFUSAL, Generator, declared, entry, errors_of
from test_execution_requirements import plan, rule
from tools import verify_submission_bundle as verifier

PROTECTED = "Never send close_call before the caller confirms that nothing else is open; keep {{notes}} private."
AMBIGUOUS = "A caller may ask for two desks at once and the source ranks neither."


def relaxed_policy():
    return declared(entry("dialogue_only"),
                    entry("protected_unenforceable", PROTECTED, ["close_reasons"]),
                    entry("source_ambiguous", AMBIGUOUS, ["greeting"]))


def guarded(policy, sources, api, declarations="strict"):
    contract = compile_policy_contract(policy, sources, api, declarations=declarations)
    proposal = plan(contract["active_rules"]["execution_actions"]["value"])
    bindings = compile_action_bindings(policy, api, declarations=declarations)
    execution = compile_execution_policy(policy, bindings, required_kinds=required_execution_kinds(proposal))
    context = {"plan": proposal, "contract": contract, "execution_policy": execution, "bindings": bindings}
    return compile_guarded_workflow(proposal, contract, api, execution, bindings)["workflow"], context


def unguarded_code(policy, api, code="SYS_DOWN", outcome="not_verified", enum=True):
    rule(policy, "close_reasons")["value"][outcome].append(code)
    reason = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["reason"]
    if enum:
        reason["enum"].append(code)
    else:
        del reason["enum"]


class RelaxedContractTests(unittest.TestCase):
    def test_strict_rejects_while_relaxed_freezes_quoted_prompt_enforced_rules(self):
        policy, sources, api = relaxed_policy()
        before = copy.deepcopy(policy)
        errors = errors_of(policy, sources, api)
        self.assertIn("policy.unsupported_constraints[1] (protected_unenforceable) is a protected restriction no typed "
                      "kind or controller effect enforces and rejects the build", errors)
        self.assertIn("policy.unsupported_constraints[2] (source_ambiguous) has no typed fallback_rule cited from the "
                      "same source statement and rejects the build", errors)
        contract = compile_policy_contract(policy, sources, api, declarations="relaxed")
        quote = rule(policy, "close_reasons")["source"]["quote"]
        self.assertEqual(contract["declaration_mode"], "relaxed")
        self.assertEqual(contract["declared_limitations"], [entry("dialogue_only")])
        self.assertEqual(contract["prompt_enforced_limitations"], [
            dict(entry("protected_unenforceable", PROTECTED, ["close_reasons"]),
                 sources=[{"rule_id": "close_reasons", "id": "synthetic", "quote": quote}]),
            dict(entry("source_ambiguous", AMBIGUOUS, ["greeting"]),
                 sources=[{"rule_id": "greeting", "id": "synthetic", "quote": quote}])])
        self.assertTrue(compile_execution_requirements(policy, sources, api, declarations="relaxed")["actions"])
        self.assertEqual(policy, before)

    def test_strict_output_is_unchanged_and_relaxed_is_marked(self):
        policy, sources, api = declared(entry("dialogue_only"))
        strict = compile_policy_contract(policy, sources, api)
        self.assertEqual(compile_policy_contract(policy, sources, api, declarations="strict"), strict)
        self.assertFalse({"declaration_mode", "prompt_enforced_limitations"} & set(strict))
        relaxed = compile_policy_contract(policy, sources, api, declarations="relaxed")
        self.assertEqual({key: value for key, value in relaxed.items()
                          if key not in {"declaration_mode", "prompt_enforced_limitations", "sha256"}},
                         {key: value for key, value in strict.items() if key != "sha256"})
        self.assertEqual(relaxed["prompt_enforced_limitations"], [])
        self.assertEqual(compile_execution_requirements(policy, sources, api, declarations="strict"),
                         compile_execution_requirements(policy, sources, api))
        for mode in ("lenient", None, ["relaxed"]):
            with self.subTest(mode=mode), self.assertRaisesRegex(PolicyContractError, "declaration mode must be strict or relaxed"):
                compile_policy_contract(policy, sources, api, declarations=mode)

    def test_relaxed_keeps_uncategorised_and_malformed_entries_fatal(self):
        policy, sources, api = declared()
        policy["unsupported_constraints"] = ["Invented unresolved conflict between two rules."]
        cases = [(policy, sources, api)]
        for broken in (dict(entry("protected_unenforceable"), rule_ids=["absent"]),
                       dict(entry("protected_unenforceable"), justification=" "),
                       dict(entry("source_ambiguous"), extra="field"), dict(entry("dialogue_only"), category="minor")):
            cases.append(declared(broken))
        for case in cases:
            with self.subTest(entries=case[0]["unsupported_constraints"]), self.assertRaises(PolicyContractError) as caught:
                compile_policy_contract(*case, declarations="relaxed")
            self.assertIn("policy contains unsupported constraints", caught.exception.errors)

    def test_relaxed_turns_other_rejected_categories_into_prompt_rules_not_controller_limitations(self):
        # A protected name hidden as dialogue_only, or a refusal quote that differs from the controller
        # text, rejects a strict build; relaxed keeps the requirement as a dialogue rule only.
        unverified = [entry("dialogue_only", "Never call close_call before thanking the caller."),
                      entry("controller_stricter", rule_ids=["close_reasons"], controller_effect=CLOSE_REFUSAL + ".")]
        policy, sources, api = declared(*unverified)
        self.assertEqual(sum("rejects the build" in error for error in errors_of(policy, sources, api)), 2)
        contract = compile_policy_contract(policy, sources, api, declarations="relaxed")
        self.assertNotIn("declared_limitations", contract)
        self.assertEqual([item["category"] for item in contract["prompt_enforced_limitations"]],
                         ["dialogue_only", "controller_stricter"])

    def test_superseded_rules_are_cited_without_their_replaced_quotes(self):
        policy, sources, api = declared(entry("protected_unenforceable", PROTECTED, ["greeting", "greeting-old"]))
        old = dict(copy.deepcopy(policy["rules"][-1]), id="greeting-old", value={"order": "replaced"})
        old["source"]["quote"] = "Invented replaced greeting rule."
        sources[0]["text"] += " " + old["source"]["quote"]
        sources[0]["sha256"] = policy["sources"][0]["sha256"] = digest(sources[0]["text"])
        policy["rules"].append(old)
        policy["rules"][-2]["overrides"] = ["greeting-old"]
        contract = compile_policy_contract(policy, sources, api, declarations="relaxed")
        self.assertEqual([source["rule_id"] for source in contract["prompt_enforced_limitations"][0]["sources"]], ["greeting"])

    def test_relaxed_extraction_accepts_a_declaration_only_policy_without_repair(self):
        policy, sources, api = relaxed_policy()
        generator = Generator([policy])
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            frozen, contract, requirements, attempt = _extract_policy(
                generator, sources, api, run_dir, Trace(run_dir / "trace.jsonl"), Deadline(1800), (),
                workflow_pending=False, declarations="relaxed")
            self.assertEqual(list(run_dir.glob("policy.rejected-*.json")), [])
        self.assertEqual((attempt, len(generator.prompts)), (0, 1))
        self.assertEqual(frozen, policy)
        self.assertEqual([item["rule_ids"] for item in contract["prompt_enforced_limitations"]], [["close_reasons"], ["greeting"]])
        self.assertEqual(requirements["contract_sha256"], contract["sha256"])
        # The extraction prompt is the strict one: relaxed mode never invites more declarations.
        self.assertNotIn("relaxed", generator.prompts[0])


class PromptEnforcedDialogueTests(unittest.TestCase):
    def test_prompts_carry_the_rules_and_sealed_compilation_is_otherwise_identical(self):
        policy, sources, api = relaxed_policy()
        workflow, context = guarded(policy, sources, api, "relaxed")
        self.assertEqual(validate_guarded_workflow(workflow, openapi=api, **context), [])
        self.assertEqual(validate_plan_execution(context["plan"], compile_execution_requirements(
            policy, sources, api, declarations="relaxed"), policy, sources, api, declarations="relaxed"), [])
        quote = rule(policy, "close_reasons")["source"]["quote"]
        system = workflow["system_prompt"]
        self.assertTrue(system.startswith("Bank rules that no system check enforces."))
        self.assertIn(PROTECTED.replace("{{notes}}", "((notes))") + ' Source: "' + quote + '"', system)
        self.assertIn(AMBIGUOUS, system)
        self.assertNotIn("{", system)
        # The node routing into close_call carries the rule that names it and cites a close_call kind;
        # the ambiguity cites only a descriptive rule and names no action, so it stays global.
        talk = workflow["nodes"]["talk"]["task_message"]
        self.assertIn("Bank rule for this step that no system check enforces: " + PROTECTED.replace("{{notes}}", "((notes))"), talk)
        self.assertNotIn(AMBIGUOUS, talk)
        stripped = copy.deepcopy(workflow)
        self.assertTrue(system.endswith("\n\n" + _OUTCOME_RULE))
        stripped["system_prompt"] = _OUTCOME_RULE
        stripped["nodes"]["talk"]["task_message"] = talk[:talk.index("\n\nBank rule for this step")]
        policy["unsupported_constraints"] = [entry("dialogue_only")]
        strict, _ = guarded(policy, sources, api)
        self.assertEqual(stripped, strict)

    def test_an_authored_system_prompt_is_kept_before_the_rules(self):
        policy, sources, api = relaxed_policy()
        contract = compile_policy_contract(policy, sources, api, declarations="relaxed")
        proposal = dict(plan(contract["active_rules"]["execution_actions"]["value"]), system_prompt="Speak English.")
        bindings = compile_action_bindings(policy, api)
        execution = compile_execution_policy(policy, bindings, required_kinds=required_execution_kinds(proposal))
        workflow = compile_guarded_workflow(proposal, contract, api, execution, bindings)["workflow"]
        self.assertTrue(workflow["system_prompt"].startswith("Speak English.\n\nBank rules that no system check enforces."))
        strict = compile_policy_contract(*declared(entry("dialogue_only")))
        self.assertEqual(compile_guarded_workflow(proposal, strict, api, execution, bindings)["workflow"]["system_prompt"],
                         "Speak English.\n\n" + _OUTCOME_RULE)

    def test_plan_prompt_lists_the_rules_only_in_relaxed_mode(self):
        policy, sources, api = relaxed_policy()
        contract = compile_policy_contract(policy, sources, api, declarations="relaxed")
        payload = json.loads(make_workflow_prompt(contract, api).split("\n\nAUTHORIZED INPUTS:\n", 1)[1])
        quote = rule(policy, "close_reasons")["source"]["quote"]
        self.assertEqual(payload["prompt_enforced_rules"]["rules"], [
            {"requirement": PROTECTED, "source_quotes": [quote]}, {"requirement": AMBIGUOUS, "source_quotes": [quote]}])
        self.assertIn("No sealed controller check enforces these source rules", payload["prompt_enforced_rules"]["guidance"])
        strict = compile_policy_contract(*declared(entry("dialogue_only")))
        self.assertNotIn("prompt_enforced_rules", make_workflow_prompt(strict, api))


class RelaxedBindingTests(unittest.TestCase):
    def test_exact_enum_close_codes_stay_unguarded_only_in_relaxed_mode(self):
        policy, sources, api = declared()
        unguarded_code(policy, api)
        with self.assertRaisesRegex(ActionBindingError, "a goal with schedule_callback leaves close codes unbound only when"):
            compile_action_bindings(policy, api)  # its table has no bound callback code
        binding = compile_action_bindings(policy, api, declarations="relaxed")
        self.assertEqual(binding["declaration_mode"], "relaxed")
        self.assertEqual(binding["categories"]["reasons"]["unbound_codes"], ["SYS_DOWN"])
        self.assertEqual(compile_execution_policy(policy, binding)["bindings_sha256"], binding["sha256"])
        self.assertTrue(compile_execution_requirements(policy, sources, api, declarations="relaxed")["actions"])
        # Without the code the relaxed binding is the strict one.
        self.assertEqual(compile_action_bindings(*declared()[::2], declarations="relaxed"), compile_action_bindings(*declared()[::2]))

    def test_guarded_spellings_builder_meanings_enum_misses_and_notes_stay_fatal(self):
        for code in ("No-Callback", "CALLBACK", "Promise", "declined"):  # guarded spellings and builder meanings
            policy, _, api = declared()
            unguarded_code(policy, api, code)
            with self.subTest(code=code), self.assertRaises(ActionBindingError):
                compile_action_bindings(policy, api, declarations="relaxed")
        policy, _, api = declared()
        unguarded_code(policy, api, enum=False)  # a free-text reason field accepts the code, as for identity-only goals
        self.assertEqual(compile_action_bindings(policy, api, declarations="relaxed")["categories"]["reasons"]["unbound_codes"],
                         ["SYS_DOWN"])
        policy, _, api = declared()
        rule(policy, "close_reasons")["value"]["not_verified"].append("SYS_DOWN")  # absent from the API enum
        with self.assertRaisesRegex(ActionBindingError, "do not match the API enum"):
            compile_action_bindings(policy, api, declarations="relaxed")
        policy, _, api = declared()
        rule(policy, "note_reasons")["value"].append("N_OPAQUE")
        with self.assertRaisesRegex(ActionBindingError, "notes vocabulary"):
            compile_action_bindings(policy, api, declarations="relaxed")
        with self.assertRaisesRegex(ActionBindingError, "declaration mode must be strict or relaxed"):
            compile_action_bindings(*declared()[::2], declarations="loose")

    def test_kernel_still_closes_only_on_protected_evidence_or_a_declared_row(self):
        policy, sources, api = declared()
        unguarded_code(policy, api)
        _, context = guarded(policy, sources, api, "relaxed")
        profile = _profile(context["contract"], context["execution_policy"], context["plan"], context["bindings"])
        catalog = _catalogue(context["contract"], api, context["bindings"])
        maps = context["bindings"]["categories"]
        state = run_guard_step("init", None, profile=profile, catalog=catalog, customer_id="invented", now="2031-05-02T10:00:00")[0]
        state.update(identity_status="verified", verified_fields=list(profile["identity_fields"]))
        for proposed in ({"outcome": "not_verified", "reason": "SYS_DOWN"}, {"outcome": "no_promise", "reason": "SYS_DOWN"},
                         {"outcome": "promise", "reason": ""}):
            with self.subTest(proposed=proposed), self.assertRaisesRegex(ValueError, "closing lacks protected evidence"):
                run_guard_step("prepare", copy.deepcopy(state), "close_call", proposed, profile=profile, catalog=catalog, maps=maps)


class RelaxedBuildTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def declaring_generator(self):
        generator = self.generator()
        candidate = test_builder.fixture()
        candidate["policy"]["unsupported_constraints"] = [entry("protected_unenforceable", PROTECTED, ["r1"])]
        generator.generate.return_value = candidate
        return generator

    def test_strict_build_still_rejects_the_declaration(self):
        generator = self.declaring_generator()
        with self.assertRaisesRegex(ValueError, "Policy extraction failed after bounded repairs"):
            execute(self.args("--generate-only"), generator=generator, client_factory=lambda **kw: self.fail("Freya touched"))
        self.assertEqual(generator.generate.call_count, 3)

    def test_relaxed_export_records_the_rules_and_recompiles_only_in_its_mode(self):
        generator = self.declaring_generator()
        manifest = execute(self.args("--declarations", "relaxed"), generator=generator,
                           client_factory=lambda **kw: test_builder.FakeClient())
        self.assertEqual((manifest["status"], manifest["policy_repairs"], generator.generate.call_count), ("exported", 0, 1))
        self.assertEqual(manifest["declaration_mode"], "relaxed")
        self.assertEqual(manifest["prompt_enforced_limitations"], [
            {"category": "protected_unenforceable", "rule_ids": ["r1"], "enforced_by": "dialogue_prompt"}])
        self.assertEqual(manifest["unguarded_close_codes"], {})
        run_dir = PROJECT / manifest["run_directory"]
        events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
        relaxed = [event for event in events if event.get("event") == "relaxed_declarations"]
        self.assertEqual(len(relaxed), 1)
        self.assertEqual(relaxed[0]["prompt_enforced_limitations"], manifest["prompt_enforced_limitations"])
        snapshot = next(event for event in events if event.get("event") == "builder_source_snapshot")
        self.assertEqual(snapshot["resolved_config"]["declaration_mode"], "relaxed")
        workflow = json.loads((run_dir / "workflow.json").read_text())
        rule_text = PROTECTED.replace("{{notes}}", "((notes))")
        quote = test_builder.BRIEF_TEXT.replace("{", "(").replace("}", ")")  # no quote can read as a template
        self.assertIn(rule_text + ' Source: "' + quote + '"', workflow["system_prompt"])
        self.assertTrue(workflow["nodes"]["start"]["task_message"].endswith(rule_text))
        run = json.loads((run_dir / "manifest.json").read_text())
        export = json.loads((run_dir / "submission.workflow.json").read_text())
        briefs = [("brief.md", test_builder.BRIEF_TEXT)]
        verifier.check_recompilation(run_dir, run, export, briefs, test_builder.contract())
        strict_run = {key: value for key, value in run.items() if key != "declaration_mode"}
        with self.assertRaises(PolicyContractError):
            verifier.check_recompilation(run_dir, strict_run, export, briefs, test_builder.contract())

    def test_relaxed_failure_manifest_names_its_mode(self):
        generator = self.declaring_generator()
        generator.generate.return_value["policy"]["unsupported_constraints"] = ["Invented legacy declaration."]
        with self.assertRaises(ValueError):
            execute(self.args("--generate-only", "--declarations", "relaxed"), generator=generator,
                    client_factory=lambda **kw: self.fail("Freya touched"))
        run_dir = next((self.directory / "out").iterdir())
        failed = json.loads((run_dir / "manifest.json").read_text())
        self.assertEqual((failed["status"], failed["declaration_mode"]), ("failed", "relaxed"))


if __name__ == "__main__":
    unittest.main()

"""Source-backed call input bindings with independently invented field names."""
import copy
import unittest

from builder.call_variables import CallVariablesError, declared_call_variables
from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import compile_execution_requirements
from builder.guards import GuardCompileError, compile_guarded_workflow, required_execution_kinds
from builder.policy import PolicyError, compile_execution_policy, execution_rule_guidance
from builder.safety import digest
from builder.validation import validate_workflow
from test_execution_requirements import packet


def inputs(names=("greeting_style", "party_label")):
    policy, sources, api = packet(["close_call"], local=True, narrow=True)
    quote = "The initial call inputs are " + ", ".join("`" + name + "`" for name in names) + "."
    sources[0]["text"] += "\n" + quote
    sources[0]["sha256"] = digest(sources[0]["text"])
    policy["sources"][0]["sha256"] = sources[0]["sha256"]
    policy["rules"].append({"id": "call-inputs", "kind": "call_variables", "value": list(names),
        "source": {"id": sources[0]["id"], "quote": quote}})
    return policy, sources, api


def context(policy, sources, api):
    plan = {"initial_node": "talk", "nodes": {
        "talk": {"name": "talk", "node_type": "conversation", "task_message": "Ask whether the intended party is available.",
            "on_enter": {"type": "tts", "text": "Hello {{greeting_style}} {{party_label}}."},
            "max_node_turns": 1, "max_turn_transition": "close",
            "transitions": [{"target_node": "close", "description": "finished",
                "condition": {"operator": "matches_intent", "description": "The conversation is finished"}}]},
        "close": {"name": "close", "node_type": "guard_action", "action": "close_call", "inputs": {},
            "success_node": "talk", "blocked_node": "talk", "failure_node": "talk"}}}
    return {"plan": plan, "contract": compile_policy_contract(policy, sources, api),
            "execution_policy": compile_execution_policy(policy, required_kinds=required_execution_kinds(plan))}


class SourceCallVariableTests(unittest.TestCase):
    def test_frozen_input_reads_need_no_extraction_defaults_or_output_metadata(self):
        policy, sources, api = inputs()
        frozen = context(policy, sources, api)
        before = copy.deepcopy((policy, sources, api, frozen))
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, policy, guard_context=frozen), [])
        self.assertEqual(set(workflow), {"initial_node", "nodes"})
        self.assertNotIn("extraction_schema", workflow["nodes"]["talk"])
        self.assertEqual(declared_call_variables(frozen["contract"]), ("greeting_style", "party_label"))
        self.assertEqual((policy, sources, api, frozen), before)

    def test_policy_or_unsigned_assertion_alone_cannot_declare_validator_inputs(self):
        policy, sources, api = inputs()
        frozen = context(policy, sources, api)
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertTrue(any("unresolved required binding" in error for error in validate_workflow(workflow, api, policy)))
        forged = copy.deepcopy(frozen)
        forged["contract"]["active_rules"]["call_variables"]["value"].append("forged_input")
        with self.assertRaises(CallVariablesError):
            declared_call_variables(forged["contract"])
        self.assertTrue(validate_workflow(workflow, api, policy, guard_context=forged))

    def test_literal_source_names_cannot_be_invented_or_substring_matches(self):
        for name in ("invented_input", "party", "party_label_extra"):
            policy, sources, api = inputs()
            policy["rules"][-1]["value"] = [name]
            with self.subTest(name=name), self.assertRaisesRegex(PolicyContractError, "occur literally"):
                compile_policy_contract(policy, sources, api)

    def test_missing_source_reference_cannot_authorize_input_even_with_new_hash(self):
        policy, sources, api = inputs()
        frozen = context(policy, sources, api)
        contract = frozen["contract"]
        contract["active_rules"]["call_variables"]["provenance"][0]["id"] = "unapproved-source"
        contract["sha256"] = digest({key: value for key, value in contract.items() if key != "sha256"})
        with self.assertRaises(CallVariablesError):
            declared_call_variables(contract)

    def test_extraction_and_action_proposals_cannot_override_source_call_inputs(self):
        for mode in ("extraction", "action"):
            policy, sources, api = inputs()
            frozen = context(policy, sources, api)
            if mode == "extraction":
                frozen["plan"]["nodes"]["talk"]["extraction_schema"] = [
                    {"name": "party_label", "type": "string", "min_confidence": 0.99}]
            else:
                frozen["plan"]["nodes"]["close"]["inputs"] = {"reason": "party_label"}
            with self.subTest(mode=mode), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(openapi=api, **frozen)

    def test_undeclared_nested_and_expression_templates_fail(self):
        for binding in ("forged_input", "party_label.secret", "party_label + forged_input"):
            policy, sources, api = inputs()
            frozen = context(policy, sources, api)
            frozen["plan"]["nodes"]["talk"]["on_enter"]["text"] = "Hello {{" + binding + "}}."
            with self.subTest(binding=binding):
                try:
                    workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
                except GuardCompileError:
                    continue
                self.assertTrue(validate_workflow(workflow, api, policy, guard_context=frozen))

    def test_new_source_names_need_no_code_edits(self):
        policy, sources, api = inputs(("opening_code", "contact_label"))
        frozen = context(policy, sources, api)
        frozen["plan"]["nodes"]["talk"]["on_enter"]["text"] = "Hello {{opening_code}} {{contact_label}}."
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, policy, guard_context=frozen), [])

    def test_mapping_and_array_grant_equivalent_reads_without_description_defaults(self):
        policy, sources, api = inputs()
        array_context = context(policy, sources, api)
        policy["rules"][-1]["value"] = {"greeting_style": "Source-provided form of address",
                                        "party_label": "Source-provided intended-party label"}
        mapped = context(policy, sources, api)
        self.assertEqual(declared_call_variables(mapped["contract"]), declared_call_variables(array_context["contract"]))
        workflow = compile_guarded_workflow(openapi=api, **mapped)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, policy, guard_context=mapped), [])
        self.assertNotIn("Source-provided", workflow["nodes"]["talk"]["on_enter"]["text"])
        self.assertNotIn("test_variables", workflow)

    def test_mapping_descriptions_are_text_metadata_not_defaults(self):
        for description in (None, "", " ", 42, True, {"default": "invented"}, ["invented"]):
            policy, sources, api = inputs()
            policy["rules"][-1]["value"] = {"party_label": description}
            with self.subTest(description=description), self.assertRaises((PolicyContractError, PolicyError, ValueError)):
                compile_execution_requirements(policy, sources, api)

    def test_controller_credential_namespaces_cannot_gain_source_declared_reads(self):
        for name in ("BANK_TOKEN", "bank_token", "Bank_Token", "auth_scheme", "Auth_Custom", "AUTH_TEAM", "BG_State"):
            policy, sources, api = inputs((name,))
            with self.subTest(name=name), self.assertRaises((PolicyContractError, PolicyError, ValueError)):
                compile_execution_requirements(policy, sources, api)

    def test_no_declaration_preserves_existing_validation_and_no_names_are_required(self):
        policy, sources, api = inputs()
        policy["rules"] = policy["rules"][:-1]
        frozen = context(policy, sources, api)
        self.assertEqual(declared_call_variables(frozen["contract"]), ())
        frozen["plan"]["nodes"]["talk"]["on_enter"]["text"] = "Hello."
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, policy, guard_context=frozen), [])

    def test_empty_declaration_and_core_restrictions_remain_explicit(self):
        policy, sources, api = inputs(())
        frozen = context(policy, sources, api)
        self.assertEqual(declared_call_variables(frozen["contract"]), ())
        policy, sources, api = inputs(("customer_id", "now"))
        frozen = context(policy, sources, api)
        frozen["plan"]["nodes"]["talk"]["on_enter"]["text"] = "Current date {{now}}."
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertEqual(validate_workflow(workflow, api, policy, guard_context=frozen), [])
        frozen["plan"]["nodes"]["talk"]["on_enter"]["text"] = "Reference {{customer_id}}."
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(openapi=api, **frozen)

    def test_bad_declaration_shape_is_repairable_before_freeze(self):
        for value in (None, {}, "party_label", ["party_label", "party_label"],
                      ["bg_state"], ["BANK_TOKEN"], ["bad.field"], [True]):
            policy, sources, api = inputs()
            policy["rules"][-1]["value"] = value
            with self.subTest(value=value), self.assertRaises((PolicyContractError, PolicyError, ValueError)):
                compile_execution_requirements(policy, sources, api)
        self.assertIn("call_variables", execution_rule_guidance())

    def test_explicit_amendment_replaces_old_input_declarations(self):
        policy, sources, api = inputs()
        text = "Replace the initial party_label field with `contact_label`; `greeting_style` stays."
        source = {"id": "overlay", "file": "overlay.txt", "text": text, "sha256": digest(text)}
        sources.append(source)
        policy["sources"].append({key: value for key, value in source.items() if key != "text"})
        policy["rules"].append({"id": "new-inputs", "kind": "call_variables",
            "value": ["greeting_style", "contact_label"], "source": {"id": "overlay", "quote": text},
            "overrides": ["call-inputs"]})
        frozen = context(policy, sources, api)
        self.assertEqual(declared_call_variables(frozen["contract"]), ("greeting_style", "contact_label"))
        workflow = compile_guarded_workflow(openapi=api, **frozen)["workflow"]
        self.assertTrue(any("party_label" in error for error in validate_workflow(workflow, api, policy, guard_context=frozen)))


if __name__ == "__main__":
    unittest.main()

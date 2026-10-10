"""Local compiler coverage with invented policy witnesses; no model or I/O."""
import copy
import unittest
from unittest.mock import patch

from builder.execution_requirements import (ExecutionRequirementsError,
    compile_execution_requirements, validate_plan_execution)
from builder.guards import execution_action_catalogue
from builder.policy import execution_rule_guidance
from builder.safety import digest
from test_guards import fixture


def packet(actions=None, *, local=False, narrow=False):
    context = fixture()  # Independently invented schema/profile, never evaluator cases.
    catalogue = execution_action_catalogue()
    selected = list(actions if actions is not None else catalogue)
    profile = context["execution_policy"]["profile"]
    profile["record_notes"] = not local
    profile["wire_codes"] = {
        "reasons": {"refused": "refused", "dispute": "dispute", "service_error": "service_error",
                    "hardship": "hardship", "failed": "identity_failed"},
        "queues": {"support": "specialist", "general": "helpdesk"}}
    if narrow:
        needed = {kind for action in selected for kind in catalogue[action]["required_kinds"]}
        profile = {kind: value for kind, value in profile.items() if kind in needed or kind == "wire_codes"}
    allowed = sorted({catalogue[action]["operation_role"] for action in selected
                      if not (local and catalogue[action]["operation_role"] == "add_note")})
    text = "An invented policy authorizes the selected roles and their typed restrictions."
    sources = [{"id": "synthetic", "file": "invented-policy.md", "text": text, "sha256": digest(text)}]
    policy = {"version": 1, "goal": "An invented service goal", "allowed_actions": allowed,
              "sources": [{key: value for key, value in sources[0].items() if key != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": copy.deepcopy(value),
                         "source": {"id": "synthetic", "quote": text}}
                        for kind, value in {**profile, "execution_actions": selected}.items()],
              "unsupported_constraints": []}
    return policy, sources, context["openapi"]


def plan(actions):
    catalogue = execution_action_catalogue()
    nodes = {"talk": {"name": "talk", "node_type": "conversation", "task_message": "Discuss the sourced goal.",
                      "transitions": []}}
    for index, action in enumerate(actions):
        name = "action_" + str(index)
        nodes[name] = {"name": name, "node_type": "guard_action", "action": action,
                       "inputs": {field: "proposal_" + field for field in catalogue[action]["input_fields"]},
                       "success_node": "talk", "blocked_node": "talk", "failure_node": "talk"}
        nodes["talk"]["transitions"].append({"target_node": name, "description": "selected role",
                                            "condition": {"operator": "always"}})
    return {"initial_node": "talk", "nodes": nodes}


def rule(policy, kind):
    return next(item for item in policy["rules"] if item["kind"] == kind)


class ExecutionRequirementsTests(unittest.TestCase):
    def test_complete_selected_scope_is_hash_bound_and_detached(self):
        policy, sources, api = packet()
        before = copy.deepcopy((policy, sources, api))
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertEqual(descriptor["sha256"], digest({k: v for k, v in descriptor.items() if k != "sha256"}))
        self.assertEqual(descriptor, compile_execution_requirements(policy, sources, api))
        self.assertFalse(descriptor["semantic_completeness_verified"])
        self.assertFalse(descriptor["runtime_behavior_verified"])
        self.assertEqual(descriptor["action_rule_ids"], ["execution_actions"])
        self.assertEqual(validate_plan_execution(plan(descriptor["actions"]), descriptor, policy, sources, api), [])
        descriptor["actions"].clear()
        descriptor["shape_guidance"]["identity_fields"] = "tampered"
        self.assertEqual((policy, sources, api), before)
        self.assertTrue(compile_execution_requirements(policy, sources, api)["actions"])

    def test_all_five_missing_dependencies_reported_before_guard_compile(self):
        policy, sources, api = packet()
        missing = {"note_reasons", "product_order", "promise_failure_disposition", "resolve_all_products", "transfer_prerequisites"}
        policy["rules"] = [item for item in policy["rules"] if item["kind"] not in missing]
        with patch("builder.execution_requirements.compile_guarded_workflow") as compiler:
            with self.assertRaises(ExecutionRequirementsError) as raised:
                compile_execution_requirements(policy, sources, api)
            compiler.assert_not_called()
        self.assertEqual(set(raised.exception.missing_kinds), missing)
        for kind in missing:
            self.assertTrue(any(kind in error and "expected" in error for error in raised.exception.errors))

    def test_missing_or_malformed_capability_declaration_is_not_inferred(self):
        for value in (None, [], "all tools", ["close_call", "close_call"], ["unknown_secret_role"]):
            policy, sources, api = packet()
            if value is None:
                policy["rules"] = [item for item in policy["rules"] if item["kind"] != "execution_actions"]
            else:
                rule(policy, "execution_actions")["value"] = value
            with self.subTest(value=value), self.assertRaises(ExecutionRequirementsError) as raised:
                compile_execution_requirements(policy, sources, api)
            self.assertNotIn("unknown_secret_role", str(raised.exception))

    def test_capability_declaration_requires_literal_source_provenance(self):
        policy, sources, api = packet()
        rule(policy, "execution_actions")["source"]["quote"] = "not a supplied witness"
        with self.assertRaises(ExecutionRequirementsError):
            compile_execution_requirements(policy, sources, api)

    def test_current_compiler_close_requirement_is_explicit_without_defaults(self):
        policy, sources, api = packet(["verify_identity"], narrow=True)
        with self.assertRaisesRegex(ExecutionRequirementsError, "current sealed phone compiler requires selected close_call"):
            compile_execution_requirements(policy, sources, api)
        self.assertNotIn("product_order", {item["kind"] for item in policy["rules"]})

    def test_identity_and_close_scope_does_not_require_money_or_calendar(self):
        actions = ["verify_identity", "close_call"]
        policy, sources, api = packet(actions, narrow=True)
        descriptor = compile_execution_requirements(policy, sources, api)
        for kind in ("floor_fields", "deadline_days", "band_ranges", "holidays", "promise_failure_limit", "transfer_prerequisites"):
            self.assertNotIn(kind, descriptor["required_kinds"])
        self.assertEqual(descriptor["actions"], sorted(actions))

    def test_callback_and_close_scope_does_not_require_promise_bands(self):
        policy, sources, api = packet(["schedule_callback", "close_call"], narrow=True)
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertIn("callback_window", descriptor["required_kinds"])
        self.assertNotIn("deadline_days", descriptor["required_kinds"])
        self.assertNotIn("floor_fields", descriptor["required_kinds"])

    def test_local_dispositions_do_not_authorize_bank_notes(self):
        policy, sources, api = packet(local=True)
        self.assertNotIn("add_note", policy["allowed_actions"])
        descriptor = compile_execution_requirements(policy, sources, api)
        for action in ("stage_note", "finalize_notes"):
            self.assertEqual(descriptor["operation_bindings"][action], {
                "operation_role": "add_note", "operation": None, "mode": "local_disposition"})
        rule(policy, "record_notes")["value"] = True
        with self.assertRaisesRegex(ExecutionRequirementsError, "permitted frozen API operation"):
            compile_execution_requirements(policy, sources, api)

    def test_unused_nonpersistent_metadata_cannot_enable_forbidden_tool(self):
        policy, sources, api = packet(local=True)
        rule(policy, "nonpersistent_failures")["value"]["add_note"] = [{"http_status": 503}]
        with self.assertRaisesRegex(ExecutionRequirementsError, "exact permitted API operation IDs"):
            compile_execution_requirements(policy, sources, api)
        self.assertNotIn("add_note", policy["allowed_actions"])

    def test_aliases_bind_actual_frozen_operation_names(self):
        policy, sources, api = packet()
        aliases = {operation: "bank_" + operation for operation in policy["allowed_actions"]}
        for methods in api["paths"].values():
            operation = methods["post"]
            operation["operationId"] = aliases[operation["operationId"]]
        policy["allowed_actions"] = list(aliases.values())
        witness = policy["rules"][0]["source"]
        policy["rules"].append({"id": "aliases", "kind": "tool_aliases", "value": aliases, "source": copy.deepcopy(witness)})
        guarantees = rule(policy, "nonpersistent_failures")["value"]
        rule(policy, "nonpersistent_failures")["value"] = {aliases[name]: cases for name, cases in guarantees.items()}
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertEqual(descriptor["operation_bindings"]["commit_promises"]["operation"], "bank_create_promise")
        self.assertEqual(descriptor["operation_bindings"]["stage_promise"]["operation_role"], "create_promise")

    def test_missing_or_noninjective_aliases_fail_before_generation(self):
        policy, sources, api = packet()
        api["paths"]["/verify_identity"]["post"]["operationId"] = "bank_verify"
        policy["allowed_actions"] = ["bank_verify" if name == "verify_identity" else name for name in policy["allowed_actions"]]
        with self.assertRaisesRegex(ExecutionRequirementsError, "explicit mappings"):
            compile_execution_requirements(policy, sources, api)
        policy["rules"].append({"id": "aliases", "kind": "tool_aliases", "value": {"verify_identity": "bank_verify", "get_account": "bank_verify"}, "source": copy.deepcopy(policy["rules"][0]["source"])})
        with self.assertRaisesRegex(ExecutionRequirementsError, "injectively"):
            compile_execution_requirements(policy, sources, api)

    def test_cross_rule_queue_mismatch_is_pre_freeze_error(self):
        policy, sources, api = packet()
        rule(policy, "transfer_prerequisites")["value"].pop("specialist")
        with self.assertRaisesRegex(ExecutionRequirementsError, "exactly match"):
            compile_execution_requirements(policy, sources, api)

    def test_all_present_typed_shapes_are_checked_without_echoing_values(self):
        policy, sources, api = packet()
        rule(policy, "product_order")["value"] = "private-value-is-not-a-shape"
        with self.assertRaises(ExecutionRequirementsError) as raised:
            compile_execution_requirements(policy, sources, api)
        self.assertIn("product_order", str(raised.exception))
        self.assertNotIn("private-value-is-not-a-shape", str(raised.exception))

    def test_later_plan_must_be_subset_of_frozen_selected_roles(self):
        policy, sources, api = packet(["close_call"], narrow=True)
        descriptor = compile_execution_requirements(policy, sources, api)
        errors = validate_plan_execution(plan(["verify_identity", "close_call"]), descriptor, policy, sources, api)
        self.assertEqual(errors, ["plan action is outside frozen execution_actions: verify_identity"])

    def test_later_plan_cannot_omit_selected_goal_capabilities(self):
        policy, sources, api = packet()
        descriptor = compile_execution_requirements(policy, sources, api)
        errors = validate_plan_execution(plan(["close_call"]), descriptor, policy, sources, api)
        self.assertEqual(errors, sorted("plan omits selected execution_actions capability: " + action
                                        for action in descriptor["actions"] if action != "close_call"))

    def test_plan_role_alias_key_is_rejected_with_fixed_operation_guidance(self):
        policy, sources, api = packet()
        policy["rules"].append({"id": "aliases", "kind": "tool_aliases", "value": {"commit_promises": "create_promise"},
                                "source": copy.deepcopy(policy["rules"][0]["source"])})
        with self.assertRaisesRegex(ExecutionRequirementsError, "operation_role values"):
            compile_execution_requirements(policy, sources, api)

    def test_self_hashed_tampering_and_stale_context_are_rejected(self):
        policy, sources, api = packet()
        descriptor = compile_execution_requirements(policy, sources, api)
        tampered = copy.deepcopy(descriptor)
        tampered["required_kinds"].remove("promise_failure_disposition")
        tampered["sha256"] = digest({key: value for key, value in tampered.items() if key != "sha256"})
        self.assertTrue(validate_plan_execution(plan(descriptor["actions"]), tampered, policy, sources, api))
        api["paths"]["/create_promise"]["post"]["description"] = "Changed supplied API annotation"
        self.assertTrue(validate_plan_execution(plan(descriptor["actions"]), descriptor, policy, sources, api))

    def test_stale_source_hash_and_malformed_plan_fail_closed(self):
        policy, sources, api = packet()
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertTrue(validate_plan_execution(None, descriptor, policy, sources, api))
        sources[0]["text"] += " A new amendment."
        self.assertTrue(validate_plan_execution(plan(descriptor["actions"]), descriptor, policy, sources, api))

    def test_public_metadata_accessors_cannot_mutate_trusted_tables(self):
        catalogue = execution_action_catalogue()
        catalogue["stage_promise"]["required_kinds"].clear()
        catalogue["stage_note"]["operation_role"] = "close_call"
        guidance = execution_rule_guidance()
        guidance["identity_fields"] = "tampered"
        self.assertIn("floor_fields", execution_action_catalogue()["stage_promise"]["required_kinds"])
        self.assertEqual(execution_action_catalogue()["stage_note"]["operation_role"], "add_note")
        self.assertNotEqual(execution_rule_guidance()["identity_fields"], "tampered")


if __name__ == "__main__":
    unittest.main()

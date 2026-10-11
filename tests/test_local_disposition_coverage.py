"""Invented sourced local closure interfaces; no fixtures or service calls."""
import copy
import unittest
from unittest.mock import patch

from builder.action_bindings import compile_action_bindings
from builder.contract import compile_policy_contract
from builder.execution_requirements import (ExecutionRequirementsError, _inspection_plan,
    _local_disposition_errors, compile_execution_requirements)
from builder.guards import GuardCompileError, _catalogue, _profile, execution_action_catalogue, run_guard_step
from builder.policy import compile_execution_policy
from builder.safety import digest


def invented_packet(*, marker=False, persisted=False, actions=None, paid_outcome=True):
    def string(enum=None):
        return {"type": "string", **({"enum": enum} if enum is not None else {})}
    aliases = {"verify_identity": "check_member_Q", "get_account": "read_lines_Q", "close_call": "finish_contact_Q"}
    outcomes = {"paid": "END_SETTLED_Q", "no_promise": "END_DECLINE_Q",
                "not_verified": "END_AUTH_Q", "not_reached": "END_ABSENT_Q"}
    reasons = {"refused": "WHY_DECLINE_Q", "failed": "WHY_AUTH_Q", "unclear": "WHY_ABSENT_Q"}
    note_codes = {"refused": "API_STOP_Q" if persisted else "LOCAL_STOP_Q"}
    if marker:
        note_codes["paid"] = "API_SETTLED_Q" if persisted else "LOCAL_SETTLED_Q"
    profile = {"identity_fields": ["member_key"], "identity_wrong_limit": 2,
        "call_variables": {"display_key": "Read-only display input"},
        "policy_applicability": {"products": "single"}, "record_notes": persisted,
        "note_reasons": list(note_codes.values()), "tool_aliases": aliases,
        "wire_codes": {"outcomes": outcomes, "reasons": reasons, "notes": note_codes},
        "close_reasons": {outcomes["paid"]: [], outcomes["no_promise"]: [reasons["refused"]],
                          outcomes["not_verified"]: [reasons["failed"]], outcomes["not_reached"]: [reasons["unclear"]]},
        "close_precedence": [outcomes["paid"], outcomes["no_promise"]],
        "identity_close": {"failed": {"outcome": outcomes["not_verified"], "reason": reasons["failed"]},
                           "incomplete": {"outcome": outcomes["not_verified"], "reason": reasons["failed"]},
                           "unreached": {"outcome": outcomes["not_reached"], "reason": reasons["unclear"]}}}
    if not paid_outcome:
        profile["close_reasons"].pop(outcomes["paid"])
        profile["wire_codes"]["outcomes"].pop("paid")
        profile["close_precedence"] = [outcomes["no_promise"]]
    selected = list(actions if actions is not None else
                    ("verify_identity", "get_account", "stage_note", "finalize_notes", "close_call"))
    profile["execution_actions"] = selected
    fields = {"verify_identity": {"customer_id": string(), "field": string(["member_key"]), "answer": string()},
              "get_account": {"customer_id": string()},
              "close_call": {"customer_id": string(), "outcome": string(list(outcomes.values())), "reason": string(list(reasons.values()))},
              "add_note": {"customer_id": string(), "product_id": string(), "reason": string(["API_STOP_Q", "API_SETTLED_Q"])}}
    product = {"type": "object", "properties": {"product_id": string(), "type": string(),
               "days_late": {"type": "integer"}, "past_due": {"type": "number"}}}
    results = {"verify_identity": {"ok": {"type": "boolean"}, "verified": {"type": "boolean"}, "wrong_answers_left": {"type": "integer"}},
               "get_account": {"ok": {"type": "boolean"}, "products": {"type": "array", "items": product}},
               "close_call": {"ok": {"type": "boolean"}}, "add_note": {"ok": {"type": "boolean"}}}
    all_aliases = {**aliases, "add_note": "write_note_Q"}
    api = {"openapi": "3.0.3", "servers": [{"url": "https://invented.bank.invalid/v43"}], "paths": {}}
    for role, request in fields.items():
        api["paths"]["/" + all_aliases[role]] = {"post": {"operationId": all_aliases[role],
            "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": request,
                "required": [name for name in request if not (role == "close_call" and name == "reason")]}}}},
            "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": results[role]}}}}}}}
    if persisted:
        profile["tool_aliases"]["add_note"] = all_aliases["add_note"]
    catalogue = execution_action_catalogue()
    allowed = sorted({all_aliases[catalogue[action]["operation_role"]] for action in selected
                      if persisted or catalogue[action]["operation_role"] != "add_note"})
    profile["tool_aliases"] = {role: actual for role, actual in profile["tool_aliases"].items() if actual in allowed}
    source = "Invented single-line policy defines the declared closing codes and local disposition meanings. display_key is supplied at call start."
    sources = [{"id": "invented", "file": "invented.txt", "text": source, "sha256": digest(source)}]
    policy = {"version": 1, "goal": "Invented account contact", "allowed_actions": allowed,
              "sources": [{key: value for key, value in sources[0].items() if key != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": "invented", "quote": source}}
                        for kind, value in profile.items()], "unsupported_constraints": []}
    return policy, sources, api


def selected_context(policy, sources, api):
    contract = compile_policy_contract(policy, sources, api)
    bindings = compile_action_bindings(policy, api)
    execution = compile_execution_policy(policy, bindings)
    actions = contract["active_rules"]["execution_actions"]["value"]
    plan = _inspection_plan(actions, execution_action_catalogue())
    return actions, contract, execution, plan, bindings


class LocalDispositionCoverageTests(unittest.TestCase):
    def test_incomplete_opaque_local_paid_coverage_rejects_before_guard_compile_without_mutation(self):
        policy, sources, api = invented_packet()
        before = copy.deepcopy((policy, sources, api))
        with patch("builder.execution_requirements.compile_guarded_workflow") as compiler:
            with self.assertRaises(ExecutionRequirementsError) as raised:
                compile_execution_requirements(policy, sources, api)
            compiler.assert_not_called()
        self.assertIn("local disposition coverage cannot represent", str(raised.exception))
        self.assertIn("source-backed note_reasons", str(raised.exception))
        for value in ("END_SETTLED_Q", "LOCAL_STOP_Q", "API_STOP_Q"):
            self.assertNotIn(value, str(raised.exception))
        self.assertEqual((policy, sources, api), before)

    def test_complete_opaque_paid_marker_is_local_and_never_grants_bank_note_permission(self):
        policy, sources, api = invented_packet(marker=True)
        before = copy.deepcopy((policy, sources, api))
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertNotIn("write_note_Q", policy["allowed_actions"])
        for action in ("stage_note", "finalize_notes"):
            self.assertEqual(descriptor["operation_bindings"][action]["mode"], "local_disposition")
            self.assertIsNone(descriptor["operation_bindings"][action]["operation"])
        self.assertEqual(_local_disposition_errors(*selected_context(policy, sources, api)), [])
        self.assertEqual((policy, sources, api), before)

    def test_complete_local_marker_can_form_protected_paid_close_through_normalized_kernel(self):
        policy, sources, api = invented_packet(marker=True)
        compile_execution_requirements(policy, sources, api)
        actions, contract, execution, plan, bindings = selected_context(policy, sources, api)
        profile = _profile(contract, execution, plan, bindings)
        catalogue = _catalogue(contract, api, bindings)
        self.assertIn("paid", profile["note_reasons"])
        self.assertIn("paid", profile["close_reasons"])
        state = run_guard_step("init", None, profile=profile, catalog=catalogue,
                               customer_id="invented-member", now="2040-03-05")[0]
        def advance(phase, action="", inputs=None, result=None):
            nonlocal state
            response = run_guard_step(phase, state, action, inputs, result, profile=profile, catalog=catalogue,
                                      maps=bindings["categories"])
            state = response[0]
            return response
        advance("prepare", "verify_identity", {"field": "member_key", "answer": "invented"})
        advance("result", result={"ok": True, "verified": True, "wrong_answers_left": 2})
        advance("prepare", "get_account")
        advance("result", result={"ok": True, "products": [{"product_id": "invented-line", "type": "invented",
            "days_late": 3, "past_due": 25}]})
        advance("prepare", "stage_note", {"product_id": "invented-line", "reason": "LOCAL_SETTLED_Q"})
        self.assertEqual(advance("prepare", "finalize_notes")[1], "done")
        close = advance("prepare", "close_call")
        self.assertEqual(close[1], "allow")
        self.assertEqual(close[2]["outcome"], "END_SETTLED_Q")
        self.assertNotIn("reason", close[2])

    def test_persisted_bank_note_mode_does_not_acquire_local_marker_default(self):
        policy, sources, api = invented_packet(persisted=True)
        before = copy.deepcopy((policy, sources, api))
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertEqual(descriptor["operation_bindings"]["finalize_notes"]["mode"], "bank_api")
        self.assertEqual(_local_disposition_errors(*selected_context(policy, sources, api)), [])
        self.assertEqual((policy, sources, api), before)

    def test_unselected_local_account_capabilities_do_not_trigger_paid_gate(self):
        full = ["verify_identity", "get_account", "stage_note", "finalize_notes", "close_call"]
        for omitted in ("get_account", "stage_note", "finalize_notes"):
            policy, sources, api = invented_packet(actions=[action for action in full if action != omitted])
            with self.subTest(omitted=omitted):
                self.assertTrue(compile_execution_requirements(policy, sources, api)["sha256"])

    def test_preidentity_only_paid_close_scope_has_no_local_account_obligation(self):
        policy, sources, api = invented_packet(actions=["close_call"])
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertEqual(descriptor["actions"], ["close_call"])
        # A pre-identity row cannot stand in for the paid fact; the refusal is
        # that protected-fact rule, never a local account marker obligation.
        identity = next(rule for rule in policy["rules"] if rule["kind"] == "identity_close")
        identity["value"]["unreached"] = {"outcome": "END_SETTLED_Q"}
        with self.assertRaisesRegex(ExecutionRequirementsError, "cannot use a protected-fact outcome"):
            compile_execution_requirements(policy, sources, api)

    def test_no_supported_paid_outcome_requires_no_paid_local_marker(self):
        policy, sources, api = invented_packet(paid_outcome=False)
        before = copy.deepcopy((policy, sources, api))
        self.assertTrue(compile_execution_requirements(policy, sources, api)["sha256"])
        self.assertEqual((policy, sources, api), before)

    def test_invalid_selected_local_profile_still_fails_before_inspection_compilation(self):
        policy, sources, api = invented_packet(marker=True)
        next(rule for rule in policy["rules"] if rule["kind"] == "note_reasons")["value"] = []
        with patch("builder.execution_requirements.compile_guarded_workflow") as compiler:
            with self.assertRaises(ExecutionRequirementsError):
                compile_execution_requirements(policy, sources, api)
            compiler.assert_not_called()

    def test_stale_or_rewritten_frozen_profile_is_not_coverage_evidence(self):
        policy, sources, api = invented_packet(marker=True)
        context = selected_context(policy, sources, api)
        context[2]["profile"]["note_reasons"] = ["LOCAL_STOP_Q"]
        with self.assertRaises(GuardCompileError):
            _local_disposition_errors(*context)
        context[2]["sha256"] = digest({key: value for key, value in context[2].items() if key != "sha256"})
        with self.assertRaisesRegex(GuardCompileError, "does not match active frozen rule"):
            _local_disposition_errors(*context)


if __name__ == "__main__":
    unittest.main()

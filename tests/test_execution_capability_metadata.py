"""Capability claims checked against invented renamed kernel operations."""
import copy
import unittest

from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import execution_action_catalogue, run_guard_step
from builder.safety import digest


def invented_context(record_notes=False):
    profile = {"identity_fields": ["member_factor"], "identity_wrong_limit": 4,
        "policy_applicability": {"products": "single"}, "resolve_all_products": True,
        "band_ranges": [{"name": "window", "min": 1, "max": 80}], "deadline_days": {"window": 3},
        "floor_fields": {"line": "floor_units"}, "business_weekdays": [0, 1, 2, 3, 4], "holidays": [],
        "require_confirmation": False, "confirmation_scope": "each", "promise_failure_limit": 2,
        "record_notes": record_notes, "note_reasons": ["cleared"],
        "transfer_reasons": {"desk": "agent_help"}, "transfer_prerequisites": {"desk": "none"}}
    requests = {
        "create_promise": {"customer_id": "member_key", "product_id": "item_key", "amount": "sum_units", "date": "due_day"},
        "add_note": {"customer_id": "member_key", "product_id": "item_key", "reason": "note_code"},
        "transfer_to_agent": {"customer_id": "member_key", "queue": "desk_code"}}
    catalog = {}
    for index, (role, fields) in enumerate(requests.items()):
        properties = {wire: {"type": "number" if name == "amount" else "string"} for name, wire in fields.items()}
        catalog[role] = {"name": "invented_op_" + str(index), "fields": fields,
                         "request_schema": {"type": "object", "properties": properties, "required": list(properties)}}
    maps = {"notes": {"semantic_to_wire": {"cleared": "N_DISP"}, "wire_to_semantic": {"N_DISP": "cleared"}},
            "queues": {"semantic_to_wire": {"desk": "Q_DESK"}, "wire_to_semantic": {"Q_DESK": "desk"}}}
    state = run_guard_step("init", None, profile=profile, catalog=catalog,
                           customer_id="invented-member", now="2031-05-02T10:00:00")[0]
    state.update(identity_status="verified", verified_fields=["member_factor"], account_loaded=True,
                 products=[{"product_id": "invented-item", "type": "line", "days_late": 3,
                            "past_due": 80, "floor_units": 10}])
    return state, profile, catalog, maps


class ExecutionCapabilityMetadataTests(unittest.TestCase):
    def setUp(self):
        self.state, self.profile, self.catalog, self.maps = invented_context()

    def prepare(self, action, inputs=None):
        before = copy.deepcopy(self.state)
        result = run_guard_step("prepare", self.state, action, inputs or {}, profile=self.profile,
                                catalog=self.catalog, maps=self.maps)
        self.assertEqual(self.state, before)
        self.state = result[0]
        return result

    def result(self, action, data):
        self.state, route, body, readback = run_guard_step("result", self.state, action, result=data,
            profile=self.profile, catalog=self.catalog, maps=self.maps)
        return route, body, readback

    def stage_promise(self):
        return self.prepare("stage_promise", {"product_id": "invented-item", "amount": 20, "date": "2031-05-06"})

    def stage_note(self):
        return self.prepare("stage_note", {"product_id": "invented-item", "reason": "N_DISP"})

    def test_identity_ok_does_not_pass_factor_and_remaining_budget_is_cumulative(self):
        self.profile = {"identity_fields": ["factor_alpha", "factor_beta"], "identity_wrong_limit": 4}
        self.catalog = {"verify_identity": {"name": "invented_check_op", "fields": {
            "customer_id": "member_wire", "field": "factor_wire", "answer": "answer_wire"},
            "request_schema": {"type": "object", "properties": {
                "member_wire": {"type": "string"},
                "factor_wire": {"type": "string", "enum": ["factor_alpha", "factor_beta"]},
                "answer_wire": {"type": "string"}},
                "required": ["member_wire", "factor_wire", "answer_wire"]}}}
        self.maps = {}
        self.state = run_guard_step("init", None, profile=self.profile, catalog=self.catalog,
                                   customer_id="invented-member", now="2031-05-02T10:00:00")[0]

        def check(field, data):
            _, route, body, _ = self.prepare("verify_identity", {"field": field, "answer": "invented-answer"})
            self.assertEqual((route, body), ("allow", {"member_wire": "invented-member",
                "factor_wire": field, "answer_wire": "invented-answer"}))
            return self.result("verify_identity", {"_http_status": 200, **data})[0]

        self.assertEqual(check("factor_alpha", {"ok": True, "verified": False, "wrong_answers_left": 3}), "done")
        self.assertEqual((self.state["wrong_answers"], self.state["verified_fields"], self.state["identity_status"]),
                         (1, [], "pending"))
        self.assertEqual(check("factor_alpha", {"ok": True, "verified": False, "wrong_answers_left": 4}), "done")
        self.assertEqual((self.state["wrong_answers"], self.state["verified_fields"]), (1, []))
        self.assertEqual(check("factor_alpha", {"ok": True, "verified": True, "wrong_answers_left": 3}), "done")
        self.assertEqual((self.state["wrong_answers"], self.state["verified_fields"], self.state["identity_status"]),
                         (1, ["factor_alpha"], "pending"))
        self.assertEqual(check("factor_beta", {"ok": True, "verified": False, "wrong_answers_left": 2}), "done")
        self.assertEqual((self.state["wrong_answers"], self.state["verified_fields"], self.state["identity_status"]),
                         (2, ["factor_alpha"], "pending"))
        self.assertEqual(check("factor_beta", {"ok": True, "verified": True}), "failure")
        self.assertTrue(self.state["quarantined"])
        self.assertEqual((self.state["wrong_answers"], self.state["verified_fields"], self.state["identity_status"]),
                         (2, ["factor_alpha"], "pending"))


    def test_catalogue_nested_metadata_is_detached_and_required_kind_dynamics_preserved(self):
        first = execution_action_catalogue()
        original = copy.deepcopy(first)
        first["stage_promise"]["controller_state_effects"]["accepted_staging"].clear()
        first["stage_note"]["required_kinds"].clear()
        first["stage_transfer"]["operation_role"] = "invented_grant"
        self.assertEqual(execution_action_catalogue(), original)
        self.assertEqual(original["stage_promise"]["operation_role"], "create_promise")
        self.assertIn("floor_fields", original["stage_promise"]["required_kinds"])
        self.assertEqual(set(original), {"verify_identity", "get_account", "stage_promise", "commit_promises",
            "stage_note", "finalize_notes", "stage_transfer", "commit_transfer", "schedule_callback", "close_call"})

    def test_pending_disposition_and_pending_promise_replace_each_other_without_bank_records(self):
        self.stage_note()
        self.assertEqual(self.state["pending_notes"], {"invented-item": "cleared"})
        self.stage_promise()
        self.assertEqual(self.state["pending_notes"], {})
        self.assertEqual(set(self.state["pending_promises"]), {"invented-item"})
        self.stage_note()
        self.assertEqual(self.state["pending_promises"], {})
        self.assertEqual(self.state["pending_notes"], {"invented-item": "cleared"})
        self.assertEqual((self.state["recorded_promises"], self.state["recorded_notes"], self.state["attempts"]), ({}, {}, {}))

    def test_recorded_promise_cannot_be_replaced_by_disposition_or_second_promise(self):
        self.stage_promise()
        _, route, body, _ = self.prepare("commit_promises")
        self.assertEqual(route, "allow")
        self.assertEqual(body["item_key"], "invented-item")
        self.assertEqual(self.state["recorded_promises"], {})
        self.assertEqual(self.result("commit_promises", {"ok": True, "promise_id": "invented-receipt", "_http_status": 200})[0], "done")
        self.assertEqual(set(self.state["recorded_promises"]), {"invented-item"})
        self.assertEqual(self.state["pending_promises"], {})
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            self.stage_note()
        with self.assertRaisesRegex(ValueError, "already recorded"):
            self.stage_promise()
        self.assertEqual(self.state, before)

    def test_positive_flag_without_record_identity_is_not_recorded_promise_evidence(self):
        self.stage_promise(); self.prepare("commit_promises")
        self.assertEqual(self.result("commit_promises", {"ok": True, "_http_status": 200})[0], "failure")
        self.assertEqual(self.state["recorded_promises"], {})
        self.assertEqual(set(self.state["pending_promises"]), {"invented-item"})
        self.assertTrue(self.state["quarantined"])

    def test_local_finalization_keeps_disposition_local_and_handoff_discards_it(self):
        del self.catalog["add_note"]  # The local path has no note API authority.
        self.stage_note()
        _, route, body, _ = self.prepare("finalize_notes")
        self.assertEqual((route, body), ("done", {}))
        self.assertTrue(self.state["notes_finalized"])
        self.assertEqual(self.state["recorded_notes"], {})
        self.prepare("stage_transfer", {"queue": "Q_DESK"})
        self.assertEqual(self.state["pending_notes"], {})
        self.assertFalse(self.state["notes_finalized"])
        self.assertEqual(self.state["transfer"]["status"], "pending")

    def test_pending_handoff_preserves_recorded_promise_until_positive_transfer_result(self):
        self.stage_promise(); self.prepare("commit_promises")
        self.result("commit_promises", {"ok": True, "promise_id": "invented-receipt", "_http_status": 200})
        recorded = copy.deepcopy(self.state["recorded_promises"])
        self.prepare("stage_transfer", {"queue": "Q_DESK"})
        self.assertEqual(self.state["recorded_promises"], recorded)
        _, route, body, _ = self.prepare("commit_transfer")
        self.assertEqual((route, body["desk_code"]), ("allow", "Q_DESK"))
        self.assertEqual(self.state["transfer"]["status"], "pending")
        self.assertEqual(self.result("commit_transfer", {"ok": True, "_http_status": 200})[0], "done")
        self.assertEqual(self.state["transfer"]["status"], "recorded")
        self.assertEqual(self.state["recorded_promises"], recorded)

    def test_handoff_rejects_persisted_notes_instead_of_discarding_bank_records(self):
        self.profile["record_notes"] = True
        self.stage_note()
        _, route, body, _ = self.prepare("finalize_notes")
        self.assertEqual((route, body["note_code"]), ("allow", "N_DISP"))
        self.assertEqual(self.state["recorded_notes"], {})
        self.result("finalize_notes", {"ok": True, "_http_status": 200})
        self.assertEqual(self.state["recorded_notes"], {"invented-item": "cleared"})
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            self.prepare("stage_transfer", {"queue": "Q_DESK"})
        self.assertEqual(self.state, before)

    def test_metadata_cannot_supply_queue_prerequisites_or_bypass_them(self):
        execution_action_catalogue()
        self.profile["transfer_prerequisites"]["desk"] = "account"
        self.state["account_loaded"] = False
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "required identity/account evidence"):
            self.prepare("stage_transfer", {"queue": "Q_DESK"})
        self.assertEqual(self.state, before)

    def test_metadata_grants_no_api_permission_and_does_not_clear_unsupported_declarations(self):
        witness = "Invented source defines only the declared call finish operation."
        sources = [{"id": "invented", "file": "invented.txt", "text": witness, "sha256": digest(witness)}]
        api = {"openapi": "3.0.3", "servers": [{"url": "https://invented.invalid/v3"}], "paths": {
            "/finish": {"post": {"operationId": "op_finishX", "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": {"customer_id": {"type": "string"}, "outcome": {"type": "string"}},
                "required": ["customer_id", "outcome"]}}}}, "responses": {"200": {"description": "Invented positive result."}}}}}}
        values = {**self.profile, "execution_actions": ["stage_promise", "close_call"],
            "close_reasons": {"promise": [], "not_reached": []}, "close_precedence": ["promise"],
            "identity_close": {phase: {"outcome": "not_reached"} for phase in ("failed", "incomplete", "unreached")},
            "tool_aliases": {"close_call": "op_finishX"}}
        policy = {"version": 1, "goal": "Invented declared finish", "allowed_actions": ["op_finishX"],
            "sources": [{k: v for k, v in sources[0].items() if k != "text"}],
            "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": "invented", "quote": witness}}
                      for kind, value in values.items()], "unsupported_constraints": []}
        original = copy.deepcopy((policy, sources, api))
        execution_action_catalogue()
        with self.assertRaisesRegex(ExecutionRequirementsError, "selected action lacks its permitted frozen API operation"):
            compile_execution_requirements(policy, sources, api)
        self.assertEqual((policy, sources, api), original)
        policy["unsupported_constraints"] = ["Invented unresolved source conflict."]
        before = copy.deepcopy(policy)
        execution_action_catalogue()
        with self.assertRaisesRegex(PolicyContractError, "unsupported constraints"):
            compile_policy_contract(policy, sources, api)
        self.assertEqual(policy, before)


if __name__ == "__main__":
    unittest.main()

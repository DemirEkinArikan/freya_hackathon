"""Independent synthetic development cases for policy helpers and wire codes.

No benchmark or held-out fixtures are imported. These pure tests do not prove
that generated conversation code uses the helpers or protects their state.
"""
from copy import deepcopy
import json
import unittest

from builder import action_bindings as binding_api
from builder import policy as helpers


NOW = "2033-12-23T11:00"
PRODUCTS = [
    {"product_id": "acct-revolver", "type": "revolver", "past_due": "103.17",
     "minimum_due_now": "40.09", "days_late": 5},
    {"product_id": "acct-vehicle", "type": "vehicle", "past_due": "227.43",
     "current_due": "65.71", "days_late": 6},
]
OUTCOMES = {"promise": "END_A7", "paid": "END_B4", "transferred": "END_C9",
            "no_promise": "END_D2", "not_verified": "END_E5", "not_reached": "END_F8"}
REASONS = {"refused": "C_NO", "hardship": "C_DISTRESS", "dispute": "C_QUERY", "service_error": "C_TECH",
           "human": "C_OPERATOR", "support": "C_CARE", "restructuring": "C_PLAN",
           "failed": "C_BAD_ID", "declined": "C_DECLINED_ID", "callback": "C_LATER",
           "no_callback": "C_NO_SLOT", "wrong_number": "C_WRONG_LINE", "deceased": "C_DECEASED", "unclear": "C_UNCLEAR"}
NOTES = {"paid": "ANNOT_PAID", "refused": "ANNOT_NO", "service_error": "ANNOT_TECH",
         "dispute": "ANNOT_QUERY", "hardship": "ANNOT_DISTRESS"}
QUEUES = {"support": "HAND_0", "general": "HAND_1", "restructuring": "HAND_2"}


def synthetic_policy():
    return {
        "identity_fields": ["birth_tag", "contact_tail"], "identity_wrong_limit": 3,
        "holidays": ["2033-12-26", "2034-01-02"], "business_weekdays": [0, 1, 2, 3, 4],
        "band_ranges": [{"name": "recent", "min": 1, "max": 5}, {"name": "aged", "min": 6, "max": 45}],
        "deadline_days": {"recent": 3, "aged": 1},
        "floor_fields": {"revolver": "minimum_due_now", "vehicle": "current_due"},
        "callback_window": {"start": "10:00", "end": "18:00", "allow_today": True, "next_business_days": 1},
        "require_confirmation": True, "promise_failure_limit": 2, "persuasion_limits": {"recent": 1, "aged": 2},
        "wire_codes": {"outcomes": deepcopy(OUTCOMES), "reasons": deepcopy(REASONS),
                       "notes": deepcopy(NOTES), "queues": deepcopy(QUEUES)},
        "note_reasons": list(NOTES.values()),
        "transfer_reasons": {QUEUES["support"]: REASONS["support"], QUEUES["general"]: REASONS["human"],
                             QUEUES["restructuring"]: REASONS["restructuring"]},
        "close_reasons": {OUTCOMES["promise"]: [], OUTCOMES["paid"]: [],
                          OUTCOMES["transferred"]: [REASONS[x] for x in ("human", "support", "restructuring")],
                          OUTCOMES["no_promise"]: [REASONS[x] for x in ("refused", "hardship", "dispute", "service_error")],
                          OUTCOMES["not_verified"]: [REASONS[x] for x in ("failed", "declined")],
                          OUTCOMES["not_reached"]: [REASONS[x] for x in ("callback", "no_callback", "wrong_number", "deceased", "unclear")]},
    }


def synthetic_api():
    specs = {
        "verify_identity": {"customer_id": "string", "field": "string", "answer": "string"},
        "get_account": {"customer_id": "string"},
        "create_promise": {"customer_id": "string", "product_id": "string", "amount": "number", "date": "string"},
        "add_note": {"customer_id": "string", "product_id": "string", "reason": "string"},
        "schedule_callback": {"customer_id": "string", "date": "string", "time": "string"},
        "transfer_to_agent": {"customer_id": "string", "queue": "string"},
        "close_call": {"customer_id": "string", "outcome": "string", "reason": "string"},
    }
    paths = {}
    for action, fields in specs.items():
        properties = {field: {"type": kind, "description": "Semantic field: " + field} for field, kind in fields.items()}
        if action == "close_call":
            properties["outcome"]["enum"] = list(OUTCOMES.values())
            properties["reason"]["enum"] = list(REASONS.values())
        if action == "add_note": properties["reason"]["enum"] = list(NOTES.values())
        if action == "transfer_to_agent": properties["queue"]["enum"] = list(QUEUES.values())
        schema = {"type": "object", "properties": properties,
                  "required": [field for field in fields if not (action == "close_call" and field == "reason")]}
        paths["/" + action] = {"post": {"operationId": action, "requestBody": {"content": {"application/json": {"schema": schema}}},
                                      "responses": {"200": {"description": "Synthetic result", "content": {"application/json": {"schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}}}}}}}
    return {"openapi": "3.0.3", "info": {"title": "Independent synthetic banking policy", "version": "1"},
            "servers": [{"url": "https://independent-bank.invalid/v1"}], "paths": paths}


def request_schema(api, action):
    return api["paths"]["/" + action]["post"]["requestBody"]["content"]["application/json"]["schema"]


class IndependentPolicyBindingsTests(unittest.TestCase):
    def setUp(self):
        self.policy = synthetic_policy()
        self.api = synthetic_api()
        self.bindings = binding_api.compile_action_bindings(self.policy, self.api)

    def pending(self, product_id="acct-revolver", amount="40.09", day="2033-12-27", state=None):
        return helpers.stage_commitment(state or {}, PRODUCTS, product_id, amount, day, NOW, self.policy)

    def promised(self, state=None):
        state = helpers.confirm_commitments(self.pending(state=state))
        return helpers.record_commitment_result(state, "acct-revolver", {"ok": True}, policy=self.policy)

    def noted(self, state, product_id, meaning):
        state = helpers.stage_note(state, PRODUCTS, product_id, NOTES[meaning], self.policy, bindings=self.bindings)
        return helpers.record_note_result(state, product_id, {"ok": True})

    def test_binding_descriptor_round_trips_opaque_vocabularies_as_plain_json(self):
        descriptor = json.loads(json.dumps(self.bindings))
        for category, mapping in (("outcomes", OUTCOMES), ("reasons", REASONS), ("notes", NOTES), ("queues", QUEUES)):
            for meaning, wire in mapping.items():
                with self.subTest(category=category, meaning=meaning):
                    self.assertEqual(binding_api.encode_action_value(descriptor, category, meaning), wire)
                    self.assertEqual(binding_api.decode_action_value(descriptor, category, wire), meaning)
        self.assertNotEqual(NOTES["refused"], REASONS["refused"])

    def test_compilation_detaches_policy_and_api_inputs(self):
        snapshot = deepcopy(self.bindings)
        self.policy["wire_codes"]["outcomes"]["promise"] = "different-code"
        request_schema(self.api, "close_call")["properties"]["outcome"]["enum"].clear()
        self.assertEqual(self.bindings, snapshot)
        self.assertEqual(binding_api.encode_action_value(self.bindings, "outcomes", "promise"), OUTCOMES["promise"])

    def test_opaque_policy_requires_explicit_api_checked_bindings(self):
        with self.assertRaises(helpers.PolicyError):
            helpers.choose_close(self.promised(), PRODUCTS, self.policy)
        with self.assertRaises(helpers.PolicyError):
            helpers.stage_transfer({}, QUEUES["general"], self.policy)
        with self.assertRaises(helpers.PolicyError):
            helpers.stage_note({}, PRODUCTS, "acct-revolver", NOTES["refused"], self.policy)

    def test_missing_or_ambiguous_meaning_mapping_fails_closed(self):
        for change in ("no_map", "missing_meaning", "duplicate_wire"):
            with self.subTest(change=change):
                policy = deepcopy(self.policy)
                if change == "no_map": policy.pop("wire_codes")
                if change == "missing_meaning": policy["wire_codes"]["outcomes"].pop("promise")
                if change == "duplicate_wire": policy["wire_codes"]["outcomes"]["paid"] = OUTCOMES["promise"]
                with self.assertRaises(ValueError):
                    binding_api.compile_action_bindings(policy, self.api)

    def test_mapping_must_agree_with_api_enum_and_policy_vocabulary(self):
        api = deepcopy(self.api)
        request_schema(api, "add_note")["properties"]["reason"]["enum"].remove(NOTES["refused"])
        with self.assertRaises(ValueError):
            binding_api.compile_action_bindings(self.policy, api)
        policy = deepcopy(self.policy)
        policy["transfer_reasons"][QUEUES["general"]] = "UNDECLARED_REASON"
        with self.assertRaises(ValueError):
            binding_api.compile_action_bindings(policy, self.api)

    def test_absent_api_enum_is_not_inferred_from_code_labels(self):
        api = deepcopy(self.api)
        request_schema(api, "close_call")["properties"]["outcome"].pop("enum")
        descriptor = binding_api.compile_action_bindings(self.policy, api)
        category = descriptor["categories"]["outcomes"]
        self.assertEqual(category["api_enum"], [])
        self.assertIs(category["api_enum_constrained"], False)
        self.assertEqual(category["semantic_to_wire"], self.bindings["categories"]["outcomes"]["semantic_to_wire"])
        self.assertNotIn("enum", request_schema(api, "close_call")["properties"]["outcome"])
        self.assertEqual(binding_api.checked_action_bindings(self.policy, descriptor), descriptor)
        for invalid in ([], None, "arbitrary", ["duplicate", "duplicate"]):
            request_schema(api, "close_call")["properties"]["outcome"]["enum"] = invalid
            with self.assertRaises(ValueError):
                binding_api.compile_action_bindings(self.policy, api)

    def test_renamed_api_field_uses_supplied_semantic_metadata(self):
        api = deepcopy(self.api)
        schema = request_schema(api, "close_call")
        schema["properties"]["completion_tag"] = schema["properties"].pop("outcome")
        schema["required"][schema["required"].index("outcome")] = "completion_tag"
        bindings = binding_api.compile_action_bindings(self.policy, api)
        self.assertEqual(bindings["categories"]["outcomes"]["field"], "completion_tag")
        self.assertEqual(binding_api.encode_action_value(bindings, "outcomes", "promise"), OUTCOMES["promise"])

    def test_ambiguous_api_fields_require_an_explicit_policy_selection(self):
        api = deepcopy(self.api)
        schema = request_schema(api, "close_call")
        schema["properties"]["completion_tag"] = deepcopy(schema["properties"]["outcome"])
        with self.assertRaises(ValueError):
            binding_api.compile_action_bindings(self.policy, api)
        policy = deepcopy(self.policy)
        policy["action_fields"] = {"close_call": {"outcome": "completion_tag"}}
        binding = binding_api.compile_action_bindings(policy, api)
        self.assertEqual(binding["categories"]["outcomes"]["field"], "completion_tag")

    def test_opaque_promise_close_requires_successful_promise_evidence(self):
        close = {"outcome": OUTCOMES["promise"]}
        for state in ({}, {"close_proposal": close}, helpers.confirm_commitments(self.pending())):
            with self.subTest(state_keys=sorted(state)):
                with self.assertRaises(helpers.PolicyError):
                    helpers.record_close_result(state, close, {"ok": True}, self.policy, bindings=self.bindings)
        with self.assertRaises(helpers.PolicyError):
            helpers.choose_close({"close_proposal": close}, PRODUCTS, self.policy, bindings=self.bindings)
        state = self.promised()
        self.assertEqual(helpers.choose_close(state, PRODUCTS, self.policy, bindings=self.bindings), close)
        closed = helpers.record_close_result(state, close, {"ok": True}, self.policy, bindings=self.bindings)
        self.assertTrue(closed["closed"])
        self.assertEqual(closed["close"], close)

    def test_failed_promise_cannot_establish_opaque_promise_outcome(self):
        state = helpers.confirm_commitments(self.pending())
        state = helpers.record_commitment_result(state, "acct-revolver", {"ok": False}, policy=self.policy)
        with self.assertRaises(helpers.PolicyError):
            helpers.record_close_result(state, {"outcome": OUTCOMES["promise"]}, {"ok": True}, self.policy, bindings=self.bindings)

    def test_opaque_transfer_close_requires_success_and_matching_reason(self):
        good = {"outcome": OUTCOMES["transferred"], "reason": REASONS["human"]}
        staged = helpers.stage_transfer({}, QUEUES["general"], self.policy, bindings=self.bindings)
        failed = helpers.record_transfer_result(staged, {"ok": False})
        for state in ({}, staged, failed):
            with self.subTest(state_keys=sorted(state)):
                with self.assertRaises(helpers.PolicyError):
                    helpers.record_close_result(state, good, {"ok": True}, self.policy, bindings=self.bindings)
        recorded = helpers.record_transfer_result(staged, {"ok": True})
        wrong = {"outcome": OUTCOMES["transferred"], "reason": REASONS["support"]}
        with self.assertRaises(helpers.PolicyError):
            helpers.record_close_result(recorded, wrong, {"ok": True}, self.policy, bindings=self.bindings)
        self.assertEqual(helpers.choose_close(recorded, PRODUCTS, self.policy, bindings=self.bindings), good)
        self.assertTrue(helpers.record_close_result(recorded, good, {"ok": True}, self.policy, bindings=self.bindings)["closed"])

    def test_product_order_maps_note_meanings_to_different_close_reason_codes(self):
        state = self.noted({}, "acct-vehicle", "service_error")
        state = self.noted(state, "acct-revolver", "refused")
        self.assertEqual(helpers.choose_close(state, PRODUCTS, self.policy, bindings=self.bindings),
                         {"outcome": OUTCOMES["no_promise"], "reason": REASONS["refused"]})
        self.assertEqual(helpers.choose_close(state, list(reversed(PRODUCTS)), self.policy, bindings=self.bindings),
                         {"outcome": OUTCOMES["no_promise"], "reason": REASONS["service_error"]})

    def test_paid_note_is_skipped_and_all_paid_uses_opaque_paid_outcome(self):
        state = self.noted({}, "acct-revolver", "paid")
        state = self.noted(state, "acct-vehicle", "dispute")
        self.assertEqual(helpers.choose_close(state, PRODUCTS, self.policy, bindings=self.bindings),
                         {"outcome": OUTCOMES["no_promise"], "reason": REASONS["dispute"]})
        paid = self.noted({}, "acct-revolver", "paid")
        paid = self.noted(paid, "acct-vehicle", "paid")
        self.assertEqual(helpers.choose_close(paid, PRODUCTS, self.policy, bindings=self.bindings), {"outcome": OUTCOMES["paid"]})

    def test_transfer_preserves_recorded_promises_discards_pending_and_has_no_notes(self):
        state = self.promised()
        state = helpers.stage_note(state, PRODUCTS, "acct-vehicle", NOTES["dispute"], self.policy, bindings=self.bindings)
        transferred = helpers.stage_transfer(state, QUEUES["restructuring"], self.policy, bindings=self.bindings)
        self.assertEqual(transferred["pending_notes"], {})
        self.assertEqual(transferred["pending_promises"], {})
        self.assertIn("acct-revolver", transferred["recorded_promises"])
        transferred = helpers.record_transfer_result(transferred, {"ok": True})
        self.assertEqual(helpers.choose_close(transferred, PRODUCTS, self.policy, bindings=self.bindings),
                         {"outcome": OUTCOMES["transferred"], "reason": REASONS["restructuring"]})
        with self.assertRaises(helpers.PolicyError):
            helpers.record_note_result(transferred, "acct-vehicle", {"ok": True})

    def test_recorded_notes_preclude_later_transfer(self):
        noted = self.noted({}, "acct-revolver", "refused")
        with self.assertRaises(helpers.PolicyError):
            helpers.stage_transfer(noted, QUEUES["support"], self.policy, bindings=self.bindings)

    def test_transfer_discards_confirmed_but_unwritten_product_promises(self):
        state = self.pending()
        state = helpers.confirm_commitments(self.pending("acct-vehicle", "65.71", state=state))
        state = helpers.record_commitment_result(state, "acct-revolver", {"ok": True}, policy=self.policy)
        self.assertIn("acct-vehicle", state["pending_promises"])
        transferred = helpers.stage_transfer(state, QUEUES["restructuring"], self.policy, bindings=self.bindings)
        self.assertEqual(transferred["pending_promises"], {})
        self.assertEqual(set(transferred["recorded_promises"]), {"acct-revolver"})
        with self.assertRaises(helpers.PolicyError):
            helpers.record_commitment_result(transferred, "acct-vehicle", {"ok": True}, policy=self.policy)

    def test_confirmation_is_required_and_amount_change_invalidates_it(self):
        pending = self.pending()
        with self.assertRaises(helpers.PolicyError):
            helpers.assert_commitment_attempt_allowed(pending, "acct-revolver", self.policy)
        confirmed = helpers.confirm_commitments(pending)
        helpers.assert_commitment_attempt_allowed(confirmed, "acct-revolver", self.policy)
        changed = helpers.stage_commitment(confirmed, PRODUCTS, "acct-revolver", "40.10", "2033-12-27", NOW, self.policy)
        with self.assertRaises(helpers.PolicyError):
            helpers.assert_commitment_attempt_allowed(changed, "acct-revolver", self.policy)

    def test_definite_write_failure_budget_is_bounded_per_product(self):
        state = self.pending()
        state = self.pending("acct-vehicle", "65.71", state=state)
        state = helpers.confirm_commitments(state)
        for _ in range(2):
            helpers.assert_commitment_attempt_allowed(state, "acct-vehicle", self.policy)
            state = helpers.record_commitment_result(state, "acct-vehicle", {"ok": False}, policy=self.policy)
        snapshot = deepcopy(state)
        self.assertEqual(state["promise_failures"]["acct-vehicle"], 2)
        with self.assertRaises(helpers.PolicyError):
            helpers.assert_commitment_attempt_allowed(state, "acct-vehicle", self.policy)
        with self.assertRaises(helpers.PolicyError):
            helpers.record_commitment_result(state, "acct-vehicle", {"ok": False}, policy=self.policy)
        self.assertEqual(state, snapshot)
        helpers.assert_commitment_attempt_allowed(state, "acct-revolver", self.policy)
        state = helpers.record_commitment_result(state, "acct-revolver", {"ok": True}, policy=self.policy)
        self.assertEqual(helpers.choose_close(state, PRODUCTS, self.policy, bindings=self.bindings), {"outcome": OUTCOMES["promise"]})

    def test_unknown_persistence_quarantines_product_without_inventing_failure(self):
        state = helpers.confirm_commitments(self.pending())
        unknown = helpers.record_commitment_result(state, "acct-revolver", {"error": "transport_timeout"}, policy=self.policy)
        self.assertEqual(unknown["promise_status"]["acct-revolver"], "unknown_persistence")
        self.assertNotIn("acct-revolver", unknown.get("recorded_promises", {}))
        self.assertEqual(unknown.get("promise_failures", {}).get("acct-revolver", 0), 0)
        with self.assertRaises(helpers.PolicyError):
            helpers.assert_commitment_attempt_allowed(unknown, "acct-revolver", self.policy)
        with self.assertRaises(helpers.PolicyError):
            self.pending(state=unknown)
        with self.assertRaises(helpers.PolicyError):
            helpers.stage_note(unknown, PRODUCTS, "acct-revolver", NOTES["service_error"], self.policy, bindings=self.bindings)
        with self.assertRaises(helpers.PolicyError):
            helpers.record_close_result(unknown, {"outcome": OUTCOMES["promise"]}, {"ok": True}, self.policy, bindings=self.bindings)

    def test_unknown_note_persistence_blocks_a_new_commitment_and_transfer(self):
        state = helpers.stage_note({}, PRODUCTS, "acct-revolver", NOTES["refused"], self.policy, bindings=self.bindings)
        unknown = helpers.record_note_result(state, "acct-revolver", {"error": "transport_timeout"})
        self.assertEqual(unknown["note_status"]["acct-revolver"], "unknown_persistence")
        self.assertNotIn("acct-revolver", unknown.get("recorded_notes", {}))
        self.assertEqual(unknown.get("note_failures", {}).get("acct-revolver", 0), 0)
        with self.assertRaises(helpers.PolicyError):
            self.pending(state=unknown)
        with self.assertRaises(helpers.PolicyError):
            helpers.record_note_result(unknown, "acct-revolver", {"ok": True})
        with self.assertRaises(helpers.PolicyError):
            helpers.stage_transfer(unknown, QUEUES["general"], self.policy, bindings=self.bindings)
        with self.assertRaises(helpers.PolicyError):
            helpers.choose_close(unknown, PRODUCTS, self.policy, bindings=self.bindings)

    def test_duplicate_successful_promise_note_transfer_and_close_are_rejected(self):
        promised = self.promised()
        with self.assertRaises(helpers.PolicyError):
            helpers.record_commitment_result(promised, "acct-revolver", {"ok": True}, policy=self.policy)
        with self.assertRaises(helpers.PolicyError):
            self.pending(state=promised)
        noted = self.noted({}, "acct-revolver", "paid")
        with self.assertRaises(helpers.PolicyError):
            helpers.record_note_result(noted, "acct-revolver", {"ok": True})
        transfer = helpers.stage_transfer({}, QUEUES["general"], self.policy, bindings=self.bindings)
        transfer = helpers.record_transfer_result(transfer, {"ok": True})
        with self.assertRaises(helpers.PolicyError):
            helpers.record_transfer_result(transfer, {"ok": True})
        with self.assertRaises(helpers.PolicyError):
            helpers.stage_transfer(transfer, QUEUES["support"], self.policy, bindings=self.bindings)
        close = helpers.choose_close(transfer, PRODUCTS, self.policy, bindings=self.bindings)
        closed = helpers.record_close_result(transfer, close, {"ok": True}, self.policy, bindings=self.bindings)
        with self.assertRaises(helpers.PolicyError):
            helpers.record_close_result(closed, close, {"ok": True}, self.policy, bindings=self.bindings)
        for action in ("get_account", "verify_identity", "create_promise", "add_note", "transfer_to_agent", "schedule_callback", "close_call"):
            with self.subTest(action=action), self.assertRaises(helpers.PolicyError):
                helpers.assert_action_allowed(closed, action)

    def test_close_attempt_is_terminal_even_without_confirmed_close_success(self):
        # Public Brief A rule46 permits exactly one close_call and requires it
        # to be last; it supplies no close retry exception. This is independent
        # of whether the close response positively establishes persistence.
        for result in ({"ok": False}, {"error": "transport_timeout"}):
            with self.subTest(result_keys=sorted(result)):
                state = self.promised()
                close = helpers.choose_close(state, PRODUCTS, self.policy, bindings=self.bindings)
                attempted = helpers.record_close_result(state, close, result, self.policy, bindings=self.bindings)
                self.assertFalse(attempted.get("closed", False))
                with self.assertRaises(helpers.PolicyError):
                    helpers.record_close_result(attempted, close, {"ok": True}, self.policy, bindings=self.bindings)
                with self.assertRaises(helpers.PolicyError):
                    helpers.choose_close(attempted, PRODUCTS, self.policy, bindings=self.bindings)
                for action in ("get_account", "create_promise", "add_note", "transfer_to_agent", "close_call"):
                    with self.subTest(action=action), self.assertRaises(helpers.PolicyError):
                        helpers.assert_action_allowed(attempted, action)

    def test_stable_product_id_cannot_be_invented_or_ambiguous(self):
        with self.assertRaises(helpers.PolicyError):
            self.pending("acct-not-returned")
        duplicates = PRODUCTS + [deepcopy(PRODUCTS[0])]
        with self.assertRaises(helpers.PolicyError):
            helpers.stage_commitment({}, duplicates, "acct-revolver", "40.09", "2033-12-27", NOW, self.policy)

    def test_persuasion_budget_uses_each_product_band_and_retains_other_counts(self):
        state = helpers.record_persuasion_attempt({}, PRODUCTS, "acct-revolver", self.policy)
        snapshot = deepcopy(state)
        with self.assertRaises(helpers.PolicyError):
            helpers.record_persuasion_attempt(state, PRODUCTS, "acct-revolver", self.policy)
        self.assertEqual(state, snapshot)
        state = helpers.record_persuasion_attempt(state, PRODUCTS, "acct-vehicle", self.policy)
        state = helpers.record_persuasion_attempt(state, PRODUCTS, "acct-vehicle", self.policy)
        with self.assertRaises(helpers.PolicyError):
            helpers.record_persuasion_attempt(state, PRODUCTS, "acct-vehicle", self.policy)

    def test_malformed_identity_evidence_does_not_consume_counter_or_factors(self):
        state = {"verified_fields": ["birth_tag"], "wrong_answers": 1, "identity_status": "pending"}
        bad_results = ({"ok": True}, {"ok": True, "verified": None}, {"ok": True, "verified": []},
                       {"ok": True, "verified": "not-a-boolean"}, {"ok": None, "verified": False},
                       {"data": None},
                       {"ok": True, "verified": False, "wrong_answers_left": -1},
                       {"ok": True, "verified": False, "wrong_answers_left": 4},
                       {"ok": True, "verified": False, "wrong_answers_left": "2"},
                       {"ok": True, "verified": False, "wrong_answers_left": True})
        for result in bad_results:
            with self.subTest(result_keys=sorted(result)):
                before = deepcopy(state)
                with self.assertRaises(helpers.PolicyError):
                    helpers.identity_record(state, "contact_tail", result, self.policy)
                self.assertEqual(state, before)

    def test_malformed_existing_identity_counter_is_not_coerced_or_advanced(self):
        for counter in (True, -1, "1"):
            with self.subTest(counter_type=type(counter).__name__):
                state = {"verified_fields": [], "wrong_answers": counter, "identity_status": "pending"}
                before = deepcopy(state)
                with self.assertRaises(helpers.PolicyError):
                    helpers.identity_record(state, "birth_tag", {"ok": True, "verified": True}, self.policy)
                self.assertEqual(state, before)

    def test_identity_success_is_retained_while_wrong_counter_is_global(self):
        state = helpers.identity_record({}, "birth_tag", {"ok": True, "verified": False, "hint": "day_month_swapped"}, self.policy)
        self.assertEqual(state["wrong_answers"], 0)
        state = helpers.identity_record(state, "birth_tag", {"ok": True, "verified": False, "wrong_answers_left": 2}, self.policy)
        state = helpers.identity_record(state, "birth_tag", {"ok": True, "verified": True, "wrong_answers_left": 2}, self.policy)
        state = helpers.identity_record(state, "contact_tail", {"ok": False, "error": "unreadable"}, self.policy)
        self.assertEqual((state["verified_fields"], state["wrong_answers"]), (["birth_tag"], 1))
        state = helpers.identity_record(state, "contact_tail", {"ok": True, "verified": False, "wrong_answers_left": 1}, self.policy)
        state = helpers.identity_record(state, "contact_tail", {"ok": True, "verified": False, "wrong_answers_left": 0}, self.policy)
        self.assertEqual(state["identity_status"], "failed")
        self.assertEqual(state["verified_fields"], ["birth_tag"])
        with self.assertRaises(helpers.PolicyError):
            helpers.identity_record(state, "contact_tail", {"ok": True, "verified": True}, self.policy)

    def test_money_bounds_and_calendar_boundaries_use_supplied_policy(self):
        self.assertEqual(helpers.product_deadline(PRODUCTS[0], NOW, self.policy).isoformat(), "2033-12-29")
        self.assertEqual(helpers.product_deadline(PRODUCTS[1], NOW, self.policy).isoformat(), "2033-12-27")
        self.assertEqual(helpers.business_deadline("2033-12-30", 1, self.policy["holidays"]).isoformat(), "2034-01-03")
        self.assertEqual(helpers.named_weekday("2033-12-23", 4, next_week=False).isoformat(), "2033-12-23")
        self.assertEqual(helpers.named_weekday("2033-12-23", 4, next_week=True).isoformat(), "2033-12-30")
        for amount in ("40.09", "103.17"):
            self.assertEqual(helpers.validate_commitment(PRODUCTS[0], amount, "2033-12-29", NOW, self.policy), [])
        for amount, day in (("40.08", "2033-12-29"), ("103.18", "2033-12-29"),
                            ("40.09", "2033-12-30"), ("40.09", "2033-12-26"), ("40.09", "2033-12-22")):
            with self.subTest(amount=amount, day=day):
                self.assertTrue(helpers.validate_commitment(PRODUCTS[0], amount, day, NOW, self.policy))
        for value in (True, "40.095", "NaN", "Infinity"):
            with self.subTest(amount_type=type(value).__name__), self.assertRaises(helpers.PolicyError):
                helpers.money_to_cents(value)

    def test_weekend_today_callback_is_allowed_but_weekend_promise_is_not(self):
        saturday = "2033-12-24T11:00"
        self.assertEqual(helpers.validate_callback("2033-12-24", "12:00", saturday, self.policy), [])
        self.assertEqual(helpers.validate_callback("2033-12-27", "10:00", saturday, self.policy), [])
        self.assertEqual(helpers.validate_callback("2033-12-27", "18:00", saturday, self.policy), [])
        for day, at in (("2033-12-24", "11:00"), ("2033-12-26", "12:00"),
                        ("2033-12-27", "18:01"), ("2033-12-28", "12:00")):
            with self.subTest(day=day, at=at):
                self.assertTrue(helpers.validate_callback(day, at, saturday, self.policy))
        self.assertTrue(helpers.validate_commitment(PRODUCTS[0], "40.09", "2033-12-24", saturday, self.policy))


if __name__ == "__main__":
    unittest.main()

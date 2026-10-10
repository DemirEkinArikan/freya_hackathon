import copy
import unittest

from builder.safety import digest

from builder.policy import (
    PolicyError, assert_action_allowed, business_deadline, choose_close, compile_execution_policy,
    confirm_commitments, identity_record, money_to_cents, named_weekday,
    product_by_id, product_deadline, product_floor_cents, record_close_result,
    record_commitment_result, record_note_result, record_transfer_result, rule_value,
    stage_commitment, stage_note, stage_transfer, validate_callback,
    validate_commitment, validate_policy,
)


PROFILE = {
    "identity_fields": ["birthday", "pin_tail"], "identity_wrong_limit": 4,
    "holidays": ["2031-03-17"],
    "band_ranges": [{"name": "first", "min": 1, "max": 3}, {"name": "second", "min": 4, "max": 30}],
    "deadline_days": {"first": 4, "second": 2},
    "floor_fields": {"card": "minimum", "loan": "instalment"},
    "callback_window": {"start": "09:15", "end": "16:45", "allow_today": True, "next_business_days": 1},
    "note_reasons": ["refused", "paid", "service_error", "hardship"],
    "transfer_reasons": {"general": "human"}, "promise_failure_limit": 2,
    "close_reasons": {"promise": [], "paid": [], "transferred": ["human"], "no_promise": ["refused", "hardship", "service_error"]},
}
PRODUCTS = [
    {"product_id": "stable-7", "type": "card", "past_due": "203.19", "minimum": "41.27", "days_late": 2},
    {"product_id": "stable-2", "type": "loan", "past_due": "180.00", "instalment": "62.15", "days_late": 9},
]


class PolicyTests(unittest.TestCase):
    def test_provenance_and_override_validation(self):
        policy = {"version": 1, "goal": "Synthetic account service", "allowed_actions": ["get_account"],
                  "sources": [{"id": "s1", "file": "source.md"}],
                  "rules": [{"id": "r1", "kind": "limit", "value": 4, "source": {"id": "s1", "quote": "Use four"}},
                            {"id": "r2", "kind": "limit", "value": 5, "overrides": ["r1"],
                             "source": {"id": "s1", "quote": "Use five instead"}}]}
        self.assertEqual(validate_policy(policy, {"s1": "Use four. Use five instead."}), [])
        self.assertEqual(rule_value(policy, "limit"), 5)
        bad = copy.deepcopy(policy)
        bad["rules"][1]["overrides"] = ["r2"]
        self.assertTrue(validate_policy(bad))
        bad = copy.deepcopy(policy)
        bad["rules"][0]["source"]["quote"] = "Fabricated quote"
        self.assertTrue(validate_policy(bad, {"s1": "Use four"}))
        bad = copy.deepcopy(policy)
        bad["rules"][1].pop("overrides")
        with self.assertRaises(PolicyError):
            rule_value(bad, "limit")

    def test_integer_cents_no_silent_rounding(self):
        self.assertEqual(money_to_cents("41.27"), 4127)
        self.assertEqual(money_to_cents(0.29), 29)
        for value in (True, "NaN", "Infinity", "1.005", "invalid"):
            with self.assertRaises(PolicyError):
                money_to_cents(value)
        self.assertEqual(product_floor_cents(PRODUCTS[0], PROFILE), 4127)

    def test_calendars_use_supplied_clock_and_policy(self):
        self.assertEqual(business_deadline("2031-03-14T12:00", 2, PROFILE["holidays"]).isoformat(), "2031-03-19")
        self.assertEqual(product_deadline(PRODUCTS[1], "2031-03-14T12:00", PROFILE).isoformat(), "2031-03-19")
        self.assertEqual(named_weekday("2031-03-14", 4, next_week=True).isoformat(), "2031-03-21")
        self.assertEqual(validate_commitment(PRODUCTS[1], "62.15", "2031-03-19", "2031-03-14T12:00", PROFILE), [])
        self.assertTrue(validate_commitment(PRODUCTS[1], "62.14", "2031-03-20", "2031-03-14T12:00", PROFILE))
        self.assertTrue(validate_commitment(PRODUCTS[0], "60.00", "2031-03-17", "2031-03-14T12:00", PROFILE))
        with self.assertRaises(PolicyError):
            business_deadline("2031-03-14", 1, weekdays=[])

    def test_callback_endpoints_and_future_guard(self):
        self.assertEqual(validate_callback("2031-03-18", "16:45", "2031-03-14T12:00", PROFILE), [])
        self.assertTrue(validate_callback("2031-03-14", "12:00", "2031-03-14T12:00", PROFILE))
        self.assertTrue(validate_callback("2031-03-18", "16:46", "2031-03-14T12:00", PROFILE))

    def test_identity_counts_only_authoritative_answers(self):
        state = identity_record({}, "birthday", {"ok": False, "error": "unreadable"}, PROFILE)
        self.assertEqual(state["wrong_answers"], 0)
        state = identity_record(state, "birthday", {"ok": True, "verified": False, "hint": "day_month_swapped"}, PROFILE)
        self.assertEqual(state["wrong_answers"], 0)
        state = identity_record(state, "birthday", {"ok": True, "verified": True}, PROFILE)
        with self.assertRaises(PolicyError):
            identity_record(state, "birthday", {"ok": True, "verified": True}, PROFILE)
        for _ in range(4):
            state = identity_record(state, "pin_tail", {"ok": True, "verified": False}, PROFILE)
        self.assertEqual(state["identity_status"], "failed")
        with self.assertRaises(PolicyError):
            identity_record(state, "pin_tail", {"ok": True, "verified": True}, PROFILE)

    def test_stable_ids_confirmation_and_no_duplicate_promise(self):
        with self.assertRaises(PolicyError):
            product_by_id(PRODUCTS, "invented")
        state = stage_commitment({}, PRODUCTS, "stable-7", "50.00", "2031-03-18", "2031-03-14T12:00", PROFILE)
        with self.assertRaises(PolicyError):
            record_commitment_result(state, "stable-7", {"ok": True})
        state = confirm_commitments(state)
        changed = stage_commitment(state, PRODUCTS, "stable-7", "51.00", "2031-03-18", "2031-03-14T12:00", PROFILE)
        self.assertEqual(changed["confirmation_status"], "pending")
        recorded = record_commitment_result(state, "stable-7", {"ok": True})
        self.assertEqual(recorded["recorded_promises"]["stable-7"]["amount_cents"], 5000)
        with self.assertRaises(PolicyError):
            stage_commitment(recorded, PRODUCTS, "stable-7", "52.00", "2031-03-18", "2031-03-14T12:00", PROFILE)

    def test_transfer_staging_and_last_close(self):
        state = stage_note({}, PRODUCTS, "stable-2", "refused", PROFILE)
        state["recorded_promises"] = {"stable-7": {"amount_cents": 5000, "date": "2031-03-18"}}
        state = stage_transfer(state, "general", PROFILE)
        self.assertEqual(state["pending_notes"], {})
        self.assertEqual(set(state["recorded_promises"]), {"stable-7"})
        state = record_transfer_result(state, {"ok": True})
        close = choose_close(state, PRODUCTS, PROFILE)
        self.assertEqual(close, {"outcome": "transferred", "reason": "human"})
        state = record_close_result(state, close, {"ok": True}, PROFILE)
        with self.assertRaises(PolicyError):
            assert_action_allowed(state, "get_account")
        with self.assertRaises(PolicyError):
            record_close_result(state, close, {"ok": True}, PROFILE)

    def test_account_order_controls_nonpaid_reason(self):
        state = {"pending_notes": {"stable-2": "service_error", "stable-7": "refused"}}
        self.assertEqual(choose_close(state, PRODUCTS, PROFILE), {"outcome": "no_promise", "reason": "refused"})

    def test_failed_writes_never_establish_successful_outcomes(self):
        state = stage_commitment({}, PRODUCTS, "stable-7", "50.00", "2031-03-18", "2031-03-14T12:00", PROFILE)
        state = record_commitment_result(confirm_commitments(state), "stable-7", {"ok": False}, PROFILE)
        self.assertNotIn("recorded_promises", state)
        state["close_proposal"] = {"outcome": "promise"}
        with self.assertRaises(PolicyError):
            choose_close(state, PRODUCTS, PROFILE)
        with self.assertRaises(PolicyError):
            record_close_result(state, {"outcome": "promise"}, {"ok": True}, PROFILE)
        note = stage_note({}, PRODUCTS, "stable-7", "refused", PROFILE)
        failed_note = record_note_result(note, "stable-7", {"ok": False})
        self.assertNotIn("recorded_notes", failed_note)
        recorded_note = record_note_result(note, "stable-7", {"ok": True})
        with self.assertRaises(PolicyError):
            stage_transfer(recorded_note, "general", PROFILE)

    def test_execution_profile_selects_typed_active_rules_and_detaches_inputs(self):
        policy = {"rules": [
            {"id": "r1", "kind": "identity_wrong_limit", "value": 4},
            {"id": "r2", "kind": "identity_wrong_limit", "value": 3, "overrides": ["r1"]},
            {"id": "r3", "kind": "identity_fields", "value": ["birthday", "pin_tail"]},
            {"id": "r4", "kind": "new_banking_goal", "value": "Uninterpreted extension prose"},
        ]}
        frozen = compile_execution_policy(policy, required_kinds=["identity_fields", "identity_wrong_limit"])
        self.assertEqual(frozen["profile"], {"identity_fields": ["birthday", "pin_tail"], "identity_wrong_limit": 3})
        policy["rules"][2]["value"].append("other")
        self.assertEqual(frozen["profile"]["identity_fields"], ["birthday", "pin_tail"])
        self.assertEqual(frozen["sha256"], digest({k: v for k, v in frozen.items() if k != "sha256"}))
        with self.assertRaises(PolicyError):
            compile_execution_policy(policy, required_kinds=["new_banking_goal"])

    def test_execution_profile_rejects_missing_prose_or_mismatched_band_constraints(self):
        for policy, required in (({}, ["identity_fields"]),
                                 ({"identity_wrong_limit": "three wrong answers"}, []),
                                 ({"band_ranges": PROFILE["band_ranges"], "deadline_days": {"first": 4}}, []),
                                 ({"band_ranges": PROFILE["band_ranges"], "persuasion_limits": {"first": 1, "other": 2}}, [])):
            with self.subTest(required=required), self.assertRaises(PolicyError):
                compile_execution_policy(policy, required_kinds=required)

    def test_legacy_write_recorder_requires_policy_after_failure_or_unknown_result(self):
        state = confirm_commitments(stage_commitment({}, PRODUCTS, "stable-7", "50.00", "2031-03-18", "2031-03-14T12:00", PROFILE))
        for result in ({"ok": False}, {"error": "transport_timeout"}):
            with self.subTest(result=result), self.assertRaises(PolicyError):
                record_commitment_result(state, "stable-7", result)
        rejected = record_commitment_result(state, "stable-7", {"ok": False}, PROFILE)
        with self.assertRaises(PolicyError):
            record_commitment_result(rejected, "stable-7", {"ok": True})

    def test_holiday_today_callback_allowed_but_future_holiday_rejected(self):
        self.assertEqual(validate_callback("2031-03-17", "12:01", "2031-03-17T12:00", PROFILE), [])
        self.assertEqual(validate_callback("2031-03-18", "09:15", "2031-03-17T12:00", PROFILE), [])
        self.assertTrue(validate_callback("2031-03-17", "12:01", "2031-03-16T12:00", PROFILE))

    def test_rejected_unknown_and_malformed_close_are_terminal_without_claiming_persistence(self):
        state = confirm_commitments(stage_commitment({}, PRODUCTS, "stable-7", "50.00", "2031-03-18", "2031-03-14T12:00", PROFILE))
        state = record_commitment_result(state, "stable-7", {"ok": True}, PROFILE)
        close = choose_close(state, PRODUCTS, PROFILE)
        for result, status in (({"ok": False}, "rejected"), ({"error": "timeout"}, "unknown_persistence"),
                               ({"ok": None}, "unknown_persistence"), ({"data": []}, "unknown_persistence")):
            with self.subTest(status=status, result_keys=sorted(result)):
                finished = record_close_result(state, close, result, PROFILE)
                self.assertTrue(finished["close_attempted"])
                self.assertEqual(finished["close_status"], status)
                self.assertFalse(finished.get("closed", False))
                self.assertNotIn("close", finished)
                for action in ("get_account", "close_call", "verify_identity", "schedule_callback"):
                    with self.assertRaises(PolicyError):
                        assert_action_allowed(finished, action)
                with self.assertRaises(PolicyError):
                    record_close_result(finished, close, {"ok": True}, PROFILE)
                with self.assertRaises(PolicyError):
                    choose_close(finished, PRODUCTS, PROFILE)
                with self.assertRaises(PolicyError):
                    stage_commitment(finished, PRODUCTS, "stable-2", "70.00", "2031-03-18", "2031-03-14T12:00", PROFILE)

    def test_known_code_categories_do_not_interpret_other_bank_goal_mappings(self):
        codes = {"goals": {"new_service_job": "opaque_job_selector"}, "outcomes": {"paid": "paid"}}
        profile = compile_execution_policy({"wire_codes": codes})
        self.assertEqual(profile["profile"]["wire_codes"], codes)
        self.assertNotIn("new_service_job", profile["profile"])

    def test_guard_extension_inputs_require_explicit_typed_values_without_prose_interpretation(self):
        values = {"nonpersistent_failures": {"write_operation": [{"http_status": 422, "error": "validation_rejection"}]},
                  "confirmation_actions": ["create_promise"], "confirmation_scope": "batch",
                  "product_order": {"field": "days_late", "direction": "descending", "tie_breaker": "account_order"},
                  "close_precedence": ["transferred", "promise", "paid", "no_promise"],
                  "identity_close": {"failed": {"outcome": "not_verified", "reason": "failed"},
                                     "incomplete": {"outcome": "not_verified", "reason": "declined"},
                                     "unreached": {"outcome": "not_reached", "reason": "unclear"}},
                  "promise_failure_disposition": {"reason": "service_error", "clear_pending": True}}
        frozen = compile_execution_policy(values, required_kinds=list(values))
        self.assertEqual(frozen["profile"], values)
        values["nonpersistent_failures"]["write_operation"][0]["error"] = "different"
        self.assertEqual(frozen["profile"]["nonpersistent_failures"]["write_operation"][0]["error"], "validation_rejection")
        invalid = (("nonpersistent_failures", {"write_operation": [{"http_status": True}]}),
                   ("nonpersistent_failures", {"write_operation": [{"http_status": 422, "predicate": "definitely not persisted"}]}),
                   ("confirmation_actions", ["create_promise", "create_promise"]),
                   ("product_order", "descending days_late"),
                   ("close_precedence", "transfer before promise"),
                   ("identity_close", {"failed": {"outcome": "not_verified"}}),
                   ("promise_failure_disposition", {"reason": "service_error", "clear_pending": "true"}))
        for kind, value in invalid:
            with self.subTest(kind=kind), self.assertRaises(PolicyError):
                compile_execution_policy({kind: value})

    def test_structured_policy_values_are_checked(self):
        source = {"id": "s1", "file": "source.md"}
        for kind, value in (("identity_wrong_limit", True), ("holidays", ["not-a-day"]),
                            ("callback_window", {"start": "12:00", "end": "11:00"}),
                            ("band_ranges", [{"name": "a", "min": 1, "max": 5}, {"name": "b", "min": 4, "max": 7}])):
            policy = {"goal": "Synthetic", "allowed_actions": [], "sources": [source],
                      "rules": [{"id": "r1", "kind": kind, "value": value, "source": {"id": "s1", "quote": "source"}}]}
            self.assertTrue(validate_policy(policy))

    def test_source_hash_matches_controller_canonical_fingerprint(self):
        text = "A source document."
        policy = {"goal": "Synthetic", "allowed_actions": [],
                  "sources": [{"id": "s1", "file": "source.md", "sha256": digest(text)}],
                  "rules": [{"id": "r1", "kind": "description", "value": "Synthetic goal",
                             "source": {"id": "s1", "quote": "source document"}}]}
        self.assertEqual(validate_policy(policy, {"s1": text}), [])
        policy["sources"][0]["sha256"] = digest("Different document")
        self.assertTrue(validate_policy(policy, {"s1": text}))

    def test_strict_ir_preserves_helper_profiles_but_rejects_bypasses(self):
        self.assertEqual(rule_value(PROFILE, "identity_wrong_limit"), 4)
        text = "Use four attempts."
        policy = {"version": 1, "goal": "Synthetic", "allowed_actions": [],
                  "sources": [{"id": "s1", "file": "source.md", "sha256": digest(text)}],
                  "rules": [{"id": "r1", "kind": "identity_wrong_limit", "value": 4,
                             "source": {"id": "s1", "quote": text}}]}
        self.assertEqual(validate_policy(policy, {"s1": text}, strict=True), [])
        policy["identity_wrong_limit"] = 8
        self.assertTrue(validate_policy(policy, {"s1": text}, strict=True))
        policy.pop("identity_wrong_limit")
        policy["rules"][0]["value"] = None
        self.assertTrue(validate_policy(policy, {"s1": text}, strict=True))
        policy["rules"][0]["value"] = 4
        policy["version"] = True
        self.assertTrue(validate_policy(policy))

    def test_extension_conflicts_use_json_typed_equality(self):
        policy = {"goal": "Synthetic", "allowed_actions": [], "sources": [{"id": "s1", "file": "source.md"}],
                  "rules": [{"id": "a", "kind": "extension", "value": True, "source": {"id": "s1", "quote": "first"}},
                            {"id": "b", "kind": "extension", "value": 1, "source": {"id": "s1", "quote": "second"}}]}
        self.assertTrue(validate_policy(policy))
        with self.assertRaises(PolicyError):
            rule_value(policy, "extension")
        policy["rules"][1]["overrides"] = ["a"]
        self.assertEqual(validate_policy(policy), [])
        policy["rules"][1]["kind"] = "different_extension"
        self.assertTrue(validate_policy(policy))


if __name__ == "__main__":
    unittest.main()

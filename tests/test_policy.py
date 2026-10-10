import copy
import unittest

from builder.safety import digest

from builder.policy import (
    PolicyError, assert_action_allowed, business_deadline, choose_close,
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
    "transfer_reasons": {"specialist": "human"},
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
        state = stage_transfer(state, "specialist", PROFILE)
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
        state = record_commitment_result(confirm_commitments(state), "stable-7", {"ok": False})
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
            stage_transfer(recorded_note, "specialist", PROFILE)

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

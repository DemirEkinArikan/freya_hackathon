"""Independent invented calendar policies; no bank examples or benchmark oracles."""
from copy import deepcopy
from datetime import date, datetime, timezone
import unittest

from builder.contract import PolicyContractError, compile_policy_contract
from builder.policy_conflicts import evaluate_conditional_conflicts
from builder.safety import digest
from test_contract_regressions import source, rule, policy, api


def ref(identifier, *path):
    return {"rule_id": identifier, "value_path": list(path)}


def fixture(*, weekdays=None, holidays=None, week_start=0):
    weekdays = [0, 1, 2, 3, 4] if weekdays is None else weekdays
    holidays = [] if holidays is None else holidays
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    offer = source("dispatch", f"Synthetic dispatch policy: weeks begin on {names[week_start]}. Offer the last declared business day of the current calendar week; if that date is today, offer the last declared business day of the following week.")
    bounds = source("calendar", "Synthetic calendar policy: an offered commitment cannot be before the supplied call date. Eligible business weekdays are "
                    + ", ".join(names[day] for day in weekdays) + ". Holidays: " + (", ".join(holidays) if holidays else "none") + ".")
    memo = source("limitations", "Synthetic operating memo: when the required offered date precedes the call date, the date-offer route is unresolved and must be blocked. This grants no replacement date, transfer or closing outcome.")
    this_week = {"op": "last_business_day_of_week", "week_offset": 0, "week_start": week_start}
    expression = {"op": "if_date_equals_now", "date": deepcopy(this_week),
                  "then": {"op": "last_business_day_of_week", "week_offset": 1, "week_start": week_start},
                  "else": deepcopy(this_week)}
    condition = {"id": "past-required-offer", "phase": "date_offer", "effect": "block_affected_route",
                 "predicate": {"operator": "lt", "required_date": ref("offer-date", "first_offer"),
                               "lower_bound": ref("date-minimum", "minimum"),
                               "calendar": {"weekdays": ref("weekdays"), "holidays": ref("holidays")}}}
    rules = [rule("offer-date", "offer_date_rule", {"first_offer": expression}, offer),
             rule("date-minimum", "date_lower_bound", {"minimum": {"op": "now_date"}}, bounds),
             rule("weekdays", "business_weekdays", deepcopy(weekdays), bounds),
             rule("holidays", "holidays", deepcopy(holidays), bounds),
             rule("conditional-calendar", "conditional_conflicts", [condition], memo)]
    documents = [offer, bounds, memo]
    return policy(documents, rules), documents, api()


def value(policy_data, identifier):
    return next(item["value"] for item in policy_data["rules"] if item["id"] == identifier)


def compile_fixture(policy_data=None, documents=None, openapi=None):
    original = fixture()
    return compile_policy_contract(policy_data if policy_data is not None else original[0],
                                   documents if documents is not None else original[1],
                                   openapi if openapi is not None else original[2], conditional_mode="inspect")


class ConditionalPolicyConflictTests(unittest.TestCase):
    def test_default_compilation_rejects_unenforced_conditional_rules(self):
        with self.assertRaisesRegex(PolicyContractError, "require runtime enforcement"):
            compile_policy_contract(*fixture())

    def test_inspection_has_source_bound_metadata_and_no_claim_of_runtime_enforcement(self):
        compiled = compile_fixture()
        descriptor = compiled["conditional_conflicts"]
        self.assertFalse(descriptor["runtime_enforced"])
        self.assertFalse(compiled["semantic_correctness_verified"])
        condition = descriptor["conditions"][0]
        self.assertEqual(condition["effect"], "block_affected_route")
        self.assertEqual(condition["bindings"]["required_date"]["rule_id"], "offer-date")
        witness = condition["bindings"]["required_date"]["provenance"][0]
        self.assertEqual(witness["id"], "dispatch")
        self.assertEqual(witness["source"]["sha256"], fixture()[1][0]["sha256"])
        self.assertEqual(condition["origin"][0]["rule_id"], "conditional-calendar")
        self.assertEqual(descriptor["sha256"], digest({k: v for k, v in descriptor.items() if k != "sha256"}))
        self.assertEqual(compiled["sha256"], digest({k: v for k, v in compiled.items() if k != "sha256"}))

    def test_weekday_clear_and_weekend_blocked_use_call_clock(self):
        compiled = compile_fixture()
        for clock, state, offered in (("2028-06-07T11:00", "clear", "2028-06-09"),
                                     ("2028-06-09T11:00", "clear", "2028-06-16"),
                                     ("2028-06-10T11:00", "blocked", "2028-06-09"),
                                     ("2028-06-11T11:00", "blocked", "2028-06-09")):
            with self.subTest(clock=clock):
                result = evaluate_conditional_conflicts(compiled, clock)[0]
                self.assertEqual(result["status"], state)
                self.assertEqual(result["required_date"], offered)
                self.assertEqual(result["lower_bound"], clock[:10])
                self.assertNotIn("replacement_date", result)
                self.assertNotIn("outcome", result)

    def test_holiday_can_make_required_date_past_without_a_weekend(self):
        input_policy, documents, schema = fixture(holidays=["2028-06-09"])
        compiled = compile_fixture(input_policy, documents, schema)
        result = evaluate_conditional_conflicts(compiled, "2028-06-09T11:00")[0]
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["required_date"], "2028-06-08")

    def test_nonstandard_source_calendar_clears_saturday_without_assuming_monday_friday(self):
        input_policy, documents, schema = fixture(weekdays=[1, 2, 3, 4, 5])
        compiled = compile_fixture(input_policy, documents, schema)
        result = evaluate_conditional_conflicts(compiled, "2028-06-10T11:00")[0]
        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["required_date"], "2028-06-17")

    def test_explicit_sunday_week_start_changes_week_scope(self):
        input_policy, documents, schema = fixture(week_start=6)
        compiled = compile_fixture(input_policy, documents, schema)
        result = evaluate_conditional_conflicts(compiled, "2028-06-11T11:00")[0]
        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["required_date"], "2028-06-16")

    def test_week_without_business_dates_is_unknown_without_fallback(self):
        input_policy, documents, schema = fixture(holidays=[f"2028-06-{day:02d}" for day in range(5, 12)])
        compiled = compile_fixture(input_policy, documents, schema)
        result = evaluate_conditional_conflicts(compiled, "2028-06-07T11:00")[0]
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "calendar_expression_unresolved")
        self.assertNotIn("required_date", result)

    def test_missing_malformed_clock_is_unknown_never_real_clock(self):
        compiled = compile_fixture()
        for clock in (None, True, {}, [], "", "not-a-date", "2028-06-07 11:00", "2028-06-07T99:00"):
            with self.subTest(clock=clock):
                result = evaluate_conditional_conflicts(compiled, clock)[0]
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["reason"], "invalid_call_clock")
                self.assertNotIn("required_date", result)

    def test_supplied_date_and_datetime_keep_their_local_date(self):
        compiled = compile_fixture()
        for clock in (date(2028, 6, 10), datetime(2028, 6, 10, 1, tzinfo=timezone.utc),
                      "2028-06-10T00:30:00+14:00"):
            self.assertEqual(evaluate_conditional_conflicts(compiled, clock)[0]["status"], "blocked")

    def test_ordinary_unsupported_constraints_are_fatal_even_in_inspection(self):
        input_policy, documents, schema = fixture()
        input_policy["unsupported_constraints"] = ["Synthetic fee exception has no supported resolution."]
        with self.assertRaisesRegex(PolicyContractError, "contains unsupported constraints"):
            compile_fixture(input_policy, documents, schema)

    def test_conflict_metadata_cannot_supply_asserted_values_quotes_or_fallbacks(self):
        for field, supplied in (("provenance", [{"quote": "invented"}]),
                                ("replacement_date", "2028-06-16"), ("fallback", "transfer")):
            input_policy, documents, schema = fixture()
            value(input_policy, "conditional-calendar")[0][field] = supplied
            with self.subTest(field=field), self.assertRaises(PolicyContractError):
                compile_fixture(input_policy, documents, schema)

    def test_unknown_rule_or_value_path_cannot_be_used_as_provenance(self):
        for reference in (ref("nonexistent", "first_offer"), ref("offer-date", "missing"),
                          ref("conditional-calendar", 0), {"rule_id": "offer-date", "value_path": [True]}):
            input_policy, documents, schema = fixture()
            value(input_policy, "conditional-calendar")[0]["predicate"]["required_date"] = reference
            with self.subTest(reference=reference), self.assertRaises(PolicyContractError):
                compile_fixture(input_policy, documents, schema)

    def test_superseded_rule_cannot_remain_the_conditional_reference(self):
        input_policy, documents, schema = fixture()
        amendment = source("amendment", "Synthetic amended dispatch: required date is now the following week's last declared business day.")
        documents.append(amendment)
        input_policy["sources"].append({k: amendment[k] for k in ("id", "file", "sha256")})
        input_policy["rules"].append(rule("amended-offer", "offer_date_rule", {"first_offer": {
            "op": "last_business_day_of_week", "week_offset": 1, "week_start": 0}}, amendment, overrides=("offer-date",)))
        with self.assertRaisesRegex(PolicyContractError, "inactive"):
            compile_fixture(input_policy, documents, schema)
        value(input_policy, "conditional-calendar")[0]["predicate"]["required_date"] = ref("amended-offer", "first_offer")
        compiled = compile_fixture(input_policy, documents, schema)
        self.assertEqual(evaluate_conditional_conflicts(compiled, "2028-06-10T11:00")[0]["status"], "clear")
        self.assertEqual(compiled["conditional_conflicts"]["conditions"][0]["bindings"]["required_date"]["rule_id"], "amended-offer")

    def test_wrong_lower_bound_and_calendar_binding_are_rejected(self):
        mutations = (
            lambda p: value(p, "date-minimum").update(minimum={"op": "literal_date", "value": "2028-01-01"}),
            lambda p: value(p, "conditional-calendar")[0]["predicate"]["calendar"].update(weekdays=ref("offer-date", "first_offer")),
            lambda p: value(p, "conditional-calendar")[0]["predicate"].update(lower_bound=ref("offer-date", "first_offer")),
        )
        for mutate in mutations:
            input_policy, documents, schema = fixture()
            mutate(input_policy)
            with self.assertRaises(PolicyContractError):
                compile_fixture(input_policy, documents, schema)

    def test_unrecognized_expression_bad_nested_shape_and_boolean_offset_fail_closed(self):
        invalid = ({"op": "python", "code": "date.today()"},
                   {"op": "last_business_day_of_week", "week_offset": True, "week_start": 0},
                   {"op": "last_business_day_of_week", "week_offset": 521, "week_start": 0},
                   {"op": "last_business_day_of_week", "week_offset": 0},
                   {"op": "last_business_day_of_week", "week_offset": 0, "week_start": True},
                   {"op": "literal_date", "value": "2028-6-7"},
                   {"op": "if_date_equals_now", "date": {"op": "now_date"}, "then": {}, "else": {"op": "now_date"}},
                   {"op": "now_date", "automatic_fallback": "next_week"})
        for expression in invalid:
            input_policy, documents, schema = fixture()
            value(input_policy, "offer-date")["first_offer"] = expression
            with self.subTest(expression=expression), self.assertRaises(PolicyContractError):
                compile_fixture(input_policy, documents, schema)

    def test_duplicate_ids_unknown_phase_and_nonblocking_effect_fail_closed(self):
        for key, altered in (("phase", "any_call"), ("effect", "ignore_conflict"), ("id", "")):
            input_policy, documents, schema = fixture()
            value(input_policy, "conditional-calendar")[0][key] = altered
            with self.assertRaises(PolicyContractError):
                compile_fixture(input_policy, documents, schema)
        input_policy, documents, schema = fixture()
        value(input_policy, "conditional-calendar").append(deepcopy(value(input_policy, "conditional-calendar")[0]))
        with self.assertRaises(PolicyContractError):
            compile_fixture(input_policy, documents, schema)

    def test_expression_resource_bound_rejects_deep_trees(self):
        expression = {"op": "now_date"}
        for _ in range(10):
            expression = {"op": "if_date_equals_now", "date": {"op": "now_date"},
                          "then": expression, "else": {"op": "now_date"}}
        input_policy, documents, schema = fixture()
        value(input_policy, "offer-date")["first_offer"] = expression
        with self.assertRaisesRegex(PolicyContractError, "structural bound"):
            compile_fixture(input_policy, documents, schema)

    def test_unquoted_rule_or_changed_controller_fingerprint_is_rejected(self):
        input_policy, documents, schema = fixture()
        input_policy["rules"][0]["source"]["quote"] = "Unquoted invented assertion."
        with self.assertRaises(PolicyContractError):
            compile_fixture(input_policy, documents, schema)
        input_policy, documents, schema = fixture()
        documents[0]["sha256"] = "0" * 64
        with self.assertRaises(PolicyContractError):
            compile_fixture(input_policy, documents, schema)

    def test_metadata_tampering_is_unknown_even_with_recomputed_outer_digest(self):
        compiled = compile_fixture()
        for mutate in (lambda c: c["conditional_conflicts"].update(runtime_enforced=True),
                       lambda c: c["conditional_conflicts"]["conditions"].clear(),
                       lambda c: c["conditional_conflicts"]["conditions"][0]["calendar"].update(weekdays=[5, 6]),
                       lambda c: c.pop("conditional_conflicts")):
            altered = deepcopy(compiled)
            mutate(altered)
            altered["sha256"] = digest({k: v for k, v in altered.items() if k != "sha256"})
            self.assertEqual(evaluate_conditional_conflicts(altered, "2028-06-10T11:00")[0]["reason"],
                             "invalid_conflict_contract")

    def test_independent_pin_rejects_whole_contract_replacement(self):
        original = compile_fixture()
        input_policy, documents, schema = fixture(weekdays=[1, 2, 3, 4, 5])
        replacement = compile_fixture(input_policy, documents, schema)
        self.assertEqual(evaluate_conditional_conflicts(replacement, "2028-06-10T11:00")[0]["status"], "clear")
        pinned = evaluate_conditional_conflicts(replacement, "2028-06-10T11:00",
                                               expected_contract_sha256=original["sha256"])[0]
        self.assertEqual(pinned["status"], "unknown")
        self.assertEqual(pinned["reason"], "invalid_conflict_contract")
        self.assertEqual(evaluate_conditional_conflicts(original, "2028-06-10T11:00",
                                                       expected_contract_sha256=original["sha256"])[0]["status"], "blocked")

    def test_detached_outputs_and_evaluation_do_not_mutate_policy_or_contract(self):
        inputs = fixture()
        before = deepcopy(inputs)
        compiled = compile_fixture(*inputs)
        snapshot = deepcopy(compiled)
        evaluate_conditional_conflicts(compiled, "2028-06-10T11:00")
        self.assertEqual(inputs, before)
        self.assertEqual(compiled, snapshot)

    def test_normal_contract_hash_and_default_behavior_are_unchanged(self):
        input_policy, documents, schema = fixture()
        input_policy["rules"] = input_policy["rules"][:-1]
        input_policy["sources"] = input_policy["sources"][:-1]
        documents = documents[:-1]
        ordinary = compile_policy_contract(input_policy, documents, schema)
        inspected = compile_policy_contract(input_policy, documents, schema, conditional_mode="inspect")
        self.assertEqual(ordinary, inspected)
        self.assertNotIn("conditional_conflicts", ordinary)
        self.assertEqual(evaluate_conditional_conflicts(ordinary, None), [])

    def test_invalid_inspection_selector_and_malformed_contract_are_not_accepted(self):
        for mode in (None, True, {}, "accept"):
            with self.subTest(mode=mode), self.assertRaises(PolicyContractError):
                compile_policy_contract(*fixture(), conditional_mode=mode)
        for invalid in ({}, {"version": True, "sha256": "0" * 64}, None):
            self.assertEqual(evaluate_conditional_conflicts(invalid, "2028-06-07T11:00")[0]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()

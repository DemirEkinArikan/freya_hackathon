"""Independent invented policy data for the controller-owned guard selector.

No evaluator fixtures, customer scripts, model calls, or hosted calls are used.
These tests establish profile transport and shape validation, not speech intent.
"""
import copy
import unittest

from builder.policy import PolicyError, compile_execution_policy
from builder.safety import digest


GUARD_VALUES = {
    "record_notes": False,
    "resolve_all_products": True,
    "confirmation_actions": [],
    "confirmation_scope": "batch",
    "product_order": {"mode": "account_order"},
    "close_precedence": ["transferred", "promise", "paid", "no_promise"],
    "identity_close": {
        "failed": {"outcome": "END_DENIED", "reason": "AUTH_FAILURE"},
        "incomplete": {"outcome": "END_DENIED", "reason": "AUTH_DECLINED"},
        "unreached": {"outcome": "END_UNREACHED"},
    },
    "nonpersistent_failures": {
        "create_promise": [{"http_status": 200, "error": "BUSY_NO_COMMIT"}],
    },
    "promise_failure_disposition": {"reason": "ANNOT_TECH", "clear_pending": True},
    "transfer_prerequisites": {"HAND_CARE": "none", "HAND_PLAN": "account"},
    "promise_readback": {
        "product_fields": ["product_caption", "category_code"],
        "value_labels": {"category_code": {"P17": "vehicle instalment loan", "P29": "credit line"}},
    },
}


def invented_policy(values):
    text = "Independent synthetic development policy for typed guard-profile transport."
    return {
        "version": 1, "goal": "Resolve permitted synthetic bank actions",
        "allowed_actions": ["close_call"],
        "sources": [{"id": "memo", "file": "invented-memo.txt", "sha256": digest(text)}],
        "rules": [{"id": "rule-" + kind, "kind": kind, "value": copy.deepcopy(value),
                   "source": {"id": "memo", "quote": text}} for kind, value in values.items()],
        "unsupported_constraints": [],
    }


class GuardProfileTests(unittest.TestCase):
    def compile_value(self, kind, value):
        return compile_execution_policy(invented_policy({kind: value}), required_kinds=[kind])

    def rejects(self, kind, values):
        for value in values:
            with self.subTest(kind=kind, value=value):
                with self.assertRaises(PolicyError):
                    self.compile_value(kind, value)

    def test_all_guard_kinds_are_selected_from_active_typed_rules(self):
        policy = invented_policy(GUARD_VALUES)
        compiled = compile_execution_policy(policy, required_kinds=list(GUARD_VALUES))
        self.assertEqual(compiled["profile"], GUARD_VALUES)
        self.assertEqual(compiled["policy_sha256"], digest(policy))
        self.assertEqual(compiled["sha256"], digest({key: value for key, value in compiled.items() if key != "sha256"}))

    def test_empty_confirmation_actions_and_false_notes_are_explicit_values(self):
        compiled = compile_execution_policy(invented_policy({"record_notes": False, "confirmation_actions": []}),
                                            required_kinds=["record_notes", "confirmation_actions"])
        self.assertIs(compiled["profile"]["record_notes"], False)
        self.assertEqual(compiled["profile"]["confirmation_actions"], [])
        self.assertNotIn("resolve_all_products", compiled["profile"])

    def test_each_supported_confirmation_action_can_be_declared(self):
        values = ["commit_promises", "commit_transfer", "schedule_callback"]
        self.assertEqual(self.compile_value("confirmation_actions", values)["profile"]["confirmation_actions"], values)

    def test_account_order_and_explicit_field_sort_are_distinct_supported_shapes(self):
        for order in ({"mode": "account_order"},
                      {"field": "delinquency_age", "direction": "ascending", "tie_breaker": "account_order"},
                      {"field": "delinquency_age", "direction": "descending", "tie_breaker": "account_order"}):
            with self.subTest(order=order):
                self.assertEqual(self.compile_value("product_order", order)["profile"]["product_order"], order)

    def test_same_nested_shape_survives_detached_profile_transport(self):
        policy = invented_policy(GUARD_VALUES)
        snapshot = copy.deepcopy(policy)
        first = compile_execution_policy(policy, required_kinds=list(GUARD_VALUES))
        first["profile"]["promise_readback"]["value_labels"]["category_code"]["P17"] = "changed locally"
        first["profile"]["identity_close"]["failed"]["reason"] = "changed locally"
        self.assertEqual(policy, snapshot)
        self.assertEqual(compile_execution_policy(policy)["profile"], GUARD_VALUES)

    def test_explicit_override_is_resolved_without_profile_defaults(self):
        policy = invented_policy({"record_notes": False})
        policy["rules"].append({"id": "notes-amendment", "kind": "record_notes", "value": True,
                                "overrides": ["rule-record_notes"],
                                "source": {"id": "memo", "quote": policy["rules"][0]["source"]["quote"]}})
        self.assertIs(compile_execution_policy(policy, required_kinds=["record_notes"])["profile"]["record_notes"], True)

    def test_each_guard_kind_is_required_only_when_requested(self):
        for kind in GUARD_VALUES:
            with self.subTest(kind=kind):
                policy = invented_policy({name: value for name, value in GUARD_VALUES.items() if name != kind})
                with self.assertRaisesRegex(PolicyError, "required execution rule is missing"):
                    compile_execution_policy(policy, required_kinds=[kind])

    def test_unknown_or_malformed_required_kinds_fail_with_policy_error(self):
        for required in (["invented_executable_predicate"], [None], [False], [[]], [{}], "record_notes"):
            with self.subTest(required=required):
                with self.assertRaises(PolicyError):
                    compile_execution_policy(invented_policy({}), required_kinds=required)

    def test_guard_booleans_do_not_coerce_integer_string_or_nested_values(self):
        for kind in ("record_notes", "resolve_all_products"):
            self.rejects(kind, [0, 1, "false", None, [], {}, {"value": True}])

    def test_confirmation_actions_reject_duplicates_unknown_names_and_wrong_shapes(self):
        self.rejects("confirmation_actions", [None, "commit_promises", {}, ["commit_promises", "commit_promises"],
                                              ["create_promise"], ["stage_transfer"], [True], [{}], [""]])

    def test_product_order_rejects_mixed_incomplete_or_invalid_shapes(self):
        self.rejects("product_order", [None, "account_order", [], {}, {"mode": "alphabetical"},
                                       {"mode": "account_order", "field": "product_id"},
                                       {"field": "age", "direction": "ascending"},
                                       {"field": "age", "direction": "ascending", "tie_breaker": "product_id"},
                                       {"field": [], "direction": "ascending", "tie_breaker": "account_order"},
                                       {"field": "age", "direction": True, "tie_breaker": "account_order"}])

    def test_transfer_prerequisites_preserve_opaque_queues_without_guessing(self):
        value = {"QUEUE-OPAQUE-17": "none", "QUEUE-OPAQUE-29": "identity", "QUEUE-OPAQUE-42": "account"}
        self.assertEqual(self.compile_value("transfer_prerequisites", value)["profile"]["transfer_prerequisites"], value)

    def test_transfer_prerequisites_reject_wrong_nested_shapes(self):
        self.rejects("transfer_prerequisites", [None, [], "none", {}, {"": "none"}, {1: "account"},
                                               {"Q": "verified"}, {"Q": True}, {"Q": None}, {"Q": ["account"]},
                                               {"Q": {"mode": "account"}}])

    def test_readback_can_use_unmapped_human_display_fields(self):
        for value in ({"product_fields": ["display_name"]},
                      {"product_fields": ["display_name"], "value_labels": {}},
                      {"product_fields": ["display_name"], "value_labels": {"display_name": {}}}):
            with self.subTest(value=value):
                self.assertEqual(self.compile_value("promise_readback", value)["profile"]["promise_readback"], value)

    def test_readback_rejects_internal_ids_and_invalid_field_lists(self):
        self.rejects("promise_readback", [None, "product_name", [], {}, {"product_fields": []},
                                         {"product_fields": "display_name"}, {"product_fields": [True]},
                                         {"product_fields": [{}]}, {"product_fields": [""]},
                                         {"product_fields": ["display_name", "display_name"]},
                                         {"product_fields": ["customer_id"]}, {"product_fields": ["product_id"]},
                                         {"product_fields": ["display_name"], "template": "unsafe extension"}])

    def test_readback_rejects_unselected_labels_and_malformed_nested_maps(self):
        self.rejects("promise_readback", [
            {"product_fields": ["caption"], "value_labels": None},
            {"product_fields": ["caption"], "value_labels": []},
            {"product_fields": ["caption"], "value_labels": {"other": {"X": "other label"}}},
            {"product_fields": ["caption"], "value_labels": {"caption": ["label"]}},
            {"product_fields": ["caption"], "value_labels": {"caption": {"X": None}}},
            {"product_fields": ["caption"], "value_labels": {"caption": {"X": {"text": "label"}}}},
            {"product_fields": ["caption"], "value_labels": {"caption": {1: "label"}}},
            {"product_fields": ["caption"], "value_labels": {"caption": {"": "label"}}},
            {"product_fields": ["caption"], "value_labels": {"caption": {"X": ""}}},
        ])

    def test_close_precedence_remains_nonempty_and_wire_vocabulary_is_preserved(self):
        value = ["END_TRANSFER", "END_PROMISE", "END_PAID", "END_NONE"]
        self.assertEqual(self.compile_value("close_precedence", value)["profile"]["close_precedence"], value)
        self.rejects("close_precedence", [[], ["promise", "promise"], [False], [["promise"]], "promise"])

    def test_identity_dispositions_reject_missing_or_malformed_proposals(self):
        value = copy.deepcopy(GUARD_VALUES["identity_close"])
        malformed = []
        for change in (
                lambda v: v.pop("unreached"),
                lambda v: v.update(extra={"outcome": "END_OTHER"}),
                lambda v: v.update(failed="END_DENIED"),
                lambda v: v["failed"].update(reason=False),
                lambda v: v["failed"].update(outcome=["END_DENIED"]),
                lambda v: v["failed"].update(extra="untrusted"),
                lambda v: v["failed"].pop("outcome")):
            altered = copy.deepcopy(value)
            change(altered)
            malformed.append(altered)
        self.rejects("identity_close", malformed)

    def test_nonpersistent_failure_http_boundaries_and_business_rejection(self):
        for status in (100, 200, 599):
            value = {"create_promise": [{"http_status": status, "error": "BUSY_NO_COMMIT"}]}
            self.assertEqual(self.compile_value("nonpersistent_failures", value)["profile"]["nonpersistent_failures"], value)
        self.rejects("nonpersistent_failures", [
            {"create_promise": [{"http_status": 99}]}, {"create_promise": [{"http_status": 600}]},
            {"create_promise": [{"http_status": True}]}, {"create_promise": [{"http_status": "200"}]},
            {"create_promise": [{"http_status": 200, "error": {"code": "BUSY"}}]},
            {"create_promise": [{"http_status": 200, "ok": False}]},
            {"create_promise": [{"http_status": 200}, {"http_status": 200}]},
        ])

    def test_failure_disposition_rejects_nonboolean_clear_pending(self):
        self.rejects("promise_failure_disposition", [
            {"reason": "ANNOT_TECH", "clear_pending": 1}, {"reason": "ANNOT_TECH", "clear_pending": "true"},
            {"reason": ["ANNOT_TECH"], "clear_pending": True}, {"reason": "ANNOT_TECH"},
            {"reason": "ANNOT_TECH", "clear_pending": True, "retry": True},
        ])


if __name__ == "__main__":
    unittest.main()

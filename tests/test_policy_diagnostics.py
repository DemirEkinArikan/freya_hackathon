import copy
import unittest

from builder.policy import (
    PolicyError, _EXECUTION_ONLY_KINDS, _PROFILE_KINDS, _SHAPE_GUIDANCE,
    compile_execution_policy,
)


class PolicyDiagnosticTests(unittest.TestCase):
    def rejection(self, kind, value):
        with self.assertRaises(PolicyError) as raised:
            compile_execution_policy({kind: value})
        return str(raised.exception)

    def test_all_typed_interfaces_have_guidance(self):
        self.assertEqual(set(_SHAPE_GUIDANCE), _PROFILE_KINDS | _EXECUTION_ONLY_KINDS)

    def test_missing_fixed_identity_scopes_are_named_even_with_choices(self):
        policy = {
            "identity_close": {"failed": {"outcome": "opaque_failure", "reason": "opaque_counter"}},
            "identity_close_choices": {
                "incomplete": [{"outcome": "opaque_incomplete", "requires": "none"}],
                "unreached": [{"outcome": "opaque_unreached", "requires": "none"}],
            },
        }
        original = copy.deepcopy(policy)
        with self.assertRaises(PolicyError) as raised:
            compile_execution_policy(policy)
        message = str(raised.exception)
        self.assertIn("missing required scopes: incomplete, unreached", message)
        self.assertIn("identity_close_choices does not replace", message)
        self.assertNotIn("opaque_", message)
        self.assertEqual(policy, original)

    def test_wrong_root_type_reports_type_and_shape_without_value(self):
        marker = "private-valued-unsupported-shape"
        message = self.rejection("identity_close", marker)
        self.assertIn("received string", message)
        self.assertIn("exactly failed, incomplete, unreached", message)
        self.assertNotIn(marker, message)

    def test_nested_identity_failures_have_fixed_field_paths(self):
        value = {"failed": {"outcome": "", "reason": None},
                 "incomplete": [], "unreached": {"reason": "hidden-reason"}}
        message = self.rejection("identity_close", value)
        self.assertIn("identity_close.value.failed.outcome", message)
        self.assertIn("identity_close.value.failed.reason", message)
        self.assertIn("identity_close.value.incomplete must be an object, received array", message)
        self.assertIn("identity_close.value.unreached.outcome", message)
        self.assertNotIn("hidden-reason", message)

    def test_extra_fields_are_rejected_without_echoing_their_names(self):
        value = {scope: {"outcome": "invented-wire-value"} for scope in ("failed", "incomplete", "unreached")}
        value["private-input-name"] = "private-input-value"
        value["failed"]["private-nested-key"] = "private-nested-value"
        message = self.rejection("identity_close", value)
        self.assertIn("unsupported scope fields", message)
        self.assertIn("permits only outcome and optional reason", message)
        self.assertNotIn("private-", message)
        self.assertNotIn("invented-wire-value", message)

    def test_complete_fixed_scopes_with_optional_reasons_are_still_accepted(self):
        value = {"failed": {"outcome": "opaque_failure", "reason": "opaque_reason"},
                 "incomplete": {"outcome": "opaque_incomplete"},
                 "unreached": {"outcome": "opaque_unreached"}}
        compiled = compile_execution_policy({"identity_close": value})
        self.assertEqual(compiled["profile"]["identity_close"], value)

    def test_other_interfaces_report_received_types_and_allowed_shapes(self):
        cases = (("require_confirmation", "confidential-marker", "boolean", "string"),
                 ("identity_wrong_limit", True, "positive integer", "boolean"),
                 ("product_order", [], "account_order", "array"),
                 ("promise_failure_disposition", None, "clear_pending", "null"),
                 ("confirmation_actions", ["confidential-marker"], "commit_transfer", "array"),
                 ("nonpersistent_failures", {"confidential-marker": [{"http_status": True}]}, "http_status", "object"))
        for kind, value, shape, received in cases:
            with self.subTest(kind=kind):
                message = self.rejection(kind, value)
                self.assertIn(kind + ".value expected", message)
                self.assertIn(shape, message)
                self.assertIn("received " + received, message)
                self.assertNotIn("confidential-marker", message)

    def test_diagnostic_length_is_independent_of_large_input_keys_and_values(self):
        marker = "private-data" * 10000
        message = self.rejection("identity_close", {marker: marker, "failed": {marker: marker}})
        self.assertLess(len(message), 1000)
        self.assertNotIn("private-data", message)


if __name__ == "__main__":
    unittest.main()

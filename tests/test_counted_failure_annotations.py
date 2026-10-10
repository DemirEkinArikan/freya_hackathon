"""Explicit annotation transport forms on invented renamed API responses."""
import copy
import unittest

from builder.execution_requirements import compile_execution_requirements, ExecutionRequirementsError
from builder.policy import _SHAPE_GUIDANCE, compile_execution_policy, execution_rule_guidance
from test_execution_requirements import add_rule, rule
from test_failure_source_provenance import supplied_packet


def annotated_packet(form="array"):
    policy, sources, api, path, actual = supplied_packet()
    first = copy.deepcopy(rule(policy, "nonpersistent_failures")["value"][actual][0]["api_source"])
    second = {"pointer": first["pointer"].replace("/429/", "/207/"),
              "quote": "Invented negative completed response records nothing."}
    api["paths"][path]["post"]["responses"]["207"] = {"description": second["quote"]}
    rule(policy, "nonpersistent_failures")["value"][actual].append(
        {"http_status": 207, "api_source": copy.deepcopy(second)})
    if form == "object":
        annotations = {"document": "openapi", "429": first, "207": second}
    else:
        annotations = [{"document": "openapi", "operation": actual, "http_status": status, **witness}
                       for status, witness in ((429, first), (207, second))]
    add_rule(policy, "promise_failure_resolution", {"operation": actual,
             "count_only": "Descriptive input is preserved, never interpreted as replay authority.",
             "api_annotations": annotations})
    return policy, sources, api, path, actual


class CountedFailureAnnotationTests(unittest.TestCase):
    def test_explicit_object_and_array_preserve_source_inputs_and_operational_scope(self):
        descriptors = []
        for form in ("object", "array"):
            policy, sources, api, _, actual = annotated_packet(form)
            before = copy.deepcopy((policy, sources, api))
            descriptor = compile_execution_requirements(policy, sources, api)
            self.assertEqual(descriptor["operation_bindings"]["commit_promises"]["operation"], actual)
            self.assertNotIn("promise_failure_resolution", compile_execution_policy(policy)["profile"])
            self.assertEqual((policy, sources, api), before)
            descriptors.append(descriptor)
        for field in ("actions", "operation_bindings", "required_kinds"):
            self.assertEqual(descriptors[0][field], descriptors[1][field])
        self.assertNotEqual(descriptors[0]["policy_sha256"], descriptors[1]["policy_sha256"])
        guide = execution_rule_guidance()["promise_failure_resolution"]
        self.assertIn("descriptive extension", guide)
        self.assertIn("array", guide)
        self.assertNotIn("promise_failure_resolution", _SHAPE_GUIDANCE)

    def test_array_fields_are_explicit_and_diagnostic_states_both_exact_forms(self):
        for field in ("document", "operation", "http_status", "pointer", "quote"):
            policy, sources, api, _, _ = annotated_packet()
            del rule(policy, "promise_failure_resolution")["value"]["api_annotations"][0][field]
            with self.subTest(field=field), self.assertRaises(ExecutionRequirementsError) as raised:
                compile_execution_requirements(policy, sources, api)
            diagnostic = str(raised.exception)
            for fragment in ("nonempty object", "nonempty array", "document", "operation", "http_status", "pointer", "quote"):
                self.assertIn(fragment, diagnostic)

    def test_array_rejects_wrong_outer_or_row_scope_types_extra_fields_and_duplicates(self):
        for alteration in ("outer_operation", "document", "operation", "status_string", "status_boolean", "status_low",
                           "status_high", "duplicate", "extra", "empty", "wrong_type"):
            policy, sources, api, _, _ = annotated_packet()
            resolution = rule(policy, "promise_failure_resolution")["value"]
            rows = resolution["api_annotations"]
            if alteration == "outer_operation": resolution["operation"] = "unquoted-private-operation-marker"
            elif alteration == "document": rows[0]["document"] = "unapproved"
            elif alteration == "operation": rows[0]["operation"] = "close_call"
            elif alteration == "status_string": rows[0]["http_status"] = "429"
            elif alteration == "status_boolean": rows[0]["http_status"] = True
            elif alteration == "status_low": rows[0]["http_status"] = 99
            elif alteration == "status_high": rows[0]["http_status"] = 600
            elif alteration == "duplicate": rows.append(copy.deepcopy(rows[0]))
            elif alteration == "extra": rows[0]["assume_nonpersistent"] = True
            elif alteration == "empty": resolution["api_annotations"] = []
            else: resolution["api_annotations"] = "unquoted-private-format-marker"
            with self.subTest(alteration=alteration), self.assertRaises(ExecutionRequirementsError) as raised:
                compile_execution_requirements(policy, sources, api)
            self.assertNotIn("unquoted-private-operation-marker", str(raised.exception))
            self.assertNotIn("unquoted-private-format-marker", str(raised.exception))

    def test_object_status_and_witness_shapes_are_explicit(self):
        for alteration in ("document", "extra_witness", "extra_key", "missing_document", "empty"):
            policy, sources, api, _, _ = annotated_packet("object")
            resolution = rule(policy, "promise_failure_resolution")["value"]
            annotations = resolution["api_annotations"]
            if alteration == "document": annotations["document"] = "unapproved"
            elif alteration == "extra_witness": annotations["429"]["operation"] = resolution["operation"]
            elif alteration == "extra_key": annotations["default"] = annotations.pop("429")
            elif alteration == "missing_document": annotations.pop("document")
            else: resolution["api_annotations"] = {"document": "openapi"}
            with self.subTest(alteration=alteration), self.assertRaisesRegex(ExecutionRequirementsError, "nonempty object"):
                compile_execution_requirements(policy, sources, api)

    def test_arrays_retain_exact_operation_status_and_literal_source_witness_checks(self):
        for alteration in ("other_operation_pointer", "other_status_pointer", "quote", "malformed_escape"):
            policy, sources, api, path, _ = annotated_packet()
            row = rule(policy, "promise_failure_resolution")["value"]["api_annotations"][0]
            if alteration == "other_operation_pointer":
                api["paths"]["/close_call"]["post"]["responses"]["429"] = {"description": row["quote"]}
                row["pointer"] = "/paths/~1close_call/post/responses/429/description"
            elif alteration == "other_status_pointer": row["pointer"] = row["pointer"].replace("/429/", "/207/")
            elif alteration == "quote": row["quote"] = "unquoted-private-witness-marker"
            else: row["pointer"] = "/paths/~9wire/post/responses/429/description"
            with self.subTest(alteration=alteration), self.assertRaisesRegex(ExecutionRequirementsError, "literal witnesses") as raised:
                compile_execution_requirements(policy, sources, api)
            self.assertNotIn("unquoted-private-witness-marker", str(raised.exception))

    def test_counted_array_cannot_create_omit_or_replace_operative_guarantees(self):
        for alteration in ("missing_one", "missing_all", "different_excerpt"):
            policy, sources, api, _, actual = annotated_packet()
            if alteration == "missing_one": rule(policy, "nonpersistent_failures")["value"][actual].pop()
            elif alteration == "missing_all": policy["rules"] = [r for r in policy["rules"] if r["kind"] != "nonpersistent_failures"]
            else: rule(policy, "promise_failure_resolution")["value"]["api_annotations"][0]["quote"] = "no persistent mutation"
            before = copy.deepcopy(policy)
            with self.subTest(alteration=alteration), self.assertRaisesRegex(ExecutionRequirementsError, "omitted or differs"):
                compile_execution_requirements(policy, sources, api)
            self.assertEqual(policy, before)

    def test_fragment_pointer_is_equivalent_without_artifact_normalization(self):
        policy, sources, api, _, _ = annotated_packet()
        row = rule(policy, "promise_failure_resolution")["value"]["api_annotations"][0]
        row["pointer"] = "#" + row["pointer"]
        before = copy.deepcopy(policy)
        self.assertTrue(compile_execution_requirements(policy, sources, api)["sha256"])
        self.assertEqual(policy, before)


if __name__ == "__main__":
    unittest.main()

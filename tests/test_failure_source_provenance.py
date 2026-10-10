"""Invented API witnesses and pre-freeze checks; no evaluator or service I/O."""
import copy
import unittest
from unittest.mock import patch

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import _catalogue, run_guard_step
from builder.policy import PolicyError, compile_execution_policy
from test_execution_requirements import add_rule, packet, rule
from test_guards import fixture


def supplied_packet():
    policy, sources, api = packet(["commit_promises", "close_call"], local=True, narrow=True)
    path = "/wire~commit/actions"
    actual = "bank_record_choice"
    api["paths"][path] = api["paths"].pop("/create_promise")
    api["paths"][path]["post"]["operationId"] = actual
    policy["allowed_actions"] = [actual if name == "create_promise" else name
                                 for name in policy["allowed_actions"]]
    add_rule(policy, "tool_aliases", {"create_promise": actual})
    quote = "Invented rejected write performs no persistent mutation."
    pointer = "/paths/" + path.replace("~", "~0").replace("/", "~1") + "/post/responses/429/description"
    api["paths"][path]["post"]["responses"]["429"] = {"description": quote}
    witness = {"pointer": pointer, "quote": quote}
    add_rule(policy, "nonpersistent_failures", {
        actual: [{"http_status": 429, "error": "invented_busy", "api_source": witness}]})
    return policy, sources, api, path, actual


class FailureSourceProvenanceTests(unittest.TestCase):
    def test_exact_renamed_operation_witness_survives_detached_profile(self):
        policy, sources, api, _, actual = supplied_packet()
        before = copy.deepcopy((policy, sources, api))
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertEqual(descriptor["operation_bindings"]["commit_promises"]["operation"], actual)
        self.assertFalse(descriptor["semantic_completeness_verified"])
        profile = compile_execution_policy(policy)["profile"]
        self.assertEqual(profile["nonpersistent_failures"], rule(policy, "nonpersistent_failures")["value"])
        self.assertEqual((policy, sources, api), before)

    def test_missing_or_malformed_api_witness_fails_before_guard_compile(self):
        for witness in (None, {}, {"pointer": "/paths", "quote": ""},
                        {"pointer": "/paths", "quote": "invented", "asserted_safe": True}):
            policy, sources, api, _, actual = supplied_packet()
            row = rule(policy, "nonpersistent_failures")["value"][actual][0]
            if witness is None:
                row.pop("api_source")
            else:
                row["api_source"] = witness
            with self.subTest(witness=witness), patch("builder.execution_requirements.compile_guarded_workflow") as compiler:
                with self.assertRaises(ExecutionRequirementsError):
                    compile_execution_requirements(policy, sources, api)
                compiler.assert_not_called()

    def test_unrelated_operation_with_identical_quote_cannot_supply_witness(self):
        policy, sources, api, _, actual = supplied_packet()
        row = rule(policy, "nonpersistent_failures")["value"][actual][0]
        api["paths"]["/close_call"]["post"]["responses"]["429"] = {"description": row["api_source"]["quote"]}
        row["api_source"]["pointer"] = "/paths/~1close_call/post/responses/429/description"
        with self.assertRaisesRegex(ExecutionRequirementsError, "actual permitted operation and exact response status"):
            compile_execution_requirements(policy, sources, api)

    def test_other_response_status_cannot_supply_identical_quote(self):
        policy, sources, api, path, actual = supplied_packet()
        row = rule(policy, "nonpersistent_failures")["value"][actual][0]
        api["paths"][path]["post"]["responses"]["207"] = {"description": row["api_source"]["quote"]}
        row["api_source"]["pointer"] = row["api_source"]["pointer"].replace("/429/", "/207/")
        with self.assertRaisesRegex(ExecutionRequirementsError, "exact response status"):
            compile_execution_requirements(policy, sources, api)

    def test_scope_literal_quote_and_pointer_are_checked_without_echoing_data(self):
        for field, value in (("quote", "private-unquoted-marker"), ("quote", " "),
                             ("pointer", "https://untrusted.invalid/annotation"),
                             ("pointer", "/paths/~9wire/post/responses/429/description"),
                             ("pointer", "/components/responses/Unrelated/description"),
                             ("pointer", "/paths/~1missing/post/responses/429/description")):
            policy, sources, api, _, actual = supplied_packet()
            row = rule(policy, "nonpersistent_failures")["value"][actual][0]
            row["api_source"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ExecutionRequirementsError) as raised:
                compile_execution_requirements(policy, sources, api)
            self.assertNotIn("private-unquoted-marker", str(raised.exception))

    def test_local_reference_resolves_under_operation_status_scope(self):
        policy, sources, api, path, actual = supplied_packet()
        response = api["paths"][path]["post"]["responses"]["429"]
        api["components"] = {"responses": {"RejectedWrite": response}}
        api["paths"][path]["post"]["responses"]["429"] = {"$ref": "#/components/responses/RejectedWrite"}
        row = rule(policy, "nonpersistent_failures")["value"][actual][0]
        row["api_source"]["pointer"] = "#" + row["api_source"]["pointer"]
        self.assertTrue(compile_execution_requirements(policy, sources, api)["sha256"])
        api["paths"][path]["post"]["responses"]["429"]["description"] = "A replacement annotation leaves persistence unspecified."
        with self.assertRaisesRegex(ExecutionRequirementsError, "literal api_source"):
            compile_execution_requirements(policy, sources, api)

    def test_wrong_actual_operation_id_cannot_use_semantic_alias_as_key(self):
        policy, sources, api, _, actual = supplied_packet()
        guarantees = rule(policy, "nonpersistent_failures")["value"]
        guarantees["create_promise"] = guarantees.pop(actual)
        with self.assertRaisesRegex(ExecutionRequirementsError, "exact permitted API operation IDs"):
            compile_execution_requirements(policy, sources, api)

    def test_declared_counted_annotations_require_all_operative_rows(self):
        policy, sources, api, path, actual = supplied_packet()
        first = rule(policy, "nonpersistent_failures")["value"][actual][0]["api_source"]
        second = {"pointer": first["pointer"].replace("/429/", "/207/"),
                  "quote": "Invented negative application result performs no write despite completed transport."}
        api["paths"][path]["post"]["responses"]["207"] = {"description": second["quote"]}
        add_rule(policy, "promise_failure_resolution", {
            "operation": actual, "counter_scope": "invented per-product scope",
            "count_only": "Untouched descriptive input; this check does not interpret it.",
            "api_annotations": {"document": "openapi", "429": copy.deepcopy(first), "207": second}})
        with self.assertRaisesRegex(ExecutionRequirementsError, "omitted or differs"):
            compile_execution_requirements(policy, sources, api)
        rule(policy, "nonpersistent_failures")["value"][actual].append({"http_status": 207, "api_source": copy.deepcopy(second)})
        self.assertTrue(compile_execution_requirements(policy, sources, api)["sha256"])
        # Fragment and plain RFC6901 pointers describe the same scoped source.
        second["pointer"] = "#" + second["pointer"]
        self.assertTrue(compile_execution_requirements(policy, sources, api)["sha256"])

    def test_declared_annotations_reject_wrong_document_operation_and_status(self):
        for update in ("document", "operation", "status", "pointer"):
            policy, sources, api, _, actual = supplied_packet()
            witness = copy.deepcopy(rule(policy, "nonpersistent_failures")["value"][actual][0]["api_source"])
            extension = {"operation": actual, "api_annotations": {"document": "openapi", "429": witness}}
            if update == "document":
                extension["api_annotations"]["document"] = "unapproved"
            elif update == "operation":
                extension["operation"] = "create_promise"
            elif update == "status":
                extension["api_annotations"]["default"] = extension["api_annotations"].pop("429")
            else:
                witness["pointer"] = witness["pointer"].replace("/429/", "/207/")
            add_rule(policy, "promise_failure_resolution", extension)
            with self.subTest(update=update), self.assertRaises(ExecutionRequirementsError):
                compile_execution_requirements(policy, sources, api)

    def test_declared_annotation_different_literal_excerpt_is_not_silently_substituted(self):
        policy, sources, api, _, actual = supplied_packet()
        witness = copy.deepcopy(rule(policy, "nonpersistent_failures")["value"][actual][0]["api_source"])
        witness["quote"] = "no persistent mutation"
        add_rule(policy, "promise_failure_resolution", {
            "operation": actual, "api_annotations": {"document": "openapi", "429": witness}})
        with self.assertRaisesRegex(ExecutionRequirementsError, "omitted or differs"):
            compile_execution_requirements(policy, sources, api)

    def test_absent_guarantee_remains_optional_without_invented_api_witness(self):
        policy, sources, api, _, _ = supplied_packet()
        policy["rules"] = [item for item in policy["rules"] if item["kind"] != "nonpersistent_failures"]
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertNotIn("nonpersistent_failures", descriptor["required_kinds"])
        self.assertNotIn("nonpersistent_failures", compile_execution_policy(policy)["profile"])

    def test_api_source_is_profile_metadata_not_an_extra_runtime_predicate(self):
        context = fixture()
        profile = context["execution_policy"]["profile"]
        profile["nonpersistent_failures"]["create_promise"][0]["api_source"] = {
            "pointer": "/paths/~1create_promise/post/responses/503/description", "quote": "Invented provenance metadata."}
        catalogue = _catalogue(context["contract"], context["openapi"], None)
        state = run_guard_step("init", None, profile=profile, catalog=catalogue,
                               customer_id="invented", now="2031-05-02")[0]
        def advance(phase, action="", inputs=None, result=None):
            nonlocal state
            response = run_guard_step(phase, state, action, inputs, result, profile=profile, catalog=catalogue)
            state = response[0]
            return response
        for field in profile["identity_fields"]:
            advance("prepare", "verify_identity", {"field": field, "answer": "invented"})
            advance("result", result={"ok": True, "verified": True, "wrong_answers_left": 4})
        advance("prepare", "get_account")
        advance("result", result={"ok": True, "products": [{"product_id": "invented-product", "type": "revolving",
            "days_late": 3, "past_due": 80, "minimum_due": 20}]})
        advance("prepare", "stage_promise", {"product_id": "invented-product", "amount": 20, "date": "2031-05-06"})
        advance("prepare", "commit_promises")
        body = advance("confirm", inputs={"consent": "yes"})[2]
        response = advance("result", result={"ok": False, "error": "temporarily_unavailable", "_http_status": 503})
        self.assertEqual(response[1], "allow")
        self.assertEqual(response[2], body)

    def test_malformed_witness_is_rejected_by_pure_profile_shape(self):
        policy, _, _, _, actual = supplied_packet()
        rule(policy, "nonpersistent_failures")["value"][actual][0]["api_source"] = {"pointer": [], "quote": "invented"}
        with self.assertRaises(PolicyError):
            compile_execution_policy(policy)


if __name__ == "__main__":
    unittest.main()

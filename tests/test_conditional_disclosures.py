"""Independently authored conditional account fields through exact compiled code."""
from copy import deepcopy
import json
import unittest

from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.contract import compile_policy_contract
from builder.policy import PolicyError, compile_execution_policy, execution_rule_guidance
from builder.safety import digest
from test_guard_aliases import renamed_context, recompile_context
from test_guard_profiles import invented_policy
from test_guards import fixture


def authored(disclosure, *, renamed=False, codes=None):
    context = renamed_context()[0] if renamed else fixture()
    profile = deepcopy(context["execution_policy"]["profile"])
    profile["account_disclosure_fields"] = deepcopy(disclosure)
    if codes is not None:
        profile.setdefault("wire_codes", {})["product_types"] = deepcopy(codes)
    products = context["openapi"]["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    products = products[profile.get("result_fields", {}).get("get_account", {}).get("products", "products")]["items"]["properties"]
    products["ending_digits"] = {"type": "string", "pattern": "^[0-9]{4}$", "description": "Only the independently sourced revolving product exposes these digits."}
    if renamed:
        return recompile_context(context, profile)
    text = "Independently authored disclosure policy: " + json.dumps(profile, sort_keys=True)
    source = {"id": "disclosure-source", "file": "independent-disclosure.txt", "text": text, "sha256": digest(text)}
    policy = {"version": 1, "goal": "Narrate permitted synthetic account facts", "allowed_actions": list(context["openapi"]["paths"][path]["post"]["operationId"] for path in context["openapi"]["paths"]),
              "sources": [{key: value for key, value in source.items() if key != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": source["id"], "quote": text}}
                        for kind, value in profile.items()], "unsupported_constraints": []}
    context["contract"] = compile_policy_contract(policy, [source], context["openapi"])
    execution = {"version": 1, "policy_sha256": digest(policy), "profile": profile}
    execution["sha256"] = digest(execution)
    context["execution_policy"] = execution
    return context


def account(context, products):
    workflow = compile_guarded_workflow(**context)["workflow"]
    variables = {"customer_id": "PRIVATE_CUSTOMER", "now": "2031-05-02"}
    def code(name):
        source = workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(source, {"vars": variables})
    def response(action, data):
        api = workflow["nodes"]["bg_" + action + "_api"]
        fields = context["execution_policy"]["profile"].get("result_fields", {}).get("verify_identity" if action == "identity" else "get_account", {})
        envelope = {"status_code": 200, "data": {fields.get(key, key): value for key, value in data.items()}}
        exec(api["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in api["response_mapping"]:
            variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
        code("bg_" + action + "_result")
    code("bg_init")
    profile = context["execution_policy"]["profile"]
    for field in profile["identity_fields"]:
        variables.update(answer_field=field, answer_text="independent-answer")
        code("identity")
        response("identity", {"ok": True, "verified": True, "wrong_answers_left": profile["identity_wrong_limit"]})
    code("account")
    response("account", {"ok": True, "products": products})
    return variables


def products(renamed=False):
    if renamed:
        return [{"asset_reference": "PRIVATE_PRODUCT_ONE", "kind_wire": "KIND_A", "delay_marker": 8,
                 "arrears_value": 300, "floor_alpha": 37.25, "ending_digits": "6248"},
                {"asset_reference": "PRIVATE_PRODUCT_TWO", "kind_wire": "KIND_B", "delay_marker": 28,
                 "arrears_value": 600, "floor_beta": 82.10}]
    return [{"product_id": "PRIVATE_PRODUCT_ONE", "type": "revolving", "days_late": 8,
             "past_due": 300, "minimum_due": 37.25, "ending_digits": "6248"},
            {"product_id": "PRIVATE_PRODUCT_TWO", "type": "fixed", "days_late": 28,
             "past_due": 600, "instalment_due": 82.10}]


class ConditionalDisclosureTests(unittest.TestCase):
    def test_source_shape_transport_and_generic_extraction_guidance(self):
        value = {"common": ["human_caption"], "by_product_type": {"invented-type-47": ["local_marker"], "invented-type-92": []}}
        compiled = compile_execution_policy(invented_policy({"account_disclosure_fields": value}))
        self.assertEqual(compiled["profile"]["account_disclosure_fields"], value)
        guidance = execution_rule_guidance()["account_disclosure_fields"]
        self.assertIn("by_product_type", guidance)
        self.assertIn("required on every product", guidance)
        self.assertIn("wire_codes.product_types", guidance)

    def test_malformed_shapes_and_internal_field_names_are_rejected(self):
        values = [{}, {"common": [], "by_product_type": {}}, {"common": "name", "by_product_type": {"T": []}},
                  {"common": [], "by_product_type": {"": ["name"]}},
                  {"common": [], "by_product_type": {"T": ["name", "name"]}},
                  {"common": ["product_id"], "by_product_type": {"T": []}},
                  {"common": [], "by_product_type": {"T": ["customer_id"]}},
                  {"common": [], "by_product_type": {"T": [False]}},
                  {"common": [], "by_product_type": {"T": []}, "optional": True}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(PolicyError):
                compile_execution_policy(invented_policy({"account_disclosure_fields": value}))

    def test_conditional_field_is_required_only_for_its_supplied_type(self):
        context = authored({"common": ["type", "past_due", "days_late"], "by_product_type": {"revolving": ["ending_digits"]}})
        variables = account(context, products())
        self.assertEqual(variables["bg_route"], "done")
        self.assertNotEqual(variables["bg_quarantined"], "yes")
        facts = json.loads(variables["bg_facts"])
        displays = [entry["fields"] for entry in facts["account"]["products"]]
        self.assertEqual(displays, [{"type": "revolving", "past_due": 300, "days_late": 8, "ending_digits": "6248"},
                                    {"type": "fixed", "past_due": 600, "days_late": 28}])
        self.assertNotIn("PRIVATE_", json.dumps(facts["account"]))
        returned = products()
        returned[1]["ending_digits"] = "1717"
        variables = account(context, returned)
        self.assertNotIn("ending_digits", json.loads(variables["bg_facts"])["account"]["products"][1]["fields"])

    def test_missing_mandatory_common_or_selected_field_still_quarantines(self):
        context = authored({"common": ["type", "past_due"], "by_product_type": {"revolving": ["ending_digits"]}})
        for missing, position in (("past_due", 1), ("ending_digits", 0)):
            values = products()
            del values[position][missing]
            with self.subTest(missing=missing):
                variables = account(context, values)
                self.assertEqual(variables["bg_quarantined"], "yes")
                self.assertFalse(json.loads(variables["bg_state"])["account_loaded"])
                if missing == "ending_digits":
                    self.assertEqual(json.loads(variables["bg_facts"])["latest_action"]["status"], "fact_projection_failed")

    def test_legacy_global_array_remains_unconditionally_required(self):
        context = authored(["type", "past_due", "ending_digits"])
        variables = account(context, products())
        self.assertEqual(variables["bg_quarantined"], "yes")
        self.assertEqual(json.loads(variables["bg_facts"])["latest_action"]["status"], "fact_projection_failed")

    def test_renamed_fields_and_opaque_type_codes_bind_through_exact_api(self):
        value = {"common": ["kind_wire", "arrears_value", "delay_marker"], "by_product_type": {"source-alpha": ["ending_digits"]}}
        context = authored(value, renamed=True, codes={"source-alpha": "KIND_A", "source-beta": "KIND_B"})
        variables = account(context, products(True))
        self.assertEqual(variables["bg_route"], "done")
        fields = [entry["fields"] for entry in json.loads(variables["bg_facts"])["account"]["products"]]
        self.assertEqual(fields[0]["ending_digits"], "6248")
        self.assertNotIn("ending_digits", fields[1])
        self.assertNotIn("asset_reference", json.dumps(fields))

    def test_unsupported_types_non_scalar_fields_aliases_and_collisions_are_rejected(self):
        for mode in ("unknown-type", "unknown-field", "identifier-alias", "non-scalar", "collision"):
            value = {"common": ["kind_wire"], "by_product_type": {"KIND_A": ["ending_digits"]}}
            if mode == "unknown-type":
                value["by_product_type"] = {"UNDECLARED": ["ending_digits"]}
            elif mode == "unknown-field":
                value["common"] = ["never_supplied"]
            elif mode == "identifier-alias":
                value["by_product_type"]["KIND_A"] = ["asset_reference"]
            elif mode == "collision":
                value["by_product_type"]["source-alpha"] = ["ending_digits"]
            context = authored(value, renamed=True, codes={"source-alpha": "KIND_A"})
            if mode == "non-scalar":
                item = context["openapi"]["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["evidence_products"]["items"]
                item["properties"]["ending_digits"] = {"type": "array", "items": {"type": "string"}}
                context = recompile_context(context, context["execution_policy"]["profile"])
            with self.subTest(mode=mode), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context)

    def test_malformed_type_or_selected_scalar_cannot_be_narrated(self):
        context = authored({"common": ["past_due"], "by_product_type": {"revolving": ["ending_digits"]}})
        for field, value in (("type", "NOT_AN_API_TYPE"), ("type", False), ("ending_digits", "not-digits")):
            values = products()
            values[0][field] = value
            with self.subTest(field=field, value=value):
                variables = account(context, values)
                self.assertEqual(variables["bg_quarantined"], "yes")
                self.assertEqual(json.loads(variables["bg_facts"])["latest_action"]["status"], "fact_projection_failed")

    def test_semantic_keys_cannot_shadow_supplied_wire_type_codes(self):
        value = {"common": ["kind_wire"], "by_product_type": {"KIND_A": ["ending_digits"]}}
        for codes in ({"source-alpha": "KIND_A", "KIND_A": "KIND_B"}, {"KIND_A": "KIND_B"}):
            context = authored(value, renamed=True, codes=codes)
            with self.subTest(codes=codes), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context)


if __name__ == "__main__":
    unittest.main()

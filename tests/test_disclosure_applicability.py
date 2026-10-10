"""Independent fictional policies for selected-goal disclosure applicability.

Exercise compiled code and immutable source contracts, without generation,
hosted calls, historical artifacts or evaluator inputs.
"""
import copy
import json
import unittest

from builder.action_bindings import compile_action_bindings
from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements, validate_plan_execution
from builder.guards import (GuardCompileError, _profile, compile_guarded_workflow,
                            required_execution_kinds, validate_guarded_workflow)
from builder.policy import PolicyError, compile_execution_policy
from builder.safety import digest


ABSENT = object()
SCOPED = {"common": ["human_label"], "by_product_type": {"TYPE-X": ["local_marker"]}}


def operation(fields, required, result):
    return {"requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "properties": fields, "required": required}}}},
        "responses": {"200": {"content": {"application/json": {"schema": {
            "type": "object", "properties": result}}}}}}


def fictional(disclosure=ABSENT, *, getter_permitted=False, getter_selected=False, codes=None):
    string = {"type": "string"}
    result = {"ok": {"type": "boolean"}, "error": string}
    closing = operation({"customer_id": string, "outcome": {
        "type": "string", "enum": ["not_verified", "not_reached"]}, "reason": string},
        ["customer_id", "outcome", "reason"], result)
    product = {"type": "object", "properties": {
        "product_id": string, "type": {"type": "string", "enum": ["TYPE-X", "TYPE-Y"]},
        "days_late": {"type": "integer"}, "past_due": {"type": "number"},
        "human_label": string, "local_marker": string}}
    getter = operation({"customer_id": string}, ["customer_id"],
                       {**result, "products": {"type": "array", "items": product}})
    identity = operation({"customer_id": string, "field": {"type": "string", "enum": ["factor_a", "factor_b"]},
                          "answer": string}, ["customer_id", "field", "answer"],
                         {**result, "verified": {"type": "boolean"}, "wrong_answers_left": {"type": "integer"}})
    api = {"openapi": "3.0.3", "servers": [{"url": "https://fictional.invalid"}],
           "paths": {"/close_call": {"post": closing}, "/get_account": {"post": getter},
                     "/verify_identity": {"post": identity}}}
    for name, method in api["paths"].items():
        method["post"]["operationId"] = name[1:]
    actions = ["verify_identity", "get_account", "close_call"] if getter_selected else ["close_call"]
    allowed = ["close_call"]
    if getter_permitted or getter_selected:
        allowed.append("get_account")
    if getter_selected:
        allowed.append("verify_identity")
    values = {"execution_actions": actions, "record_notes": False,
              "close_reasons": {"not_verified": ["failed", "declined"], "not_reached": ["unclear"]},
              "identity_close": {"failed": {"outcome": "not_verified", "reason": "failed"},
                                 "incomplete": {"outcome": "not_verified", "reason": "declined"},
                                 "unreached": {"outcome": "not_reached", "reason": "unclear"}}}
    if disclosure is not ABSENT:
        values["account_disclosure_fields"] = copy.deepcopy(disclosure)
    if codes is not None:
        values["wire_codes"] = {"product_types": copy.deepcopy(codes)}
    if getter_selected:
        values.update(identity_fields=["factor_a", "factor_b"], identity_wrong_limit=2,
                      product_order={"mode": "account_order"}, resolve_all_products=True)
    text = "Independent fictional disclosure requirements: " + json.dumps(values, sort_keys=True)
    source = {"id": "fictional", "file": "fictional-disclosure.txt", "text": text, "sha256": digest(text)}
    policy = {"version": 1, "goal": "Handle fictional contact and its permitted account facts",
              "allowed_actions": allowed, "sources": [{k: v for k, v in source.items() if k != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": value,
                         "source": {"id": "fictional", "quote": text}} for kind, value in values.items()],
              "unsupported_constraints": []}
    nodes = {"closing": {"name": "closing", "node_type": "guard_action", "action": "close_call",
                         "inputs": {"outcome": "proposed_outcome", "reason": "proposed_reason"},
                         "success_node": "closing", "blocked_node": "closing", "failure_node": "closing"}}
    if getter_selected:
        nodes.update(identity={"name": "identity", "node_type": "guard_action", "action": "verify_identity",
            "inputs": {"field": "proposed_factor", "answer": "proposed_answer"},
            "success_node": "closing", "blocked_node": "closing", "failure_node": "closing"},
            account={"name": "account", "node_type": "guard_action", "action": "get_account", "inputs": {},
            "success_node": "closing", "blocked_node": "closing", "failure_node": "closing"})
    return policy, [source], api, {"initial_node": "identity" if getter_selected else "closing", "nodes": nodes}


def context(values):
    policy, sources, api, plan = values
    bindings = compile_action_bindings(policy, api)
    return {"plan": plan, "contract": compile_policy_contract(policy, sources, api), "openapi": api,
            "execution_policy": compile_execution_policy(policy, bindings, required_kinds=required_execution_kinds(plan)),
            "bindings": bindings}


def rehash(value):
    value["sha256"] = digest({key: item for key, item in value.items() if key != "sha256"})


class Session:
    def __init__(self, compiled):
        self.workflow = compile_guarded_workflow(**compiled)["workflow"]
        self.variables = {"customer_id": "FICTIONAL-CUSTOMER", "now": "2032-04-07T11:00"}
        self.code("bg_init")

    def code(self, name):
        source = self.workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(source, {"vars": self.variables})

    def response(self, name, data):
        node = self.workflow["nodes"]["bg_" + name + "_api"]
        envelope = {"status_code": 200, "data": copy.deepcopy(data)}
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for item in node["response_mapping"]:
            self.variables[item["variable"]] = envelope["data"].get(item["path"][5:], "")
        self.code("bg_" + name + "_result")

    def account(self, products):
        for factor in ("factor_a", "factor_b"):
            self.variables.update(proposed_factor=factor, proposed_answer="fictional-answer")
            self.code("identity")
            self.response("identity", {"ok": True, "verified": True, "wrong_answers_left": 2})
        self.code("account")
        self.response("account", {"ok": True, "products": products})

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])

    @property
    def facts(self):
        return json.loads(self.variables["bg_facts"])


class DisclosureApplicabilityTests(unittest.TestCase):
    def test_unused_legacy_and_scoped_metadata_compile_and_close_without_getter_selection(self):
        cases = ((value, permission) for value in (["human_label"], SCOPED) for permission in (False, True))
        for disclosure, getter_permitted in cases:
            with self.subTest(disclosure=disclosure, getter_permitted=getter_permitted):
                values = fictional(disclosure, getter_permitted=getter_permitted)
                before = copy.deepcopy(values)
                requirements = compile_execution_requirements(*values[:3])
                compiled = context(values)
                self.assertEqual(validate_plan_execution(values[3], requirements, *values[:3]), [])
                frozen = copy.deepcopy(compiled["execution_policy"])
                runtime = _profile(compiled["contract"], frozen, compiled["plan"], compiled["bindings"])
                self.assertEqual(runtime["account_disclosure_fields"], [])
                self.assertEqual(frozen["profile"]["account_disclosure_fields"], disclosure)
                session = Session(compiled)
                self.assertEqual(validate_guarded_workflow(session.workflow, **compiled), [])
                session.variables.update(proposed_outcome="not_reached", proposed_reason="unclear")
                session.code("closing")
                self.assertEqual(session.variables["bg_route"], "allow")
                self.assertEqual(json.loads(session.variables["bg_body"])["outcome"], "not_reached")
                session.response("closing", {"ok": True})
                self.assertTrue(session.state["closed"])
                self.assertFalse(session.state["account_loaded"])
                self.assertEqual(session.workflow["nodes"]["bg_closing_result"]["transitions"][0]["target_node"], "bg_terminal")
                self.assertEqual(session.workflow["nodes"]["bg_terminal"]["on_enter"], {"type": "silent"})
                self.assertNotIn("bg_account_api", session.workflow["nodes"])
                self.assertEqual(compiled["execution_policy"], frozen)
                self.assertEqual(values, before)
                self.assertEqual(requirements["policy_sha256"], digest(values[0]))

    def test_optional_getter_permission_does_not_activate_unused_schema_or_type_binding(self):
        metadata = {"common": ["not_in_this_api"], "by_product_type": {"not_in_api_enum": ["another_unused_field"]}}
        for getter_permitted in (False, True):
            with self.subTest(getter_permitted=getter_permitted):
                values = fictional(metadata, getter_permitted=getter_permitted)
                requirements = compile_execution_requirements(*values[:3])
                self.assertEqual(requirements["actions"], ["close_call"])
                compiled = context(values)
                Session(compiled)
                self.assertEqual(compiled["execution_policy"]["profile"]["account_disclosure_fields"], metadata)

    def test_unused_binding_collisions_remain_frozen_and_inert_but_selected_getter_rejects(self):
        metadata = {"common": [], "by_product_type": {"source-X": [], "TYPE-X": []}}
        codes = {"source-X": "TYPE-X"}
        for getter_permitted in (False, True):
            values = fictional(metadata, getter_permitted=getter_permitted, codes=codes)
            compiled = context(values)
            before = copy.deepcopy(compiled)
            compile_execution_requirements(*values[:3])
            Session(compiled)
            self.assertEqual(compiled, before)
        with self.assertRaisesRegex(GuardCompileError, "keys collide"):
            Session(context(fictional(metadata, getter_selected=True, codes=codes)))

    def test_absent_disclosure_metadata_remains_absent_in_frozen_and_runtime_profiles(self):
        values = fictional()
        compiled = context(values)
        compile_execution_requirements(*values[:3])
        runtime = _profile(compiled["contract"], compiled["execution_policy"], compiled["plan"], compiled["bindings"])
        self.assertNotIn("account_disclosure_fields", runtime)
        self.assertNotIn("account_disclosure_fields", compiled["execution_policy"]["profile"])

    def test_unused_malformed_metadata_is_rejected_before_suppression(self):
        for value in ({"common": [], "by_product_type": {}}, {"common": [], "by_product_type": {"": []}},
                      {"common": [False], "by_product_type": {"TYPE-X": []}},
                      {"common": [], "by_product_type": {"TYPE-X": ["human_label", "human_label"]}},
                      {"common": [], "by_product_type": {"TYPE-X": ["product_id"]}},
                      {"common": [], "by_product_type": {"TYPE-X": []}, "extra": True}):
            with self.subTest(value=value), self.assertRaises(PolicyError):
                context(fictional(value))

    def test_unused_supplied_metadata_cannot_be_omitted_changed_or_shape_corrupted(self):
        compiled = context(fictional(SCOPED))
        for mode in ("omit", "change", "corrupt", "stale-hash"):
            changed = copy.deepcopy(compiled)
            profile = changed["execution_policy"]["profile"]
            if mode == "omit":
                del profile["account_disclosure_fields"]
            elif mode == "corrupt":
                profile["account_disclosure_fields"] = {"common": "human_label", "by_product_type": {"TYPE-X": []}}
            else:
                profile["account_disclosure_fields"] = ["human_label"]
            if mode != "stale-hash":
                rehash(changed["execution_policy"])
            with self.subTest(mode=mode), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**changed)

    def test_selected_getter_still_requires_exact_fields_and_supplied_type_enum(self):
        for value, expected in (({"common": ["not_in_api"], "by_product_type": {"TYPE-X": []}}, "missing or is not"),
                                ({"common": ["human_label"], "by_product_type": {"not_in_enum": []}}, "outside the supplied API enum")):
            with self.subTest(value=value), self.assertRaisesRegex(GuardCompileError, expected):
                Session(context(fictional(value, getter_selected=True)))

    def test_selected_common_type_overlap_is_safe_and_retains_exact_frozen_values(self):
        value = {"common": ["human_label"], "by_product_type": {
            "TYPE-X": ["human_label", "local_marker"], "TYPE-Y": ["human_label"]}}
        values = fictional(value, getter_selected=True)
        compiled = context(values)
        before = copy.deepcopy(compiled)
        compile_execution_requirements(*values[:3])
        session = Session(compiled)
        session.account([{"product_id": "FICTIONAL-X", "type": "TYPE-X", "days_late": 4,
                          "past_due": 12.50, "human_label": "Alpha service", "local_marker": "M-X"},
                         {"product_id": "FICTIONAL-Y", "type": "TYPE-Y", "days_late": 8,
                          "past_due": 37.25, "human_label": "Beta service"}])
        self.assertEqual(session.variables["bg_route"], "done")
        self.assertNotEqual(session.variables["bg_quarantined"], "yes")
        self.assertEqual([row["fields"] for row in session.facts["account"]["products"]],
                         [{"human_label": "Alpha service", "local_marker": "M-X"}, {"human_label": "Beta service"}])
        self.assertEqual(compiled, before)

    def test_selected_applicable_field_remains_mandatory_after_inert_goal_support(self):
        session = Session(context(fictional(SCOPED, getter_selected=True)))
        session.account([{"product_id": "FICTIONAL-X", "type": "TYPE-X", "days_late": 4,
                          "past_due": 12.50, "human_label": "Alpha service"}])
        self.assertEqual(session.variables["bg_quarantined"], "yes")
        self.assertFalse(session.state["account_loaded"])
        self.assertEqual(session.facts["latest_action"]["status"], "fact_projection_failed")


if __name__ == "__main__":
    unittest.main()

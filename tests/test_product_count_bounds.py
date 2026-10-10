"""Invented count policies exercised through exact compiled protected code.

No evaluator inputs, generation, or hosted calls are used. These checks prove
local account acceptance and privacy boundaries, not conversation compliance.
"""
import copy
import json
import unittest

from builder.contract import compile_policy_contract
from builder.guards import (GuardCompileError, _KERNEL, _profile,
                            compile_guarded_workflow, required_execution_kinds)
from builder.policy import PolicyError, compile_execution_policy
from builder.safety import digest
from test_guards import fixture


MISSING = object()
PRODUCTS = [
    {"product_id": "BOUND-PRODUCT-Z", "type": "fixed", "days_late": 28,
     "past_due": 600.11, "instalment_due": 82.10},
    {"product_id": "BOUND-PRODUCT-A", "type": "revolving", "days_late": 8,
     "past_due": 300.19, "minimum_due": 37.25},
    {"product_id": "BOUND-PRODUCT-M", "type": "fixed", "days_late": 28,
     "past_due": 200.50, "instalment_due": 40.25},
    {"product_id": "BOUND-PRODUCT-Q", "type": "revolving", "days_late": 12,
     "past_due": 150.75, "minimum_due": 25.00},
]


def profile_values(bounds=MISSING, *, single=False, order=MISSING):
    profile = copy.deepcopy(fixture()["execution_policy"]["profile"])
    profile["account_disclosure_fields"] = ["type", "past_due", "days_late"]
    if bounds is not MISSING:
        profile["product_count_bounds"] = copy.deepcopy(bounds)
    if single:
        profile["policy_applicability"] = {"products": "single"}
        del profile["product_order"]
        del profile["resolve_all_products"]
    if order is not MISSING:
        profile["product_order"] = copy.deepcopy(order)
    return profile


def sourced_policy(profile):
    # The complete invented input is its own literal witness. No approved bank
    # input or generated policy is modified to manufacture these regressions.
    text = "Independent synthetic count policy: " + json.dumps(profile)
    source = {"id": "synthetic", "file": "invented-count-policy.txt", "text": text, "sha256": digest(text)}
    api_context = fixture()
    policy = {"version": 1, "goal": "Resolve synthetic account products",
              "allowed_actions": [op["name"] for op in api_context["contract"]["permitted_operations"]],
              "sources": [{k: v for k, v in source.items() if k != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": copy.deepcopy(value),
                         "source": {"id": "synthetic", "quote": text}}
                        for kind, value in profile.items()], "unsupported_constraints": []}
    return policy, [source], api_context


def context_for(bounds=MISSING, *, single=False, order=MISSING):
    policy, sources, context = sourced_policy(profile_values(bounds, single=single, order=order))
    context["contract"] = compile_policy_contract(policy, sources, context["openapi"])
    context["execution_policy"] = compile_execution_policy(
        policy, required_kinds=required_execution_kinds(context["plan"]))
    return context, policy


def rehash(value):
    value["sha256"] = digest({key: item for key, item in value.items() if key != "sha256"})


def kernel_order(products, profile, *, account_loaded):
    namespace = {}
    exec(_KERNEL, namespace)
    return namespace["guard_order"]({"products": copy.deepcopy(products), "account_loaded": account_loaded}, profile)


class CompiledSession:
    def __init__(self, context):
        self.workflow = compile_guarded_workflow(**context)["workflow"]
        self.variables = {"customer_id": "SYNTHETIC-COUNT-CUSTOMER", "now": "2031-05-02T11:00"}
        self.code("bg_init")

    def code(self, name):
        code = self.workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(code, {"vars": self.variables})

    def result(self, entry, data):
        node = self.workflow["nodes"]["bg_" + entry + "_api"]
        envelope = {"status_code": 200, "data": copy.deepcopy(data)}
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            self.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
        self.code("bg_" + entry + "_result")

    def verify(self):
        for field in ("postal_code", "memorable_word"):
            self.variables.update(answer_field=field, answer_text="synthetic-answer")
            self.code("identity")
            self.result("identity", {"ok": True, "verified": True, "wrong_answers_left": 4})

    def account(self, products):
        self.verify()
        self.code("account")
        self.result("account", {"ok": True, "products": products})

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])

    @property
    def facts(self):
        return json.loads(self.variables["bg_facts"])


class ProductCountBoundsTests(unittest.TestCase):
    def test_partial_and_zero_bounds_preserve_exact_supplied_shape_without_defaults(self):
        for bounds in ({"min": 0}, {"max": 0}, {"min": 0, "max": 0},
                       {"min": 2}, {"max": 4}, {"min": 2, "max": 4}):
            with self.subTest(bounds=bounds):
                context, policy = context_for(bounds)
                before = copy.deepcopy(policy)
                execution = compile_execution_policy(policy)
                self.assertEqual(execution["profile"]["product_count_bounds"], bounds)
                runtime = _profile(context["contract"], context["execution_policy"], context["plan"], None)
                self.assertEqual(runtime["product_count_bounds"], bounds)
                execution["profile"]["product_count_bounds"]["max"] = 99
                self.assertEqual(policy, before)
                self.assertEqual(context["execution_policy"]["profile"]["product_count_bounds"], bounds)

    def test_wrong_json_types_negative_values_unknown_keys_and_reversed_interval_reject(self):
        malformed = [None, [], "count", False, 1, {}, {"minimum": 1}, {"max": 2, "other": 0},
                     {"min": 3, "max": 2}]
        for bound in (True, False, 1.0, "1", None, -1, [], {}):
            malformed.extend(({"min": bound}, {"max": bound}))
        for bounds in malformed:
            with self.subTest(bounds=bounds):
                policy, _, _ = sourced_policy(profile_values(bounds))
                with self.assertRaisesRegex(PolicyError, "product_count_bounds"):
                    compile_execution_policy(policy)

    def test_active_bounds_cannot_be_omitted_or_changed_in_narrow_profile(self):
        context, _ = context_for({"min": 1, "max": 3})
        for value in (MISSING, {"min": 1, "max": 99}):
            with self.subTest(value=value):
                changed = copy.deepcopy(context)
                if value is MISSING:
                    del changed["execution_policy"]["profile"]["product_count_bounds"]
                else:
                    changed["execution_policy"]["profile"]["product_count_bounds"] = value
                rehash(changed["execution_policy"])
                with self.assertRaisesRegex(GuardCompileError, "product_count_bounds"):
                    compile_guarded_workflow(**changed)

    def test_single_scope_with_lower_bound_above_one_is_a_compile_conflict(self):
        for bounds in ({"min": 2}, {"min": 2, "max": 4}):
            with self.subTest(bounds=bounds):
                context, _ = context_for(bounds, single=True)
                with self.assertRaises(GuardCompileError):
                    compile_guarded_workflow(**context)

    def test_unused_count_rule_remains_metadata_until_an_account_consumer_is_selected(self):
        context, _ = context_for({"min": 2, "max": 4}, single=True)
        original_account = copy.deepcopy(context["plan"]["nodes"]["account"])
        keep = {"talk", "identity", "callback", "close"}
        context["plan"]["nodes"] = {name: node for name, node in context["plan"]["nodes"].items() if name in keep}
        context["plan"]["nodes"]["talk"]["transitions"] = [edge for edge in context["plan"]["nodes"]["talk"]["transitions"]
            if edge["target_node"] in keep]
        before = copy.deepcopy(context)
        runtime = _profile(context["contract"], context["execution_policy"], context["plan"], None)
        self.assertEqual(runtime["product_count_bounds"], {"min": 2, "max": 4})
        self.assertEqual(runtime["policy_applicability"], {"products": "single"})
        session = CompiledSession(context)
        api_names = {node["pre_actions"][0]["function"]["name"] for node in session.workflow["nodes"].values()
                     if node["node_type"] == "api_request"}
        self.assertEqual(api_names, {"verify_identity", "schedule_callback", "close_call"})
        session.verify()
        self.assertEqual(session.state["identity_status"], "verified")
        session.variables.update(day="2031-05-06", at="12:00")
        session.code("callback")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertFalse(session.state["account_loaded"])
        self.assertFalse(session.state["quarantined"])
        self.assertEqual(context, before)
        # Selecting the consumer changes applicability, not the frozen policy.
        context["plan"]["nodes"]["account"] = original_account
        with self.assertRaisesRegex(GuardCompileError, "minimum exceeds supplied single-product scope"):
            compile_guarded_workflow(**context)
        self.assertEqual(context["contract"], before["contract"])
        self.assertEqual(context["execution_policy"], before["execution_policy"])

    def test_single_waiver_and_intrinsic_maximum_survive_looser_supplied_bound(self):
        context, _ = context_for({"min": 1, "max": 4}, single=True)
        self.assertNotIn("product_order", context["execution_policy"]["profile"])
        self.assertNotIn("resolve_all_products", context["execution_policy"]["profile"])
        accepted = CompiledSession(context)
        accepted.account(PRODUCTS[:1])
        self.assertEqual(accepted.variables["bg_route"], "done")
        self.assertFalse(accepted.state["quarantined"])
        rejected = CompiledSession(context)
        rejected.account(PRODUCTS[:2])
        self.assertTrue(rejected.state["quarantined"])
        self.assertEqual(rejected.variables["bg_quarantined"], "yes")
        self.assertFalse(rejected.state["account_loaded"])

    def test_supplied_count_bounds_preserve_account_order_and_stable_field_order(self):
        orders = [({"mode": "account_order"}, [1, 2, 3]),
                  ({"field": "days_late", "direction": "ascending", "tie_breaker": "account_order"}, [2, 1, 3]),
                  ({"field": "days_late", "direction": "descending", "tie_breaker": "account_order"}, [1, 3, 2])]
        for order, expected in orders:
            with self.subTest(order=order):
                context, _ = context_for({"min": 2, "max": 3}, order=order)
                session = CompiledSession(context)
                session.account(PRODUCTS[:3])
                self.assertEqual(session.variables["bg_route"], "done")
                self.assertEqual(session.state["products"], PRODUCTS[:3])
                references = session.facts["internal_action_references"]["products"]
                self.assertEqual([item["returned_position"] for item in references], expected)
                self.assertEqual(context["execution_policy"]["profile"]["product_order"], order)

    def test_loaded_accounts_below_minimum_or_above_maximum_quarantine_before_disclosure(self):
        for supplied in (PRODUCTS[:1], PRODUCTS):
            with self.subTest(count=len(supplied)):
                context, _ = context_for({"min": 2, "max": 3})
                session = CompiledSession(context)
                session.account(supplied)
                self.assertTrue(session.state["quarantined"])
                self.assertFalse(session.state["account_loaded"])
                self.assertEqual(session.state["products"], [])
                self.assertEqual(session.variables["bg_body"], "")
                self.assertEqual(session.variables["bg_quarantined"], "yes")
                self.assertEqual(session.facts["call_status"], "quarantined")
                self.assertNotIn("account", session.facts)
                self.assertFalse(session.state["attempts"])
                for product in supplied:
                    self.assertNotIn(product["product_id"], json.dumps(session.facts))
                for action in ("stage", "commit", "note", "finish", "transfer", "handoff", "close"):
                    session.variables.update(product="BOUND-PRODUCT-A", amount="37.25", day="2031-05-06",
                                             note_reason="refused", queue="helpdesk")
                    session.code(action)
                    self.assertEqual(session.variables["bg_body"], "")
                    self.assertEqual(session.variables["bg_quarantined"], "yes")
                    self.assertFalse(session.state["attempts"])

    def test_exact_lower_and_upper_bound_accounts_remain_usable(self):
        for count in (2, 3):
            with self.subTest(count=count):
                context, _ = context_for({"min": 2, "max": 3})
                session = CompiledSession(context)
                session.account(PRODUCTS[:count])
                self.assertEqual(session.variables["bg_route"], "done")
                self.assertFalse(session.state["quarantined"])
                self.assertTrue(session.state["account_loaded"])
                self.assertEqual(len(session.facts["account"]["products"]), count)
                session.variables.update(product="BOUND-PRODUCT-A", amount="37.25", day="2031-05-06")
                session.code("stage")
                self.assertEqual(session.variables["bg_route"], "done")
                self.assertIn("BOUND-PRODUCT-A", session.state["pending_promises"])

    def test_positive_minimum_does_not_force_account_access_before_identity_or_handoff(self):
        context, _ = context_for({"min": 2, "max": 3})
        session = CompiledSession(context)
        self.assertEqual(session.variables["bg_route"], "done")
        session.verify()
        self.assertEqual(session.state["identity_status"], "verified")
        self.assertFalse(session.state["account_loaded"])
        self.assertFalse(session.state["quarantined"])
        preidentity = CompiledSession(context)
        preidentity.variables["queue"] = "helpdesk"
        preidentity.code("transfer")
        preidentity.code("handoff")
        self.assertEqual(preidentity.variables["bg_route"], "allow")
        preidentity.result("handoff", {"ok": True})
        preidentity.code("close")
        self.assertEqual(preidentity.variables["bg_route"], "allow")
        self.assertEqual(json.loads(preidentity.variables["bg_body"])["outcome"], "transferred")
        self.assertFalse(preidentity.state["account_loaded"])

    def test_order_helper_enforces_upper_bound_always_and_lower_bound_only_when_loaded(self):
        context, _ = context_for({"min": 2, "max": 3})
        runtime = _profile(context["contract"], context["execution_policy"], context["plan"], None)
        self.assertEqual(kernel_order([], runtime, account_loaded=False), [])
        with self.assertRaises(ValueError):
            kernel_order(PRODUCTS, runtime, account_loaded=False)
        with self.assertRaises(ValueError):
            kernel_order(PRODUCTS[:1], runtime, account_loaded=True)

    def test_absent_bounds_add_no_count_default_and_retain_legacy_account_capacity(self):
        context, _ = context_for()
        self.assertNotIn("product_count_bounds", required_execution_kinds(context["plan"]))
        self.assertNotIn("product_count_bounds", context["execution_policy"]["profile"])
        runtime = _profile(context["contract"], context["execution_policy"], context["plan"], None)
        self.assertNotIn("product_count_bounds", runtime)
        session = CompiledSession(context)
        session.account(PRODUCTS)
        self.assertEqual(session.variables["bg_route"], "done")
        self.assertEqual(len(session.state["products"]), 4)
        self.assertFalse(session.state["quarantined"])


if __name__ == "__main__":
    unittest.main()

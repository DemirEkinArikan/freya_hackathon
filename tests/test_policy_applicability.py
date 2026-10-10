"""Source-backed applicability exercised through exact compiled guard code.

All policies, accounts and responses here are synthetic development inputs.
These regressions establish local compilation/execution, not hosted compliance.
"""
import copy
import json
import unittest

from builder.contract import PolicyContractError, compile_policy_contract
from builder.guards import (GuardCompileError, _catalogue, _profile,
                            compile_guarded_workflow, required_execution_kinds,
                            run_guard_step)
from builder.policy import MissingExecutionRules, PolicyError, compile_execution_policy
from builder.safety import digest
from test_guards import fixture


PRODUCTS = [
    {"product_id": "PRODUCT-Z", "type": "fixed", "days_late": 28,
     "past_due": 600.11, "instalment_due": 82.10},
    {"product_id": "PRODUCT-A", "type": "revolving", "days_late": 8,
     "past_due": 300.19, "minimum_due": 37.25},
    {"product_id": "PRODUCT-M", "type": "fixed", "days_late": 28,
     "past_due": 200.50, "instalment_due": 40.25},
]


def values(scope="single", omit_dimensions=True):
    result = copy.deepcopy(fixture()["execution_policy"]["profile"])
    if scope is not None:
        result["policy_applicability"] = {"products": scope}
    if omit_dimensions:
        for kind in ("product_order", "resolve_all_products"):
            result.pop(kind)
    return result


def sourced_context(profile, *, amendment=None):
    context = fixture()
    scope = profile.get("policy_applicability", {}).get("products", "undeclared")
    text = "Independent synthetic policy witness. Product scope is " + str(scope) + "."
    sources = [{"id": "base", "file": "synthetic-base.txt", "text": text, "sha256": digest(text)}]
    policy = {"version": 1, "goal": "Resolve synthetic account products",
              "allowed_actions": [op["name"] for op in context["contract"]["permitted_operations"]],
              "rules": [{"id": "base-" + kind, "kind": kind, "value": copy.deepcopy(value),
                         "source": {"id": "base", "quote": text}}
                        for kind, value in profile.items()], "unsupported_constraints": []}
    if amendment is not None:
        quote = "This synthetic amendment replaces product scope with " + amendment + "."
        sources.append({"id": "overlay", "file": "synthetic-overlay.txt", "text": quote, "sha256": digest(quote)})
        policy["rules"].append({"id": "overlay-scope", "kind": "policy_applicability",
                                "value": {"products": amendment}, "overrides": ["base-policy_applicability"],
                                "source": {"id": "overlay", "quote": quote}})
    policy["sources"] = [{k: v for k, v in source.items() if k != "text"} for source in sources]
    context["contract"] = compile_policy_contract(policy, sources, context["openapi"])
    context["execution_policy"] = compile_execution_policy(policy)
    return context, policy, sources


def rehash(value):
    value["sha256"] = digest({k: v for k, v in value.items() if k != "sha256"})


class CompiledSession:
    def __init__(self, context):
        self.workflow = compile_guarded_workflow(**context)["workflow"]
        self.variables = {"customer_id": "SYNTHETIC-CUSTOMER", "now": "2031-05-02T11:00"}
        self.code("bg_init")

    def code(self, name):
        source = self.workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(source, {"vars": self.variables})

    def result(self, entry, data, status=200):
        node = self.workflow["nodes"]["bg_" + entry + "_api"]
        envelope = {"status_code": status, "data": copy.deepcopy(data)}
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            self.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
        self.code("bg_" + entry + "_result")

    def account(self, products):
        for field in ("postal_code", "memorable_word"):
            self.variables.update(answer_field=field, answer_text="synthetic-answer")
            self.code("identity")
            self.result("identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        self.code("account")
        self.result("account", {"ok": True, "products": products})

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])

    @property
    def facts(self):
        return json.loads(self.variables["bg_facts"])


class PolicyApplicabilityTests(unittest.TestCase):
    def test_single_scope_waives_only_missing_dimensions_without_inventing_rules(self):
        context, policy, _ = sourced_context(values())
        before = copy.deepcopy(context)
        execution = compile_execution_policy(policy, required_kinds=required_execution_kinds(context["plan"]))
        self.assertNotIn("product_order", execution["profile"])
        self.assertNotIn("resolve_all_products", execution["profile"])
        runtime = _profile(context["contract"], execution, context["plan"], None)
        self.assertNotIn("product_order", runtime)
        self.assertIs(runtime["resolve_all_products"], True)
        self.assertEqual(context, before)
        self.assertEqual(execution["policy_sha256"], digest(policy))
        self.assertNotIn("resolve_all_products", context["contract"]["active_rules"])
        compile_guarded_workflow(**context)

    def test_single_scope_preserves_every_explicit_constraint(self):
        supplied = values(omit_dimensions=False)
        for resolve in (False, True):
            with self.subTest(resolve=resolve):
                supplied["resolve_all_products"] = resolve
                context, policy, _ = sourced_context(supplied)
                execution = compile_execution_policy(policy, required_kinds=required_execution_kinds(context["plan"]))
                self.assertEqual(execution["profile"], supplied)
                self.assertEqual(_profile(context["contract"], execution, context["plan"], None), supplied)

    def test_missing_scope_and_multiple_scope_require_both_dimensions(self):
        for scope in (None, "multiple"):
            with self.subTest(scope=scope):
                context, policy, _ = sourced_context(values(scope))
                with self.assertRaises(MissingExecutionRules) as error:
                    compile_execution_policy(policy, required_kinds=required_execution_kinds(context["plan"]))
                self.assertEqual(set(error.exception.missing_kinds), {"product_order", "resolve_all_products"})
                with self.assertRaises(GuardCompileError):
                    compile_guarded_workflow(**context)

    def test_malformed_scope_fails_typed_validation(self):
        context, policy, _ = sourced_context(values())
        invalid = [None, "single", [], {}, {"products": True}, {"products": 1},
                   {"products": []}, {"products": {}}, {"products": "unknown"},
                   {"products": "single", "resolve_all_products": False}]
        for value in invalid:
            with self.subTest(value=value):
                broken = copy.deepcopy(policy)
                next(rule for rule in broken["rules"] if rule["kind"] == "policy_applicability")["value"] = value
                with self.assertRaisesRegex(PolicyError, "policy_applicability"):
                    compile_execution_policy(broken, required_kinds=required_execution_kinds(context["plan"]))

    def test_applicability_quote_must_exist_in_approved_source(self):
        context, policy, sources = sourced_context(values())
        next(rule for rule in policy["rules"] if rule["kind"] == "policy_applicability")["source"]["quote"] = "Absent single-product instruction."
        with self.assertRaisesRegex(PolicyContractError, "quote is not present"):
            compile_policy_contract(policy, sources, context["openapi"])

    def test_explicit_sourced_overlay_controls_scope_and_retains_provenance(self):
        context, policy, sources = sourced_context(values("multiple"), amendment="single")
        active = context["contract"]["active_rules"]["policy_applicability"]
        self.assertEqual(active["value"], {"products": "single"})
        self.assertEqual(active["rule_ids"], ["overlay-scope"])
        self.assertEqual(active["provenance"][0]["id"], "overlay")
        self.assertEqual(active["provenance"][0]["quote"], sources[1]["text"])
        self.assertEqual({source["id"] for source in policy["sources"]}, {"base", "overlay"})
        execution = compile_execution_policy(policy, required_kinds=required_execution_kinds(context["plan"]))
        self.assertNotIn("product_order", execution["profile"])
        opposite, opposite_policy, _ = sourced_context(values("single"), amendment="multiple")
        with self.assertRaises(MissingExecutionRules):
            compile_execution_policy(opposite_policy, required_kinds=required_execution_kinds(opposite["plan"]))
        policy["rules"][-1].pop("overrides")
        with self.assertRaisesRegex(PolicyContractError, "conflicting active values"):
            compile_policy_contract(policy, sources, context["openapi"])

    def test_active_scope_and_explicit_dimensions_cannot_be_omitted_or_changed(self):
        context, _, _ = sourced_context(values(omit_dimensions=False))
        for kind in ("policy_applicability", "product_order", "resolve_all_products"):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(context)
                del changed["execution_policy"]["profile"][kind]
                rehash(changed["execution_policy"])
                with self.assertRaisesRegex(GuardCompileError, "active execution rule omitted from profile: " + kind):
                    compile_guarded_workflow(**changed)
        context["execution_policy"]["profile"]["policy_applicability"] = {"products": "multiple"}
        rehash(context["execution_policy"])
        with self.assertRaisesRegex(GuardCompileError, "does not match active frozen rule: policy_applicability"):
            compile_guarded_workflow(**context)

    def test_multiple_product_sort_and_account_order_ties_remain_stable(self):
        for direction, expected in (("ascending", [2, 1, 3]), ("descending", [1, 3, 2])):
            with self.subTest(direction=direction):
                supplied = values("multiple", omit_dimensions=False)
                supplied["product_order"]["direction"] = direction
                context, _, _ = sourced_context(supplied)
                session = CompiledSession(context)
                session.account(PRODUCTS)
                self.assertEqual(session.variables["bg_route"], "done")
                references = session.facts["internal_action_references"]["products"]
                self.assertEqual([item["returned_position"] for item in references], expected)
                self.assertEqual(session.state["products"], PRODUCTS)

    def test_single_product_can_commit_and_close_without_sort_or_resolution_rule(self):
        context, _, _ = sourced_context(values())
        session = CompiledSession(context)
        session.account(PRODUCTS[:1])
        self.assertEqual(session.variables["bg_route"], "done")
        session.variables.update(product="PRODUCT-Z", amount="82.10", day="2031-05-06")
        session.code("stage")
        self.assertEqual(session.variables["bg_route"], "done")
        session.code("commit")
        self.assertEqual(session.variables["bg_route"], "confirm")
        self.assertIn("82.10", session.variables["bg_readback"])
        session.variables["bg_consent"] = "yes"
        session.code("bg_commit_accept")
        body = json.loads(session.variables["bg_body"])
        self.assertEqual(body["product_id"], "PRODUCT-Z")
        session.result("commit", {"ok": True, "promise_id": "SYNTHETIC-PROMISE"})
        session.code("close")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(json.loads(session.variables["bg_body"])["outcome"], "promise")

    def test_unexpected_multiple_products_quarantine_compiled_account_projection(self):
        for explicit in (False, True):
            with self.subTest(explicit_constraints=explicit):
                context, _, _ = sourced_context(values(omit_dimensions=not explicit))
                session = CompiledSession(context)
                session.account(PRODUCTS[:2])
                self.assertEqual(session.variables["bg_route"], "blocked")
                self.assertTrue(session.state["quarantined"])
                self.assertFalse(session.state["account_loaded"])
                self.assertEqual(session.state["products"], [])
                self.assertEqual(session.facts["call_status"], "quarantined")
                for action in ("stage", "transfer", "handoff", "close"):
                    session.variables.update(product="PRODUCT-Z", amount="82.10", day="2031-05-06", queue="helpdesk")
                    session.code(action)
                    self.assertEqual(session.variables["bg_route"], "blocked")
                    self.assertEqual(session.variables["bg_body"], "")
                self.assertFalse(session.state["attempts"])

    def test_unresolved_single_product_cannot_close_under_missing_resolution_rule(self):
        context, _, _ = sourced_context(values())
        session = CompiledSession(context)
        session.account(PRODUCTS[:1])
        profile = _profile(context["contract"], context["execution_policy"], context["plan"], None)
        catalog = _catalogue(context["contract"], context["openapi"], None)
        with self.assertRaisesRegex(ValueError, "policy requires every product resolved before close"):
            run_guard_step("prepare", session.state, "close_call", profile=profile, catalog=catalog)
        session.code("close")
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertEqual(session.variables["bg_body"], "")
        self.assertFalse(session.state["close_attempted"])
        self.assertFalse(session.state["attempts"])

    def test_explicit_single_product_sort_constraint_is_not_silently_discarded(self):
        # Keep the account's required band field valid, but demand a distinct,
        # explicitly sourced sorting field that this account did not return.
        context, _, _ = sourced_context({**values(omit_dimensions=False),
            "product_order": {"field": "priority_rank", "direction": "descending", "tie_breaker": "account_order"}})
        session = CompiledSession(context)
        session.account(PRODUCTS[:1])
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertTrue(session.state["quarantined"])
        self.assertEqual(context["execution_policy"]["profile"]["product_order"]["field"], "priority_rank")

    def test_missing_retry_and_failure_disposition_never_invent_recovery(self):
        supplied = values()
        for kind in ("nonpersistent_failures", "promise_failure_disposition"):
            del supplied[kind]
        context, policy, _ = sourced_context(supplied)
        execution = compile_execution_policy(policy, required_kinds=required_execution_kinds(context["plan"]))
        self.assertEqual(execution["profile"], supplied)
        session = CompiledSession(context)
        session.account(PRODUCTS[:1])
        session.variables.update(product="PRODUCT-Z", amount="82.10", day="2031-05-06")
        session.code("stage")
        session.code("commit")
        session.variables["bg_consent"] = "yes"
        session.code("bg_commit_accept")
        session.result("commit", {"ok": False, "error": "temporarily_unavailable"}, status=503)
        self.assertEqual(session.variables["bg_route"], "failure")
        self.assertTrue(session.state["quarantined"])
        self.assertFalse(session.state["recorded_promises"])
        self.assertFalse(session.state["pending_notes"])
        session.code("commit")
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertEqual(session.variables["bg_body"], "")


if __name__ == "__main__":
    unittest.main()

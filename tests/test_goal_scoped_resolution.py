"""Independent source rules exercised through the exact compiled guard code."""
from copy import deepcopy
import json
import unittest

from builder.contract import PolicyContractError, compile_policy_contract
from builder.build import make_policy_prompt
from builder.guards import GuardCompileError, compile_guarded_workflow, validate_guarded_workflow
from builder.policy import PolicyError, compile_execution_policy, execution_rule_guidance
from builder.safety import digest
from builder.validation import validate_code
from test_guards import fixture


SELECTOR = "initial_service_code"
ALL = "REQUEST_71"
PARTIAL = "REQUEST_94"


def authored(rule, *, names=None, wire_codes=None, resolution_quote=None):
    context = fixture()
    profile = deepcopy(context["execution_policy"]["profile"])
    profile["resolve_all_products"] = deepcopy(rule)
    if names is None:
        names = [SELECTOR] if isinstance(rule, dict) else []
    if names:
        profile["call_variables"] = deepcopy(names)
    if wire_codes is not None:
        profile["wire_codes"] = deepcopy(wire_codes)
    text = "Independently authored synthetic service instructions: " + json.dumps(profile, sort_keys=True)
    if resolution_quote is not None:
        text += "\n" + resolution_quote
    source = {"id": "scope-source", "file": "independent-service-scope.txt", "text": text, "sha256": digest(text)}
    policy = {"version": 1, "goal": "Resolve the explicitly supplied service scope", "allowed_actions": [
        operation["post"]["operationId"] for operation in context["openapi"]["paths"].values()],
        "sources": [{key: value for key, value in source.items() if key != "text"}],
        "rules": [{"id": kind, "kind": kind, "value": value,
            "source": {"id": source["id"], "quote": resolution_quote if kind == "resolve_all_products" and resolution_quote is not None else text}}
            for kind, value in profile.items()], "unsupported_constraints": []}
    context["contract"] = compile_policy_contract(policy, [source], context["openapi"])
    context["execution_policy"] = compile_execution_policy(policy)
    return context


def scoped(selector=SELECTOR):
    return {"selector": selector, "by_value": {ALL: True, PARTIAL: False}}


class CompiledCall:
    def __init__(self, context, values=None):
        self.context = context
        self.workflow = compile_guarded_workflow(**context)["workflow"]
        self.variables = {"customer_id": "independent-customer", "now": "2031-05-02"}
        self.variables.update(values or {})
        self.code("bg_init")

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])

    def code(self, node):
        source = self.workflow["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(source, {"vars": self.variables})

    def target(self, node):
        def matches(condition):
            if condition.get("operator") == "always":
                return True
            if "conditions" in condition:
                values = [matches(item) for item in condition["conditions"]]
                return any(values) if condition.get("logic") == "or" else all(values)
            return condition.get("operator") == "equals" and self.variables.get(condition["variable"]) == condition["operand"]
        return next(item["target_node"] for item in self.workflow["nodes"][node]["transitions"] if matches(item["condition"]))

    def response(self, entry, data):
        node = self.workflow["nodes"]["bg_" + entry + "_api"]
        envelope = {"status_code": 200, "data": deepcopy(data)}
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            self.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
        self.code("bg_" + entry + "_result")

    def account(self):
        for field in self.context["execution_policy"]["profile"]["identity_fields"]:
            self.variables.update(answer_field=field, answer_text="source-independent-answer")
            self.code("identity")
            self.response("identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        self.code("account")
        self.response("account", {"ok": True, "products": [
            {"product_id": "asset-one", "type": "revolving", "days_late": 8, "past_due": 300, "minimum_due": 37.25},
            {"product_id": "asset-two", "type": "fixed", "days_late": 28, "past_due": 600, "instalment_due": 82.10}]})

    def promise(self, product="asset-one", amount="50.00"):
        self.variables.update(product=product, amount=amount, day="2031-05-06")
        self.code("stage")
        self.code("commit")
        self.variables["bg_consent"] = "yes"
        self.code("bg_commit_accept")
        self.response("commit", {"ok": True, "promise_id": "independent-record-" + product})


class GoalScopedResolutionTests(unittest.TestCase):
    def test_source_rule_preserves_typed_values_and_generic_guidance(self):
        context = authored(scoped())
        self.assertEqual(context["contract"]["active_rules"]["resolve_all_products"]["value"], scoped())
        self.assertEqual(context["execution_policy"]["profile"]["resolve_all_products"], scoped())
        guidance = execution_rule_guidance()["resolve_all_products"]
        for phrase in ("selector", "by_value", "read-only", "quarantine", "never allowed outcomes"):
            self.assertIn(phrase, guidance)
        self.assertEqual(validate_guarded_workflow(compile_guarded_workflow(**context)["workflow"], **context), [])

    def test_fresh_policy_prompt_agrees_with_typed_selector_interface(self):
        context = authored(scoped())
        prompt = make_policy_prompt([], context["openapi"])
        header, data = prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)
        interface = json.loads(data)["execution_interface"]["rule_shapes"]["resolve_all_products"]
        self.assertEqual(interface, execution_rule_guidance()["resolve_all_products"])
        self.assertIn('selector:"source_declared_input_name"', header)
        self.assertIn('by_value:{"supplied_input_code":boolean}', header)
        self.assertNotIn("record_notes/resolve_all_products: explicit booleans", header)

    def test_boolean_legacy_true_blocks_partial_and_false_keeps_legal_partial_close(self):
        for required in (True, False):
            with self.subTest(required=required):
                call = CompiledCall(authored(required))
                call.account()
                call.promise()
                call.code("close")
                self.assertEqual(call.variables["bg_route"], "blocked" if required else "allow")
                self.assertEqual(call.state["call_inputs"], {})
                self.assertFalse(call.state["quarantined"])
                if required:
                    self.assertEqual(call.target("close"), "bg_terminal")
                    self.assertFalse(call.state["close_attempted"])
                else:
                    self.assertEqual(json.loads(call.variables["bg_body"])["outcome"], "promise")

    def test_default_commitment_scope_requires_all_products_before_actual_close(self):
        call = CompiledCall(authored(scoped()), {SELECTOR: ALL})
        call.account()
        call.promise()
        call.code("close")
        self.assertEqual(call.variables["bg_route"], "blocked")
        self.assertFalse(call.variables["bg_body"])
        self.assertFalse(call.state["close_attempted"])
        call.promise("asset-two", "82.10")
        call.code("close")
        self.assertEqual(call.variables["bg_route"], "allow")
        self.assertEqual(set(call.state["recorded_promises"]), {"asset-one", "asset-two"})
        self.assertTrue(call.state["close_attempted"])
        call.response("close", {"ok": True})
        self.assertTrue(call.state["closed"])

    def test_explicit_other_scope_allows_sourced_partial_close_without_disposition_fabrication(self):
        call = CompiledCall(authored(scoped()), {SELECTOR: PARTIAL})
        call.account()
        call.promise()
        call.variables.update(outcome="paid", close_reason="refused")
        call.code("close")
        self.assertEqual(call.target("close"), "bg_close_api")
        self.assertEqual(json.loads(call.variables["bg_body"]), {"customer_id": "independent-customer", "outcome": "promise"})
        self.assertEqual(set(call.state["recorded_promises"]), {"asset-one"})
        self.assertFalse(call.state["pending_notes"])
        self.assertFalse(call.state["recorded_notes"])
        self.assertFalse(call.state["notes_finalized"])

    def test_missing_unknown_or_nonstrings_quarantine_before_any_api(self):
        context = authored(scoped())
        for values in ({}, {SELECTOR: "UNKNOWN"}, {SELECTOR: ""}, {SELECTOR: None}, {SELECTOR: True}, {SELECTOR: 71}, {SELECTOR: [ALL]}):
            with self.subTest(values=values):
                call = CompiledCall(context, values)
                self.assertEqual(call.variables["bg_quarantined"], "yes")
                self.assertEqual(call.target("bg_init"), "bg_quarantine")
                self.assertFalse(call.variables["bg_body"])
                self.assertFalse(call.state.get("attempts"))
                call.variables[SELECTOR] = PARTIAL
                call.code("close")
                self.assertEqual(call.target("close"), "bg_quarantine")
                self.assertFalse(call.state.get("close_attempted"))

    def test_later_ordinary_variables_cannot_change_initial_selected_scope(self):
        for initial, mutated, route in ((ALL, PARTIAL, "blocked"), (PARTIAL, ALL, "allow")):
            with self.subTest(initial=initial):
                call = CompiledCall(authored(scoped()), {SELECTOR: initial})
                call.account()
                call.promise()
                call.variables[SELECTOR] = mutated
                call.code("close")
                self.assertEqual(call.variables["bg_route"], route)
                self.assertEqual(call.state["call_inputs"], {SELECTOR: initial})
        context = authored(scoped())
        context["plan"]["nodes"]["talk"]["extraction_schema"].append({"name": SELECTOR, "type": "string", "min_confidence": 0.99})
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_only_declared_inputs_are_captured_and_nested_initial_values_are_detached(self):
        value = {"friendly_label": ["source-defined"]}
        call = CompiledCall(authored(scoped(), names={SELECTOR: "Source-provided service code", "initial_metadata": "Source-provided metadata"}),
                            {SELECTOR: ALL, "initial_metadata": value, "undeclared_hint": PARTIAL})
        value["friendly_label"].append("later-mutation")
        call.variables["initial_metadata"] = {"friendly_label": ["replacement"]}
        call.code("identity")
        self.assertEqual(call.state["call_inputs"], {SELECTOR: ALL, "initial_metadata": {"friendly_label": ["source-defined"]}})

    def test_selector_requires_its_own_literal_quote_and_declared_read_only_name(self):
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**authored(scoped(), names=["unrelated_initial_code"]))
        for quote in ("Resolve supplied products according to the service scope.", "Use initial_service_code_extra for service scope."):
            with self.subTest(quote=quote), self.assertRaises(PolicyContractError):
                authored(scoped(), resolution_quote=quote)

    def test_malformed_typed_rules_cannot_enable_default_or_truthy_behavior(self):
        rules = [{}, {"selector": SELECTOR}, {"selector": SELECTOR, "by_value": {}},
                 {"selector": "bg_state", "by_value": {ALL: True}}, {"selector": False, "by_value": {ALL: True}},
                 {"selector": SELECTOR, "by_value": {"": True}}, {"selector": SELECTOR, "by_value": {ALL: 1}},
                 {"selector": SELECTOR, "by_value": {ALL: "false"}}, {"selector": SELECTOR, "by_value": {ALL: True}, "default": False},
                 "false", 1]
        for rule in rules:
            with self.subTest(rule=rule), self.assertRaises((PolicyError, PolicyContractError)):
                authored(rule)

    def test_exact_selector_category_may_bind_keys_but_unrelated_goals_category_cannot(self):
        rule = {"selector": SELECTOR, "by_value": {"source-whole": True, "source-focused": False}}
        mapping = {"source-whole": ALL, "source-focused": PARTIAL}
        call = CompiledCall(authored(rule, wire_codes={SELECTOR: mapping}), {SELECTOR: PARTIAL})
        call.account()
        call.promise()
        call.code("close")
        self.assertEqual(call.variables["bg_route"], "allow")
        unrelated = CompiledCall(authored(rule, wire_codes={"goals": mapping}), {SELECTOR: PARTIAL})
        self.assertEqual(unrelated.target("bg_init"), "bg_quarantine")
        actual = CompiledCall(authored(scoped(), wire_codes={"goals": mapping}), {SELECTOR: ALL})
        self.assertEqual(actual.variables["bg_route"], "done")
        other_name = "initial_contact_kind"
        renamed = CompiledCall(authored(scoped(other_name), names=[other_name]), {other_name: PARTIAL})
        renamed.account()
        renamed.promise()
        renamed.code("close")
        self.assertEqual(renamed.variables["bg_route"], "allow")

    def test_mixed_semantic_wire_selector_keys_and_shadowing_are_rejected(self):
        mapping = {"source-whole": ALL, "source-focused": PARTIAL}
        for values, codes in (({ALL: True, "source-whole": True}, mapping),
                              ({ALL: True}, {"source-whole": ALL, ALL: PARTIAL})):
            rule = {"selector": SELECTOR, "by_value": values}
            with self.subTest(values=values), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**authored(rule, wire_codes={SELECTOR: codes}))

    def test_malformed_sealed_input_shape_quarantines_and_preserves_recorded_evidence(self):
        call = CompiledCall(authored(scoped()), {SELECTOR: ALL})
        call.account()
        call.promise()
        call.variables.update(product="asset-two", amount="82.10", day="2031-05-06")
        call.code("stage")
        call.code("commit")
        call.variables["bg_consent"] = "yes"
        call.code("bg_commit_accept")
        self.assertTrue(call.state["inflight"])
        original = deepcopy(call.variables)
        for values in ({}, {SELECTOR: "UNKNOWN"}, {SELECTOR: False}, {SELECTOR: ALL, "forged": PARTIAL}, []):
            for node in ("stage", "close", "bg_commit_accept", "bg_commit_result"):
                with self.subTest(values=values, node=node):
                    call.variables = deepcopy(original)
                    state = call.state
                    state["call_inputs"] = deepcopy(values)
                    call.variables["bg_state"] = json.dumps(state)
                    call.code(node)
                    self.assertEqual(call.target(node), "bg_quarantine")
                    self.assertEqual(call.state["recorded_promises"], state["recorded_promises"])
                    self.assertEqual(call.state["inflight"], state["inflight"])
                    self.assertFalse(call.variables["bg_body"])

    def test_generated_code_retains_sandbox_and_no_template_boundaries(self):
        workflow = compile_guarded_workflow(**authored(scoped()))["workflow"]
        for node in workflow["nodes"].values():
            if node["node_type"] == "code":
                code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
                self.assertNotIn("{{", code)
                self.assertNotIn("}}", code)
                self.assertEqual(validate_code(code), [])


if __name__ == "__main__":
    unittest.main()

"""Invented goal policies, executed through sealed code without bank/model I/O.

These regressions establish local dependency selection and protected closing
behavior. They do not establish hosted conversation or source completeness.
"""
import copy
import json
import unittest

from builder.action_bindings import compile_action_bindings
from builder.contract import compile_policy_contract
from builder.execution_requirements import (ExecutionRequirementsError,
    compile_execution_requirements, validate_plan_execution)
from builder.guards import (GuardCompileError, _profile, compile_guarded_workflow,
                            execution_action_catalogue, validate_guarded_workflow)
from builder.policy import compile_execution_policy
from builder.safety import digest
from test_execution_requirements import add_rule, packet, plan, rule


DIMENSIONS = {"product_order", "resolve_all_products"}
ABSENT = DIMENSIONS | {"policy_applicability", "close_precedence"}
CONSUMERS = ("get_account", "stage_promise", "commit_promises", "stage_note", "finalize_notes")
NO_ACCOUNT = (("verify_identity", "close_call"),
              ("schedule_callback", "close_call"),
              ("stage_transfer", "commit_transfer", "close_call"), ("close_call",))
PRODUCT = {"product_id": "GOAL-PRODUCT", "type": "revolving", "days_late": 8,
           "past_due": 300.19, "minimum_due": 37.25}


def remove(policy, kinds):
    policy["rules"] = [item for item in policy["rules"] if item["kind"] not in kinds]


def witness(policy, sources):
    """Author a literal witness for this independent synthetic test policy."""
    text = "Invented goal restrictions: " + json.dumps(
        {item["kind"]: item["value"] for item in policy["rules"]}, sort_keys=True)
    sources[0].update(text=text, sha256=digest(text))
    policy["sources"] = [{key: value for key, value in sources[0].items() if key != "text"}]
    for item in policy["rules"]:
        item["source"] = {"id": sources[0]["id"], "quote": text}


def synthetic(actions, *, narrow=True):
    policy, sources, api = packet(list(actions), local=True, narrow=narrow)
    # These cases explicitly supply fixed identity dispositions; optional
    # rules must not disappear just because the capability catalogue narrows.
    if not any(item["kind"] == "identity_close" for item in policy["rules"]):
        full_policy, _, _ = packet(list(actions), local=True)
        add_rule(policy, "identity_close", rule(full_policy, "identity_close")["value"])
    remove(policy, {"close_precedence"})
    witness(policy, sources)
    return policy, sources, api


def compiled(policy, sources, api):
    requirements = compile_execution_requirements(policy, sources, api)
    authored = plan(requirements["actions"])
    close = next(node for node in authored["nodes"].values() if node.get("action") == "close_call")
    close["inputs"] = {"outcome": "proposal_outcome", "reason": "proposal_reason"}
    errors = validate_plan_execution(authored, requirements, policy, sources, api)
    if errors:
        raise AssertionError(errors)
    bindings = compile_action_bindings(policy, api)
    context = {"plan": authored, "contract": compile_policy_contract(policy, sources, api),
               "openapi": api, "bindings": bindings,
               "execution_policy": compile_execution_policy(
                   policy, bindings, required_kinds=requirements["required_kinds"])}
    workflow = compile_guarded_workflow(**context)["workflow"]
    errors = validate_guarded_workflow(workflow, **context)
    if errors:
        raise AssertionError(errors)
    return requirements, context, workflow


def rehash(value):
    value["sha256"] = digest({key: item for key, item in value.items() if key != "sha256"})


class SealedSession:
    def __init__(self, context, workflow):
        self.workflow = workflow
        self.entries = {node["action"]: name for name, node in context["plan"]["nodes"].items()
                        if node.get("node_type") == "guard_action"}
        self.inputs = {node["action"]: node["inputs"] for node in context["plan"]["nodes"].values()
                       if node.get("node_type") == "guard_action"}
        self.variables = {"customer_id": "SYNTHETIC-GOAL-SESSION", "now": "2031-05-02T11:00"}
        self.code("bg_init")

    def code(self, name):
        code = self.workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(code, {"vars": self.variables})

    def action(self, action, **inputs):
        for field, value in inputs.items():
            self.variables[self.inputs[action][field]] = value
        self.code(self.entries[action])

    def result(self, action, data):
        entry = self.entries[action]
        node = self.workflow["nodes"]["bg_" + entry + "_api"]
        envelope = {"status_code": 200, "data": copy.deepcopy(data)}
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            self.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
        self.code("bg_" + entry + "_result")

    def target(self, name):
        def matches(condition):
            if condition.get("operator") == "always":
                return True
            if "conditions" in condition:
                values = [matches(item) for item in condition["conditions"]]
                return any(values) if condition.get("logic") == "or" else all(values)
            return (condition.get("operator") == "equals"
                    and self.variables.get(condition["variable"]) == condition["operand"])
        node = self.workflow["nodes"][name]
        return next((item["target_node"] for item in node["transitions"] if matches(item["condition"])),
                    node.get("passthrough_default_transition"))

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])

    @property
    def body(self):
        return json.loads(self.variables["bg_body"])


class GoalRequirementsTests(unittest.TestCase):
    def session(self, values):
        requirements, context, workflow = compiled(*values)
        return requirements, context, SealedSession(context, workflow)

    def assert_silent_terminal(self, session, name, expected="bg_terminal"):
        self.assertEqual(session.target(name), expected)
        terminal = session.workflow["nodes"][expected]
        self.assertEqual(terminal["node_type"], "end_call")
        self.assertEqual(terminal["on_enter"], {"type": "silent"})
        self.assertEqual(terminal["transitions"], [])

    def test_noncollection_goals_compile_without_fabricating_scope_dimensions_or_ranking(self):
        for actions in NO_ACCOUNT:
            with self.subTest(actions=actions):
                values = synthetic(actions)
                before = copy.deepcopy(values)
                requirements, context, _ = self.session(values)
                self.assertTrue(ABSENT.isdisjoint(requirements["required_kinds"]))
                frozen = context["execution_policy"]
                self.assertTrue(ABSENT.isdisjoint(frozen["profile"]))
                runtime = _profile(context["contract"], frozen, context["plan"], context["bindings"])
                self.assertEqual(runtime["close_precedence"], [])
                self.assertTrue((ABSENT - {"close_precedence"}).isdisjoint(runtime))
                runtime["close_precedence"].append("promise")
                self.assertNotIn("close_precedence", frozen["profile"])
                self.assertEqual(values, before)
                self.assertEqual(frozen["policy_sha256"], digest(values[0]))

    def test_failed_identity_closes_from_recorded_verification_without_ranking(self):
        _, _, session = self.session(synthetic(["verify_identity", "close_call"]))
        session.action("verify_identity", field="postal_code", answer="invented-answer")
        session.result("verify_identity", {"ok": True, "verified": False, "wrong_answers_left": 0})
        self.assertEqual(session.state["identity_status"], "failed")
        session.action("close_call", outcome="promise", reason="refused")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["outcome"], "not_verified")
        self.assertEqual(session.body["reason"], "identity_failed")
        self.assertFalse(session.state["account_loaded"])
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])
        self.assert_silent_terminal(session, "bg_" + session.entries["close_call"] + "_result")

    def test_close_only_goal_uses_sourced_unreached_disposition_without_account_rules(self):
        _, _, session = self.session(synthetic(["close_call"]))
        session.action("close_call", outcome="not_reached", reason="")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["outcome"], "not_reached")
        self.assertNotIn("reason", session.body)
        self.assertFalse(session.state["account_loaded"])
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])

    def callback_values(self):
        policy, sources, api = synthetic(["schedule_callback", "close_call"])
        rule(policy, "close_reasons")["value"]["not_reached"] = ["callback", "no_callback"]
        rule(policy, "identity_close")["value"]["unreached"]["reason"] = "no_callback"
        rule(policy, "wire_codes")["value"]["reasons"].update(callback="callback", no_callback="no_callback")
        add_rule(policy, "identity_close_choices", {"unreached": [
            {"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"},
            {"outcome": "not_reached", "reason": "no_callback", "requires": "no_recorded_callback"}]})
        api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["reason"]["enum"].extend(["callback", "no_callback"])
        witness(policy, sources)
        return policy, sources, api

    def test_recorded_callback_supports_close_without_collections_dimensions_or_ranking(self):
        _, _, session = self.session(self.callback_values())
        session.action("schedule_callback", date="2031-05-06", time="12:00")
        self.assertEqual(session.variables["bg_route"], "allow")
        session.result("schedule_callback", {"ok": True})
        self.assertEqual(len(session.state["callbacks"]), 1)
        session.action("close_call", outcome="not_reached", reason="callback")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["reason"], "callback")
        self.assertFalse(session.state["account_loaded"])
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])

    def test_callback_claim_without_bank_record_refuses_close_and_terminates(self):
        _, _, session = self.session(self.callback_values())
        session.action("close_call", outcome="not_reached", reason="callback")
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.variables["bg_body"])
        self.assertFalse(session.state["close_attempted"])
        self.assertEqual(session.state["callbacks"], [])
        self.assert_silent_terminal(session, session.entries["close_call"])

    def test_preidentity_transfer_uses_sole_protected_fact_without_invented_priority(self):
        _, _, session = self.session(synthetic(["stage_transfer", "commit_transfer", "close_call"]))
        session.action("stage_transfer", queue="helpdesk")
        session.action("commit_transfer")
        self.assertEqual(session.variables["bg_route"], "allow")
        session.result("commit_transfer", {"ok": True})
        session.action("close_call", outcome="promise", reason="refused")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("transferred", "dispute"))
        self.assertFalse(session.state["account_loaded"])
        self.assertNotEqual(session.state["identity_status"], "verified")
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])

    def test_each_collection_consumer_requires_both_dimensions_even_without_getter(self):
        for action in CONSUMERS:
            for missing in ({"product_order"}, {"resolve_all_products"}, DIMENSIONS):
                with self.subTest(action=action, missing=missing):
                    policy, sources, api = synthetic([action, "close_call"])
                    remove(policy, missing)
                    witness(policy, sources)
                    self.assertNotIn("policy_applicability", {item["kind"] for item in policy["rules"]})
                    with self.assertRaises(ExecutionRequirementsError) as raised:
                        compile_execution_requirements(policy, sources, api)
                    self.assertEqual(set(raised.exception.missing_kinds), missing)
                    if action != "get_account":
                        self.assertNotIn("get_account", rule(policy, "execution_actions")["value"])

    def test_explicit_singleton_retains_original_missing_dimension_exemption_for_consumers(self):
        for action in CONSUMERS:
            with self.subTest(action=action):
                policy, sources, api = synthetic([action, "close_call"])
                remove(policy, DIMENSIONS)
                add_rule(policy, "policy_applicability", {"products": "single"})
                witness(policy, sources)
                requirements, context, _ = self.session((policy, sources, api))
                self.assertTrue(DIMENSIONS.isdisjoint(requirements["required_kinds"]))
                self.assertTrue(DIMENSIONS.isdisjoint(context["execution_policy"]["profile"]))
                self.assertEqual(context["execution_policy"]["profile"]["policy_applicability"], {"products": "single"})

    def test_supplied_optional_dimensions_and_partial_ranking_remain_exact_and_frozen(self):
        policy, sources, api = synthetic(["close_call"])
        supplied = {"product_order": {"mode": "account_order"}, "resolve_all_products": False,
                    "close_precedence": ["promise", "transferred"]}
        for kind, value in supplied.items():
            add_rule(policy, kind, value)
        witness(policy, sources)
        before = copy.deepcopy((policy, sources, api))
        _, context, _ = self.session((policy, sources, api))
        runtime = _profile(context["contract"], context["execution_policy"], context["plan"], context["bindings"])
        for kind, value in supplied.items():
            self.assertEqual(context["execution_policy"]["profile"][kind], value)
            self.assertEqual(runtime[kind], value)
        self.assertNotIn("policy_applicability", context["execution_policy"]["profile"])
        self.assertEqual((policy, sources, api), before)

    def test_supplied_empty_or_malformed_ranking_is_not_treated_as_absence(self):
        for value in ([], "promise", ["promise", "promise"], ["not_verified"]):
            with self.subTest(value=value):
                policy, sources, api = synthetic(["close_call"])
                add_rule(policy, "close_precedence", value)
                witness(policy, sources)
                with self.assertRaises(ExecutionRequirementsError):
                    compile_execution_requirements(policy, sources, api)

    def test_active_optional_ranking_and_dimensions_cannot_be_removed_or_changed(self):
        policy, sources, api = synthetic(["close_call"])
        for kind, value in (("close_precedence", ["transferred", "promise"]),
                            ("product_order", {"mode": "account_order"}), ("resolve_all_products", False)):
            add_rule(policy, kind, value)
        witness(policy, sources)
        _, context, _ = self.session((policy, sources, api))
        for kind in ("close_precedence", "product_order", "resolve_all_products"):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(context)
                del changed["execution_policy"]["profile"][kind]
                rehash(changed["execution_policy"])
                with self.assertRaisesRegex(GuardCompileError, "active execution rule omitted from profile: " + kind):
                    compile_guarded_workflow(**changed)
        context["execution_policy"]["profile"]["close_precedence"].reverse()
        rehash(context["execution_policy"])
        with self.assertRaisesRegex(GuardCompileError, "does not match active frozen rule: close_precedence"):
            compile_guarded_workflow(**context)

    def test_multiple_real_unranked_facts_refuse_close_without_losing_bank_records(self):
        _, _, session = self.session(synthetic(list(execution_action_catalogue()), narrow=False))
        for field in ("postal_code", "memorable_word"):
            session.action("verify_identity", field=field, answer="invented-answer")
            session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        session.action("stage_promise", product_id="GOAL-PRODUCT", amount="37.25", date="2031-05-06")
        self.assertEqual(session.variables["bg_route"], "done")
        session.action("commit_promises")
        self.assertEqual(session.variables["bg_route"], "confirm")
        session.variables["bg_consent"] = "yes"  # Fresh explicit consent through the sealed handler.
        session.code("bg_" + session.entries["commit_promises"] + "_accept")
        session.result("commit_promises", {"ok": True, "promise_id": "GOAL-PROMISE"})
        session.action("stage_transfer", queue="helpdesk")
        session.action("commit_transfer")
        session.result("commit_transfer", {"ok": True})
        before = session.state
        self.assertEqual(len(before["recorded_promises"]), 1)
        self.assertEqual(before["transfer"]["status"], "recorded")
        session.action("close_call", outcome="transferred", reason="dispute")
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.variables["bg_body"])
        self.assertFalse(session.state["close_attempted"])
        self.assertFalse(session.state["closed"])
        self.assertEqual(session.state["recorded_promises"], before["recorded_promises"])
        self.assertEqual(session.state["transfer"], before["transfer"])
        self.assert_silent_terminal(session, session.entries["close_call"])


if __name__ == "__main__":
    unittest.main()

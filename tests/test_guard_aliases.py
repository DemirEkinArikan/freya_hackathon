"""Invented fully renamed contracts, evaluated through compiled guard code."""
import copy
import json
import unittest

from builder.action_bindings import compile_action_bindings
from builder.contract import compile_policy_contract
from builder.guards import GuardCompileError, compile_guarded_workflow, validate_guarded_workflow
from builder.safety import digest
from builder.validation import validate_workflow
from test_guards import fixture


def renamed_context():
    base = fixture()
    api = copy.deepcopy(base["openapi"])
    roles = [op["name"] for op in base["contract"]["permitted_operations"]]
    aliases = {role: "operation_" + str(i) + "_release" for i, role in enumerate(roles)}
    action_fields, result_fields = {}, {}
    product_fields = {"product_id": "asset_reference", "type": "kind_wire", "days_late": "delay_marker", "past_due": "arrears_value"}
    extra_product_fields = {"minimum_due": "floor_alpha", "instalment_due": "floor_beta"}
    renamed_products = {**product_fields, **extra_product_fields}
    for path, methods in api["paths"].items():
        op = methods["post"]
        role = op["operationId"]
        op["operationId"] = aliases[role]
        request = op["requestBody"]["content"]["application/json"]["schema"]
        action_fields[role] = {field: "input_" + field for field in request["properties"]}
        request["properties"] = {action_fields[role][field]: schema for field, schema in request["properties"].items()}
        request["required"] = [action_fields[role][field] for field in request["required"]]
        response = op["responses"]["200"]["content"]["application/json"]["schema"]
        result_fields[role] = {field: "evidence_" + field for field in response["properties"]}
        response["properties"] = {result_fields[role][field]: schema for field, schema in response["properties"].items()}
        if role == "get_account":
            products = response["properties"][result_fields[role]["products"]]["items"]["properties"]
            response["properties"][result_fields[role]["products"]]["items"]["properties"] = {
                renamed_products[field]: schema for field, schema in products.items()}
    maps = {"outcomes": {"promise": "C_A", "paid": "C_B", "transferred": "C_C", "no_promise": "C_D", "not_verified": "C_E", "not_reached": "C_F"},
            "reasons": {"refused": "WHY_R", "dispute": "WHY_D", "service_error": "WHY_S", "hardship": "WHY_H", "failed": "WHY_F"},
            "notes": {"refused": "NOTE_R", "paid": "NOTE_P", "dispute": "NOTE_D", "service_error": "NOTE_S"},
            "queues": {"general": "QUEUE_G", "support": "QUEUE_S"}}
    def request(role):
        return api["paths"]["/" + role]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    request("close_call")[action_fields["close_call"]["outcome"]]["enum"] = list(maps["outcomes"].values())
    request("close_call")[action_fields["close_call"]["reason"]]["enum"] = list(maps["reasons"].values())
    request("add_note")[action_fields["add_note"]["reason"]]["enum"] = list(maps["notes"].values())
    request("transfer_to_agent")[action_fields["transfer_to_agent"]["queue"]]["enum"] = list(maps["queues"].values())
    request("verify_identity")[action_fields["verify_identity"]["field"]]["enum"] = ["challenge_A", "challenge_B"]
    product_properties = api["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"][result_fields["get_account"]["products"]]["items"]["properties"]
    product_properties[product_fields["type"]]["enum"] = ["KIND_A", "KIND_B"]
    profile = copy.deepcopy(base["execution_policy"]["profile"])
    profile.update(tool_aliases=aliases, action_fields=action_fields, result_fields=result_fields,
                   product_fields=product_fields, wire_codes=maps,
                   identity_fields=["challenge_A", "challenge_B"],
                   floor_fields={"KIND_A": "floor_alpha", "KIND_B": "floor_beta"},
                   product_order={"mode": "account_order"},
                   note_reasons=list(maps["notes"].values()),
                   transfer_reasons={"QUEUE_G": "WHY_H", "QUEUE_S": "WHY_D"},
                   transfer_prerequisites={"QUEUE_G": "account", "QUEUE_S": "none"},
                   close_reasons={"C_A": [], "C_B": [], "C_C": ["WHY_H", "WHY_D"],
                                  "C_D": ["WHY_R", "WHY_D", "WHY_S"], "C_E": ["WHY_F"], "C_F": []},
                   close_precedence=["C_C", "C_A", "C_B", "C_D"],
                   identity_close={"failed": {"outcome": "C_E", "reason": "WHY_F"},
                                   "incomplete": {"outcome": "C_E", "reason": "WHY_F"},
                                   "unreached": {"outcome": "C_F"}},
                   promise_failure_disposition={"reason": "NOTE_S", "clear_pending": True},
                   nonpersistent_failures={aliases["create_promise"]: [{"http_status": 503, "error": "ERR_BUSY"}]},
                   promise_readback={"product_fields": ["kind_wire"], "value_labels": {"kind_wire": {
                       "KIND_A": "everyday account", "KIND_B": "instalment account"}}})
    context = {"plan": copy.deepcopy(base["plan"]), "openapi": api}
    return recompile_context(context, profile), aliases


def recompile_context(context, profile):
    text = "Independent invented renamed-operation contract witness."
    source = {"id": "invented", "file": "invented-aliases.txt", "text": text, "sha256": digest(text)}
    operations = [operation["operationId"] for path in context["openapi"]["paths"].values() for operation in path.values()]
    policy = {"version": 1, "goal": "Resolve invented contracts", "allowed_actions": operations,
              "sources": [{k: v for k, v in source.items() if k != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": "invented", "quote": text}}
                        for kind, value in profile.items()], "unsupported_constraints": []}
    result = copy.deepcopy(context)
    result["contract"] = compile_policy_contract(policy, [source], result["openapi"])
    bindings = compile_action_bindings(policy, result["openapi"])
    execution = {"version": 1, "policy_sha256": digest(policy), "bindings_sha256": bindings["sha256"], "profile": copy.deepcopy(profile)}
    execution["sha256"] = digest(execution)
    result.update(execution_policy=execution, bindings=bindings)
    return result


class GuardAliasTests(unittest.TestCase):
    def setUp(self):
        self.context, self.aliases = renamed_context()
        self.workflow = compile_guarded_workflow(**self.context)["workflow"]
        self.variables = {"customer_id": "private-session-identifier", "now": "2031-05-02"}
        self.code("bg_init")

    def code(self, node):
        source = self.workflow["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(source, {"vars": self.variables})

    def api_result(self, entry, data, status=200):
        node = self.workflow["nodes"]["bg_" + entry + "_api"]
        envelope = {"status_code": status, "data": copy.deepcopy(data)}
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            self.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
        self.code("bg_" + entry + "_result")

    def account(self):
        for question in ("challenge_A", "challenge_B"):
            self.variables.update(answer_field=question, answer_text="provided-answer")
            self.code("identity")
            self.api_result("identity", {"evidence_ok": True, "evidence_verified": True, "evidence_wrong_answers_left": 4})
        self.code("account")
        self.api_result("account", {"evidence_ok": True, "evidence_products": [
            {"asset_reference": "internal-product-Z", "kind_wire": "KIND_A", "delay_marker": 8, "arrears_value": 300, "floor_alpha": 37.25},
            {"asset_reference": "internal-product-A", "kind_wire": "KIND_B", "delay_marker": 28, "arrears_value": 600, "floor_beta": 82.10}]})

    def stage(self, pid="internal-product-Z", amount="50.00"):
        self.variables.update(product=pid, amount=amount, day="2031-05-06")
        self.code("stage")

    def test_all_seven_actual_ids_and_fields_are_compiler_bound(self):
        self.assertEqual(validate_guarded_workflow(self.workflow, **self.context), [])
        self.assertEqual(validate_workflow(self.workflow, self.context["openapi"]), [])
        names = [node["pre_actions"][0]["function"]["name"] for node in self.workflow["nodes"].values()
                 if node["node_type"] == "api_request"]
        self.assertEqual(set(names), set(self.aliases.values()))
        self.account()
        self.stage()
        self.code("commit")
        self.assertEqual(self.variables["bg_route"], "confirm")
        self.assertIn("everyday account", self.variables["bg_readback"])
        self.assertIn("50.00", self.variables["bg_readback"])
        self.assertNotIn("private-session-identifier", self.variables["bg_readback"])
        self.assertNotIn("internal-product-Z", self.variables["bg_readback"])
        self.variables["bg_consent"] = "yes"
        self.code("bg_commit_accept")
        self.assertEqual(json.loads(self.variables["bg_body"]), {
            "input_customer_id": "private-session-identifier", "input_product_id": "internal-product-Z", "input_amount": 50.0, "input_date": "2031-05-06"})
        self.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "actual-record"})
        state = json.loads(self.variables["bg_state"])
        self.assertIn("internal-product-Z", state["recorded_promises"])

    def test_nonpersistent_guarantee_uses_actual_operation_id_but_semantic_counter(self):
        self.account()
        self.stage()
        self.code("commit")
        self.variables["bg_consent"] = "yes"
        self.code("bg_commit_accept")
        original = self.variables["bg_body"]
        self.api_result("commit", {"evidence_ok": False, "evidence_error": "ERR_BUSY"}, status=503)
        self.assertEqual(self.variables["bg_route"], "allow")
        self.assertEqual(self.variables["bg_body"], original)
        state = json.loads(self.variables["bg_state"])
        self.assertEqual(state["promise_failures"]["internal-product-Z"], 1)
        self.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "actual-record"})
        self.assertIn("internal-product-Z", json.loads(self.variables["bg_state"])["recorded_promises"])

    def test_opaque_notes_and_close_are_derived_after_success(self):
        self.account()
        for pid, reason in [("internal-product-Z", "NOTE_P"), ("internal-product-A", "NOTE_R")]:
            self.variables.update(product=pid, note_reason=reason)
            self.code("note")
        self.code("finish")
        self.assertEqual(json.loads(self.variables["bg_body"])["input_reason"], "NOTE_P")
        self.api_result("finish", {"evidence_ok": True})
        self.assertEqual(json.loads(self.variables["bg_body"])["input_reason"], "NOTE_R")
        self.api_result("finish", {"evidence_ok": True})
        self.code("close")
        self.assertEqual(json.loads(self.variables["bg_body"]), {
            "input_customer_id": "private-session-identifier", "input_outcome": "C_D", "input_reason": "WHY_R"})
        self.api_result("close", {"evidence_ok": True})
        self.assertEqual(self.variables["bg_route"], "closed")

    def test_missing_operation_alias_never_guesses_from_paths(self):
        profile = copy.deepcopy(self.context["execution_policy"]["profile"])
        del profile["tool_aliases"]["verify_identity"]
        with self.assertRaisesRegex(GuardCompileError, "explicit.*tool_aliases"):
            compile_guarded_workflow(**recompile_context(self.context, profile))

    def test_duplicate_operation_alias_is_rejected(self):
        context = copy.deepcopy(self.context)
        context["contract"]["active_rules"]["tool_aliases"]["value"]["get_account"] = self.aliases["verify_identity"]
        context["contract"]["sha256"] = digest({k: v for k, v in context["contract"].items() if k != "sha256"})
        context["execution_policy"]["profile"]["tool_aliases"]["get_account"] = self.aliases["verify_identity"]
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        with self.assertRaisesRegex(GuardCompileError, "injectively"):
            compile_guarded_workflow(**context)

    def test_unknown_alias_and_canonical_guarantee_on_renamed_api_fail_closed(self):
        profile = copy.deepcopy(self.context["execution_policy"]["profile"])
        profile["nonpersistent_failures"] = {"create_promise": [{"http_status": 503, "error": "ERR_BUSY"}]}
        with self.assertRaisesRegex(GuardCompileError, "exact permitted API"):
            compile_guarded_workflow(**recompile_context(self.context, profile))
        context = copy.deepcopy(self.context)
        context["contract"]["active_rules"]["tool_aliases"]["value"]["verify_identity"] = "absent_operation"
        context["contract"]["sha256"] = digest({k: v for k, v in context["contract"].items() if k != "sha256"})
        context["execution_policy"]["profile"]["tool_aliases"]["verify_identity"] = "absent_operation"
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        with self.assertRaisesRegex(GuardCompileError, "unknown or nonpermitted"):
            compile_guarded_workflow(**context)

    def test_alias_collision_with_unchanged_canonical_role_is_rejected(self):
        context = fixture()
        aliases = {"verify_identity": "get_account"}
        witness = copy.deepcopy(context["contract"]["active_rules"]["identity_fields"])
        witness["value"] = aliases
        context["contract"]["active_rules"]["tool_aliases"] = witness
        context["contract"]["sha256"] = digest({k: v for k, v in context["contract"].items() if k != "sha256"})
        context["execution_policy"]["profile"]["tool_aliases"] = aliases
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        with self.assertRaisesRegex(GuardCompileError, "injectively"):
            compile_guarded_workflow(**context)

    def test_binding_category_and_actual_url_cannot_be_reassigned(self):
        context = copy.deepcopy(self.context)
        context["bindings"]["categories"]["queues"]["operation"]["url"] = "https://other.invalid/request"
        context["bindings"]["sha256"] = digest({k: v for k, v in context["bindings"].items() if k != "sha256"})
        context["execution_policy"]["bindings_sha256"] = context["bindings"]["sha256"]
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        with self.assertRaisesRegex(GuardCompileError, "exact frozen API"):
            compile_guarded_workflow(**context)


if __name__ == "__main__":
    unittest.main()

import copy
import json
import unittest
from pathlib import Path

from builder.validation import assess_workflow, validate_code, validate_workflow

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = json.loads((ROOT / "build-the-builder/tools/openapi.json").read_text())
BASE = OPENAPI["servers"][0]["url"]


def api_tool(name, fields, required=()):
    return {"type": "function", "config": {"timeoutMs": 10000, "maxRetries": 0},
            "function": {"name": name, "handler": "__function__:api_call", "description": "Call the documented operation.",
                         "parameters": {"type": "object", "properties": {
                             "url": {"type": "string", "default": BASE + "/" + name},
                             "method": {"type": "string", "default": "POST"},
                             "body": {"type": "object", "properties": fields, "required": list(required)}}}}}


def valid_workflow():
    verify = api_tool("verify_identity", {
        "customer_id": {"type": "string", "default": "{{customer_id}}"},
        "field": {"type": "string", "enum": ["date_of_birth", "phone_last4"], "default": "{{field}}"},
        "answer": {"type": "string", "default": "{{answer}}"}}, ("field", "answer"))
    account = api_tool("get_account", {"customer_id": {"type": "string", "default": "{{customer_id}}"}})
    return {"initial_node": "identity", "nodes": {
        "identity": {"name": "identity", "node_type": "conversation", "task_message": "Check identity.",
                     "tools": [verify], "transitions": [{"target_node": "account", "condition": {
                         "logic": "or", "conditions": [{"variable": "identity_status", "operator": "equals", "operand": "verified"}]}}]},
        "account": {"name": "account", "node_type": "api_request", "on_enter": {"type": "silent"},
                    "pre_actions": [account], "response_mapping": [{"path": "data.products", "variable": "products"}],
                    "passthrough": True, "passthrough_default_transition": "goodbye",
                    "transitions": [{"target_node": "goodbye", "condition": {"operator": "always"}}]},
        "goodbye": {"name": "goodbye", "node_type": "end_call", "on_enter": {"type": "silent"},
                    "disable_inactivity_monitor": True, "transitions": [], "terminal": {
                        "type": "function", "function": {"name": "call_complete", "handler": "__function__:end_call",
                                                           "parameters": {"type": "object", "properties": {
                                                               "message_mode": {"type": "string", "default": "none"}}}}}}
    }}


class ValidationTests(unittest.TestCase):
    def errors(self, workflow):
        return "\n".join(validate_workflow(workflow, OPENAPI))

    def test_valid_shape_is_not_behavioral_proof(self):
        self.assertEqual(validate_workflow(valid_workflow(), OPENAPI), [])
        assessment = assess_workflow(valid_workflow(), OPENAPI)
        self.assertTrue(assessment["structurally_valid"])
        self.assertFalse(assessment["behavioral_compliance_proven"])
        self.assertTrue(assessment["requires_host_tests"])

    def test_nulls_config_and_field_placement(self):
        workflow = valid_workflow()
        workflow["agent_config"] = {}
        workflow["nodes"]["identity"]["pre_actions"] = [workflow["nodes"]["account"]["pre_actions"][0]]
        workflow["test_variables"] = {"bad": None}
        errors = self.errors(workflow)
        self.assertIn("agent_config", errors)
        self.assertIn("pre_actions belong", errors)
        self.assertIn("null", errors)

    def test_graph_targets_deadends_and_unreachable(self):
        workflow = valid_workflow()
        workflow["nodes"]["account"]["transitions"][0]["target_node"] = "invented"
        workflow["nodes"]["island"] = {"name": "island", "node_type": "conversation", "transitions": []}
        errors = self.errors(workflow)
        self.assertIn("target_node is unknown", errors)
        self.assertIn("unreachable", errors)
        workflow = valid_workflow()
        workflow["nodes"]["identity"]["transitions"] = [{"target_node": "identity", "condition": {"operator": "always"}}]
        self.assertIn("no graph path", self.errors(workflow))

    def test_passthrough_needs_fallback(self):
        workflow = valid_workflow()
        workflow["nodes"]["account"]["transitions"] = []
        self.assertIn("final unconditional fallback", self.errors(workflow))

    def test_variable_outer_or_and_string_flags(self):
        workflow = valid_workflow()
        transition = workflow["nodes"]["identity"]["transitions"][0]
        transition["condition"] = {"variable": "verified", "operator": "equals", "operand": True}
        errors = self.errors(workflow)
        self.assertIn("outer OR", errors)
        self.assertIn("string flags", errors)
        transition["condition"] = {"logic": "or", "conditions": [{"logic": "and", "conditions": [
            {"variable": "dob", "operator": "equals", "operand": "verified"},
            {"variable": "phone", "operator": "equals", "operand": "verified"}]}]}
        self.assertEqual(validate_workflow(workflow, OPENAPI), [])

    def test_api_endpoint_handler_body_and_mapping(self):
        workflow = valid_workflow()
        tool = workflow["nodes"]["account"]["pre_actions"][0]
        tool["function"]["parameters"]["properties"]["url"]["default"] = "https://other.example/get_account"
        self.assertIn("OpenAPI operation", self.errors(workflow))
        workflow = valid_workflow()
        tool = workflow["nodes"]["account"]["pre_actions"][0]
        tool["response_mapping"] = [{"path": "products", "variable": "products"}]
        self.assertIn("belongs on the node", self.errors(workflow))
        workflow["nodes"]["account"]["response_mapping"][0]["path"] = "products"
        self.assertIn("begin with data.", self.errors(workflow))
        tool["function"]["handler"] = "__function__:invented"
        self.assertIn("documented allowlist", self.errors(workflow))

    def test_fixed_defaults_and_enums(self):
        workflow = valid_workflow()
        properties = workflow["nodes"]["identity"]["tools"][0]["function"]["parameters"]["properties"]
        properties["url"]["const"] = properties["url"].pop("default")
        self.assertIn("URL/method defaults", self.errors(workflow))
        properties["url"]["default"] = properties["url"].pop("const")
        properties["body"]["properties"]["field"]["enum"] = ["invented_field"]
        self.assertIn("OpenAPI values", self.errors(workflow))
        properties["body"]["properties"]["field"] = {"type": "string", "default": "date_of_birth"}
        self.assertEqual(validate_workflow(workflow, OPENAPI), [])

    def test_unsafe_code_and_real_clock(self):
        for source in ("import os\nos.system('echo unsafe')", "open('file')", "eval('1')",
                       "x.__class__", "getattr(x, 'name')", "import urllib.request", "import datetime\ndatetime.date.today()"):
            self.assertTrue(validate_code(source, require_fallback=False))
        self.assertTrue(validate_code("vars['value'] = '{{danger}}'"))
        self.assertTrue(validate_code("try:\n    vars['status'] = 'good'\nexcept Exception:\n    pass"))
        self.assertEqual(validate_code("import json\ntry:\n    vars['status'] = 'good'\nexcept Exception:\n    vars['status'] = 'unknown'"), [])

    def test_pre_hook_cannot_redirect_engine_request(self):
        for source in ("config['url'] = 'https://outside.example'", "config.update({'url': 'https://outside.example'})",
                       "alias = config\nalias['method'] = 'GET'", "headers = config['headers']\nheaders.clear()"):
            workflow = valid_workflow()
            workflow["nodes"]["identity"]["tools"][0]["pre_process_code"] = source
            self.assertIn("URL/method/headers are protected", self.errors(workflow))
        workflow = valid_workflow()
        workflow["nodes"]["identity"]["tools"][0]["pre_process_code"] = "config['body']['answer'] = str(vars.get('answer') or '')"
        self.assertEqual(validate_workflow(workflow, OPENAPI), [])

    def test_unverified_sandbox_modules_are_explicit(self):
        workflow = valid_workflow()
        workflow["nodes"]["identity"]["tools"][0]["post_process_code"] = "import decimal\nresult['data']['amount'] = str(decimal.Decimal('1.2'))"
        assessment = assess_workflow(workflow, OPENAPI)
        self.assertEqual(assessment["unverified_code_modules"], ["decimal"])
        self.assertFalse(assessment["behavioral_compliance_proven"])

    def test_auth_schema_not_scrubbed_but_actual_secret_rejected(self):
        workflow = valid_workflow()
        tool = workflow["nodes"]["identity"]["tools"][0]
        tool["function"]["parameters"]["properties"]["headers"] = {"type": "object", "properties": {
            "Authorization": {"type": "string", "default": "Bearer {{BANK_TOKEN}}"}}}
        self.assertEqual(validate_workflow(workflow, OPENAPI), [])
        tool["function"]["parameters"]["properties"]["headers"]["properties"]["Authorization"]["default"] = "Bearer actual-fixture-secret"
        self.assertIn("credential-like", self.errors(workflow))

    def test_malformed_input_returns_errors(self):
        self.assertTrue(validate_workflow([], OPENAPI))
        workflow = valid_workflow()
        workflow["nodes"]["account"]["transitions"] = ["wrong"]
        self.assertTrue(validate_workflow(workflow, OPENAPI))
        for value in ([], {}, [None], {"unexpected": True}):
            for field in ("node_type", "on_enter"):
                workflow = valid_workflow()
                workflow["nodes"]["identity"][field] = value
                self.assertTrue(validate_workflow(workflow, OPENAPI))
            workflow = valid_workflow()
            workflow["nodes"]["identity"]["tools"][0]["function"]["handler"] = value
            self.assertTrue(validate_workflow(workflow, OPENAPI))


if __name__ == "__main__":
    unittest.main()

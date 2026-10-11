"""Public entry routes remain declarations, never permission to bypass guards."""
from copy import deepcopy
import unittest

from builder.guards import (GuardCompileError, compile_guarded_workflow,
                            required_execution_kinds, validate_guarded_workflow)
from builder.runtime import plan_generation_schema
from builder.validation import validate_workflow
from test_guards import fixture


class AuthoredPassthroughTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()

    def router(self, target="close", entry=None):
        node = self.context["plan"]["nodes"]["talk"]
        node.update(on_enter=entry or {"type": "silent"}, passthrough=True,
                    passthrough_default_transition=target,
                    transitions=node["transitions"] + [{
                        "target_node": target, "description": "entry fallback",
                        "condition": {"operator": "always"}}])
        return node

    def reject(self, context):
        with self.assertRaises(GuardCompileError):
            required_execution_kinds(context["plan"])
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_silent_entry_route_compiles_through_public_guarded_action(self):
        self.router()
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        node = workflow["nodes"]["talk"]
        self.assertIs(node["passthrough"], True)
        self.assertEqual(node["on_enter"], {"type": "silent"})
        self.assertEqual(node["passthrough_default_transition"], node["transitions"][-1]["target_node"])
        self.assertEqual(workflow["nodes"]["close"]["node_type"], "code")
        self.assertEqual(validate_guarded_workflow(workflow, **self.context), [])
        self.assertEqual(validate_workflow(workflow, self.context["openapi"]), [])

    def test_speaking_entry_preserves_literal_speech_and_sealed_closing_route(self):
        entry = {"type": "tts", "text": "Thank you. Goodbye."}
        self.router(entry=entry)
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        self.assertEqual(workflow["nodes"]["talk"]["on_enter"], entry)
        self.assertEqual(validate_guarded_workflow(workflow, **self.context), [])
        changed = deepcopy(workflow)
        changed["nodes"]["talk"]["passthrough_default_transition"] = "bg_close_api"
        self.assertTrue(validate_guarded_workflow(changed, **self.context))

    def test_absent_or_false_passthrough_preserves_waiting_declaration(self):
        for declared in (None, False):
            with self.subTest(declared=declared):
                context = deepcopy(self.context)
                if declared is not None:
                    context["plan"]["nodes"]["talk"]["passthrough"] = declared
                workflow = compile_guarded_workflow(**context)["workflow"]
                node = workflow["nodes"]["talk"]
                self.assertEqual(node.get("passthrough", False), False)
                self.assertNotIn("passthrough_default_transition", node)
                self.assertEqual(node["extraction_schema"], context["plan"]["nodes"]["talk"]["extraction_schema"])
                self.assertEqual(validate_guarded_workflow(workflow, **context), [])

    def test_passthrough_boolean_is_strict_before_execution_kind_selection(self):
        for value in (None, 0, 1, "true", "false", [], {}):
            with self.subTest(value=value):
                context = deepcopy(self.context)
                context["plan"]["nodes"]["talk"]["passthrough"] = value
                self.reject(context)

    def test_default_rejects_null_malformed_unknown_and_private_api_targets(self):
        for value in (None, "", 1, True, [], {}, "missing", "bg_close_api", "close_call"):
            with self.subTest(value=value):
                context = deepcopy(self.context)
                context["plan"]["nodes"]["talk"]["passthrough_default_transition"] = value
                self.reject(context)

    def test_immediate_route_requires_declared_matching_final_always_fallback(self):
        self.router()
        cases = []
        missing = deepcopy(self.context)
        del missing["plan"]["nodes"]["talk"]["passthrough_default_transition"]
        cases.append(missing)
        empty = deepcopy(self.context)
        empty["plan"]["nodes"]["talk"]["transitions"] = []
        cases.append(empty)
        conditional = deepcopy(self.context)
        conditional["plan"]["nodes"]["talk"]["transitions"][-1]["condition"] = {
            "operator": "matches_intent", "description": "caller has answered"}
        cases.append(conditional)
        conflicting = deepcopy(self.context)
        conflicting["plan"]["nodes"]["talk"]["transitions"][-1]["target_node"] = "identity"
        cases.append(conflicting)
        for index, context in enumerate(cases):
            with self.subTest(index=index):
                self.reject(context)

    def test_passthrough_cannot_declare_executable_nodes_or_action_override(self):
        for target in ("bg_close_api", "rogue"):
            with self.subTest(target=target):
                context = deepcopy(self.context)
                context["plan"]["nodes"][target] = {
                    "name": target, "node_type": "api_request", "pre_actions": []}
                node = context["plan"]["nodes"]["talk"]
                node.update(passthrough=True, passthrough_default_transition=target,
                            transitions=[{"target_node": target, "description": "bypass",
                                          "condition": {"operator": "always"}}])
                self.reject(context)
        context = deepcopy(self.context)
        context["plan"]["nodes"]["close"]["passthrough"] = True
        context["plan"]["nodes"]["close"]["passthrough_default_transition"] = "talk"
        self.reject(context)

    def test_wire_schema_keeps_plan_inside_frozen_contract_json_string(self):
        self.router()
        schema = plan_generation_schema(self.context["contract"]["sha256"])
        self.assertEqual(schema["properties"]["plan_json"]["type"], "string")
        self.assertEqual(schema["properties"]["contract_sha256"]["enum"],
                         [self.context["contract"]["sha256"]])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(required_execution_kinds(self.context["plan"]),
                         required_execution_kinds(fixture()["plan"]))


if __name__ == "__main__":
    unittest.main()

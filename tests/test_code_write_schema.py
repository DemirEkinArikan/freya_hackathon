"""Hosted readiness H4: every code-step write must satisfy the variable's declared schema.

The platform drops a code write that the workflow-wide extraction schema refuses
("variable 'bg_consent' rejected by schema validation", 27 times in the first
hosted readiness check), so a reset to '' on an enum variable never happened.
"""
from copy import deepcopy
import unittest

from builder.validation import code_write_schema_errors, validate_workflow
from test_replay_guarded import authored

CONSENT = {"bg_consent": [{"name": "bg_consent", "type": "string", "enum_values": ["yes", "no", "unclear"]}]}


def code_sources(workflow):
    return {name: tool["function"]["parameters"]["properties"]["code"]["default"]
            for name, node in workflow["nodes"].items() for tool in node.get("pre_actions", [])
            if tool["function"]["handler"] == "__function__:code_exec"}


class CodeWriteSchemaTests(unittest.TestCase):
    def test_compiled_consent_resets_write_a_declared_value(self):
        workflow, context, api, _ = authored()
        declared = workflow["nodes"]["bg_commit_confirm"]["extraction_schema"][0]
        self.assertIn("unclear", declared["enum_values"])
        sources = code_sources(workflow)
        self.assertTrue(all("vars['bg_consent'] = ''" not in code for code in sources.values()))
        self.assertIn("vars['bg_consent'] = 'unclear'", sources["bg_init"])
        self.assertEqual(validate_workflow(workflow, api, guard_context=context), [])

    def test_validator_refuses_the_reset_the_platform_rejected(self):
        workflow, _, api, _ = authored()
        broken = deepcopy(workflow)
        code = broken["nodes"]["bg_init"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
        code["default"] = code["default"].replace("vars['bg_consent'] = 'unclear'", "vars['bg_consent'] = ''")
        errors = [e for e in validate_workflow(broken, api) if "bg_consent" in e]
        self.assertTrue(errors and all(e.startswith("node.bg_init.pre_actions[0].code: code writes '' to bg_consent") for e in errors), errors)

    def test_constant_writes_are_checked_against_type_enum_and_pattern(self):
        schemas = dict(CONSENT, count=[{"type": "integer"}], hour=[{"type": "string", "validation_pattern": "^[0-2][0-9]$"}])
        for source, refused in (("vars['bg_consent'] = 'unclear'", False), ("vars['bg_consent'] = ''", True),
                                ("vars['bg_consent'] = 'YES'", True), ("vars['other'] = ''", False),
                                ("vars['count'] = 3", False), ("vars['count'] = '3'", True), ("vars['count'] = True", True),
                                ("vars['hour'] = '09'", False), ("vars['hour'] = ''", True), ("vars['bg_consent'] = None", True)):
            with self.subTest(source=source):
                self.assertEqual(bool(code_write_schema_errors(source, schemas, "code")), refused)

    def test_undeterminable_writes_to_declared_variables_are_refused(self):
        for source in ("vars['bg_consent'] = answer", "vars['bg_consent'] += 'x'", "vars['bg_consent'], x = 'yes', 1",
                       "vars[name] = 'yes'", "vars.update(bg_consent='')", "vars.setdefault('bg_consent', '')"):
            with self.subTest(source=source):
                self.assertTrue(code_write_schema_errors(source, CONSENT, "code"))
        self.assertEqual(code_write_schema_errors("vars[name] = ''\nvars['x'] = y", {}, "code"), [])


if __name__ == "__main__":
    unittest.main()

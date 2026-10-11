"""The plan check and final validation report the same authored extraction-entry errors.

Invented plans only, plus an env-gated no-model replay of retained build attempts
whose run directories stay untracked.
"""
import os
import unittest
from pathlib import Path

from builder.call_variables import declared_call_variables
from builder.guards import GuardCompileError, _plan_errors, compile_guarded_workflow, required_execution_kinds
from builder.safety import digest, read_json
from builder.validation import validate_workflow
from test_guards import fixture

ROOT = Path(__file__).resolve().parents[1]
RETAINED_RUN = os.environ.get("FREYA_PLANCHECK_RUN", "")
MIN_CONFIDENCE = "min_confidence must be explicit and within 0..1"
STAGE_NOTE = ("wrong semantic input fields for stage_note: inputs must be an object with required product_id, reason; "
              "missing: product_id, reason; unsupported: none. Each value names a public extracted variable holding "
              "the proposal; the controller validates it and does not choose it")
BAD_ENTRIES = [
    {"name": "omitted", "type": "string"},
    {"name": "boolean_confidence", "type": "string", "min_confidence": True},
    {"name": "high_confidence", "type": "string", "min_confidence": 1.5},
    {"name": "invented_type", "type": "money", "min_confidence": 0.9},
    {"name": "empty_enum", "type": "string", "min_confidence": 0.9, "enum_values": []},
    {"name": "numeric_enum", "type": "string", "min_confidence": 0.9, "enum_values": [1]},
    {"name": "float_retries", "type": "string", "min_confidence": 0.9, "max_retries": 1.0},
    {"name": "broken_pattern", "type": "string", "min_confidence": 0.9, "validation_pattern": "("},
    {"name": "huge_repeat", "type": "string", "min_confidence": 0.9, "validation_pattern": "a{4294967296}"},
    {"name": "deep_pattern", "type": "string", "min_confidence": 0.9, "validation_pattern": "(" * 5000 + ")" * 5000},
    {"name": "partial_prompt", "type": "string", "min_confidence": 0.9, "extraction_prompt": "{{extractor.speech}}"},
]


def final_errors(context):
    workflow = compile_guarded_workflow(**context)["workflow"]
    return validate_workflow(workflow, context["openapi"],
                             guard_context={k: v for k, v in context.items() if k != "openapi"})


class PlanCheckParityTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.plan = self.context["plan"]
        self.plan["nodes"]["talk"]["extraction_schema"].extend(BAD_ENTRIES)

    def test_entry_errors_alone_do_not_stop_compilation_and_reach_final_validation(self):
        self.assertEqual(_plan_errors(self.plan), [])
        self.assertEqual(required_execution_kinds(self.plan), required_execution_kinds(fixture()["plan"]))
        errors = final_errors(self.context)
        self.assertEqual(len(errors), len(BAD_ENTRIES))
        for index in (1, 2, 3):
            self.assertIn(f"node.talk.extraction_schema[{index}]: {MIN_CONFIDENCE}", errors)
        self.assertIn("node.talk.extraction_schema[4]: unknown extraction type", errors)
        for index in (8, 9, 10):
            self.assertIn(f"node.talk.extraction_schema[{index}]: validation_pattern must be a valid regex string", errors)

    def test_a_rejected_plan_lists_the_same_entry_errors_final_validation_raises(self):
        expected = final_errors(self.context)
        self.plan["nodes"]["note"]["inputs"] = {}
        errors = _plan_errors(self.plan)
        self.assertEqual(sorted(errors), sorted(["note: " + STAGE_NOTE] + expected))
        for check in (lambda: required_execution_kinds(self.plan), lambda: compile_guarded_workflow(**self.context)):
            with self.assertRaises(GuardCompileError) as caught:
                check()
            self.assertEqual(set(caught.exception.errors), set(errors))

    def test_protected_or_unsupported_entries_also_get_their_shape_errors(self):
        talk = self.plan["nodes"]["talk"]
        talk["extraction_schema"] = [{"name": "bg_state", "type": "string"},
                                     {"name": "choice", "type": "string", "enum": ["yes"], "min_confidence": 0.9}]
        self.assertEqual(sorted(_plan_errors(self.plan)), sorted([
            "talk: extraction cannot write protected or engine variables",
            "talk: unsupported extraction fields",
            "node.talk.extraction_schema[0]: " + MIN_CONFIDENCE,
            "node.talk.extraction_schema[1]: extraction uses documented enum_values, not enum"]))

    def test_wrong_action_inputs_name_the_required_and_optional_fields(self):
        nodes = fixture()["plan"]["nodes"]
        nodes["note"]["inputs"] = {}
        nodes["commit"]["inputs"] = {"amount": "amount"}
        nodes["account"]["inputs"] = []
        errors = _plan_errors({"initial_node": "talk", "nodes": nodes})
        self.assertIn("note: " + STAGE_NOTE, errors)
        self.assertIn("commit: wrong semantic input fields for commit_promises: inputs must be an object with no "
                      "required fields and optional product_id; missing: none; unsupported: amount.", " ".join(errors))
        self.assertIn("account: wrong semantic input fields for get_account: inputs must be an object with no "
                      "required fields; missing: none; unsupported: none.", " ".join(errors))


@unittest.skipUnless(RETAINED_RUN, "set FREYA_PLANCHECK_RUN to a retained generated build run directory")
class RetainedPlanCheckReplayTests(unittest.TestCase):
    """No-model replay of a retained build's first two workflow attempts."""

    def setUp(self):
        self.run = Path(RETAINED_RUN)
        self.openapi = read_json(ROOT / "build-the-builder" / "tools" / "openapi.json")
        self.assertEqual(digest(self.openapi), read_json(self.run / "attempts" / "plan-001" / "attempt.json")["tool_api_sha256"])

    def test_first_attempt_now_reports_its_min_confidence_errors_at_the_plan_check(self):
        attempt = self.run / "attempts" / "plan-001"
        plan = read_json(attempt / "plan.json")
        retained = read_json(attempt / "validation.json")["errors"]
        self.assertFalse([error for error in retained if MIN_CONFIDENCE in error])
        omitted = {f"node.{name}.extraction_schema[{index}]: {MIN_CONFIDENCE}" for name, node in plan["nodes"].items()
                   for index, entry in enumerate(node.get("extraction_schema", [])) if "min_confidence" not in entry}
        self.assertTrue(omitted)
        with self.assertRaises(GuardCompileError) as caught:
            required_execution_kinds(plan)
        errors = set(caught.exception.errors)
        contract = read_json(self.run / "policy.contract.json")
        self.assertEqual(set(_plan_errors(plan, [op["name"] for op in contract["permitted_operations"]],
                                          declared_call_variables(contract))), errors)
        self.assertEqual({error for error in errors if MIN_CONFIDENCE in error}, omitted)
        rest = errors - omitted
        self.assertEqual(len(rest), len(retained))
        for old in retained:
            self.assertTrue(any(error.startswith(old + " for ") for error in rest), old)

    def test_second_attempt_keeps_exactly_its_final_validation_errors(self):
        attempt = self.run / "attempts" / "plan-002"
        context = read_json(attempt / "guard-context.json")
        retained = read_json(attempt / "validation.json")["errors"]
        self.assertTrue(retained)
        self.assertEqual(_plan_errors(context["plan"]), [])
        workflow = compile_guarded_workflow(openapi=self.openapi, **context)["workflow"]
        self.assertEqual(validate_workflow(workflow, self.openapi, read_json(self.run / "policy.json"),
                                           guard_context=context), retained)


if __name__ == "__main__":
    unittest.main()

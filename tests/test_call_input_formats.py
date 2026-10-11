"""Source-declared input formats through exact compiled code, without models."""
from copy import deepcopy
import json
import unittest

from builder.contract import PolicyContractError, compile_policy_contract
from builder.guards import GuardCompileError, compile_guarded_workflow, validate_guarded_workflow
from builder.policy import compile_execution_policy, execution_rule_guidance
from builder.safety import digest
from builder.validation import validate_code
from test_goal_scoped_resolution import CompiledCall
from test_guards import fixture


def authored(formats=None, *, names=None, quote=None):
    context = fixture()
    profile = deepcopy(context["execution_policy"]["profile"])
    if formats is not None:
        profile["call_input_formats"] = deepcopy(formats)
    names = list(formats) if names is None and isinstance(formats, dict) else names
    if names:
        profile["call_variables"] = deepcopy(names)
    text = "Independently supplied input validation instructions: " + json.dumps(profile, sort_keys=True)
    if quote is not None:
        text += "\n" + quote
    source = {"id": "input-format-source", "file": "independent-input-format.txt", "text": text, "sha256": digest(text)}
    policy = {"version": 1, "goal": "Perform only source-authorized synthetic banking operations", "allowed_actions": [
        operation["post"]["operationId"] for operation in context["openapi"]["paths"].values()],
        "sources": [{key: value for key, value in source.items() if key != "text"}],
        "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": source["id"],
            "quote": quote if kind == "call_input_formats" and quote is not None else text}} for kind, value in profile.items()],
        "unsupported_constraints": []}
    context["contract"] = compile_policy_contract(policy, [source], context["openapi"])
    context["execution_policy"] = compile_execution_policy(policy)
    return context


class CallInputFormatTests(unittest.TestCase):
    def test_optional_source_rule_is_typed_and_legacy_absence_is_unchanged(self):
        formats = {"now": "offset_datetime"}
        context = authored(formats)
        self.assertEqual(context["execution_policy"]["profile"]["call_input_formats"], formats)
        self.assertEqual(context["contract"]["active_rules"]["call_input_formats"]["value"], formats)
        self.assertIn("never infer formats", execution_rule_guidance()["call_input_formats"])
        for value in ("2031-05-02", "2031-05-02T11:30"):
            with self.subTest(value=value):
                legacy = CompiledCall(authored(), {"now": value})
                self.assertEqual(legacy.variables["bg_route"], "done")
                self.assertNotIn("call_input_formats", legacy.context["execution_policy"]["profile"])

    def test_full_local_datetime_offset_and_raw_value_are_preserved_without_utc_conversion(self):
        context = authored({"now": "offset_datetime"})
        for value, clock in (("2031-05-02T23:30+13:00", "23:30"), ("2031-05-02T00:15-12:00", "00:15"),
                             ("2031-05-02T11:30Z", "11:30"), ("2031-05-02 11:30:42.125+03:00", "11:30"),
                             ("2031-05-02t11:30:42,125+00:00", "11:30")):
            with self.subTest(value=value):
                call = CompiledCall(context, {"now": value})
                self.assertEqual(call.variables["bg_route"], "done")
                self.assertEqual(call.state["call_inputs"], {"now": value})
                self.assertEqual((call.state["now"], call.state["now_time"]), ("2031-05-02", clock))
                self.assertNotIn(value, call.variables["bg_facts"])

    def test_invalid_clock_inputs_quarantine_before_any_action_and_cannot_be_repaired_by_speech(self):
        context = authored({"now": "offset_datetime"})
        values = (None, "", False, 42, "2031-05-02", "2031-05-02T11:30", "2031-05-02T11:30+0300",
                  "2031-05-02T11:30+03:99", "2031-05-02T11:30+25:00", "2031-02-30T11:30+03:00",
                  "2031-05-02T24:00+03:00", "2031-05-02T11:60+03:00", "2031-05-02X11:30+03:00",
                  "2031-05-02T11:30+03:00 trailing", "2031-05-02T11:30+03:00\n")
        for value in values:
            with self.subTest(value=value):
                call = CompiledCall(context, {"now": value})
                self.assertEqual(call.variables["bg_quarantined"], "yes")
                self.assertEqual(call.target("bg_init"), "bg_quarantine")
                self.assertFalse(call.state.get("attempts"))
                call.variables.update(now="2031-05-02T11:30+03:00", answer_field="postal_code", answer_text="provided-answer")
                for node in ("identity", "account", "callback", "close"):
                    call.code(node)
                    self.assertEqual(call.target(node), "bg_quarantine")
                    self.assertFalse(call.variables["bg_body"])
                    self.assertFalse(call.state.get("attempts"))

    def test_unknown_input_name_format_namespace_and_malformed_maps_reject(self):
        for formats in ({}, [], "offset_datetime", {"now": "date"}, {"now": False}, {"now": {"default": "offset_datetime"}},
                        {"bg_state": "offset_datetime"}, {"bank_token": "offset_datetime"}, {42: "offset_datetime"}):
            with self.subTest(formats=formats), self.assertRaises((PolicyContractError, ValueError)):
                authored(formats)
        with self.assertRaisesRegex(PolicyContractError, "also be declared"):
            authored({"now": "offset_datetime"}, names=["unrelated_input"])
        for quote in ("The supplied clock must have an explicit offset.", "now_extra needs an explicit offset."):
            with self.subTest(quote=quote), self.assertRaises(PolicyContractError):
                authored({"now": "offset_datetime"}, quote=quote)

    def test_format_names_are_generic_source_declared_readonly_and_api_format_does_not_infer_them(self):
        context = authored({"initial_reference_clock": "offset_datetime"})
        call = CompiledCall(context, {"initial_reference_clock": "2047-08-09T16:25+05:45"})
        self.assertEqual(call.variables["bg_route"], "done")
        self.assertEqual(call.state["call_inputs"], {"initial_reference_clock": "2047-08-09T16:25+05:45"})
        call.variables["initial_reference_clock"] = "caller's offset-less correction"
        call.variables.update(answer_field="postal_code", answer_text="provided-answer")
        call.code("identity")
        self.assertEqual(call.variables["bg_route"], "allow")
        self.assertEqual(call.state["call_inputs"]["initial_reference_clock"], "2047-08-09T16:25+05:45")
        context["plan"]["nodes"]["talk"]["extraction_schema"].append({"name": "initial_reference_clock", "type": "string", "min_confidence": 0.99})
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)
        legacy = authored()
        field = legacy["openapi"]["paths"]["/schedule_callback"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["date"]
        field["format"] = "date-time"
        self.assertNotIn("call_input_formats", legacy["execution_policy"]["profile"])

    def test_later_malformed_snapshot_preserves_actual_records_and_inflight_while_sealing_every_phase(self):
        call = CompiledCall(authored({"now": "offset_datetime"}), {"now": "2031-05-02T11:30+03:00"})
        call.account()
        call.promise()
        call.variables.update(product="asset-two", amount="82.10", day="2031-05-06")
        call.code("stage")
        call.code("commit")
        call.variables["bg_consent"] = "yes"
        call.code("bg_commit_accept")
        self.assertTrue(call.state["inflight"])
        original = deepcopy(call.variables)
        for value in ("2031-05-02", "2031-05-02T11:30", "2031-05-02T11:30+03:99", False):
            for node in ("identity", "stage", "close", "bg_commit_accept", "bg_commit_result"):
                with self.subTest(value=value, node=node):
                    call.variables = deepcopy(original)
                    state = call.state
                    state["call_inputs"]["now"] = value
                    call.variables["bg_state"] = json.dumps(state)
                    call.code(node)
                    self.assertEqual(call.target(node), "bg_quarantine")
                    self.assertEqual(call.state["recorded_promises"], state["recorded_promises"])
                    self.assertEqual(call.state["inflight"], state["inflight"])
                    self.assertFalse(call.variables["bg_body"])

    def test_capture_is_not_a_fallback_default_or_new_bank_permission(self):
        context = authored({"now": "offset_datetime"})
        before = deepcopy(context)
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(context, before)
        self.assertNotIn("test_variables", workflow)
        self.assertEqual(validate_guarded_workflow(workflow, **context), [])
        for node in workflow["nodes"].values():
            if node["node_type"] == "code":
                code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
                self.assertEqual(validate_code(code), [])
                self.assertNotIn("{{", code)
                self.assertNotIn("}}", code)


if __name__ == "__main__":
    unittest.main()

"""Platform lint: report-only findings for verified hosted Freya behaviour."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_builder
from builder.build import PROJECT, execute
from builder.platform_lint import lint_workflow
from builder.safety import digest
from tools import platform_lint as cli

ALWAYS = {"operator": "always"}


def equals(variable, operand):
    return {"logic": "or", "conditions": [{"variable": variable, "operator": "equals", "operand": operand}]}


def row(target, condition=ALWAYS, **extra):
    return {"target_node": target, "description": target, "condition": condition, **extra}


def code(name, source, rows):
    return {"name": name, "node_type": "code", "passthrough": True, "on_enter": {"type": "silent"}, "transitions": rows,
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                             "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": source}}}}}]}


def api(name, operation, body, rows):
    return {"name": name, "node_type": "api_request", "passthrough": True, "on_enter": {"type": "silent"}, "transitions": rows,
            "pre_actions": [{"type": "function", "function": {"name": operation, "handler": "__function__:api_call",
                             "parameters": {"type": "object", "properties": {"body": {"type": "object", "properties": body}}}}}]}


END = {"name": "bye", "node_type": "end_call", "transitions": [], "terminal": {"type": "function", "function": {
    "name": "end_call", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {}}}}}
VERIFY_BODY = {"customer_id": {"type": "string", "default": "{{customer_id}}"},
               "field": {"type": "string", "default": "{{factor}}"},
               "answer": {"type": "string", "default": "{{answer}}", "description": "For a birth date: the date as YYYY-MM-DD."}}


def broken():
    """One shape for each verified failure family."""
    ask = {"name": "ask", "node_type": "conversation", "task_message": "Ask the security question.", "routing_prompt": "Route.",
           "max_node_turns": 10, "max_turn_transition": "stop",
           "extraction_schema": [
               {"name": "answer", "type": "string", "description": "The answer.", "min_confidence": 0.95},
               {"name": "factor", "type": "string", "enum_values": ["date_of_birth", "phone_last4"], "min_confidence": 0.7},
               {"name": "pin", "type": "string", "validation_pattern": "^[0-9]{4}$", "min_confidence": 0.7},
               {"name": "day", "type": "date", "description": "Callback day.", "min_confidence": 0.7},
               {"name": "consent", "type": "string", "enum_values": ["yes", "no"], "min_confidence": 0.95}],
           "transitions": [row("check", equals("factor", "phone_last4")),
                           row("talk", {"operator": "matches_intent", "description": "wants help"}), row("check")]}
    talk = {"name": "talk", "node_type": "conversation", "task_message": "Help.", "transitions": [],
            "tools": [{"type": "function", "function": {"name": "lookup", "handler": "__function__:api_call"}}]}
    return {"initial_node": "init", "system_prompt": "Today is {{system.todays_date_en_est}}.", "test_variables": {"now": "2026-10-14"},
            "nodes": {"init": code("init", "vars['consent'] = ''\n", [row("ask")]), "ask": ask, "talk": talk,
                      "check": api("check", "verify_identity", VERIFY_BODY, [row("result")]),
                      "result": code("result", "vars['route'] = 'done'\n" + "#" * 90000, [row("check", equals("route", "allow")), row("ask")]),
                      "stop": code("stop", "vars['stop'] = 'yes'\n", [row("bye")]), "bye": END}}


def repaired():
    """The same call with every family fixed; the lint must stay silent."""
    workflow = broken()
    nodes = workflow["nodes"]
    workflow["system_prompt"] = "Today is {{now}}. Speak after every tool result."
    nodes["init"] = code("init", "vars['consent'] = 'no'\n", [row("ask")])
    nodes["check"] = api("check", "verify_identity", {**VERIFY_BODY, "field": {"type": "string", "default": "{{next_factor}}"}},
                         [row("result")])
    ask = nodes["ask"]
    del ask["routing_prompt"]
    ask.update(task_message="Ask the security question. No tool call is allowed in this turn.", max_node_turns=20,
               transitions=[row("check", equals("factor", "phone_last4")), row("talk")])
    ask["extraction_schema"][0].update(min_confidence=0.7, description=(
        "The answer: a birth date as YYYY-MM-DD with the year the caller said, or four digits. "
        "Join pieces said over consecutive turns."))
    ask["extraction_schema"][1]["min_confidence"] = 0.7
    ask["extraction_schema"][2]["description"] = "Four digits; join the pieces."
    ask["extraction_schema"][3]["description"] = "Callback day with its year, resolved from {{now}}."
    nodes["result"] = code("result", "vars['route'] = 'done'\n", [row("bye", {"logic": "or", "conditions": [
        {"variable": "result_failure_count", "operator": "greater_than", "operand": 0}]}), row("check", equals("route", "allow")), row("close")])
    nodes["stop"] = code("stop", "vars['stop'] = 'yes'\n", [row("close")])
    nodes["close"] = api("close", "close_call", {}, [row("bye")])
    # The failed-step row ends the call; a lifecycle close covers every end, as in the Plan B agents.
    workflow["tools"] = [{"type": "function", "config": {"alwaysRunsAt": ["end_of_call"]},
                          "function": {"name": "close_call", "handler": "__function__:api_call"}}]
    return workflow


class PlatformLintTests(unittest.TestCase):
    def rules(self, workflow, policy=None):
        return {(finding["rule"], finding["node"]): finding["severity"] for finding in lint_workflow(workflow, policy)["findings"]}

    def test_each_verified_failure_family_is_reported_with_its_evidence(self):
        report = lint_workflow(broken())
        found = sorted((finding["severity"], finding["rule"], finding["node"]) for finding in report["findings"])
        self.assertEqual(found, sorted([
            ("blocker", "code_payload_limit", "result"), ("blocker", "routing_prompt_on_intent_node", "ask"),
            ("blocker", "stale_allow_redispatch", "result"), ("high", "answer_format_unstated", "ask"),
            ("high", "checked_value_confidence", "ask"), ("high", "controller_known_input", "check"),
            ("high", "date_year_not_required", "ask"), ("high", "end_without_close", "bye"),
            ("high", "enum_write_refused", "init"), ("high", "platform_clock_reference", "workflow"),
            ("high", "relative_date_platform_clock", "ask"), ("high", "same_target_with_always", "ask"),
            ("high", "speak_after_tool_result_missing", "talk"), ("high", "split_value_not_joined", "ask"),
            ("high", "split_value_not_joined", "ask"), ("high", "turn_cap_low", "ask"),
            ("medium", "no_tool_line_missing", "ask"), ("low", "date_year_not_required", "ask")]))
        self.assertTrue(all(finding["evidence"] and finding["detail"] for finding in report["findings"]))
        self.assertEqual(report["summary"]["findings"], len(report["findings"]))
        self.assertEqual(report["auto_fixes"], [])
        self.assertFalse(report["hosted_behavior_verified"])

    def test_the_repaired_shapes_are_silent(self):
        self.assertEqual(lint_workflow(repaired())["findings"], [])

    def test_adjacent_same_target_rows_are_low_and_policy_clock_and_closer_are_read(self):
        workflow = repaired()
        workflow["nodes"]["ask"]["transitions"] = [row("talk", equals("factor", "phone_last4")), row("talk")]
        policy = {"rules": [{"kind": "closing_sequence", "value": {"operation": "finish_call"}},
                            {"kind": "call_variables", "value": ["now"]}, {"kind": "note", "value": "{{system.iso_utc_now}}"}]}
        found = self.rules(workflow, policy)
        self.assertEqual(found[("same_target_with_always", "ask")], "low")
        self.assertEqual(found[("platform_clock_reference", "policy")], "high")
        self.assertEqual(found[("end_without_close", "bye")], "high")  # the policy names another close operation
        workflow["tools"][0]["function"]["name"] = "finish_call"
        self.assertNotIn(("end_without_close", "bye"), self.rules(workflow, policy))

    def test_readbacks_and_candidates_are_unwrapped_and_nulls_tolerated(self):
        workflow = broken()
        workflow["global_nodes"] = []
        workflow["nodes"]["talk"]["routing_prompt"] = None
        workflow["nodes"]["talk"]["extraction_schema"] = None
        expected = self.rules(broken())
        self.assertEqual(self.rules({"id": "readback", "workflow": workflow}), expected)
        self.assertEqual(lint_workflow(None)["findings"], [])

    def test_guarded_build_records_the_lint_without_rejecting(self):
        tests = test_builder.BuilderTests()
        test_builder.BuilderTests.setUp(tests)
        self.addCleanup(test_builder.BuilderTests.tearDown, tests)
        generator = tests.generator()
        generator.generate.return_value = test_builder.fixture()
        manifest = execute(tests.args("--generate-only"), generator=generator,
                           client_factory=lambda **kwargs: self.fail("Freya touched"))
        run = PROJECT / manifest["run_directory"]
        report = json.loads((run / "platform-lint.json").read_text())
        self.assertEqual(manifest["status"], "generated_validated")
        self.assertEqual(manifest["platform_lint"], report["summary"])
        self.assertIn(("code_payload_limit", "done"), {(f["rule"], f["node"]) for f in report["findings"]})
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        event = next(event for event in events if event["event"] == "platform_lint")
        self.assertEqual(event["findings"], report["summary"]["findings"])
        # workflow.json is written before the lint and the manifest digest after it: the lint changed nothing.
        self.assertEqual(manifest["workflow_sha256"], digest(json.loads((run / "workflow.json").read_text())))
        with patch("builder.build.lint_workflow", side_effect=RuntimeError("lint fault")):
            manifest = execute(tests.args("--generate-only"), generator=generator,
                               client_factory=lambda **kwargs: self.fail("Freya touched"))
        self.assertEqual(manifest["status"], "generated_validated")
        self.assertEqual(manifest["platform_lint"], {"error": "RuntimeError: lint fault"})

    def test_cli_reads_files_and_prints_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps({"policy": {"rules": []}, "workflow": broken()}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([str(path), "--summary"]), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed["summary"], lint_workflow(broken())["summary"])


if __name__ == "__main__":
    unittest.main()

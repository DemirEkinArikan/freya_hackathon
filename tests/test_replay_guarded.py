"""Compiled integration runs with evaluator-only synthetic protocol responses."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from builder.guards import compile_guarded_workflow
from development_protocol_samples import protocol_response
from test_guards import fixture
from test_guard_aliases import renamed_context
from test_call_variables import inputs as call_inputs, context as call_context
from tools.replay_guarded import replay, ReplayError, _execute


FIELDS = ("answer_field", "answer_text", "product", "amount", "day", "note_reason", "queue", "outcome", "close_reason")


def authored(renamed=False):
    if renamed:
        source, aliases = renamed_context()
    else:
        source = fixture()
        aliases = {name: name for name in ("verify_identity", "get_account", "create_promise", "add_note", "transfer_to_agent", "close_call")}
    source["plan"]["nodes"]["talk"]["extraction_schema"] = [
        {"name": name, "type": "string", "min_confidence": 0.95} for name in FIELDS]
    source["plan"]["nodes"]["talk"]["max_node_turns"] = 100
    workflow = compile_guarded_workflow(**source)["workflow"]
    context = {key: value for key, value in source.items() if key != "openapi"}
    return workflow, context, source["openapi"], aliases


def frame(context, action, **values):
    choices = list(context["plan"]["nodes"]["talk"]["transitions"])
    index = next(index for index, row in enumerate(choices) if row["target_node"] == action)
    return {"node": "talk", "values": values, "intent_transition": index}


def scenario(context, aliases, *, failures=0, renamed=False):
    identity_fields = ["challenge_A", "challenge_B"] if renamed else ["postal_code", "memorable_word"]
    product = {"asset_reference": "synthetic-product", "kind_wire": "KIND_A", "delay_marker": 3, "arrears_value": 80, "floor_alpha": 20} if renamed else {
        "product_id": "synthetic-product", "type": "revolving", "days_late": 3, "past_due": 80, "minimum_due": 20}
    frames = [frame(context, "identity", answer_field=value, answer_text="synthetic-answer") for value in identity_fields]
    frames += [frame(context, "account"), frame(context, "stage", product="synthetic-product", amount="20.00", day="2031-05-06"),
               frame(context, "commit"), {"node": "bg_commit_confirm", "values": {"bg_consent": "yes"}},
               frame(context, "finish"), frame(context, "close")]
    responses = []
    def add(role, *, ok=True, status=200):
        response = protocol_response(role, ok=ok, status=status, remaining=4, products=[product])
        if not ok:
            response["data"]["error"] = "ERR_BUSY" if renamed else "temporarily_unavailable"
        if renamed:
            response["data"] = {"evidence_" + key: value for key, value in response["data"].items()}
        responses.append({"operation": aliases[role], "envelope": response})
    add("verify_identity"); add("verify_identity"); add("get_account")
    for _ in range(failures):
        add("create_promise", ok=False, status=503)
    if failures < 2:
        add("create_promise")
    else:
        add("add_note")
    add("close_call")
    return {"version": 1, "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"}, "frames": frames, "responses": responses}


class GuardedReplayTests(unittest.TestCase):
    def test_confirmation_and_success_end_with_one_final_close(self):
        workflow, context, api, aliases = authored()
        report = replay(workflow, context, api, scenario(context, aliases))
        self.assertEqual(report["status"], "terminated")
        self.assertEqual([call["tool"] for call in report["calls"]], ["verify_identity", "verify_identity", "get_account", "create_promise", "close_call"])
        self.assertEqual(report["calls"][-1]["arguments"]["outcome"], "promise")
        self.assertTrue(report["final_state"]["closed"])
        self.assertEqual((report["unused_frames"], report["unused_responses"]), (0, 0))
        self.assertTrue(report["mock"])
        self.assertFalse(report["conversation_compliance_proven"])

    def test_exact_confirmed_request_retries_without_another_confirmation(self):
        workflow, context, api, aliases = authored()
        report = replay(workflow, context, api, scenario(context, aliases, failures=1))
        promises = [call for call in report["calls"] if call["tool"] == "create_promise"]
        self.assertEqual(len(promises), 2)
        self.assertEqual(promises[0]["arguments"], promises[1]["arguments"])
        self.assertEqual(promises[0]["arguments"], {"customer_id": "synthetic-session", "product_id": "synthetic-product", "amount": 20, "date": "2031-05-06"})
        self.assertEqual(sum(step["node"] == "bg_commit_confirm" for step in report["steps"]), 1)

    def test_renamed_body_result_fields_and_opaque_outcome_are_executed(self):
        workflow, context, api, aliases = authored(True)
        report = replay(workflow, context, api, scenario(context, aliases, failures=1, renamed=True))
        self.assertEqual(report["status"], "terminated")
        promises = [call for call in report["calls"] if call["tool"] == aliases["create_promise"]]
        self.assertEqual(promises[0]["arguments"], {"input_customer_id": "synthetic-session", "input_product_id": "synthetic-product", "input_amount": 20, "input_date": "2031-05-06"})
        self.assertEqual(promises[0]["arguments"], promises[1]["arguments"])
        self.assertEqual(report["calls"][-1]["arguments"]["input_outcome"], "C_A")

    def test_two_failures_record_disposition_and_close_without_success_claim(self):
        workflow, context, api, aliases = authored()
        report = replay(workflow, context, api, scenario(context, aliases, failures=2))
        self.assertEqual(report["status"], "terminated")
        self.assertEqual([call["tool"] for call in report["calls"]][-4:], ["create_promise", "create_promise", "add_note", "close_call"])
        self.assertEqual(report["calls"][-2]["arguments"]["reason"], "service_error")
        self.assertEqual(report["calls"][-1]["arguments"]["outcome"], "no_promise")
        self.assertFalse(report["final_state"]["recorded_promises"])

    def test_transfer_discards_staged_work_and_preserves_a_recorded_promise(self):
        for recorded in (False, True):
            workflow, context, api, aliases = authored()
            script = scenario(context, aliases)
            other = deepcopy(script["responses"][2]["envelope"]["data"]["products"][0])
            other.update(product_id="synthetic-second", days_late=4, type="fixed", instalment_due=20)
            script["responses"][2]["envelope"]["data"]["products"].append(other)
            script["frames"] = script["frames"][:6 if recorded else 4] + [
                frame(context, "note", product="synthetic-second", note_reason="refused"),
                frame(context, "transfer", queue="specialist"), frame(context, "handoff"), frame(context, "close")]
            script["responses"] = script["responses"][:4 if recorded else 3] + [
                {"operation": "transfer_to_agent", "envelope": protocol_response("transfer_to_agent")},
                {"operation": "close_call", "envelope": protocol_response("close_call")}]
            with self.subTest(recorded=recorded):
                report = replay(workflow, context, api, script)
                self.assertEqual(report["status"], "terminated")
                self.assertEqual([call["tool"] for call in report["calls"]][-2:], ["transfer_to_agent", "close_call"])
                self.assertNotIn("add_note", [call["tool"] for call in report["calls"]])
                self.assertEqual(report["calls"][-1]["arguments"], {"customer_id": "synthetic-session", "outcome": "transferred", "reason": "hardship"})
                self.assertFalse(report["final_state"]["pending_promises"])
                self.assertFalse(report["final_state"]["pending_notes"])
                self.assertEqual(bool(report["final_state"]["recorded_promises"]), recorded)
                self.assertEqual((report["unused_frames"], report["unused_responses"]), (0, 0))

    def test_refused_confirmation_has_no_commit_and_missing_close_is_not_success(self):
        workflow, context, api, aliases = authored()
        script = scenario(context, aliases)
        script["frames"] = script["frames"][:6]
        script["frames"][-1]["values"]["bg_consent"] = "no"
        script["responses"] = script["responses"][:3]
        report = replay(workflow, context, api, script)
        self.assertEqual(report["status"], "missing_conversation_frame")
        self.assertNotIn("create_promise", [call["tool"] for call in report["calls"]])
        self.assertFalse(report["final_state"]["recorded_promises"])
        self.assertFalse(report["final_state"]["closed"])

    def test_sourced_call_inputs_are_accepted_only_initially(self):
        policy, sources, api = call_inputs(("opening_code", "contact_label"))
        context = call_context(policy, sources, api)
        context["plan"]["nodes"]["talk"]["on_enter"]["text"] = "Hello {{opening_code}} {{contact_label}}."
        workflow = compile_guarded_workflow(openapi=api, **context)["workflow"]
        script = {"version": 1, "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02",
                  "opening_code": "Ms", "contact_label": "Synthetic Party"},
                  "frames": [{"node": "talk", "values": {}}],
                  "responses": [{"operation": "close_call", "envelope": protocol_response("close_call")}]}
        self.assertEqual(replay(workflow, context, api, script)["status"], "terminated")
        script["frames"][0]["values"]["contact_label"] = "Replacement Party"
        with self.assertRaisesRegex(ReplayError, "this node's extraction"):
            replay(workflow, context, api, script)
        script["frames"][0]["values"] = {}
        script["initial_inputs"]["invented_input"] = "unsourced"
        with self.assertRaisesRegex(ReplayError, "declared call inputs"):
            replay(workflow, context, api, script)

    def test_documented_string_comparison_and_case_insensitive_contains_route_normally(self):
        workflow, context, api, aliases = authored()
        node = context["plan"]["nodes"]["talk"]
        node["transitions"].insert(0, {"target_node": "close", "description": "synthetic numeric or textual condition",
            "condition": {"logic": "or", "conditions": [
                {"variable": "amount", "operator": "equals", "operand": 20},
                {"variable": "answer_text", "operator": "contains", "operand": "BANK"}]}})
        workflow = compile_guarded_workflow(openapi=api, **context)["workflow"]
        for values in ({"amount": "20"}, {"answer_text": "bank"}):
            script = {"version": 1, "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"},
                      "frames": [{"node": "talk", "values": values}],
                      "responses": [{"operation": "close_call", "envelope": protocol_response("close_call")}]}
            with self.subTest(values=values):
                report = replay(workflow, context, api, script)
                self.assertEqual(report["status"], "terminated")
                self.assertEqual([call["tool"] for call in report["calls"]], ["close_call"])

    def test_tampered_compiled_code_is_rejected_before_any_execution(self):
        workflow, context, api, aliases = authored()
        workflow["nodes"]["bg_init"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"] += "\nvars['forged'] = 'unsafe'\n"
        with patch("tools.replay_guarded._execute", side_effect=AssertionError("must not execute")) as execute:
            with self.assertRaisesRegex(ReplayError, "exact trusted workflow"):
                replay(workflow, context, api, scenario(context, aliases))
            execute.assert_not_called()

    def test_initial_and_frame_state_forgery_is_refused(self):
        for initial in (True, False):
            workflow, context, api, aliases = authored()
            script = scenario(context, aliases)
            if initial:
                script["initial_inputs"]["bg_state"] = "forged"
            else:
                script["frames"][0]["values"]["bg_state"] = "forged"
            with self.subTest(initial=initial), self.assertRaises(ReplayError):
                replay(workflow, context, api, script)
        script = scenario(context, aliases)
        script["frames"][0]["values"]["bg_consent"] = "yes"
        with self.assertRaisesRegex(ReplayError, "this node's extraction"):
            replay(workflow, context, api, script)

    def test_mock_response_order_cannot_hide_an_extra_bank_call(self):
        workflow, context, api, aliases = authored()
        script = scenario(context, aliases)
        script["responses"][0]["operation"] = "get_account"
        with self.assertRaisesRegex(ReplayError, "actual next API operation"):
            replay(workflow, context, api, script)

    def test_absorbing_automatic_loop_is_detected_despite_structural_terminal_path(self):
        workflow, context, api, aliases = authored()
        node = context["plan"]["nodes"]["talk"]
        node["transitions"].insert(0, {"target_node": "talk", "description": "loop while call clock exists",
            "condition": {"logic": "or", "conditions": [{"variable": "now", "operator": "is_set"}]}})
        workflow = compile_guarded_workflow(openapi=api, **context)["workflow"]
        script = {"version": 1, "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"}, "frames": [], "responses": []}
        report = replay(workflow, context, api, script)
        self.assertEqual(report["status"], "absorbing_loop")
        self.assertFalse(report["calls"])

    def test_step_limit_and_missing_evidence_are_not_terminal_success(self):
        workflow, context, api, aliases = authored()
        script = scenario(context, aliases)
        self.assertEqual(replay(workflow, context, api, script, max_steps=1)["status"], "step_limit")
        script["responses"] = []
        self.assertEqual(replay(workflow, context, api, script)["status"], "missing_mock_response")

    def test_execution_overrun_cannot_be_swallowed_into_success(self):
        with self.assertRaisesRegex(ReplayError, "allocation"):
            _execute("try:\n    while True:\n        pass\nexcept Exception:\n    pass\n", {}, 0.01)

    def test_direct_cli_replays_repository_inputs_and_never_overwrites_a_report(self):
        root = Path(__file__).resolve().parents[1]
        artifacts = root / "artifacts"
        artifacts.mkdir(exist_ok=True)
        workflow, context, api, aliases = authored()
        with tempfile.TemporaryDirectory(prefix="replay-test-", dir=artifacts) as directory:
            directory = Path(directory)
            command = [sys.executable, str(root / "tools/replay_guarded.py")]
            for argument, value in (("workflow", workflow), ("guard-context", context),
                                    ("tool-api", api), ("script", scenario(context, aliases))):
                path = directory / (argument + ".json")
                path.write_text(json.dumps(value), encoding="utf-8")
                command.extend(["--" + argument, str(path)])
            output = directory / "report.json"
            command.extend(["--output", str(output)])
            first = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "terminated")
            before = output.read_bytes()
            second = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(second.returncode, 1)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

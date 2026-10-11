"""Scheduling evidence is distinct from extraction in exact trusted replays."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from builder.guards import compile_guarded_workflow
from development_protocol_samples import protocol_response
from test_guards import fixture
from tools.replay_guarded import ReplayError, replay


def route(target, condition=None, *, tool=False):
    result = {"target_node": target, "condition": condition or {"operator": "always"}}
    if tool:
        result["is_tool_call"] = True
    return result


def equals(name, value):
    return {"logic": "or", "conditions": [{"variable": name, "operator": "equals", "operand": value}]}


def intent(description="The caller selected this route"):
    return {"operator": "matches_intent", "description": description}


def conversation(name, transitions, *, entry="silent", passthrough=None, cap=20):
    result = {"name": name, "node_type": "conversation", "task_message": "Synthetic scheduling test.",
              "on_enter": {"type": entry},
              "extraction_schema": [{"name": "answer_text", "type": "string", "min_confidence": 0.95}],
              "transitions": transitions, "max_node_turns": cap, "max_turn_transition": "close"}
    if entry == "tts":
        result["on_enter"]["text"] = "A fixed synthetic acknowledgement."
    if passthrough is not None:
        result.update(passthrough=True, passthrough_default_transition=passthrough)
    return result


def compiled(nodes=None):
    source = fixture()
    close = source["plan"]["nodes"]["close"]
    source["plan"] = {"initial_node": "talk", "nodes": {
        "talk": conversation("talk", [route("close")]), "close": close}}
    if nodes:
        source["plan"]["nodes"].update(deepcopy(nodes))
    workflow = compile_guarded_workflow(**source)["workflow"]
    context = {key: value for key, value in source.items() if key != "openapi"}
    # replay independently recompiles and validates this exact controller context.
    return workflow, context, source["openapi"]


def caller(node="talk", values=None, selected=None):
    result = {"node": node, "event": "caller", "values": values or {}}
    if selected is not None:
        result["intent_transition"] = selected
    return result


def completion(selected=0, node="talk"):
    return {"node": node, "event": "agent_completion", "transition": selected}


def entry_routing(selected, node="talk"):
    return {"node": node, "event": "entry_routing", "intent_transition": selected}


def script(frames=(), *, responses=None, version=2):
    return {"version": version, "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"},
            "frames": deepcopy(list(frames)),
            "responses": deepcopy(responses if responses is not None else [
                {"operation": "close_call", "envelope": protocol_response("close_call")}])}


class ReplaySchedulingTests(unittest.TestCase):
    def run_script(self, nodes=None, frames=(), **kwargs):
        workflow, context, api = compiled(nodes)
        return replay(workflow, context, api, script(frames, **kwargs))

    def assert_waiting_without_call(self, report, status="missing_conversation_frame"):
        self.assertEqual(report["status"], status)
        self.assertFalse(report["calls"])
        self.assertFalse(report["final_state"]["closed"])
        self.assertIsNone(report["terminal_node"])

    def test_silent_and_llm_ordinary_always_routes_wait_for_caller(self):
        for entry in ("silent", "llm", "tts"):
            with self.subTest(entry=entry):
                report = self.run_script({"talk": conversation("talk", [route("close")], entry=entry)})
                self.assert_waiting_without_call(report)
                self.assertEqual(report["frames_consumed"], 0)
                self.assertEqual([item["node"] for item in report["prompts"]], ["talk"])

    def test_ordinary_route_consumes_one_supplied_caller_turn(self):
        report = self.run_script(frames=[caller()])
        self.assertEqual(report["status"], "terminated")
        self.assertEqual(report["frames_consumed"], 1)
        self.assertEqual([item["tool"] for item in report["calls"]], ["close_call"])

    def test_future_node_frame_does_not_skip_wait_or_reorder_evidence(self):
        nodes = {"talk": conversation("talk", [route("later")]),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes, [caller("later"), caller("talk")])
        self.assert_waiting_without_call(report)
        self.assertEqual((report["frames_consumed"], report["unused_frames"]), (0, 2))
        self.assertNotIn("later", [item["node"] for item in report["steps"]])

    def test_unmatched_caller_turns_wait_without_rerunning_node_entry(self):
        nodes = {"talk": conversation("talk", [route("close", equals("answer_text", "done"))])}
        for turns in (1, 3):
            with self.subTest(turns=turns):
                report = self.run_script(nodes, [caller() for _ in range(turns)])
                self.assert_waiting_without_call(report)
                self.assertEqual(report["frames_consumed"], turns)
                self.assertEqual(sum(item["node"] == "talk" for item in report["prompts"]), 1)

    def test_turn_cap_counts_only_supplied_caller_turns(self):
        nodes = {"talk": conversation("talk", [route("close", equals("answer_text", "done"))], cap=2)}
        report = self.run_script(nodes, [caller()])
        self.assert_waiting_without_call(report)
        report = self.run_script(nodes, [caller(), caller(), caller()])
        self.assertEqual(report["status"], "terminated")
        self.assertEqual((report["frames_consumed"], report["unused_frames"]), (2, 1))
        self.assertEqual([item["tool"] for item in report["calls"]], ["close_call"])

    def test_ordinary_default_does_not_replace_an_unmatched_caller_route(self):
        talk = conversation("talk", [route("close", equals("answer_text", "done"))])
        talk["passthrough_default_transition"] = "close"
        report = self.run_script({"talk": talk}, [caller()])
        self.assert_waiting_without_call(report)
        self.assertEqual(report["frames_consumed"], 1)

    def test_passthrough_deterministic_branch_precedes_matching_default_fallback(self):
        nodes = {"talk": conversation("talk", [route("later", equals("now", "2031-05-02")), route("close")],
                                      passthrough="close"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes)
        self.assert_waiting_without_call(report)
        self.assertEqual(report["frames_consumed"], 0)
        self.assertEqual([item["node"] for item in report["prompts"]], ["talk", "later"])
        nodes["talk"]["transitions"][0]["condition"] = equals("now", "2031-05-03")
        report = self.run_script(nodes)
        self.assertEqual(report["status"], "terminated")
        self.assertNotIn("later", [item["node"] for item in report["steps"]])

    def test_fixed_tts_passthrough_finishes_without_a_caller_frame(self):
        nodes = {"talk": conversation("talk", [route("close")], entry="tts", passthrough="close")}
        report = self.run_script(nodes)
        self.assertEqual(report["status"], "terminated")
        self.assertEqual(report["frames_consumed"], 0)
        self.assertEqual([item["tool"] for item in report["calls"]], ["close_call"])
        self.assertEqual(report["prompts"][0]["on_enter"], nodes["talk"]["on_enter"])

    def test_passthrough_does_not_consume_caller_evidence(self):
        nodes = {"talk": conversation("talk", [route("later")], passthrough="later"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes, [caller("talk"), caller("later")])
        self.assert_waiting_without_call(report)
        self.assertEqual((report["frames_consumed"], report["unused_frames"]), (0, 2))

    def test_llm_passthrough_resolves_tool_choice_before_ordinary_entry_route(self):
        nodes = {"talk": conversation("talk", [route("later", intent(), tool=True), route("close")],
                                      entry="llm", passthrough="close"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes)
        self.assert_waiting_without_call(report, "missing_agent_completion")
        report = self.run_script(nodes, [completion()])
        self.assert_waiting_without_call(report)
        self.assertIn("later", [item["node"] for item in report["prompts"]])
        report = self.run_script(nodes, [entry_routing(None)])
        self.assertEqual(report["status"], "terminated")
        self.assertEqual(report["frames_consumed"], 1)

    def test_fixed_passthrough_entry_rejects_forged_tool_completion(self):
        for entry in ("silent", "tts"):
            nodes = {"talk": conversation("talk", [route("later", intent(), tool=True), route("close")],
                                          entry=entry, passthrough="close"),
                     "later": conversation("later", [route("close")])}
            with self.subTest(entry=entry):
                report = self.run_script(nodes)
                self.assertEqual(report["status"], "terminated")
                workflow, context, api = compiled(nodes)
                with self.assertRaises(ReplayError):
                    replay(workflow, context, api, script([completion()]))

    def test_tool_routes_require_explicit_agent_completion_for_llm_and_tts(self):
        for entry in ("llm", "tts", "silent"):
            for condition in ({"operator": "always"}, intent(), equals("answer_text", "never-supplied")):
                with self.subTest(entry=entry, condition=condition):
                    nodes = {"talk": conversation("talk", [route("close", condition, tool=True)], entry=entry)}
                    report = self.run_script(nodes)
                    self.assert_waiting_without_call(report, "missing_agent_completion" if entry == "llm" else "missing_conversation_frame")
                    frames = [completion()] if entry == "llm" else [caller(), completion()]
                    if entry != "llm":
                        workflow, context, api = compiled(nodes)
                        with self.assertRaises(ReplayError):
                            replay(workflow, context, api, script([completion()]))
                    report = self.run_script(nodes, frames)
                    self.assertEqual(report["status"], "terminated")
                    self.assertEqual(report["frames_consumed"], len(frames))
                    self.assertTrue(report["final_state"]["closed"])

    def test_caller_turn_cannot_trigger_unconditional_tool_route(self):
        nodes = {"talk": conversation("talk", [route("close", tool=True)], entry="llm")}
        report = self.run_script(nodes, [caller()])
        self.assert_waiting_without_call(report, "missing_agent_completion")
        self.assertEqual(report["frames_consumed"], 1)

    def test_router_ignores_tool_condition_and_uses_ordinary_route(self):
        nodes = {"talk": conversation("talk", [route("later", equals("answer_text", "done"), tool=True),
                                               route("close")], entry="llm"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes, [caller(values={"answer_text": "done"})])
        self.assertEqual(report["status"], "terminated")
        self.assertNotIn("later", [item["node"] for item in report["steps"]])

    def test_multiple_tools_choose_actual_index_without_routing_condition(self):
        nodes = {"talk": conversation("talk", [route("later", intent("First offered tool"), tool=True),
                                               route("close", intent("Second offered tool"), tool=True)], entry="llm"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes, [completion(1)])
        self.assertEqual(report["status"], "terminated")
        self.assertNotIn("later", [item["node"] for item in report["steps"]])

    def test_tool_selection_preserves_private_entry_gate(self):
        workflow, context, api = compiled({"talk": conversation("talk", [route("close", intent(), tool=True)], entry="llm")})
        target = workflow["nodes"]["talk"]["transitions"][0]["target_node"]
        self.assertTrue(target.startswith("bg_gate_"))
        self.assertNotEqual(target, "close")
        report = replay(workflow, context, api, script([completion()]))
        path = [item["node"] for item in report["steps"]]
        self.assertLess(path.index(target), path.index("close"))
        self.assertEqual(report["calls"][0]["tool"], "close_call")

    def test_entry_intent_cannot_override_deterministic_fallback(self):
        nodes = {"talk": conversation("talk", [route("later", intent()), route("close")], passthrough="close"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes)
        self.assertEqual(report["status"], "terminated")
        report = self.run_script(nodes, [entry_routing(None)])
        self.assertEqual(report["status"], "terminated")
        report = self.run_script(nodes, [entry_routing(0)])
        self.assertEqual(report["status"], "terminated")
        self.assertNotIn("later", [item["node"] for item in report["steps"]])

    def test_true_deterministic_or_routes_before_fallback_despite_intent_leaf(self):
        condition = {"logic": "or", "conditions": [equals("now", "2031-05-02"), intent()]}
        nodes = {"talk": conversation("talk", [route("later", condition), route("close")], passthrough="close"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes)
        self.assert_waiting_without_call(report)
        self.assertEqual([item["node"] for item in report["prompts"]], ["talk", "later"])
        self.assertEqual(report["frames_consumed"], 0)

    def test_duplicate_targets_keep_list_order_without_merge_emulation(self):
        # The kit merges duplicate targets at load but does not document the
        # merged position; the compiled controller relies on list order.
        nodes = {"talk": conversation("talk", [route("later", intent()),
                                               route("close", equals("now", "2031-05-02")),
                                               route("later")], passthrough="later"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes)
        self.assertEqual(report["status"], "terminated")
        self.assertNotIn("later", [item["node"] for item in report["steps"]])
        self.assertEqual(report["frames_consumed"], 0)

    def test_false_deterministic_and_resolves_entry_intent_without_evidence(self):
        condition = {"logic": "or", "conditions": [{"logic": "and", "conditions": [
            equals("now", "2031-05-03"), intent()]}]}
        nodes = {"talk": conversation("talk", [route("later", condition), route("close")], passthrough="close"),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes)
        self.assertEqual(report["status"], "terminated")
        self.assertEqual(report["frames_consumed"], 0)

    def test_completion_events_do_not_count_toward_caller_turn_cap(self):
        nodes = {"talk": conversation("talk", [route("later", intent(), tool=True)], entry="llm", cap=1),
                 "later": conversation("later", [route("close")])}
        report = self.run_script(nodes, [completion()])
        self.assert_waiting_without_call(report)
        self.assertIn("later", [item["node"] for item in report["prompts"]])

    def test_first_entry_only_llm_does_not_invent_a_model_phase_on_reentry(self):
        talk = conversation("talk", [route("later", intent("Visit later"), tool=True),
                                     route("close", intent("Close call"), tool=True)], entry="llm")
        talk["on_enter_first_entry_only"] = True
        nodes = {"talk": talk,
                 "later": conversation("later", [route("talk", intent(), tool=True)], entry="llm")}
        report = self.run_script(nodes, [completion(), completion(node="later")])
        self.assert_waiting_without_call(report)
        self.assertEqual([item["entry_executed"] for item in report["prompts"]], [True, True, False])
        workflow, context, api = compiled(nodes)
        with self.assertRaises(ReplayError):
            replay(workflow, context, api, script([completion(), completion(node="later"), completion(1)]))
        report = self.run_script(nodes, [completion(), completion(node="later"), caller(), completion(1)])
        self.assertEqual(report["status"], "terminated")

    def test_forged_malformed_or_cross_kind_selectors_are_rejected(self):
        nodes = {"talk": conversation("talk", [route("close", intent(), tool=True)], entry="llm")}
        workflow, context, api = compiled(nodes)
        malformed = [
            {"node": "talk", "event": "agent_completion", "transition": value}
            for value in (True, False, -1, 1, 0.0, "close", "bg_gate_0", None)
        ]
        malformed += [
            {"node": "talk", "event": "agent_completion"},
            {"node": "talk", "event": "agent_completion", "transition": 0, "values": {}},
            {"node": "talk", "event": "agent_completion", "transition": 0, "intent_transition": 0},
            {"node": "talk", "event": "agent_completion", "transition": 0, "target_node": "close"},
            {"node": "talk", "event": "tool_call", "transition": 0},
            {"node": "talk", "values": {}},
            caller(selected=0),
            {"node": "talk", "event": "caller", "values": {}, "transition": 0},
            entry_routing(0),
            completion(node="bg_gate_0"),
        ]
        for event in malformed:
            with self.subTest(event=event), self.assertRaises(ReplayError):
                replay(workflow, context, api, script([event]))
        workflow, context, api = compiled()
        with self.assertRaises(ReplayError):
            replay(workflow, context, api, script([completion()]))

    def test_completion_cannot_write_protected_state_or_call_inputs(self):
        workflow, context, api = compiled({"talk": conversation("talk", [route("close", tool=True)], entry="llm")})
        for name in ("bg_state", "bg_route", "bg_control_entries", "customer_id", "now", "bg_consent"):
            event = completion()
            event["values"] = {name: "forged"}
            with self.subTest(name=name), self.assertRaises(ReplayError):
                replay(workflow, context, api, script([event]))

    def test_v2_caller_and_initial_inputs_cannot_forge_controller_state(self):
        workflow, context, api = compiled()
        for name in ("bg_state", "bg_route", "bg_control_entries", "customer_id", "now", "bg_consent"):
            with self.subTest(name=name), self.assertRaises(ReplayError):
                replay(workflow, context, api, script([caller(values={name: "forged"})]))
        forged = script([caller()])
        forged["initial_inputs"]["bg_state"] = "forged"
        with self.assertRaises(ReplayError):
            replay(workflow, context, api, forged)

    def test_invalid_future_event_is_rejected_before_automatic_bank_call(self):
        workflow, context, api = compiled({"talk": conversation("talk", [route("close")], entry="tts", passthrough="close")})
        with patch("tools.replay_guarded._execute", side_effect=AssertionError("must not execute")) as execute:
            with self.assertRaises(ReplayError):
                replay(workflow, context, api, script([completion(selected="forged", node="invented")]))
            execute.assert_not_called()

    def test_forged_response_operation_or_node_is_rejected_before_execution(self):
        workflow, context, api = compiled({"talk": conversation("talk", [route("close")], entry="tts", passthrough="close")})
        for operation, node in (("invented_operation", "bg_close_api"), ("close_call", "bg_init"),
                                ("close_call", "bg_close_result"), ("close_call", "invented_node")):
            evidence = {"operation": operation, "node": node, "envelope": protocol_response("close_call")}
            with self.subTest(operation=operation, node=node):
                with patch("tools.replay_guarded._execute", side_effect=AssertionError("must not execute")) as execute:
                    with self.assertRaises(ReplayError):
                        replay(workflow, context, api, script(responses=[evidence]))
                    execute.assert_not_called()

    def test_legacy_v1_caller_schema_still_waits_and_routes(self):
        workflow, context, api = compiled()
        report = replay(workflow, context, api, script(version=1))
        self.assert_waiting_without_call(report)
        report = replay(workflow, context, api, script([{"node": "talk", "values": {}}], version=1))
        self.assertEqual(report["status"], "terminated")
        with self.assertRaises(ReplayError):
            replay(workflow, context, api, script([completion()], version=1))

    def test_automatic_passthrough_cycle_stops_at_real_controller_bound(self):
        nodes = {"talk": conversation("talk", [route("close", equals("answer_text", "done")), route("later")], passthrough="later"),
                 "later": conversation("later", [route("talk")], passthrough="talk")}
        # This changes the compiled controller allocation, never its validation.
        with patch("builder.guards.CONTROLLED_ENTRY_LIMIT", 8):
            workflow, context, api = compiled(nodes)
            report = replay(workflow, context, api, script(responses=[]), max_steps=100)
        self.assertEqual(report["status"], "terminated")
        self.assertEqual(report["terminal_node"], "bg_terminal")
        self.assertTrue(report["final_state"]["quarantined"])
        self.assertFalse(report["calls"])
        self.assertEqual(report["frames_consumed"], 0)
        self.assertLess(len(report["steps"]), 30)


if __name__ == "__main__":
    unittest.main()

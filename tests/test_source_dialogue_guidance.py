"""Source-only prompt transport and ideal-extraction pre-close speech ordering.

These synthetic checks do not execute a model or establish spoken compliance.
"""
from copy import deepcopy
import json
import unittest

from builder.build import make_policy_prompt, make_workflow_prompt
from builder.contract import compile_policy_contract
from builder.guards import compile_guarded_workflow
from builder.safety import digest
from development_protocol_samples import protocol_response
from test_call_variables import context as call_context, inputs
from tools.replay_guarded import replay


SPEECH = {
    "adjustment": "Please discuss a revised date before agreeing to changed terms.",
    "advice": "You can review these arrangements in the supplied customer channel.",
    "farewell": "Thank you for speaking with us. Goodbye.",
}


def source_context(*, speaking=False):
    policy, sources, api = inputs(())
    quote = json.dumps(SPEECH, ensure_ascii=False)
    source = {"id": "dialogue", "file": "synthetic-dialogue.txt", "text": quote,
              "sha256": digest(quote)}
    sources.append(source)
    policy["sources"].append({key: value for key, value in source.items() if key != "text"})
    policy["rules"].append({"id": "closing-speech", "kind": "closing_speech", "value": deepcopy(SPEECH),
                            "source": {"id": source["id"], "quote": quote}})
    disposition = {"failed": {"outcome": "not_verified", "reason": "identity_failed"},
                   "incomplete": {"outcome": "not_verified", "reason": "identity_failed"},
                   "unreached": {"outcome": "not_reached"}}
    closure_quote = "The synthetic source prescribes these identity dispositions: " + json.dumps(disposition)
    sources[0]["text"] += "\n" + closure_quote
    sources[0]["sha256"] = digest(sources[0]["text"])
    policy["sources"][0]["sha256"] = sources[0]["sha256"]
    policy["rules"].append({"id": "explicit-identity-close", "kind": "identity_close", "value": disposition,
                            "source": {"id": sources[0]["id"], "quote": closure_quote}})
    context = call_context(policy, sources, api)
    context["plan"]["nodes"]["talk"]["on_enter"] = {"type": "tts", "text": "Hello."}
    if speaking:
        context["plan"]["nodes"]["talk"]["transitions"][0]["target_node"] = "adjustment"
        context["plan"]["nodes"]["talk"]["max_turn_transition"] = "adjustment"
        names = list(SPEECH)
        for index, name in enumerate(names):
            target = names[index + 1] if index + 1 < len(names) else "close"
            context["plan"]["nodes"][name] = {
                "name": name, "node_type": "conversation", "task_message": "Speak the applicable source instruction.",
                "on_enter": {"type": "tts", "text": SPEECH[name]}, "max_node_turns": 1,
                "max_turn_transition": target,
                "transitions": [{"target_node": target, "description": "continue",
                                 "condition": {"operator": "always"}}]}
    return context, api, policy, sources


class SourceDialogueGuidanceTests(unittest.TestCase):
    def test_policy_prompt_agrees_with_typed_conditional_interface_using_approved_inputs_only(self):
        context, api, policy, sources = source_context()
        before = deepcopy((sources, api))
        prompt = make_policy_prompt(sources, api)
        header, data = prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)
        payload = json.loads(data)
        self.assertEqual(set(payload), {"sources", "openapi", "execution_interface"})
        self.assertEqual(payload["sources"], sources)
        self.assertEqual(payload["openapi"], api)
        shape = payload["execution_interface"]["rule_shapes"]["account_disclosure_fields"]
        for field in ("common", "by_product_type"):
            self.assertIn(field, shape)
            self.assertIn(field, header)
        self.assertIn("Do not flatten conditional fields", header)
        self.assertIn("explicit unsupported constraints", header)
        self.assertNotIn("Preserve conditional disclosure constraints separately", header)
        self.assertEqual((sources, api), before)

    def test_active_amendment_and_conditional_fields_reach_prompt_without_history_or_mutation(self):
        context, api, policy, sources = source_context()
        old = next(rule for rule in policy["rules"] if rule["kind"] == "closing_speech")
        old["value"] = {"farewell": "Historical superseded speech marker."}
        old_source = next(source for source in sources if source["id"] == "dialogue")
        old_source["text"] = json.dumps(old["value"])
        old_source["sha256"] = digest(old_source["text"])
        old["source"]["quote"] = old_source["text"]
        next(source for source in policy["sources"] if source["id"] == "dialogue")["sha256"] = old_source["sha256"]
        amendment = {"id": "dialogue-amendment", "file": "synthetic-amendment.txt",
                     "text": json.dumps(SPEECH, ensure_ascii=False)}
        amendment["sha256"] = digest(amendment["text"])
        sources.append(amendment)
        policy["sources"].append({key: value for key, value in amendment.items() if key != "text"})
        policy["rules"].append({"id": "updated-speech", "kind": "closing_speech", "value": deepcopy(SPEECH),
            "overrides": [old["id"]], "source": {"id": amendment["id"], "quote": amendment["text"]}})
        disclosure = {"common": ["past_due"], "by_product_type": {"revolving": ["days_late"], "fixed": ["instalment_due"]}}
        disclosure_quote = "For every product disclose past_due; for revolving also days_late, for fixed also instalment_due."
        sources[0]["text"] += "\n" + disclosure_quote
        sources[0]["sha256"] = digest(sources[0]["text"])
        policy["sources"][0]["sha256"] = sources[0]["sha256"]
        policy["rules"].append({"id": "display", "kind": "account_disclosure_fields", "value": disclosure,
                                "source": {"id": sources[0]["id"], "quote": disclosure_quote}})
        contract = compile_policy_contract(policy, sources, api)
        before = deepcopy(contract)
        prompt = make_workflow_prompt(contract, api)
        header, data = prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)
        payload = json.loads(data)
        self.assertEqual(payload["active_rules"]["closing_speech"], SPEECH)
        self.assertEqual(payload["active_rules"]["account_disclosure_fields"], disclosure)
        self.assertEqual(payload["policy_contract_sha256"], contract["sha256"])
        self.assertNotIn("Historical superseded speech marker.", prompt)
        self.assertTrue(all("text" not in source for source in payload["sources"]))
        self.assertEqual(contract, before)
        self.assertIn("ends the call silently", header)
        self.assertIn("before final commitment and closing", header)
        self.assertIn("by_product_type", payload["runtime_reference"])
        self.assertNotIn("Conditional disclosure permissions remain\nunsupported", prompt)

    def test_absent_speech_rule_gains_no_default_bank_advice_or_values(self):
        context, api, policy, sources = source_context()
        policy["rules"] = [rule for rule in policy["rules"] if rule["kind"] != "closing_speech"]
        sources = [source for source in sources if source["id"] != "dialogue"]
        policy["sources"] = [source for source in policy["sources"] if source["id"] != "dialogue"]
        contract = compile_policy_contract(policy, sources, api)
        prompt = make_workflow_prompt(contract, api)
        header, data = prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)
        payload = json.loads(data)
        self.assertNotIn("closing_speech", payload["active_rules"])
        self.assertNotIn("account_disclosure_fields", payload["active_rules"])
        for text in SPEECH.values():
            self.assertNotIn(text, prompt)
        self.assertIn("Do not\ninvent default advice or farewell requirements", header)
        self.assertIn("when the source does not", header)
        self.assertIn("Never place bg_facts in authored on_enter speech/TTS", payload["runtime_reference"])

    def test_authored_speaking_route_precedes_exact_silent_guarded_terminal(self):
        context, api, policy, sources = source_context(speaking=True)
        workflow = compile_guarded_workflow(openapi=api, **context)["workflow"]
        script = {"version": 1, "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"},
                  "frames": [{"node": name, "values": {}} for name in ["talk", *SPEECH]],
                  "responses": [{"operation": "close_call", "envelope": protocol_response("close_call")}]}
        report = replay(workflow, context, api, script)
        self.assertEqual(report["status"], "terminated")
        self.assertEqual([item["node"] for item in report["prompts"]], ["talk", *SPEECH])
        self.assertEqual([item["on_enter"]["text"] for item in report["prompts"]][1:], list(SPEECH.values()))
        order = [step["node"] for step in report["steps"]]
        self.assertLess(order.index("farewell"), order.index("close"))
        self.assertEqual(workflow["nodes"][order[-1]]["node_type"], "end_call")
        self.assertEqual([call["tool"] for call in report["calls"]], ["close_call"])
        self.assertEqual((report["unused_frames"], report["unused_responses"]), (0, 0))
        self.assertFalse(report["conversation_compliance_proven"])

    def test_frozen_speech_rule_alone_does_not_make_compiler_speak_it(self):
        context, api, policy, sources = source_context()
        workflow = compile_guarded_workflow(openapi=api, **context)["workflow"]
        report = replay(workflow, context, api, {"version": 1,
            "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"},
            "frames": [{"node": "talk", "values": {}}],
            "responses": [{"operation": "close_call", "envelope": protocol_response("close_call")}]})
        self.assertEqual(report["status"], "terminated")
        self.assertEqual([item["on_enter"] for item in report["prompts"]], [{"type": "tts", "text": "Hello."}])
        for phrase in SPEECH.values():
            self.assertNotIn(phrase, json.dumps(workflow))
        self.assertFalse(report["conversation_compliance_proven"])


if __name__ == "__main__":
    unittest.main()

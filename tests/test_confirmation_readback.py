"""Sourced read-back clauses and conversation language in the sealed confirmation.

Invented wording and synthetic packets only: no evaluator cases, model, bank or
platform I/O. Speech rendered in a declared language is not proven here.
"""
import ast
import copy
import json
import os
import unittest
from pathlib import Path

from builder.execution_requirements import ExecutionRequirementsError
from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.policy import PolicyError, compile_execution_policy, execution_rule_guidance
from builder.safety import digest, read_json
from builder.validation import validate_code, validate_workflow
from test_goal_requirements import compiled
from test_guard_attempt_scopes import WireSession, callback_packet
from test_guard_profiles import invented_policy

CLAUSE = "the invented call reaches only the contact already on the bank file"
LANGUAGE = "Görüşme dili Türkçedir"
QUESTION = "Bu randevuyu onaylıyor musunuz?"
LEGACY = "Please confirm these exact terms: {{bg_readback}}. Do you agree?"
WAIT = "Wait for a clear yes or no about the exact terms just read. Do not change terms, call tools, or treat another topic as agreement."
MEMO = "Invented wording memo: also state that " + CLAUSE + ". " + LANGUAGE + ". Ask: " + QUESTION
RUNS = os.environ.get("FREYA_READBACK_RUNS")  # JSON {retained run directory: its OpenAPI file}


def packet(value, memo=MEMO, question=False, **kwargs):
    policy, sources, api = callback_packet(opaque=True, **kwargs)
    sources.append({"id": "wording", "file": "invented-wording.txt", "text": memo, "sha256": digest(memo)})
    policy["sources"].append({key: item for key, item in sources[-1].items() if key != "text"})
    rules = {"confirmation_readback": value}
    if question:
        rules.update(confirmation_question=QUESTION, confirmation_question_actions=["schedule_callback"])
    for kind, item in rules.items():
        policy["rules"].append({"id": kind, "kind": kind, "value": copy.deepcopy(item),
                                "source": {"id": "wording", "quote": memo}})
    return policy, sources, api


def confirm_node(workflow):
    return next(node for name, node in workflow["nodes"].items() if name.startswith("bg_") and name.endswith("_confirm"))


def code_sizes(workflow):
    sizes = {}
    for name, node in workflow["nodes"].items():
        if node["node_type"] == "code":
            code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
            sizes[name] = (len(list(ast.walk(ast.parse(code)))), len(code), code)
    return sizes


def rehash(value):
    value["sha256"] = digest({key: item for key, item in value.items() if key != "sha256"})


def validation(context, workflow):
    return validate_workflow(workflow, context["openapi"], guard_context={
        key: item for key, item in context.items() if key != "openapi"})


class ConfirmationReadbackTests(unittest.TestCase):
    def session(self, value, **kwargs):
        _, context, workflow = compiled(*packet(value, **kwargs))
        # The shared synthetic plan carries one authored-routing note; wording adds none.
        _, plain_context, plain = compiled(*callback_packet(opaque=True))
        self.assertEqual(validation(context, workflow), validation(plain_context, plain))
        return context, workflow, WireSession(context, workflow)

    def assert_consent_guards(self, s):
        s.action("schedule_callback", date="2031-05-06", time="12:00")
        self.assertEqual((s.variables["bg_route"], s.variables["bg_readback"]), ("confirm", "Callback on 2031-05-06 at 12:00"))
        for answer in ("unclear", "no"):
            s.confirm("schedule_callback", answer)
            self.assertEqual((s.variables["bg_route"], s.variables["bg_body"], s.state["attempts"]), ("blocked", "", {}))
            s.action("schedule_callback", date="2031-05-06", time="12:00")
        s.confirm("schedule_callback", "yes")
        self.assertEqual(s.variables["bg_route"], "allow")
        # The wording adds no destination or other field: only the sealed request is sent.
        self.assertEqual(s.body, {"session_ref": "SYNTHETIC-GOAL-SESSION", "pay_on": "2031-05-06", "at": "12:00"})

    def test_absent_rule_keeps_the_legacy_confirmation(self):
        _, context, workflow = compiled(*callback_packet(opaque=True))
        self.assertNotIn("confirmation_readback", context["execution_policy"]["profile"])
        node = confirm_node(workflow)
        self.assertEqual((node["on_enter"], node["task_message"]), ({"type": "tts", "text": LEGACY}, WAIT))

    def test_sourced_clause_is_spoken_verbatim_after_the_protected_terms(self):
        context, workflow, s = self.session({"actions": ["schedule_callback"], "clauses": [CLAUSE]})
        node = confirm_node(workflow)
        self.assertEqual(node["on_enter"], {"type": "tts", "text": "Please confirm these exact terms: {{bg_readback}}; " + CLAUSE + ". Do you agree?"})
        self.assertEqual(node["task_message"], WAIT)
        self.assert_consent_guards(s)
        _, workflow, _ = self.session({"actions": ["schedule_callback"], "clauses": [CLAUSE]}, question=True)
        self.assertEqual(confirm_node(workflow)["on_enter"], {"type": "tts", "text": "{{bg_readback}}; " + CLAUSE + ". " + QUESTION})

    def test_declared_language_speaks_the_same_protected_terms_and_guards(self):
        legacy = confirm_node(compiled(*callback_packet(opaque=True))[2])
        value = {"actions": ["schedule_callback"], "clauses": [CLAUSE], "language": LANGUAGE}
        for question in (False, True):
            with self.subTest(question=question):
                context, workflow, s = self.session(value, question=question)
                node = confirm_node(workflow)
                self.assertEqual(node["on_enter"], {"type": "llm"})
                task = node["task_message"]
                self.assertIn('"' + LANGUAGE + '"', task)
                self.assertIn("{{bg_readback}}; " + CLAUSE + ". Then ask ", task)
                self.assertIn('this question: "' + QUESTION + '"' if question else "whether the caller agrees to exactly these terms", task)
                self.assertTrue(task.endswith(WAIT))
                self.assertNotIn("Please confirm", json.dumps(node, ensure_ascii=False))
                for key in set(legacy) - {"on_enter", "task_message"}:
                    self.assertEqual(node[key], legacy[key])
                self.assert_consent_guards(s)

    def test_unsourced_wording_is_rejected_at_extraction_and_in_the_sealed_compiler(self):
        for value in ({"actions": ["schedule_callback"], "clauses": [CLAUSE + " today"]},
                      {"actions": ["schedule_callback"], "language": "Invented other language"}):
            with self.subTest(value=value), self.assertRaisesRegex(ExecutionRequirementsError, "wording must appear literally"):
                compiled(*packet(value))
        _, context, _ = compiled(*packet({"actions": ["schedule_callback"], "clauses": [CLAUSE], "language": LANGUAGE}))
        for tamper in ("quote", "value"):
            changed = copy.deepcopy(context)
            rule = changed["contract"]["active_rules"]["confirmation_readback"]
            if tamper == "quote":
                rule["provenance"][0]["quote"] = rule["provenance"][0]["quote"].replace(CLAUSE, "an invented other fact")
            else:
                for holder in (rule, changed["execution_policy"]["profile"]["confirmation_readback"]):
                    target = holder["value"] if holder is rule else holder
                    target["clauses"] = ["an unquoted invented fact"]
            rehash(changed["contract"])
            rehash(changed["execution_policy"])
            with self.subTest(tamper=tamper), self.assertRaisesRegex(GuardCompileError, "occur literally"):
                compile_guarded_workflow(**changed)

    def test_value_bearing_templated_or_open_wording_cannot_freeze(self):
        good = {"actions": ["schedule_callback"], "clauses": [CLAUSE]}
        texts = ("call back on 2031-05-06", "to 0212 555 0100", "within ٣ days", "{{phone}}", "a {phone}",
                 "read bg_state", "AUTH_value", "bank_TOKEN", "a\nb", "a\x1bb", "", " \t", 1, None)
        values = [dict(good, clauses=[text]) for text in texts] + [dict(good, language=text) for text in texts]
        values += [{}, [], "schedule_callback", {"actions": ["schedule_callback"]}, {"clauses": [CLAUSE]},
                   dict(good, actions=[]), dict(good, actions=["schedule_callback", "schedule_callback"]),
                   dict(good, actions=["create_promise"]), dict(good, actions="schedule_callback"),
                   dict(good, clauses=[]), dict(good, clauses=CLAUSE), dict(good, clauses=[CLAUSE, CLAUSE]),
                   dict(good, destination="bank_held"), dict(good, fields=["phone"])]
        for value in values:
            with self.subTest(value=value), self.assertRaises(PolicyError):
                compile_execution_policy(invented_policy({"confirmation_readback": value}))
        for value in (good, {"actions": ["commit_promises", "schedule_callback"], "language": LANGUAGE}):
            self.assertEqual(compile_execution_policy(invented_policy({"confirmation_readback": value}))["profile"]["confirmation_readback"], value)
        self.assertIn("no digits", execution_rule_guidance()["confirmation_readback"])

    def test_a_selected_named_action_must_require_confirmation(self):
        with self.assertRaises(ExecutionRequirementsError) as raised:
            compiled(*packet({"actions": ["schedule_callback"], "clauses": [CLAUSE]}, confirmation=False))
        self.assertIn("requires no confirmation", str(raised.exception))
        _, context, workflow = compiled(*packet({"actions": ["commit_transfer"], "clauses": [CLAUSE]}))
        self.assertEqual(confirm_node(workflow)["on_enter"], {"type": "tts", "text": LEGACY})

    def test_authored_copies_gain_no_readback_privilege(self):
        _, context, workflow = compiled(*packet({"actions": ["schedule_callback"], "language": LANGUAGE}))
        task = confirm_node(workflow)["task_message"]
        unsealed = validate_workflow(workflow, context["openapi"])
        self.assertTrue(any("_confirm.task_message: authored text cannot read raw protected" in error for error in unsealed))
        tampered = copy.deepcopy(workflow)
        confirm_node(tampered)["task_message"] = task + " {{customer_id}}"
        self.assertIn("guarded workflow differs from exact controller compilation (code, bodies, mappings, routing or protected writers)",
                      validation(context, tampered))
        authored = copy.deepcopy(context)
        talk = next(node for node in authored["plan"]["nodes"].values() if node["node_type"] == "conversation")
        talk["task_message"] = task
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**authored)

    def test_wording_adds_no_sealed_code(self):
        plain = code_sizes(compiled(*callback_packet(opaque=True))[2])
        worded = code_sizes(compiled(*packet({"actions": ["schedule_callback"], "clauses": [CLAUSE], "language": LANGUAGE}))[2])
        self.assertEqual(set(plain), set(worded))
        for name, (nodes, chars, code) in worded.items():
            self.assertEqual(nodes, plain[name][0])
            self.assertLess(chars - plain[name][1], 600)
            self.assertEqual(validate_code(code, name), [])


@unittest.skipUnless(RUNS, "retained run artifacts are local only")
class RetainedPlanReadbackSize(unittest.TestCase):
    """Recompile retained plans with invented wording; the validator's code limits still hold."""

    def test_retained_plans_stay_within_code_limits(self):
        for run, api in json.loads(RUNS).items():
            with self.subTest(run=Path(run).name):
                manifest = read_json(Path(run) / "manifest.json")
                saved = read_json(Path(run) / manifest["selected_attempt_directory"] / "guard-context.json")
                context = {key: saved[key] for key in ("plan", "contract", "execution_policy", "bindings")}
                context["openapi"] = read_json(Path(api))
                plain = code_sizes(compile_guarded_workflow(**context)["workflow"])
                profile = context["execution_policy"]["profile"]
                actions = sorted(set(profile.get("confirmation_actions", []))
                                 | ({"commit_promises"} if profile.get("require_confirmation") is True else set()))
                value = {"actions": actions, "clauses": [CLAUSE], "language": LANGUAGE}
                profile["confirmation_readback"] = copy.deepcopy(value)
                context["contract"]["active_rules"]["confirmation_readback"] = {
                    "value": copy.deepcopy(value), "rule_ids": ["readback"], "superseded_rule_ids": [], "superseded_provenance": [],
                    "provenance": [{"rule_id": "readback", "id": "wording", "quote": MEMO}]}
                rehash(context["contract"])
                rehash(context["execution_policy"])
                workflow = compile_guarded_workflow(**context)["workflow"]
                worded = code_sizes(workflow)
                for name, (nodes, chars, code) in worded.items():
                    self.assertEqual(nodes, plain[name][0])
                    self.assertLessEqual(nodes, 16000)
                    self.assertLessEqual(chars, 100000)
                    self.assertEqual(validate_code(code, name), [])
                self.assertTrue(all(node["on_enter"] == {"type": "llm"} for name, node in workflow["nodes"].items()
                                    if name.startswith("bg_") and name.endswith("_confirm")))


if __name__ == "__main__":
    unittest.main()

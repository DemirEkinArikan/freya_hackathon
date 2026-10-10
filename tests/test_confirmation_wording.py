"""Source-exact question transport without changing protected agreement semantics."""
from copy import deepcopy
import json
import unittest

from builder.contract import compile_policy_contract, PolicyContractError
from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.policy import PolicyError, compile_execution_policy, execution_rule_guidance
from builder.safety import digest
from builder.validation import validate_workflow
from test_guard_profiles import invented_policy
from test_replay_guarded import authored, scenario
from tools.replay_guarded import replay


ABSENT = object()


def quoted_context(question=ABSENT, quote=None):
    workflow, context, api, aliases = authored()
    if question is ABSENT:
        return workflow, context, api, aliases
    profile = deepcopy(context["execution_policy"]["profile"])
    profile["confirmation_question"] = question
    text = 'After the protected read-back, ask exactly: "' + (quote if quote is not None else str(question)) + '".'
    witnesses = [dict(id="base", file="independent-policy.txt", text="Invented independent policy witness."),
                 dict(id="wording", file="independent-wording.txt", text=text)]
    for source in witnesses:
        source["sha256"] = digest(source["text"])
    policy = {"version": 1, "goal": "Confirm permitted synthetic terms",
              "allowed_actions": [operation["name"] for operation in context["contract"]["permitted_operations"]],
              "sources": [{key: value for key, value in source.items() if key != "text"} for source in witnesses],
              "rules": [dict(id=kind, kind=kind, value=value, source=dict(
                  id="wording" if kind == "confirmation_question" else "base",
                  quote=text if kind == "confirmation_question" else witnesses[0]["text"]))
                        for kind, value in profile.items()], "unsupported_constraints": []}
    context["contract"] = compile_policy_contract(policy, witnesses, api)
    context["execution_policy"] = compile_execution_policy(policy)
    workflow = compile_guarded_workflow(openapi=api, **context)["workflow"]
    return workflow, context, api, aliases


def code(workflow, variables, node):
    source = workflow["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    exec(source, {"vars": variables})


def result(workflow, variables, action, data):
    node = workflow["nodes"]["bg_" + action + "_api"]
    envelope = {"status_code": 200, "data": deepcopy(data)}
    exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
    for mapping in node["response_mapping"]:
        variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
    code(workflow, variables, "bg_" + action + "_result")


class ConfirmationWordingTests(unittest.TestCase):
    def test_absent_rule_preserves_legacy_text_without_new_requirement(self):
        workflow, context, api, aliases = quoted_context()
        self.assertNotIn("confirmation_question", context["execution_policy"]["profile"])
        self.assertEqual(workflow["nodes"]["bg_commit_confirm"]["on_enter"], {
            "type": "tts", "text": "Please confirm these exact terms: {{bg_readback}}. Do you agree?"})
        self.assertEqual(validate_workflow(workflow, api, guard_context=context), [])

    def test_quoted_english_and_turkish_questions_are_preserved_exactly(self):
        for question in ("Do you confirm these exact arrangements?", "Bu koşulları aynen onaylıyor musunuz?"):
            with self.subTest(question=question):
                workflow, context, api, aliases = quoted_context(question)
                self.assertEqual(context["contract"]["active_rules"]["confirmation_question"]["value"], question)
                self.assertIn(question, context["contract"]["active_rules"]["confirmation_question"]["provenance"][0]["quote"])
                self.assertEqual(workflow["nodes"]["bg_commit_confirm"]["on_enter"], {"type": "tts", "text": "{{bg_readback}}. " + question})
                self.assertEqual(validate_workflow(workflow, api, guard_context=context), [])
                report = replay(workflow, context, api, scenario(context, aliases))
                self.assertEqual(report["status"], "terminated")
                self.assertEqual(sum(call["tool"] == "create_promise" for call in report["calls"]), 1)
                self.assertFalse(report["conversation_compliance_proven"])

    def test_unsourced_invalid_or_templated_questions_are_rejected(self):
        with self.assertRaises(PolicyContractError):
            quoted_context("A rewritten question?", quote="The actual prescribed question?")
        for question in (None, False, 1, [], {}, "", " \t", "Confirm {{bg_state}}?", "Confirm {{BANK_TOKEN}}?", "{{first_name}} confirm?",
                         "Confirm\x1bthis?", "Confirm\nthis?", "Confirm BG_state?", "Confirm auth_secret?", "Confirm bank_TOKEN?"):
            policy = invented_policy({"confirmation_question": question})
            policy["rules"][0]["source"]["quote"] += " " + str(question)
            with self.subTest(question=question), self.assertRaises(PolicyError):
                compile_execution_policy(policy)
        self.assertIn("variable templates", execution_rule_guidance()["confirmation_question"])

    def test_frozen_prescribed_question_cannot_be_silently_omitted(self):
        workflow, context, api, aliases = quoted_context("Bu işlem için onayınız var mı?")
        del context["execution_policy"]["profile"]["confirmation_question"]
        context["execution_policy"]["sha256"] = digest({key: value for key, value in context["execution_policy"].items() if key != "sha256"})
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(openapi=api, **context)

    def test_new_question_keeps_stale_unclear_and_immutable_request_guards(self):
        workflow, context, api, aliases = quoted_context("Bu koşulları onaylıyor musunuz?")
        script = scenario(context, aliases)
        variables = deepcopy(script["initial_inputs"])
        code(workflow, variables, "bg_init")
        for index, field in enumerate(context["execution_policy"]["profile"]["identity_fields"]):
            variables.update(answer_field=field, answer_text="independent-answer")
            code(workflow, variables, "identity")
            result(workflow, variables, "identity", script["responses"][index]["envelope"]["data"])
        code(workflow, variables, "account")
        result(workflow, variables, "account", script["responses"][2]["envelope"]["data"])
        variables.update(product="synthetic-product", amount="20.00", day="2031-05-06")
        code(workflow, variables, "stage")
        variables["bg_consent"] = "yes"  # An older answer must never authorize this read-back.
        code(workflow, variables, "commit")
        self.assertEqual(variables["bg_route"], "confirm")
        self.assertEqual((variables["bg_consent"], variables["bg_body"]), ("", ""))
        variables["bg_consent"] = "unclear"
        code(workflow, variables, "bg_commit_accept")
        self.assertEqual((variables["bg_route"], variables["bg_body"]), ("blocked", ""))
        self.assertFalse(json.loads(variables["bg_state"])["attempts"])
        code(workflow, variables, "commit")
        variables.update(bg_consent="yes", amount="77.77", day="2031-05-07")
        code(workflow, variables, "bg_commit_accept")
        self.assertEqual(variables["bg_route"], "allow")
        self.assertEqual(json.loads(variables["bg_body"]), {"customer_id": "synthetic-session", "product_id": "synthetic-product", "amount": 20, "date": "2031-05-06"})

    def test_authored_copy_or_untrusted_custom_readback_has_no_privilege(self):
        workflow, context, api, aliases = quoted_context("Is this exact arrangement confirmed?")
        self.assertTrue(validate_workflow(workflow, api))
        copied = deepcopy(workflow)
        copied["nodes"]["talk"]["on_enter"] = deepcopy(copied["nodes"]["bg_commit_confirm"]["on_enter"])
        self.assertTrue(validate_workflow(copied, api, guard_context=context))
        context["plan"]["nodes"]["talk"]["on_enter"] = deepcopy(copied["nodes"]["talk"]["on_enter"])
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(openapi=api, **context)
        workflow, context, api, aliases = quoted_context("Is this exact arrangement confirmed?")
        workflow["nodes"]["bg_commit_confirm"]["on_enter"]["text"] += " {{customer_id}}"
        self.assertTrue(validate_workflow(workflow, api, guard_context=context))


if __name__ == "__main__":
    unittest.main()

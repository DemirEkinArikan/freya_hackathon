"""Local bookkeeping remains independent from optional bank note writes."""
import copy
import json
import unittest

from builder.action_bindings import ActionBindingError, checked_action_bindings, compile_action_bindings
from builder.contract import compile_policy_contract
from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.safety import digest
from builder.validation import validate_workflow
from test_guard_aliases import renamed_context


def local_context(record_notes=False):
    context, aliases = renamed_context()
    profile = copy.deepcopy(context["execution_policy"]["profile"])
    profile["record_notes"] = record_notes
    del profile["tool_aliases"]["add_note"]
    text = "Invented policy: dispositions are local; bank notes are prohibited."
    source = {"id": "local", "file": "local.txt", "text": text, "sha256": digest(text)}
    permitted = [op["name"] for op in context["contract"]["permitted_operations"] if op["name"] != aliases["add_note"]]
    policy = {"version": 1, "goal": "Resolve invented accounts with local dispositions", "allowed_actions": permitted,
              "sources": [{k: v for k, v in source.items() if k != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": "local", "quote": text}}
                        for kind, value in profile.items()], "unsupported_constraints": []}
    # The API may expose a note service whose enum is unrelated to our local
    # dispositions. Policy forbids using it, so no enum proof may be invented.
    note_schema = context["openapi"]["paths"]["/add_note"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    note_schema["properties"]["input_reason"]["enum"] = ["API_ONLY_CODE"]
    context["contract"] = compile_policy_contract(policy, [source], context["openapi"])
    if record_notes is False:
        binding = compile_action_bindings(policy, context["openapi"])
        execution = {"version": 1, "policy_sha256": digest(policy), "profile": profile, "bindings_sha256": binding["sha256"]}
        execution["sha256"] = digest(execution)
        context.update(bindings=binding, execution_policy=execution)
    return context, policy, aliases


class LocalDispositionTests(unittest.TestCase):
    def setUp(self):
        self.context, self.policy, self.aliases = local_context()
        self.workflow = compile_guarded_workflow(**self.context)["workflow"]
        self.variables = {"customer_id": "private-call-id", "now": "2031-05-02"}
        self.code("bg_init")
        for field in ("challenge_A", "challenge_B"):
            self.variables.update(answer_field=field, answer_text="answer")
            self.code("identity")
            self.result("identity", {"evidence_ok": True, "evidence_verified": True, "evidence_wrong_answers_left": 4})
        self.code("account")
        self.result("account", {"evidence_ok": True, "evidence_products": [
            {"asset_reference": "asset-first", "kind_wire": "KIND_A", "delay_marker": 8, "arrears_value": 300, "floor_alpha": 37.25},
            {"asset_reference": "asset-next", "kind_wire": "KIND_B", "delay_marker": 28, "arrears_value": 600, "floor_beta": 82.10}]})

    def code(self, node):
        code = self.workflow["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(code, {"vars": self.variables})

    def result(self, entry, data):
        node = self.workflow["nodes"]["bg_" + entry + "_api"]
        result = {"status_code": 200, "data": copy.deepcopy(data)}
        exec(node["pre_actions"][0]["post_process_code"], {"result": result})
        for mapping in node["response_mapping"]:
            self.variables[mapping["variable"]] = result["data"].get(mapping["path"][5:], "")
        self.code("bg_" + entry + "_result")

    def note(self, product, reason):
        self.variables.update(product=product, note_reason=reason)
        self.code("note")

    def test_six_permitted_operations_local_category_has_no_false_api_proof(self):
        category = self.context["bindings"]["categories"]["notes"]
        self.assertEqual(set(category), {"binding_kind", "semantic_to_wire", "wire_to_semantic"})
        self.assertEqual(category["binding_kind"], "local_disposition")
        self.assertEqual(self.context["bindings"]["openapi_sha256"], digest(self.context["openapi"]))
        self.assertEqual(checked_action_bindings(self.policy, self.context["bindings"]), self.context["bindings"])
        self.assertEqual(len(self.context["contract"]["permitted_operations"]), 6)
        self.assertNotIn("bg_finish_api", self.workflow["nodes"])
        self.assertEqual(validate_workflow(self.workflow, self.context["openapi"]), [])

    def test_local_refusal_closes_with_opaque_reason_without_note_calls(self):
        self.note("asset-first", "NOTE_R")
        self.note("asset-next", "NOTE_P")
        self.code("finish")
        self.assertEqual(self.variables["bg_route"], "done")
        self.code("close")
        self.assertEqual(json.loads(self.variables["bg_body"])["input_outcome"], "C_D")
        self.assertEqual(json.loads(self.variables["bg_body"])["input_reason"], "WHY_R")
        self.assertFalse(json.loads(self.variables["bg_state"])["recorded_notes"])

    def test_all_paid_local_dispositions_close_as_paid(self):
        self.note("asset-first", "NOTE_P")
        self.note("asset-next", "NOTE_P")
        self.code("finish")
        self.code("close")
        body = json.loads(self.variables["bg_body"])
        self.assertEqual(body["input_outcome"], "C_B")
        self.assertNotIn("input_reason", body)

    def test_human_handoff_after_finalized_local_dispositions_discards_them(self):
        self.note("asset-first", "NOTE_R")
        self.note("asset-next", "NOTE_R")
        self.code("finish")
        self.variables["queue"] = "QUEUE_S"
        self.code("transfer")
        self.assertEqual(self.variables["bg_route"], "done")
        state = json.loads(self.variables["bg_state"])
        self.assertFalse(state["pending_notes"])
        self.assertFalse(state["notes_finalized"])
        self.code("handoff")
        self.result("handoff", {"evidence_ok": True})
        self.code("close")
        self.assertEqual(json.loads(self.variables["bg_body"])["input_outcome"], "C_C")

    def test_handoff_after_local_finalization_preserves_recorded_promise(self):
        self.variables.update(product="asset-first", amount="50", day="2031-05-06")
        self.code("stage")
        self.code("commit")
        self.variables["bg_consent"] = "yes"
        self.code("bg_commit_accept")
        self.result("commit", {"evidence_ok": True, "evidence_promise_id": "saved-one"})
        self.note("asset-next", "NOTE_R")
        self.code("finish")
        self.variables["queue"] = "QUEUE_S"
        self.code("transfer")
        self.assertIn("asset-first", json.loads(self.variables["bg_state"])["recorded_promises"])

    def test_actual_note_policy_cannot_use_local_category_or_unpermitted_service(self):
        context, policy, unused = local_context(record_notes=True)
        with self.assertRaises(ActionBindingError):
            compile_action_bindings(policy, context["openapi"])
        del policy["rules"][next(i for i, rule in enumerate(policy["rules"]) if rule["kind"] == "record_notes")]
        with self.assertRaises(ActionBindingError):
            compile_action_bindings(policy, context["openapi"])
        regular, unused = renamed_context()
        self.assertIn("operation", regular["bindings"]["categories"]["notes"])
        self.assertNotIn("binding_kind", regular["bindings"]["categories"]["notes"])

    def test_local_descriptor_rejects_extra_api_claims_and_modified_mappings(self):
        for mutation in (lambda category: category.update(operation={"name": self.aliases["add_note"]}),
                         lambda category: category["semantic_to_wire"].update(refused="CHANGED")):
            bindings = copy.deepcopy(self.context["bindings"])
            mutation(bindings["categories"]["notes"])
            bindings["sha256"] = digest({k: v for k, v in bindings.items() if k != "sha256"})
            with self.assertRaises(ActionBindingError):
                checked_action_bindings(self.policy, bindings)
            context = copy.deepcopy(self.context)
            context["bindings"] = bindings
            context["execution_policy"]["bindings_sha256"] = bindings["sha256"]
            context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
            with self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context)

    def test_checked_local_category_is_invalid_when_recording_absent_or_true(self):
        for remove in (False, True):
            policy = copy.deepcopy(self.policy)
            index = next(i for i, rule in enumerate(policy["rules"]) if rule["kind"] == "record_notes")
            if remove:
                del policy["rules"][index]
            else:
                policy["rules"][index]["value"] = True
            binding = copy.deepcopy(self.context["bindings"])
            binding["policy_sha256"] = digest(policy)
            binding["sha256"] = digest({k: v for k, v in binding.items() if k != "sha256"})
            with self.assertRaises(ActionBindingError):
                checked_action_bindings(policy, binding)


if __name__ == "__main__":
    unittest.main()

"""Synthetic regressions for dependencies of supplied failure dispositions.

No generation, evaluator inputs, or hosted calls are used. A local disposition
is policy bookkeeping and must never authorize an otherwise forbidden API.
"""
import copy
import json
import unittest

from builder.action_bindings import compile_action_bindings
from builder.contract import compile_policy_contract
from builder.guards import (GuardCompileError, _profile, compile_guarded_workflow,
                            required_execution_kinds)
from builder.policy import compile_execution_policy
from builder.safety import digest
from test_guards import fixture
from test_local_dispositions import local_context


ERROR = "supplied promise failure disposition requires its explicit note_reasons/local-disposition binding"


def without_note_actions(plan):
    plan = copy.deepcopy(plan)
    removed = {name for name, node in plan["nodes"].items()
               if node.get("action") in {"stage_note", "finalize_notes"}}
    for name in removed:
        del plan["nodes"][name]
    for node in plan["nodes"].values():
        if "transitions" in node:
            node["transitions"] = [edge for edge in node["transitions"] if edge["target_node"] not in removed]
    return plan


def rebuild(context, profile, *, bindings=False, note_actions=False):
    context = copy.deepcopy(context)
    if not note_actions:
        context["plan"] = without_note_actions(context["plan"])
    text = "Independent synthetic policy for supplied failure-disposition dependencies."
    source = {"id": "synthetic", "file": "synthetic-dispositions.txt", "text": text, "sha256": digest(text)}
    policy = {"version": 1, "goal": "Resolve synthetic account actions",
              "allowed_actions": [operation["name"] for operation in context["contract"]["permitted_operations"]],
              "sources": [{k: v for k, v in source.items() if k != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": copy.deepcopy(value),
                         "source": {"id": "synthetic", "quote": text}}
                        for kind, value in profile.items()], "unsupported_constraints": []}
    context["contract"] = compile_policy_contract(policy, [source], context["openapi"])
    binding = compile_action_bindings(policy, context["openapi"]) if bindings else None
    context["execution_policy"] = compile_execution_policy(
        policy, binding, required_kinds=required_execution_kinds(context["plan"]))
    if binding is not None:
        context["bindings"] = binding
    else:
        context.pop("bindings", None)
    return context


class LocalSession:
    """Execute exact code nodes and mapped envelopes from a compiled workflow."""
    def __init__(self, context):
        self.workflow = compile_guarded_workflow(**context)["workflow"]
        self.variables = {"customer_id": "SYNTHETIC-CALL", "now": "2031-05-02"}
        self.code("bg_init")

    def code(self, name):
        code = self.workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(code, {"vars": self.variables})

    def result(self, name, data, status=200):
        node = self.workflow["nodes"]["bg_" + name + "_api"]
        envelope = {"status_code": status, "data": copy.deepcopy(data)}
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            self.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
        self.code("bg_" + name + "_result")

    def promise(self):
        for field in ("challenge_A", "challenge_B"):
            self.variables.update(answer_field=field, answer_text="synthetic-answer")
            self.code("identity")
            self.result("identity", {"evidence_ok": True, "evidence_verified": True, "evidence_wrong_answers_left": 4})
        self.code("account")
        self.result("account", {"evidence_ok": True, "evidence_products": [
            {"asset_reference": "SYNTHETIC-PRODUCT", "kind_wire": "KIND_A",
             "delay_marker": 8, "arrears_value": 300, "floor_alpha": 37.25}]})
        self.variables.update(product="SYNTHETIC-PRODUCT", amount="50.00", day="2031-05-06")
        self.code("stage")
        self.code("commit")
        self.variables["bg_consent"] = "yes"
        self.code("bg_commit_accept")

    def failure(self):
        self.result("commit", {"evidence_ok": False, "evidence_error": "ERR_BUSY"}, status=503)

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])


class SuppliedDispositionTests(unittest.TestCase):
    def test_callback_and_identity_plans_preserve_unused_disposition_without_commit_dependencies(self):
        base, _, aliases = local_context()
        commitment_inputs = {"require_confirmation", "confirmation_scope", "promise_failure_limit", "promise_readback"}
        for selected in ({"callback", "close"}, {"identity", "close"}, {"callback", "identity", "close"}):
            with self.subTest(selected=selected):
                narrowed = copy.deepcopy(base)
                narrowed["plan"]["nodes"] = {name: node for name, node in narrowed["plan"]["nodes"].items()
                                              if name == "talk" or name in selected}
                narrowed["plan"]["nodes"]["talk"]["transitions"] = [edge for edge in narrowed["plan"]["nodes"]["talk"]["transitions"]
                    if edge["target_node"] in narrowed["plan"]["nodes"]]
                profile = copy.deepcopy(base["execution_policy"]["profile"])
                for kind in commitment_inputs | {"nonpersistent_failures", "note_reasons"}:
                    del profile[kind]
                context = rebuild(narrowed, profile, bindings=True)
                required = set(required_execution_kinds(context["plan"]))
                self.assertFalse(required & commitment_inputs)
                self.assertNotIn("notes", context["bindings"]["categories"])
                self.assertNotIn("note_reasons", context["execution_policy"]["profile"])
                runtime = _profile(context["contract"], context["execution_policy"], context["plan"], context["bindings"])
                self.assertEqual(runtime["promise_failure_disposition"], profile["promise_failure_disposition"])
                session = LocalSession(context)
                api_names = {node["pre_actions"][0]["function"]["name"] for node in session.workflow["nodes"].values()
                             if node["node_type"] == "api_request"}
                self.assertNotIn(aliases["add_note"], api_names)
                self.assertNotIn(aliases["create_promise"], api_names)
                if "callback" in selected:
                    session.variables.update(day="2031-05-06", at="12:00")
                    session.code("callback")
                    self.assertEqual(session.variables["bg_route"], "allow")
                    session.result("callback", {"evidence_ok": True})
                    self.assertEqual(len(session.state["callbacks"]), 1)
                if "identity" in selected:
                    session.variables.update(answer_field="challenge_A", answer_text="synthetic-answer")
                    session.code("identity")
                    session.result("identity", {"evidence_ok": True, "evidence_verified": False,
                                                "evidence_wrong_answers_left": 0})
                    self.assertEqual(session.state["identity_status"], "failed")
                    expected_outcome = "C_E"
                else:
                    session.variables.update(outcome="C_F", close_reason="")
                    expected_outcome = "C_F"
                session.code("close")
                self.assertEqual(session.variables["bg_route"], "allow")
                self.assertEqual(json.loads(session.variables["bg_body"])["input_outcome"], expected_outcome)
                self.assertFalse(session.state["pending_notes"])
                self.assertFalse(session.state["recorded_promises"])

    def test_selecting_commit_activates_dependency_in_same_frozen_profile(self):
        base, _, _ = local_context()
        narrowed = copy.deepcopy(base)
        narrowed["plan"]["nodes"] = {name: node for name, node in narrowed["plan"]["nodes"].items()
                                      if name in {"talk", "identity", "callback", "close"}}
        narrowed["plan"]["nodes"]["talk"]["transitions"] = [edge for edge in narrowed["plan"]["nodes"]["talk"]["transitions"]
            if edge["target_node"] in narrowed["plan"]["nodes"]]
        profile = copy.deepcopy(base["execution_policy"]["profile"])
        del profile["note_reasons"]
        context = rebuild(narrowed, profile, bindings=True)
        compile_guarded_workflow(**context)
        before = copy.deepcopy(context)
        context["plan"]["nodes"]["commit"] = copy.deepcopy(base["plan"]["nodes"]["commit"])
        with self.assertRaises(GuardCompileError) as error:
            compile_guarded_workflow(**context)
        self.assertEqual(error.exception.errors, (ERROR,))
        self.assertEqual(context["contract"], before["contract"])
        self.assertEqual(context["execution_policy"], before["execution_policy"])
        self.assertEqual(context["bindings"], before["bindings"])

    def test_active_disposition_requires_notes_even_when_no_note_action_selected(self):
        context = fixture()
        profile = copy.deepcopy(context["execution_policy"]["profile"])
        del profile["note_reasons"]
        context = rebuild(context, profile)
        self.assertNotIn("note_reasons", required_execution_kinds(context["plan"]))
        self.assertIn("promise_failure_disposition", context["contract"]["active_rules"])
        self.assertTrue(profile["record_notes"])
        with self.assertRaises(GuardCompileError) as error:
            compile_guarded_workflow(**context)
        self.assertEqual(error.exception.errors, (ERROR,))

    def test_local_only_missing_or_wrong_reason_rejected_without_note_permission(self):
        base, _, aliases = local_context()
        for reasons in (None, ["NOTE_R"], ["NOTE_P", "NOTE_D"]):
            with self.subTest(reasons=reasons):
                profile = copy.deepcopy(base["execution_policy"]["profile"])
                if reasons is None:
                    del profile["note_reasons"]
                else:
                    profile["note_reasons"] = reasons
                context = rebuild(base, profile, bindings=True)
                self.assertFalse(profile["record_notes"])
                self.assertNotIn("note_reasons", required_execution_kinds(context["plan"]))
                self.assertNotIn(aliases["add_note"], {op["name"] for op in context["contract"]["permitted_operations"]})
                with self.assertRaises(GuardCompileError) as error:
                    compile_guarded_workflow(**context)
                self.assertEqual(error.exception.errors, (ERROR,))

    def test_decoded_reason_can_use_note_or_close_wire_code_without_changing_binding(self):
        base, _, aliases = local_context()
        for reason in ("NOTE_S", "WHY_S"):
            with self.subTest(reason=reason):
                profile = copy.deepcopy(base["execution_policy"]["profile"])
                profile["note_reasons"] = ["NOTE_S"]
                profile["promise_failure_disposition"] = {"reason": reason, "clear_pending": True}
                context = rebuild(base, profile, bindings=True)
                before = copy.deepcopy(context)
                runtime = _profile(context["contract"], context["execution_policy"], context["plan"], context["bindings"])
                self.assertEqual(runtime["note_reasons"], ["service_error"])
                self.assertEqual(runtime["promise_failure_disposition"], {"reason": "service_error", "clear_pending": True})
                session = LocalSession(context)
                api_names = {node["pre_actions"][0]["function"]["name"] for node in session.workflow["nodes"].values()
                             if node["node_type"] == "api_request"}
                self.assertNotIn(aliases["add_note"], api_names)
                self.assertEqual(context["bindings"]["categories"]["notes"]["binding_kind"], "local_disposition")
                self.assertNotIn("operation", context["bindings"]["categories"]["notes"])
                session.promise()
                self.assertEqual(session.variables["bg_route"], "allow")
                session.failure()
                self.assertEqual(session.variables["bg_route"], "allow")
                session.failure()
                self.assertEqual(session.variables["bg_route"], "failure")
                self.assertFalse(session.state["quarantined"])
                self.assertEqual(session.state["pending_notes"], {"SYNTHETIC-PRODUCT": "service_error"})
                self.assertFalse(session.state["pending_promises"])
                self.assertFalse(session.state["recorded_notes"])
                self.assertEqual(context, before)

    def test_false_clear_pending_is_preserved_and_does_not_create_local_disposition(self):
        base, _, _ = local_context()
        profile = copy.deepcopy(base["execution_policy"]["profile"])
        profile["promise_failure_disposition"]["clear_pending"] = False
        context = rebuild(base, profile, bindings=True)
        runtime = _profile(context["contract"], context["execution_policy"], context["plan"], context["bindings"])
        self.assertIs(runtime["promise_failure_disposition"]["clear_pending"], False)
        session = LocalSession(context)
        session.promise()
        session.failure()
        session.failure()
        self.assertTrue(session.state["quarantined"])
        self.assertIn("SYNTHETIC-PRODUCT", session.state["pending_promises"])
        self.assertFalse(session.state["pending_notes"])

    def test_missing_optional_disposition_or_guarantee_compiles_and_fails_safely(self):
        base, _, aliases = local_context()
        omissions = [("promise_failure_disposition",), ("nonpersistent_failures",),
                     ("promise_failure_disposition", "nonpersistent_failures", "note_reasons")]
        for removed in omissions:
            with self.subTest(removed=removed):
                profile = copy.deepcopy(base["execution_policy"]["profile"])
                for kind in removed:
                    del profile[kind]
                context = rebuild(base, profile, bindings=True)
                for kind in removed:
                    self.assertNotIn(kind, context["execution_policy"]["profile"])
                session = LocalSession(context)
                self.assertNotIn(aliases["add_note"], {node["pre_actions"][0]["function"]["name"]
                    for node in session.workflow["nodes"].values() if node["node_type"] == "api_request"})
                session.promise()
                session.failure()
                if "nonpersistent_failures" not in removed:
                    self.assertEqual(session.variables["bg_route"], "allow")
                    session.failure()
                self.assertEqual(session.variables["bg_route"], "failure")
                self.assertTrue(session.state["quarantined"])
                self.assertFalse(session.state["pending_notes"])
                self.assertFalse(session.state["recorded_promises"])
                self.assertIn("SYNTHETIC-PRODUCT", session.state["pending_promises"])
                session.code("commit")
                self.assertEqual(session.variables["bg_route"], "blocked")
                self.assertEqual(session.variables["bg_body"], "")

    def test_declared_local_recovery_and_finalization_never_grant_note_api(self):
        base, _, aliases = local_context()
        context = rebuild(base, base["execution_policy"]["profile"], bindings=True, note_actions=True)
        session = LocalSession(context)
        self.assertNotIn("bg_finish_api", session.workflow["nodes"])
        self.assertNotIn(aliases["add_note"], {op["name"] for op in context["contract"]["permitted_operations"]})
        session.promise()
        session.failure()
        session.failure()
        session.code("finish")
        self.assertEqual(session.variables["bg_route"], "done")
        self.assertEqual(session.variables["bg_body"], "")
        self.assertFalse(session.state["recorded_notes"])
        session.code("close")
        body = json.loads(session.variables["bg_body"])
        self.assertEqual(body["input_outcome"], "C_D")
        self.assertEqual(body["input_reason"], "WHY_S")


if __name__ == "__main__":
    unittest.main()

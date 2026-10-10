"""Independent invented contracts and the exact embedded execution kernel."""
import copy
import json
import unittest

from builder.contract import compile_policy_contract
from builder.guards import (GuardCompileError, _catalogue, _profile,
                            compile_guarded_workflow, required_execution_kinds,
                            run_guard_step, validate_guarded_workflow)
from builder.safety import digest
from builder.validation import validate_code, validate_workflow


def fixture():
    def string(enum=None, format=None):
        value = {"type": "string"}
        if enum is not None:
            value["enum"] = enum
        if format is not None:
            value["format"] = format
        return value
    c = string()
    number = {"type": "number"}
    product = {"type": "object", "properties": {"product_id": c, "type": string(["revolving", "fixed"]),
               "days_late": {"type": "integer"}, "past_due": number, "minimum_due": number, "instalment_due": number}}
    requests = {
        "verify_identity": {"customer_id": c, "field": string(["postal_code", "memorable_word"]), "answer": c},
        "get_account": {"customer_id": c},
        "create_promise": {"customer_id": c, "product_id": c, "amount": number, "date": string(format="date")},
        "add_note": {"customer_id": c, "product_id": c, "reason": string(["refused", "paid", "dispute", "service_error"])},
        "schedule_callback": {"customer_id": c, "date": string(format="date"), "time": c},
        "transfer_to_agent": {"customer_id": c, "queue": string(["specialist", "helpdesk"])},
        "close_call": {"customer_id": c, "outcome": string(["promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"]),
                       "reason": string(["refused", "dispute", "service_error", "hardship", "identity_failed"])},
    }
    responses = {name: {"ok": {"type": "boolean"}, "error": c} for name in requests}
    responses["verify_identity"].update(verified={"type": "boolean"}, wrong_answers_left={"type": "integer"}, hint=c)
    responses["get_account"]["products"] = {"type": "array", "items": product}
    responses["create_promise"]["promise_id"] = c
    api = {"openapi": "3.0.3", "servers": [{"url": "https://bank.invalid/v9"}], "paths": {}}
    for name, fields in requests.items():
        api["paths"]["/" + name] = {"post": {"operationId": name,
            "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": fields,
                "required": [key for key in fields if not (name == "close_call" and key == "reason")]}}}},
            "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": responses[name]}}}}}}}
    profile = {"identity_fields": ["postal_code", "memorable_word"], "identity_wrong_limit": 4,
        "band_ranges": [{"name": "recent", "min": 1, "max": 19}, {"name": "older", "min": 20, "max": 80}],
        "deadline_days": {"recent": 5, "older": 2}, "floor_fields": {"revolving": "minimum_due", "fixed": "instalment_due"},
        "holidays": ["2031-05-05"], "business_weekdays": [0, 1, 2, 3, 4], "require_confirmation": True, "confirmation_scope": "batch",
        "promise_failure_limit": 2, "record_notes": True, "resolve_all_products": True,
        "nonpersistent_failures": {"create_promise": [{"http_status": 503, "error": "temporarily_unavailable"}]},
        "promise_failure_disposition": {"reason": "service_error", "clear_pending": True},
        "promise_readback": {"product_fields": ["type"], "value_labels": {"type": {"revolving": "revolving account", "fixed": "fixed instalment loan"}}},
        "product_order": {"field": "days_late", "direction": "ascending", "tie_breaker": "account_order"},
        "close_precedence": ["transferred", "promise", "paid", "no_promise"],
        "identity_close": {"failed": {"outcome": "not_verified", "reason": "identity_failed"},
                           "incomplete": {"outcome": "not_verified", "reason": "identity_failed"},
                           "unreached": {"outcome": "not_reached"}},
        "note_reasons": ["refused", "paid", "dispute", "service_error"],
        "transfer_reasons": {"specialist": "hardship", "helpdesk": "dispute"},
        "transfer_prerequisites": {"specialist": "account", "helpdesk": "none"},
        "callback_window": {"start": "09:15", "end": "16:45", "allow_today": False, "next_business_days": 2},
        "close_reasons": {"promise": [], "paid": [], "transferred": ["hardship", "dispute"],
                          "no_promise": ["refused", "dispute", "service_error"], "not_verified": ["identity_failed"], "not_reached": []}}
    text = "Invented independent policy witness."
    sources = [{"id": "source", "file": "invented.txt", "sha256": digest(text), "text": text}]
    policy = {"version": 1, "goal": "Resolve synthetic accounts", "allowed_actions": list(requests),
        "sources": [{k: v for k, v in sources[0].items() if k != "text"}],
        "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": "source", "quote": text}} for kind, value in profile.items()],
        "unsupported_constraints": []}
    contract = compile_policy_contract(policy, sources, api)
    execution = {"version": 1, "policy_sha256": digest(policy), "profile": copy.deepcopy(profile)}
    execution["sha256"] = digest(execution)
    actions = {"identity": ("verify_identity", {"field": "answer_field", "answer": "answer_text"}),
               "account": ("get_account", {}), "stage": ("stage_promise", {"product_id": "product", "amount": "amount", "date": "day"}),
               "commit": ("commit_promises", {}), "note": ("stage_note", {"product_id": "product", "reason": "note_reason"}),
               "finish": ("finalize_notes", {}), "transfer": ("stage_transfer", {"queue": "queue"}),
               "handoff": ("commit_transfer", {}), "callback": ("schedule_callback", {"date": "day", "time": "at"}),
               "close": ("close_call", {"outcome": "outcome", "reason": "close_reason"})}
    names = list(actions)
    dialogue = {"name": "talk", "node_type": "conversation", "task_message": "Discuss permitted choices.",
        "extraction_schema": [{"name": "answer_text", "type": "string", "min_confidence": 0.95}],
        "max_node_turns": 2, "max_turn_transition": "close", "transitions": [
            {"target_node": name, "description": name, "condition": {"operator": "matches_intent", "description": name}} for name in names]}
    nodes = {"talk": dialogue}
    nodes.update({name: {"name": name, "node_type": "guard_action", "action": action, "inputs": inputs,
                        "success_node": "talk", "blocked_node": "talk", "failure_node": "talk"} for name, (action, inputs) in actions.items()})
    return {"plan": {"initial_node": "talk", "nodes": nodes}, "contract": contract, "openapi": api,
            "execution_policy": execution}


class GuardCompilerTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()

    def test_exact_compilation_and_structural_validation(self):
        bundle = compile_guarded_workflow(**self.context)
        workflow = bundle["workflow"]
        self.assertEqual(validate_guarded_workflow(workflow, **self.context), [])
        self.assertEqual(validate_workflow(workflow, self.context["openapi"]), [])
        for node in workflow["nodes"].values():
            if node["node_type"] == "code":
                code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
                self.assertNotIn("{{", code)
                self.assertNotIn("}}", code)
                self.assertEqual(validate_code(code), [])
        self.assertFalse(bundle["guard_manifest"]["host_behavior_verified"])
        self.assertNotIn("bg_callback_confirm", workflow["nodes"])
        self.assertNotIn("bg_handoff_confirm", workflow["nodes"])

    def test_canonical_code_body_mapping_and_route_tampering(self):
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        mutations = [
            lambda w: w["nodes"]["bg_commit_api"]["pre_actions"][0]["function"]["parameters"]["properties"]["url"].update(default="https://other.invalid"),
            lambda w: w["nodes"]["commit"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"].update(default="vars['bg_route'] = 'allow'"),
            lambda w: w["nodes"]["bg_commit_api"]["response_mapping"][0].update(variable="user_ok"),
            lambda w: w["nodes"]["talk"]["transitions"][0].update(target_node="bg_commit_api"),
            lambda w: w["nodes"]["talk"]["extraction_schema"].append({"name": "bg_state", "type": "string", "min_confidence": 0.9}),
            lambda w: w.update(test_variables={"bg_state": "forged"}),
        ]
        for change in mutations:
            with self.subTest(change=change):
                tampered = copy.deepcopy(workflow)
                change(tampered)
                self.assertTrue(validate_guarded_workflow(tampered, **self.context))

    def test_authored_execution_and_protected_writes_are_rejected(self):
        for field, value in [("tools", []), ("pre_actions", []), ("pre_process_code", "pass")]:
            context = copy.deepcopy(self.context)
            context["plan"]["nodes"]["talk"][field] = value
            with self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context)
            with self.assertRaises(GuardCompileError):
                required_execution_kinds(context["plan"])
        for name in ("bg_state", "bg_route", "customer_id", "now", "create_promise_status_code"):
            context = copy.deepcopy(self.context)
            context["plan"]["nodes"]["talk"]["extraction_schema"][0]["name"] = name
            with self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context)
        context = copy.deepcopy(self.context)
        context["plan"]["nodes"]["close"]["node_type"] = "end_call"
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_profile_shadow_and_changed_api_are_rejected(self):
        context = copy.deepcopy(self.context)
        context["execution_policy"]["profile"]["identity_wrong_limit"] = 99
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)
        context = copy.deepcopy(self.context)
        context["openapi"]["paths"]["/create_promise"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["amount"]["minimum"] = 500
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_required_kinds_and_missing_profile_fail_closed(self):
        self.assertIn("identity_wrong_limit", required_execution_kinds(self.context["plan"]))
        context = copy.deepcopy(self.context)
        del context["execution_policy"]["profile"]["floor_fields"]
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        with self.assertRaisesRegex(GuardCompileError, "floor_fields"):
            compile_guarded_workflow(**context)

    def test_determinism_and_detachment(self):
        first = compile_guarded_workflow(**self.context)
        second = compile_guarded_workflow(**self.context)
        self.assertEqual(first, second)
        first["workflow"]["nodes"]["talk"]["task_message"] = "Changed"
        self.assertNotEqual(first, second)
        self.assertEqual(self.context["plan"]["nodes"]["talk"]["task_message"], "Discuss permitted choices.")

    def test_guard_context_integration_does_not_trust_manifest_metadata(self):
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        guard_context = {k: v for k, v in self.context.items() if k != "openapi"}
        self.assertEqual(validate_workflow(workflow, self.context["openapi"], guard_context=guard_context), [])
        workflow["nodes"]["bg_commit_api"]["pre_process_code"] = "pass"
        self.assertTrue(validate_workflow(workflow, self.context["openapi"], guard_context=guard_context))
        from builder.validation import assess_workflow
        plain = fixture()["plan"]
        self.assertFalse(assess_workflow(plain, self.context["openapi"])["action_guard_compilation_verified"])

    def test_local_only_dispositions_emit_no_add_note_nodes(self):
        context = copy.deepcopy(self.context)
        rule = context["contract"]["active_rules"]["record_notes"]
        rule["value"] = False
        context["contract"]["sha256"] = digest({k: v for k, v in context["contract"].items() if k != "sha256"})
        context["execution_policy"]["profile"]["record_notes"] = False
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertNotIn("bg_finish_api", workflow["nodes"])
        self.assertEqual(validate_workflow(workflow, context["openapi"]), [])

    def test_pinned_kit_extraction_fields_and_confirmation_enum(self):
        context = copy.deepcopy(self.context)
        context["plan"]["nodes"]["talk"]["extraction_schema"][0].update(
            enum_values=["yes", "no"], validation_pattern="^(yes|no)$", max_retries=0)
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(validate_workflow(workflow, context["openapi"]), [])
        consent = workflow["nodes"]["bg_commit_confirm"]["extraction_schema"][0]
        self.assertEqual(consent["enum_values"], ["yes", "no", "unclear"])
        self.assertNotIn("enum", consent)
        context["plan"]["nodes"]["talk"]["extraction_schema"][0]["enum"] = ["yes"]
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_malformed_nested_plans_have_bounded_compile_errors(self):
        for node in [{"name": "talk", "node_type": "guard_action", "action": []},
                     {"name": "talk", "node_type": "conversation", "extraction_schema": 42},
                     {"name": "talk", "node_type": "conversation", "transitions": [None]}]:
            context = copy.deepcopy(self.context)
            context["plan"]["nodes"]["talk"] = node
            with self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context)

    def test_boolean_execution_version_is_rejected(self):
        context = copy.deepcopy(self.context)
        context["execution_policy"]["version"] = True
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)


class GuardExecutionTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.profile = _profile(self.context["contract"], self.context["execution_policy"], self.context["plan"], None)
        self.catalog = _catalogue(self.context["contract"], self.context["openapi"], None)
        self.state = self.step("init", customer_id="invented-customer", now="2031-05-02")[0]

    def step(self, phase, action="", inputs=None, result=None, **kwargs):
        return run_guard_step(phase, getattr(self, "state", None), action, inputs, result,
                              profile=self.profile, catalog=self.catalog, **kwargs)

    def advance(self, phase, action="", inputs=None, result=None):
        result = self.step(phase, action, inputs, result)
        self.state = result[0]
        return result

    def account(self):
        for field in self.profile["identity_fields"]:
            self.advance("prepare", "verify_identity", {"field": field, "answer": "supplied"})
            self.advance("result", result={"ok": True, "verified": True, "wrong_answers_left": 4})
        self.advance("prepare", "get_account")
        self.advance("result", result={"ok": True, "products": [
            {"product_id": "stable-a", "type": "revolving", "days_late": 8, "past_due": 300, "minimum_due": 37.25},
            {"product_id": "stable-b", "type": "fixed", "days_late": 28, "past_due": 600, "instalment_due": 82.10}]})

    def stage(self, pid="stable-a", amount="37.25", day="2031-05-06"):
        return self.advance("prepare", "stage_promise", {"product_id": pid, "amount": amount, "date": day})

    def test_identity_order_and_one_total_api_counter_with_hint_exemptions(self):
        with self.assertRaises(ValueError):
            self.step("prepare", "get_account")
        with self.assertRaises(ValueError):
            self.step("prepare", "verify_identity", {"field": "memorable_word", "answer": "x"})
        self.advance("prepare", "verify_identity", {"field": "postal_code", "answer": "x"})
        self.advance("result", result={"ok": True, "verified": False, "wrong_answers_left": 4, "hint": "retry_form"})
        self.assertEqual(self.state["wrong_answers"], 0)
        self.advance("prepare", "verify_identity", {"field": "postal_code", "answer": "x"})
        self.advance("result", result={"ok": True, "verified": True, "wrong_answers_left": 2})
        self.advance("prepare", "verify_identity", {"field": "memorable_word", "answer": "x"})
        self.advance("result", result={"ok": True, "verified": False, "wrong_answers_left": 0})
        self.assertEqual(self.state["wrong_answers"], 4)
        self.assertEqual(self.state["identity_status"], "failed")
        with self.assertRaises(ValueError):
            self.step("prepare", "verify_identity", {"field": "memorable_word", "answer": "x"})
        with self.assertRaises(ValueError):
            self.step("prepare", "get_account")

    def test_stable_product_ids_floor_dates_and_integer_cents(self):
        self.account()
        for pid, amount, day in [("made-up", "50", "2031-05-06"), ("stable-a", "37.24", "2031-05-06"),
                                 ("stable-a", "301", "2031-05-06"), ("stable-a", "40.001", "2031-05-06"),
                                 ("stable-a", "40", "2031-05-05"), ("stable-b", "90", "2031-05-09")]:
            with self.subTest(pid=pid, amount=amount, day=day), self.assertRaises(ValueError):
                self.step("prepare", "stage_promise", {"product_id": pid, "amount": amount, "date": day})
        self.stage()
        self.assertEqual(self.state["pending_promises"]["stable-a"]["amount_cents"], 3725)

    def test_confirmation_snapshot_and_success_evidence_gate_write(self):
        self.account()
        self.stage()
        state, route, body, readback = self.advance("prepare", "commit_promises")
        self.assertEqual(route, "confirm")
        self.assertEqual(body, {})
        self.assertIn("37.25", readback)
        with self.assertRaises(ValueError):
            self.step("confirm", inputs={"consent": "no"})
        _, route, body, _ = self.advance("confirm", inputs={"consent": "yes"})
        self.assertEqual(route, "allow")
        self.assertEqual(body["product_id"], "stable-a")
        self.assertEqual(body["amount"], 37.25)
        self.assertFalse(self.state["recorded_promises"])
        self.advance("result", result={"ok": True, "promise_id": "persisted-one"})
        self.assertIn("stable-a", self.state["recorded_promises"])
        with self.assertRaises(ValueError):
            self.stage()

    def test_changed_proposal_invalidates_confirmation(self):
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        self.stage(amount="55.10")
        self.assertFalse(self.state["batch"])
        with self.assertRaises(ValueError):
            self.step("confirm", inputs={"consent": "yes"})
        self.advance("prepare", "commit_promises")
        body = self.advance("confirm", inputs={"consent": "yes"})[2]
        self.assertEqual(body["amount"], 55.10)

    def test_collective_confirmation_and_partial_success_then_unknown(self):
        self.account()
        self.stage()
        self.stage("stable-b", "82.10", "2031-05-06")
        self.advance("prepare", "commit_promises")
        self.advance("confirm", inputs={"consent": "yes"})
        self.assertEqual(self.advance("result", result={"ok": True, "promise_id": "one"})[1], "allow")
        self.assertIn("stable-a", self.state["recorded_promises"])
        self.assertNotIn("stable-b", self.state["recorded_promises"])
        self.advance("result", result={})
        self.assertTrue(self.state["quarantined"])
        self.assertIn("stable-a", self.state["recorded_promises"])
        with self.assertRaises(ValueError):
            self.step("prepare", "close_call", {"outcome": "no_promise", "reason": "service_error"})
        with self.assertRaises(ValueError):
            self.step("prepare", "commit_promises")

    def test_transfer_discards_pending_notes_and_preserves_recorded_promises(self):
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        self.advance("confirm", inputs={"consent": "yes"})
        self.advance("result", result={"ok": True, "promise_id": "one"})
        self.advance("prepare", "stage_note", {"product_id": "stable-b", "reason": "dispute"})
        self.advance("prepare", "stage_transfer", {"queue": "helpdesk"})
        self.assertFalse(self.state["pending_notes"])
        self.assertIn("stable-a", self.state["recorded_promises"])
        with self.assertRaises(ValueError):
            self.step("prepare", "finalize_notes")
        self.assertEqual(self.advance("prepare", "commit_transfer")[1], "allow")
        self.advance("result", result={"ok": True})
        body = self.advance("prepare", "close_call", {"outcome": "promise"})[2]
        self.assertEqual(body["outcome"], "transferred")
        self.assertEqual(body["reason"], "dispute")
        self.advance("result", result={"ok": True})
        self.assertTrue(self.state["closed"])
        for action in ("close_call", "get_account", "schedule_callback"):
            with self.assertRaises(ValueError):
                self.step("prepare", action)

    def test_notes_are_deferred_until_all_products_resolved_and_phase_sealed(self):
        self.account()
        self.advance("prepare", "stage_note", {"product_id": "stable-a", "reason": "paid"})
        with self.assertRaises(ValueError):
            self.step("prepare", "finalize_notes")
        self.advance("prepare", "stage_note", {"product_id": "stable-b", "reason": "refused"})
        self.advance("prepare", "finalize_notes")
        self.assertFalse(self.state["recorded_notes"])
        self.advance("result", result={"ok": True})
        self.advance("result", result={"ok": True})
        with self.assertRaises(ValueError):
            self.step("prepare", "stage_transfer", {"queue": "specialist"})
        body = self.advance("prepare", "close_call")[2]
        self.assertEqual(body["outcome"], "no_promise")
        self.assertEqual(body["reason"], "refused")

    def test_callback_calendar_and_duplicate_write(self):
        for day, at in [("2031-05-02", "10:00"), ("2031-05-05", "10:00"), ("2031-05-06", "17:00")]:
            with self.assertRaises(ValueError):
                self.step("prepare", "schedule_callback", {"date": day, "time": at})
        _, route, body, _ = self.advance("prepare", "schedule_callback", {"date": "2031-05-06", "time": "10:00"})
        self.assertEqual(route, "allow")
        self.advance("result", result={"ok": True})
        with self.assertRaises(ValueError):
            self.step("prepare", "schedule_callback", {"date": "2031-05-06", "time": "10:00"})

    def test_forged_numeric_boolean_result_does_not_count_as_success(self):
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        self.advance("confirm", inputs={"consent": "yes"})
        self.advance("result", result={"ok": 1, "promise_id": "untrusted"})
        self.assertFalse(self.state["recorded_promises"])
        self.assertTrue(self.state["quarantined"])

    def test_qualified_nonpersistent_failure_retries_exact_body_with_budget(self):
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        original = self.advance("confirm", inputs={"consent": "yes"})[2]
        result = self.advance("result", result={"ok": False, "error": "temporarily_unavailable", "_http_status": "503"})
        self.assertEqual(result[1], "allow")
        self.assertEqual(result[2], original)
        result = self.advance("result", result={"ok": False, "error": "temporarily_unavailable", "_http_status": 503})
        self.assertEqual(result[1], "failure")
        self.assertEqual(self.state["promise_failures"]["stable-a"], 2)
        self.assertNotIn("stable-a", self.state["pending_promises"])
        self.assertEqual(self.state["pending_notes"]["stable-a"], "service_error")
        self.assertFalse(self.state["quarantined"])
        with self.assertRaises(ValueError):
            self.step("prepare", "commit_promises")

    def test_different_error_or_ambiguous_status_never_authorizes_retry(self):
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        self.advance("confirm", inputs={"consent": "yes"})
        self.advance("result", result={"ok": False, "error": "another_error", "_http_status": 503})
        self.assertTrue(self.state["quarantined"])
        self.assertEqual(self.state["promise_failures"]["stable-a"], 1)

    def test_explicit_http200_nonpersistent_business_failure_can_retry(self):
        self.profile["nonpersistent_failures"]["create_promise"] = [{"http_status": 200, "error": "capacity_rejection"}]
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        original = self.advance("confirm", inputs={"consent": "yes"})[2]
        result = self.advance("result", result={"ok": "false", "error": "capacity_rejection", "_http_status": 200})
        self.assertEqual(result[1], "allow")
        self.assertEqual(result[2], original)
        self.assertEqual(self.state["promise_failures"]["stable-a"], 1)

    def test_positive_http200_never_counts_as_a_qualified_failure(self):
        self.profile["nonpersistent_failures"]["create_promise"] = [{"http_status": 200}]
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        self.advance("confirm", inputs={"consent": "yes"})
        result = self.advance("result", result={"ok": True, "promise_id": "recorded", "_http_status": 200})
        self.assertEqual(result[1], "done")
        self.assertFalse(self.state["promise_failures"])
        self.assertIn("stable-a", self.state["recorded_promises"])

    def test_missing_negative_evidence_cannot_retry_on_status_alone(self):
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        self.advance("confirm", inputs={"consent": "yes"})
        result = self.advance("result", result={"error": "temporarily_unavailable", "_http_status": 503})
        self.assertEqual(result[1], "failure")
        self.assertTrue(self.state["quarantined"])
        self.assertFalse(self.state["promise_failures"])

    def test_amount_must_roundtrip_integer_cents_before_api_snapshot(self):
        self.account()
        self.state["products"][0]["past_due"] = "90071992547409.91"
        self.stage(amount="90071992547409.91")
        with self.assertRaisesRegex(ValueError, "precision"):
            self.step("prepare", "commit_promises")
        self.assertFalse(self.state["attempts"])

    def test_close_uses_policy_identity_disposition_not_proposed_reason(self):
        body = self.advance("prepare", "close_call", {"outcome": "promise", "reason": "refused"})[2]
        self.assertEqual(body["outcome"], "not_verified")
        self.assertEqual(body["reason"], "identity_failed")

    def test_exact_embedded_code_handles_mapped_json_product_lists(self):
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        variables = {"customer_id": "invented-customer", "now": "2031-05-02"}
        def code(name):
            source = workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
            exec(source, {"vars": variables})
        code("bg_init")
        for field in self.profile["identity_fields"]:
            variables.update(answer_field=field, answer_text="supplied")
            code("identity")
            variables.update(bg_identity_result_ok="true", bg_identity_result_verified="true", bg_identity_result_wrong_answers_left="4")
            code("bg_identity_result")
        code("account")
        variables.update(bg_account_result_ok="true", bg_account_result_products='[{"product_id":"api-owned","type":"revolving","days_late":8,"past_due":300,"minimum_due":37.25}]')
        code("bg_account_result")
        self.assertEqual(variables["bg_route"], "done")
        variables.update(product="api-owned", amount="50", day="2031-05-06")
        code("stage")
        code("commit")
        self.assertEqual(variables["bg_route"], "confirm")
        variables.update(amount="999", bg_consent="yes")
        code("bg_commit_accept")
        self.assertIn('"amount":50.0', variables["bg_body"])
        self.assertEqual(variables["bg_route"], "allow")

    def test_canonical_status_hook_uses_envelope_not_automatic_name(self):
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        node = workflow["nodes"]["bg_commit_api"]
        hook = node["pre_actions"][0]["post_process_code"]
        result = {"status_code": 503, "data": {"ok": False, "error": "temporarily_unavailable"}}
        exec(hook, {"result": result})
        self.assertIs(result["data"]["ok"], False)
        self.assertEqual(result["data"]["error"], "temporarily_unavailable")
        self.assertEqual(result["data"]["bg_http_status"], 503)
        mapping = next(m for m in node["response_mapping"] if m["path"] == "data.bg_http_status")
        self.assertTrue(mapping["variable"].startswith("bg_"))
        self.account()
        self.stage()
        self.advance("prepare", "commit_promises")
        self.advance("confirm", inputs={"consent": "yes"})
        # Neither operation_status_code nor node_status_code supplies evidence.
        variables = {"bg_state": json.dumps(self.state),
                     "create_promise_status_code": 200, "bg_commit_api_status_code": 200}
        for entry in node["response_mapping"]:
            variables[entry["variable"]] = result["data"].get(entry["path"][5:], "")
        source = workflow["nodes"]["bg_commit_result"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(source, {"vars": variables})
        self.assertEqual(variables["bg_route"], "allow")
        state = json.loads(variables["bg_state"])
        self.assertEqual(state["promise_failures"]["stable-a"], 1)

    def test_status_hook_does_not_synthesize_or_trust_body_status(self):
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        hook = workflow["nodes"]["bg_commit_api"]["pre_actions"][0]["post_process_code"]
        result = {"data": {"ok": False, "bg_http_status": 503}}
        exec(hook, {"result": result})
        self.assertNotIn("bg_http_status", result["data"])
        self.assertIs(result["data"]["ok"], False)


if __name__ == "__main__":
    unittest.main()

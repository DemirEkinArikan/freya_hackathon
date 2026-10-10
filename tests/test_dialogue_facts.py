"""Invented in-memory tests of the identical compiler-authored fact writer."""
import copy
import json
import unittest

from test_guards import fixture
from builder.guards import (GuardCompileError, _catalogue, _code, _disclosure_schema,
                            _plan_errors, _profile, compile_guarded_workflow,
                            validate_guarded_workflow)
from builder.safety import digest


CUSTOMER = "PRIVATE_CUSTOMER_TOKEN_91"
PRODUCTS = [
    {"product_id": "INTERNAL_PRODUCT_Z9", "type": "fixed", "days_late": 28,
     "past_due": 600.11, "instalment_due": 82.10,
     "unapproved_debug": {"private": "DO_NOT_PROJECT_API_EXTRAS"}},
    {"product_id": "INTERNAL_PRODUCT_A2", "type": "revolving", "days_late": 8,
     "past_due": 300.19, "minimum_due": 37.25,
     "unapproved_debug": {"customer_id": CUSTOMER}},
]


class DialogueFactTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.configure()

    def configure(self, disclosure=None):
        if disclosure is not None:
            self.context["contract"]["active_rules"]["account_disclosure_fields"] = {"value": copy.deepcopy(disclosure)}
            self.context["execution_policy"]["profile"]["account_disclosure_fields"] = copy.deepcopy(disclosure)
            for name in ("contract", "execution_policy"):
                value = self.context[name]
                value["sha256"] = digest({key: item for key, item in value.items() if key != "sha256"})
        self.profile = _profile(self.context["contract"], self.context["execution_policy"], self.context["plan"], None)
        self.catalog = _catalogue(self.context["contract"], self.context["openapi"], None)
        self.catalog["get_account"]["disclosure_fields"] = _disclosure_schema(self.context["contract"], self.profile, self.catalog)

    def step(self, phase, state=None, action="", inputs=None, data=None):
        inputs, data = inputs or {}, data or {}
        variables = {"customer_id": CUSTOMER, "now": "2031-05-02T11:00",
                     "bg_facts": '{"forged":"STALE_FACT_MUST_DISAPPEAR"}',
                     "bg_state": state if isinstance(state, str) else json.dumps(state)}
        public = {key: "proposal_" + key for key in inputs if key != "consent"}
        variables.update({public[key]: value for key, value in inputs.items() if key != "consent"})
        variables["bg_consent"] = inputs.get("consent", "")
        mapped = {key: "mapped_" + key for key in data}
        variables.update({mapped[key]: value for key, value in data.items()})
        namespace = {"vars": variables}
        exec(_code(phase, action, public, self.profile, self.catalog, {}, mapped), namespace)
        self.assertNotIn("STALE_FACT_MUST_DISAPPEAR", variables["bg_facts"])
        try:
            next_state = json.loads(variables["bg_state"])
        except json.JSONDecodeError:
            next_state = variables["bg_state"]
        return next_state, json.loads(variables["bg_facts"]), variables

    def account(self, disclosure=None, products=None):
        if disclosure is not None:
            self.configure(disclosure)
        state, _, _ = self.step("init")
        for field in self.profile["identity_fields"]:
            state, _, _ = self.step("prepare", state, "verify_identity", {"field": field, "answer": "invented answer"})
            state, _, _ = self.step("result", state, "verify_identity",
                                    data={"ok": True, "verified": True, "wrong_answers_left": self.profile["identity_wrong_limit"]})
        state, _, _ = self.step("prepare", state, "get_account")
        return self.step("result", state, "get_account", data={"ok": True, "products": copy.deepcopy(products or PRODUCTS)})

    def stage(self, state, product, amount):
        return self.step("prepare", state, "stage_promise",
                         {"product_id": product, "amount": amount, "date": "2031-05-06"})

    def test_initial_facts_have_next_factor_without_customer_or_account_data(self):
        _, facts, _ = self.step("init")
        self.assertEqual(facts["identity"]["next_factor"], self.profile["identity_fields"][0])
        self.assertEqual(facts["identity"]["wrong_answers_left"], self.profile["identity_wrong_limit"])
        self.assertEqual(facts["account"]["products"], [])
        self.assertEqual(facts["internal_action_references"]["products"], [])
        self.assertNotIn(CUSTOMER, json.dumps(facts))

    def test_wrong_identity_hint_is_current_result_only_and_does_not_guess_a_count(self):
        state, _, _ = self.step("init")
        field = self.profile["identity_fields"][0]
        state, _, _ = self.step("prepare", state, "verify_identity", {"field": field, "answer": "invented answer"})
        state, facts, _ = self.step("result", state, "verify_identity", data={
            "ok": True, "verified": False, "wrong_answers_left": self.profile["identity_wrong_limit"], "hint": "INVENTED_FORMAT_HINT"})
        self.assertEqual(facts["identity"]["latest_hint"], "INVENTED_FORMAT_HINT")
        self.assertEqual(facts["identity"]["latest_check"], {"field": field, "verified": False})
        self.assertEqual(facts["identity"]["wrong_answers"], 0)
        self.assertEqual(facts["identity"]["next_factor"], field)
        _, next_facts, _ = self.step("prepare", state, "verify_identity", {"field": field, "answer": "corrected answer"})
        self.assertNotIn("latest_hint", next_facts["identity"])
        self.assertNotIn("latest_check", next_facts["identity"])

    def test_verified_factor_advances_and_ignores_inapplicable_hint(self):
        state, _, _ = self.step("init")
        field = self.profile["identity_fields"][0]
        state, _, _ = self.step("prepare", state, "verify_identity", {"field": field, "answer": "invented answer"})
        _, facts, _ = self.step("result", state, "verify_identity", data={
            "ok": True, "verified": True, "wrong_answers_left": 3, "hint": "STALE_HINT"})
        self.assertEqual(facts["identity"]["next_factor"], self.profile["identity_fields"][1])
        self.assertEqual(facts["identity"]["wrong_answers"], 1)
        self.assertEqual(facts["identity"]["wrong_answers_left"], 3)
        self.assertNotIn("latest_hint", facts["identity"])

    def test_undeclared_account_narration_is_blocked_but_internal_refs_are_ordered(self):
        _, facts, _ = self.account()
        self.assertEqual(facts["account"]["narration_status"], "blocked_until_policy_declares_fields")
        self.assertEqual(facts["account"]["products"], [])
        refs = facts["internal_action_references"]["products"]
        self.assertEqual([value["product_ref"] for value in refs], [PRODUCTS[1]["product_id"], PRODUCTS[0]["product_id"]])
        self.assertEqual([value["returned_position"] for value in refs], [2, 1])
        readable = copy.deepcopy(facts)
        readable.pop("internal_action_references")
        for identifier in (CUSTOMER, PRODUCTS[0]["product_id"], PRODUCTS[1]["product_id"], "DO_NOT_PROJECT_API_EXTRAS"):
            self.assertNotIn(identifier, json.dumps(readable))

    def test_explicit_empty_narration_permissions_expose_no_account_fields(self):
        _, facts, _ = self.account([])
        self.assertEqual(facts["account"]["narration_status"], "blocked_until_policy_declares_fields")
        self.assertEqual(facts["account"]["products"], [])
        self.assertNotIn(CUSTOMER, json.dumps(facts))
        self.assertEqual(validate_guarded_workflow(compile_guarded_workflow(**self.context)["workflow"], **self.context), [])

    def test_declared_account_fields_are_exact_allowlist_not_raw_api_passthrough(self):
        _, facts, _ = self.account(["type", "past_due"])
        self.assertEqual(facts["account"]["narration_status"], "approved_fields_only")
        self.assertEqual(facts["account"]["products"][0]["fields"], {"type": "revolving", "past_due": 300.19})
        for forbidden in ("days_late", "minimum_due", "instalment_due", "unapproved_debug", "DO_NOT_PROJECT_API_EXTRAS", CUSTOMER):
            self.assertNotIn(forbidden, json.dumps(facts["account"]))

    def test_internal_constraints_use_actual_floors_caps_bands_and_policy_calendar(self):
        _, facts, _ = self.account()
        first, second = facts["internal_action_references"]["products"]
        self.assertEqual(first["proposal_constraints_status"], "available")
        self.assertEqual(first["proposal_constraints"], {"product_type": "revolving", "band": "recent",
            "floor_amount_cents": 3725, "past_due_cap_cents": 30019, "business_deadline": "2031-05-12",
            "positive_amount_required": True})
        self.assertEqual(second["proposal_constraints"]["floor_amount_cents"], 8210)
        self.assertEqual(second["proposal_constraints"]["business_deadline"], "2031-05-07")
        self.assertTrue(first["proposal_turn_available"])
        self.assertFalse(second["proposal_turn_available"])
        self.assertEqual(second["proposal_constraints_status"], "blocked")
        self.assertEqual(first["remaining_failure_attempts"], 2)
        self.assertEqual(first["resolution_status"], "unresolved")
        self.assertNotIn("persuasion", json.dumps(facts))
        self.assertEqual(facts["account"]["products"], [])

    def test_missing_floor_data_does_not_create_invented_proposal_constraints(self):
        products = copy.deepcopy(PRODUCTS)
        products[0].pop("instalment_due")
        _, facts, _ = self.account(products=products)
        second = facts["internal_action_references"]["products"][1]
        self.assertEqual(second["proposal_constraints_status"], "unavailable")
        self.assertNotIn("proposal_constraints", second)
        self.assertEqual(facts["account"]["narration_status"], "blocked_until_policy_declares_fields")

    def test_missing_unknown_nested_and_identifier_disclosure_fields_fail_compilation(self):
        for fields in (["type", "type"], "type", [None], ["missing"], ["product_id"], ["customer_id"]):
            with self.subTest(fields=fields):
                context = fixture()
                context["contract"]["active_rules"]["account_disclosure_fields"] = {"value": fields}
                context["execution_policy"]["profile"]["account_disclosure_fields"] = fields
                for name in ("contract", "execution_policy"):
                    context[name]["sha256"] = digest({key: item for key, item in context[name].items() if key != "sha256"})
                with self.assertRaises(GuardCompileError):
                    compile_guarded_workflow(**context)

    def test_renamed_internal_product_identifier_cannot_be_declared_for_speech(self):
        self.catalog["get_account"]["product_fields"]["product_id"] = "opaque_account_reference"
        with self.assertRaisesRegex(GuardCompileError, "identifier aliases"):
            _disclosure_schema(self.context["contract"], {"account_disclosure_fields": ["opaque_account_reference"]}, self.catalog)

    def test_nonfinite_or_malformed_declared_scalar_clears_facts(self):
        for value in (float("nan"), float("inf"), {"unsafe": "nested"}, None):
            with self.subTest(value=value):
                products = copy.deepcopy(PRODUCTS)
                products[0]["past_due"] = value
                _, facts, _ = self.account(["past_due"], products)
                self.assertEqual(facts["call_status"], "quarantined")
                self.assertNotIn("account", facts)
                self.assertNotIn("internal_action_references", facts)

    def test_approved_text_containing_internal_identifier_is_not_exposed(self):
        products = copy.deepcopy(PRODUCTS)
        products[0]["type"] = "description " + CUSTOMER
        # Remove the synthetic type enum only for this explicitly declared raw field test.
        account_operation = next(op for op in self.context["contract"]["permitted_operations"] if op["name"] == "get_account")
        account_operation["response_schemas"]["200"]["application/json"]["properties"]["products"]["items"]["properties"]["type"].pop("enum")
        self.context["openapi"]["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["products"]["items"]["properties"]["type"].pop("enum")
        self.context["contract"]["sha256"] = digest({key: value for key, value in self.context["contract"].items() if key != "sha256"})
        _, facts, _ = self.account(["type"], products)
        self.assertNotIn(CUSTOMER, json.dumps(facts))
        self.assertEqual(facts["call_status"], "quarantined")

    def test_staged_and_confirming_promises_never_claim_recorded_success(self):
        state, _, _ = self.account()
        state, facts, _ = self.stage(state, PRODUCTS[1]["product_id"], "50.00")
        self.assertEqual(facts["product_states"][0]["promise"], "staged")
        self.assertEqual(facts["recorded_counts"]["promises"], 0)
        _, facts, _ = self.step("prepare", state, "commit_promises")
        self.assertEqual(facts["latest_action"]["status"], "awaiting_confirmation")
        self.assertEqual(facts["recorded_counts"]["promises"], 0)

    def test_actual_recorded_promise_is_visible_without_promise_or_customer_ids(self):
        state, _, _ = self.account()
        state, _, _ = self.stage(state, PRODUCTS[1]["product_id"], "50.00")
        state, _, _ = self.step("prepare", state, "commit_promises")
        state, _, _ = self.step("confirm", state, "commit_promises", {"consent": "yes"})
        _, facts, _ = self.step("result", state, "commit_promises", data={"ok": True, "promise_id": "PRIVATE_PROMISE_ID"})
        self.assertEqual(facts["recorded_counts"]["promises"], 1)
        self.assertEqual(facts["product_states"][0]["promise"], "recorded")
        first = facts["internal_action_references"]["products"][0]
        self.assertEqual(first["resolution_status"], "recorded_promise")
        self.assertEqual(first["proposal_constraints_status"], "blocked")
        self.assertEqual(facts["latest_action"]["status"], "recorded")
        self.assertNotIn("PRIVATE_PROMISE_ID", json.dumps(facts))
        self.assertNotIn(CUSTOMER, json.dumps(facts))

    def test_unknown_persistence_clears_display_and_preserves_only_true_success_counts(self):
        state, _, _ = self.account(["type"])
        state, _, _ = self.stage(state, PRODUCTS[1]["product_id"], "50.00")
        state, _, _ = self.stage(state, PRODUCTS[0]["product_id"], "100.00")
        state, _, _ = self.step("prepare", state, "commit_promises")
        state, _, _ = self.step("confirm", state, "commit_promises", {"consent": "yes"})
        state, facts, _ = self.step("result", state, "commit_promises", data={"ok": True, "promise_id": "PRIVATE_FIRST_PROMISE"})
        self.assertEqual(facts["recorded_counts"]["promises"], 1)
        _, facts, _ = self.step("result", state, "commit_promises", data={"ok": None})
        self.assertEqual(facts["call_status"], "quarantined")
        self.assertEqual(facts["recorded_counts"]["promises"], 1)
        for key in ("account", "identity", "internal_action_references", "product_states"):
            self.assertNotIn(key, facts)
        self.assertEqual(facts["latest_action"]["status"], "unknown_or_malformed_evidence")

    def test_projection_failure_after_positive_write_preserves_reconciled_evidence(self):
        state, _, _ = self.account()
        state, _, _ = self.stage(state, PRODUCTS[1]["product_id"], "50.00")
        state, _, _ = self.step("prepare", state, "commit_promises")
        state, _, _ = self.step("confirm", state, "commit_promises", {"consent": "yes"})
        state, facts, variables = self.step("result", state, "commit_promises", data={
            "ok": True, "promise_id": "PRIVATE_PROMISE", "error": {"malformed_optional": True}})
        self.assertIn(PRODUCTS[1]["product_id"], state["recorded_promises"])
        self.assertTrue(state["quarantined"])
        self.assertEqual(facts["recorded_counts"]["promises"], 1)
        self.assertEqual(facts["latest_action"]["status"], "fact_projection_failed")
        self.assertEqual(variables["bg_body"], "")
        self.assertNotIn("account", facts)
        self.assertNotIn("internal_action_references", facts)

    def test_nonfinite_account_projection_preserves_preexisting_recorded_callback(self):
        self.configure(["past_due"])
        state, _, _ = self.step("init")
        state, _, _ = self.step("prepare", state, "schedule_callback", {"date": "2031-05-06", "time": "12:00"})
        state, facts, _ = self.step("result", state, "schedule_callback", data={"ok": True})
        self.assertEqual(facts["recorded_counts"]["callbacks"], 1)
        for field in self.profile["identity_fields"]:
            state, _, _ = self.step("prepare", state, "verify_identity", {"field": field, "answer": "answer"})
            state, _, _ = self.step("result", state, "verify_identity",
                                    data={"ok": True, "verified": True, "wrong_answers_left": 4})
        state, _, _ = self.step("prepare", state, "get_account")
        products = copy.deepcopy(PRODUCTS)
        products[0]["past_due"] = float("nan")
        state, facts, _ = self.step("result", state, "get_account", data={"ok": True, "products": products})
        self.assertTrue(state["quarantined"])
        self.assertEqual(len(state["callbacks"]), 1)
        self.assertEqual(facts["recorded_counts"]["callbacks"], 1)
        self.assertEqual(state["products"], [])
        self.assertNotIn("account", facts)

    def test_qualified_negative_retry_reports_error_without_fabricating_success(self):
        state, _, _ = self.account()
        state, _, _ = self.stage(state, PRODUCTS[1]["product_id"], "50.00")
        state, _, _ = self.step("prepare", state, "commit_promises")
        state, _, _ = self.step("confirm", state, "commit_promises", {"consent": "yes"})
        _, facts, _ = self.step("result", state, "commit_promises", data={
            "ok": False, "_http_status": 503, "error": "temporarily_unavailable"})
        self.assertEqual(facts["latest_action"]["status"], "retrying_nonpersistent_rejection")
        self.assertEqual(facts["latest_action"]["error"], "temporarily_unavailable")
        self.assertEqual(facts["recorded_counts"]["promises"], 0)
        self.assertEqual(facts["product_states"][0]["confirmed_write_failures"], 1)
        first = facts["internal_action_references"]["products"][0]
        self.assertEqual(first["remaining_failure_attempts"], 1)
        self.assertEqual(first["proposal_constraints_status"], "blocked")

    def test_malformed_identity_result_clears_prior_factors_and_hints_from_view(self):
        state, _, _ = self.step("init")
        state, _, _ = self.step("prepare", state, "verify_identity", {"field": self.profile["identity_fields"][0], "answer": "answer"})
        _, facts, _ = self.step("result", state, "verify_identity", data={
            "ok": True, "verified": None, "wrong_answers_left": 3, "hint": "MUST_NOT_APPEAR"})
        self.assertEqual(facts["call_status"], "quarantined")
        self.assertNotIn("identity", facts)
        self.assertNotIn("MUST_NOT_APPEAR", json.dumps(facts))

    def test_parse_error_resets_forged_previous_facts_to_unavailable(self):
        _, facts, _ = self.step("prepare", "malformed-state", "get_account")
        self.assertEqual(facts, {"version": 1, "route": "blocked", "call_status": "unavailable",
                                 "latest_action": {"action": "get_account", "status": "blocked_or_malformed"}})

    def test_authored_fact_writer_and_direct_raw_template_reads_are_rejected(self):
        for template in ("{{bg_state}}", "{{ bg_body }}", "{{bg_readback}}", "{{customer_id}}",
                         "{{get_account_status_code}}", "{{verify_identity_response}}"):
            with self.subTest(template=template):
                context = fixture()
                context["plan"]["nodes"]["talk"]["task_message"] = "Read " + template
                with self.assertRaisesRegex(GuardCompileError, "raw protected"):
                    compile_guarded_workflow(**context)
        context = fixture()
        context["plan"]["nodes"]["talk"]["extraction_schema"][0]["name"] = "bg_facts"
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_template_checks_cover_nested_authored_strings_and_actual_aliases(self):
        for patch in (
            lambda p: p.update(system_prompt="{{bg_state}}"),
            lambda p: p["nodes"]["talk"]["extraction_schema"][0].update(extraction_prompt="{{bg_body}}"),
            lambda p: p["nodes"]["talk"]["transitions"][0].update(description="{{customer_id}}"),
            lambda p: p["nodes"]["talk"].update(on_enter={"type": "tts", "text": "{{bg_facts}}"})):
            proposal = fixture()["plan"]
            patch(proposal)
            self.assertTrue(_plan_errors(proposal))
        proposal = fixture()["plan"]
        proposal["nodes"]["talk"]["task_message"] = "{{opaque_account_read_response}}"
        self.assertTrue(_plan_errors(proposal, ["opaque_account_read"]))
        proposal["nodes"]["talk"]["task_message"] = "{{bank-account_response}}"
        self.assertTrue(_plan_errors(proposal, ["bank-account"]))

    def test_facts_and_now_can_be_read_in_model_instructions_without_prose_word_bans(self):
        context = fixture()
        context["plan"]["nodes"]["talk"]["task_message"] = "Use {{bg_facts}} and {{now}}. Do not use raw bg_state or customer_id."
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(validate_guarded_workflow(workflow, **context), [])

    def test_routing_prompt_can_read_facts_and_now_but_not_raw_engine_values(self):
        context = fixture()
        context["plan"]["nodes"]["talk"]["routing_prompt"] = "Route using current {{bg_facts}} and supplied {{now}}."
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(workflow["nodes"]["talk"]["routing_prompt"], context["plan"]["nodes"]["talk"]["routing_prompt"])
        self.assertEqual(validate_guarded_workflow(workflow, **context), [])
        for invalid in ("{{bg_state}}", {}, False):
            context["plan"]["nodes"]["talk"]["routing_prompt"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**context)

    def test_nested_condition_raw_reads_are_rejected_and_safe_views_are_allowed(self):
        for variable in ("bg_state", "bg_body", "customer_id", "create_promise_result"):
            context = fixture()
            context["plan"]["nodes"]["talk"]["transitions"][0]["condition"] = {
                "logic": "or", "conditions": [{"logic": "and", "conditions": [
                    {"variable": variable, "operator": "is_set"}]}]}
            with self.subTest(variable=variable), self.assertRaisesRegex(GuardCompileError, "conditions cannot read raw protected"):
                compile_guarded_workflow(**context)
        for variable in ("bg_facts", "now", "proposed_amount"):
            proposal = fixture()["plan"]
            proposal["nodes"]["talk"]["transitions"][0]["condition"] = {
                "logic": "or", "conditions": [{"variable": variable, "operator": "is_set"}]}
            self.assertEqual(_plan_errors(proposal), [])
        proposal["nodes"]["talk"]["transitions"][0]["condition"]["conditions"][0]["variable"] = "opaque_account_read_result"
        self.assertTrue(_plan_errors(proposal, ["opaque_account_read"]))

    def test_tampering_with_fact_writer_is_detected_by_exact_recompilation(self):
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        writer = workflow["nodes"]["bg_init"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
        writer["default"] += "\nvars['bg_facts'] = vars['bg_state']\n"
        self.assertTrue(validate_guarded_workflow(workflow, **self.context))


if __name__ == "__main__":
    unittest.main()

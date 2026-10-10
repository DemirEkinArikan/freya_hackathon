"""Independent guard regressions using invented, in-memory development inputs."""
from copy import deepcopy
import json
import unittest

from test_guards import fixture
from builder.contract import _operation_catalogue
from builder.guards import (GuardCompileError, _catalogue, _profile,
                            compile_guarded_workflow, run_guard_step,
                            validate_guarded_workflow)
from builder.safety import digest


def revised_context(updates=None):
    context = fixture()
    values = {"transfer_prerequisites": {"specialist": "account", "helpdesk": "none"},
              "product_order": {"mode": "account_order"},
              "promise_readback": {"product_fields": ["type"], "value_labels": {
                  "type": {"revolving": "revolving credit account", "fixed": "vehicle loan"}}}}
    values.update(updates or {})
    for kind, value in values.items():
        context["contract"]["active_rules"][kind] = {
            "value": deepcopy(value), "rule_ids": ["independent-" + kind],
            "provenance": [{"rule_id": "independent-" + kind, "id": "source",
                            "quote": "Invented independent policy witness."}]}
        context["execution_policy"]["profile"][kind] = deepcopy(value)
    rehash(context)
    return context


def rehash(context, refresh_api=False):
    if refresh_api:
        names = [op["name"] for op in context["contract"]["permitted_operations"]]
        context["contract"]["permitted_operations"] = _operation_catalogue(context["openapi"], names)
    for key in ("contract", "execution_policy"):
        context[key]["sha256"] = digest({k: v for k, v in context[key].items() if k != "sha256"})


class IndependentGuardRegressions(unittest.TestCase):
    def setUp(self):
        self.context = revised_context()
        self.profile = _profile(self.context["contract"], self.context["execution_policy"], self.context["plan"], None)
        self.catalog = _catalogue(self.context["contract"], self.context["openapi"], None)
        self.state = run_guard_step("init", None, profile=self.profile, catalog=self.catalog,
                                    customer_id="private-session-token", now="2031-05-02T11:00")[0]
        # The older vehicle product intentionally precedes the recent card in
        # returned order, while its internal ID sorts after the card's ID.
        self.products = [
            {"product_id": "internal-Z", "type": "fixed", "days_late": 28,
             "past_due": "600.11", "instalment_due": "82.10"},
            {"product_id": "internal-A", "type": "revolving", "days_late": 8,
             "past_due": "300.19", "minimum_due": "37.25"},
        ]

    def step(self, phase, action="", inputs=None, result=None):
        value = run_guard_step(phase, self.state, action, inputs, result,
                               profile=self.profile, catalog=self.catalog)
        self.state = value[0]
        return value

    def account(self):
        for field in self.profile["identity_fields"]:
            self.step("prepare", "verify_identity", {"field": field, "answer": "supplied"})
            self.step("result", result={"ok": True, "verified": True,
                                       "wrong_answers_left": self.profile["identity_wrong_limit"]})
        self.step("prepare", "get_account")
        self.step("result", result={"ok": True, "products": deepcopy(self.products)})

    def stage(self, pid="internal-Z", amount="82.10"):
        return self.step("prepare", "stage_promise", {"product_id": pid, "amount": amount, "date": "2031-05-06"})

    def confirm(self):
        self.step("prepare", "commit_promises")
        return self.step("confirm", inputs={"consent": "yes"})

    def test_metadata_hash_cannot_bless_changed_execution_hook(self):
        bundle = compile_guarded_workflow(**self.context)
        altered = deepcopy(bundle["workflow"])
        tool = altered["nodes"]["bg_commit_api"]["pre_actions"][0]
        tool["pre_process_code"] += "\nvars['bg_state'] = 'forged'\n"
        bundle["guard_manifest"]["workflow_sha256"] = digest(altered)
        bundle["guard_manifest"]["host_behavior_verified"] = True
        self.assertTrue(validate_guarded_workflow(altered, **self.context))

    def test_authored_extraction_cannot_write_confirmation_or_result_evidence(self):
        for variable in ("bg_consent", "bg_readback", "bg_body", "bg_identity_result_verified"):
            with self.subTest(variable=variable):
                context = deepcopy(self.context)
                context["plan"]["nodes"]["talk"]["extraction_schema"].append({"name": variable, "type": "string"})
                with self.assertRaises(GuardCompileError):
                    compile_guarded_workflow(**context)

    def test_policy_permits_preverification_handoff_without_account_disclosure(self):
        self.step("prepare", "stage_transfer", {"queue": "helpdesk"})
        body = self.step("prepare", "commit_transfer")[2]
        self.assertEqual(body, {"customer_id": "private-session-token", "queue": "helpdesk"})
        self.assertFalse(self.state["account_loaded"])
        self.assertFalse(self.state["verified_fields"])
        self.step("result", result={"ok": True})
        close = self.step("prepare", "close_call")[2]
        self.assertEqual((close["outcome"], close["reason"]), ("transferred", "dispute"))

    def test_policy_requiring_account_blocks_preverification_queue(self):
        with self.assertRaises(ValueError):
            self.step("prepare", "stage_transfer", {"queue": "specialist"})

    def test_today_callback_rejects_past_and_equal_supplied_time(self):
        context = revised_context({"callback_window": {"start": "09:15", "end": "16:45", "allow_today": True, "next_business_days": 2}})
        profile = _profile(context["contract"], context["execution_policy"], context["plan"], None)
        catalog = _catalogue(context["contract"], context["openapi"], None)
        for at in ("10:59", "11:00"):
            state = run_guard_step("init", None, profile=profile, catalog=catalog,
                                   customer_id="session", now="2031-05-02T11:00")[0]
            with self.subTest(at=at), self.assertRaises(ValueError):
                run_guard_step("prepare", state, "schedule_callback", {"date": "2031-05-02", "time": at}, profile=profile, catalog=catalog)

    def test_weekend_today_callback_is_allowed_when_policy_allows_today(self):
        context = revised_context({"callback_window": {"start": "09:15", "end": "16:45", "allow_today": True, "next_business_days": 1}})
        profile = _profile(context["contract"], context["execution_policy"], context["plan"], None)
        catalog = _catalogue(context["contract"], context["openapi"], None)
        state = run_guard_step("init", None, profile=profile, catalog=catalog,
                               customer_id="session", now="2031-05-03T11:00")[0]
        value = run_guard_step("prepare", state, "schedule_callback", {"date": "2031-05-03", "time": "12:00"}, profile=profile, catalog=catalog)
        self.assertEqual(value[1], "allow")

    def test_exhausted_product_cannot_restart_writes_with_same_or_changed_terms(self):
        self.account()
        self.stage()
        self.confirm()
        failure = {"ok": False, "error": "temporarily_unavailable", "_http_status": 503}
        self.step("result", result=failure)
        self.step("result", result=failure)
        self.assertEqual(self.state["promise_failures"]["internal-Z"], 2)
        for amount in ("82.10", "90.11"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.stage(amount=amount)

    def test_returned_account_order_controls_batch_write_order(self):
        self.account()
        self.stage()
        self.stage("internal-A", "37.25")
        first = self.confirm()[2]
        self.assertEqual(first["product_id"], "internal-Z")
        second = self.step("result", result={"ok": True, "promise_id": "actual-one"})[2]
        self.assertEqual(second["product_id"], "internal-A")

    def test_returned_order_controls_first_nonpaid_closing_reason(self):
        self.account()
        self.step("prepare", "stage_note", {"product_id": "internal-Z", "reason": "dispute"})
        self.step("prepare", "stage_note", {"product_id": "internal-A", "reason": "refused"})
        first = self.step("prepare", "finalize_notes")[2]
        self.assertEqual(first["product_id"], "internal-Z")
        self.step("result", result={"ok": True})
        self.step("result", result={"ok": True})
        close = self.step("prepare", "close_call")[2]
        self.assertEqual((close["outcome"], close["reason"]), ("no_promise", "dispute"))

    def test_customer_readback_uses_trusted_labels_and_omits_internal_ids(self):
        self.account()
        self.stage()
        value = self.step("prepare", "commit_promises")
        self.assertEqual(value[1], "confirm")
        readback = value[3]
        self.assertIn("vehicle loan", readback)
        self.assertIn("82.10", readback)
        self.assertIn("2031-05-06", readback)
        for secret in ("private-session-token", "internal-Z", "customer_id", "product_id"):
            self.assertNotIn(secret, readback)
        body = self.step("confirm", inputs={"consent": "yes"})[2]
        self.assertEqual(body["customer_id"], "private-session-token")
        self.assertEqual(body["product_id"], "internal-Z")

    def test_conflicting_request_aliases_cannot_overwrite_session_identity(self):
        context = revised_context({"action_fields": {"create_promise": {"customer_id": "product_id"}}})
        schema = context["openapi"]["paths"]["/create_promise"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        schema["required"].remove("customer_id")
        rehash(context, refresh_api=True)
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_arbitrary_string_result_field_cannot_be_success_evidence(self):
        context = revised_context({"result_fields": {"create_promise": {"ok": "promise_id"}}})
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_malformed_identity_verified_value_cannot_advance_normal_flow(self):
        self.step("prepare", "verify_identity", {"field": self.profile["identity_fields"][0], "answer": "supplied"})
        value = self.step("result", result={"ok": True, "verified": None, "wrong_answers_left": self.profile["identity_wrong_limit"]})
        self.assertEqual(value[1], "failure")
        self.assertTrue(self.state["quarantined"])
        self.assertFalse(self.state["verified_fields"])
        self.assertEqual(self.state["wrong_answers"], 0)

    def test_unknown_promise_result_never_establishes_closing_or_retry(self):
        self.account()
        self.stage()
        self.confirm()
        self.step("result", result={})
        self.assertTrue(self.state["quarantined"])
        self.assertFalse(self.state["recorded_promises"])
        for action in ("commit_promises", "close_call", "stage_transfer"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.step("prepare", action, {"queue": "helpdesk"})


if __name__ == "__main__":
    unittest.main()

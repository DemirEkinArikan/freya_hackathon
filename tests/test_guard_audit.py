"""Guard transitions executed as embedded code with independent synthetic inputs."""
from copy import deepcopy
import json
import unittest

import test_guard_regressions as regression
from builder.guards import GuardCompileError, _catalogue, _code, _profile, compile_guarded_workflow
from builder.validation import validate_code, validate_workflow


class GuardAuditTests(unittest.TestCase):
    def setUp(self):
        self.context = regression.revised_context({
            "confirmation_actions": ["commit_promises", "commit_transfer", "schedule_callback"]})
        self.profile = _profile(self.context["contract"], self.context["execution_policy"], self.context["plan"], None)
        self.catalog = _catalogue(self.context["contract"], self.context["openapi"], None)
        self.variables = {"customer_id": "synthetic-session", "now": "2031-05-02T11:00"}
        self.execute("init")
        self.products = [
            {"product_id": "asset-Z", "type": "fixed", "days_late": 28,
             "past_due": "600.11", "instalment_due": "82.10"},
            {"product_id": "asset-A", "type": "revolving", "days_late": 8,
             "past_due": "300.19", "minimum_due": "37.25"}]

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])

    def execute(self, phase, action="", inputs=None, result=None):
        inputs, result = inputs or {}, result or {}
        public = {key: "public_" + key for key in inputs}
        mapped = {key: "mapped_" + key for key in result}
        self.variables.update({public[key]: value for key, value in inputs.items()})
        self.variables.update({mapped[key]: value for key, value in result.items()})
        self.variables["bg_consent"] = inputs.get("consent", "")
        source = _code(phase, action, public, self.profile, self.catalog, {}, mapped)
        self.assertEqual(validate_code(source), [])
        exec(source, {"vars": self.variables})
        return self.variables["bg_route"], json.loads(self.variables["bg_body"] or '{}')

    def account(self):
        for field in self.profile["identity_fields"]:
            self.execute("prepare", "verify_identity", {"field": field, "answer": "invented-answer"})
            self.execute("result", "verify_identity", result={"ok": True, "verified": True, "wrong_answers_left": 4})
        self.execute("prepare", "get_account")
        self.execute("result", "get_account", result={"ok": True, "products": deepcopy(self.products)})

    def stage(self, pid="asset-Z", amount="82.10", day="2031-05-06"):
        return self.execute("prepare", "stage_promise", {"product_id": pid, "amount": amount, "date": day})

    def confirm(self):
        self.execute("prepare", "commit_promises")
        return self.execute("confirm", "commit_promises", {"consent": "yes"})

    def test_invalid_correction_cancels_previous_readback_and_requires_new_readback(self):
        self.account()
        self.stage()
        self.execute("prepare", "commit_promises")
        self.assertTrue(self.state["batch"])
        self.assertEqual(self.stage(amount="0")[0], "blocked")
        self.assertFalse(self.state["batch"])
        self.assertEqual(self.state["pending_promises"]["asset-Z"]["amount_cents"], 8210)
        self.assertEqual(self.execute("confirm", "commit_promises", {"consent": "yes"})[0], "blocked")
        self.assertFalse(self.state["attempts"])

    def test_no_unclear_or_missing_consent_cancels_snapshot_without_quarantine(self):
        self.account()
        self.stage()
        for consent in ("no", "unclear", ""):
            with self.subTest(consent=consent):
                self.execute("prepare", "commit_promises")
                self.assertEqual(self.execute("confirm", "commit_promises", {"consent": consent})[0], "blocked")
                self.assertFalse(self.state["batch"])
                self.assertFalse(self.state["quarantined"])
                self.assertFalse(self.state["attempts"])
        self.assertEqual(self.confirm()[0], "allow")

    def test_confirmation_node_cannot_send_another_actions_snapshot(self):
        self.account()
        self.stage()
        self.execute("prepare", "commit_promises")
        route, body = self.execute("confirm", "schedule_callback", {"consent": "yes"})
        self.assertEqual((route, body), ("blocked", {}))
        self.assertFalse(self.state["attempts"])
        self.assertTrue(self.state["quarantined"])
        self.assertEqual(self.state["pending_promises"]["asset-Z"]["amount_cents"], 8210)

    def test_changed_terms_require_fresh_confirmation_and_preserve_exact_new_body(self):
        self.account()
        self.stage()
        self.execute("prepare", "commit_promises")
        self.stage(amount="94.23", day="2031-05-07")
        self.execute("prepare", "commit_promises")
        self.assertIn("94.23", self.variables["bg_readback"])
        self.assertIn("2031-05-07", self.variables["bg_readback"])
        body = self.execute("confirm", "commit_promises", {"consent": "yes"})[1]
        self.assertEqual(body, {"customer_id": "synthetic-session", "product_id": "asset-Z", "amount": 94.23, "date": "2031-05-07"})

    def test_result_node_requires_its_actual_inflight_action(self):
        self.account()
        self.stage()
        self.confirm()
        self.assertEqual(self.execute("result", "schedule_callback", result={"ok": True})[0], "blocked")
        self.assertFalse(self.state["recorded_promises"])
        self.assertFalse(self.state["callbacks"])
        self.assertTrue(self.state["quarantined"])
        self.assertEqual(self.state["inflight"]["action"], "commit_promises")

    def test_exception_after_partial_write_preserves_recorded_and_uncertain_evidence(self):
        self.account()
        self.stage()
        self.stage("asset-A", "37.25")
        self.confirm()
        self.execute("result", "commit_promises", result={"ok": True, "promise_id": "persisted-first"})
        # Unicode digits pass str.isdigit but int cannot parse them. An invalid
        # envelope must not discard previously recorded evidence on exception.
        self.execute("result", "commit_promises", result={"ok": True, "promise_id": "untrusted-second", "_http_status": "²"})
        self.assertTrue(self.state["quarantined"])
        self.assertIn("asset-Z", self.state["recorded_promises"])
        self.assertNotIn("asset-A", self.state["recorded_promises"])
        self.assertEqual(self.state["inflight"]["semantic"]["product_id"], "asset-A")
        self.assertFalse(self.variables["bg_body"])

    def test_exhausted_second_write_keeps_success_and_allows_evidenced_close(self):
        self.account()
        self.stage()
        self.stage("asset-A", "37.25")
        self.confirm()
        self.execute("result", "commit_promises", result={"ok": True, "promise_id": "persisted-first"})
        failure = {"ok": False, "error": "temporarily_unavailable", "_http_status": 503}
        self.assertEqual(self.execute("result", "commit_promises", result=failure)[0], "allow")
        self.assertEqual(self.execute("result", "commit_promises", result=failure)[0], "failure")
        self.assertEqual(self.state["promise_failures"], {"asset-A": 2})
        self.assertIn("asset-Z", self.state["recorded_promises"])
        self.assertEqual(self.state["pending_notes"], {"asset-A": "service_error"})
        self.execute("prepare", "finalize_notes")
        self.execute("result", "finalize_notes", result={"ok": True})
        self.assertEqual(self.execute("prepare", "close_call")[1]["outcome"], "promise")

    def test_all_batch_items_meet_api_bounds_before_first_write(self):
        # Policy amounts and API numeric bounds are independent constraints.
        self.catalog["create_promise"]["request_schema"]["properties"]["amount"]["maximum"] = 100
        self.account()
        self.stage()
        self.stage("asset-A", "137.25")
        self.assertEqual(self.execute("prepare", "commit_promises")[0], "blocked")
        self.assertFalse(self.state["attempts"])
        self.assertFalse(self.state["inflight"])
        self.assertFalse(self.state["batch"])

    def test_next_request_failure_cannot_erase_just_recorded_success(self):
        self.account()
        self.stage()
        self.stage("asset-A", "37.25")
        self.confirm()
        # Exercise defense in depth if protected snapshot state is damaged.
        state = self.state
        state["batch"]["items"][1]["amount"] = 38.25
        self.variables["bg_state"] = json.dumps(state)
        self.execute("result", "commit_promises", result={"ok": True, "promise_id": "persisted-first"})
        self.assertTrue(self.state["quarantined"])
        self.assertIn("asset-Z", self.state["recorded_promises"])
        self.assertNotIn("asset-A", self.state["recorded_promises"])
        self.assertFalse(self.variables["bg_body"])

    def test_transfer_discards_unwritten_choices_preserves_success_and_wins_close(self):
        self.account()
        self.stage()
        self.confirm()
        self.execute("result", "commit_promises", result={"ok": True, "promise_id": "persisted-first"})
        self.execute("prepare", "stage_note", {"product_id": "asset-A", "reason": "dispute"})
        self.execute("prepare", "stage_transfer", {"queue": "helpdesk"})
        self.assertFalse(self.state["pending_notes"])
        self.assertFalse(self.state["pending_promises"])
        self.assertIn("asset-Z", self.state["recorded_promises"])
        self.assertEqual(self.execute("prepare", "commit_transfer")[0], "confirm")
        self.execute("confirm", "commit_transfer", {"consent": "yes"})
        self.execute("result", "commit_transfer", result={"ok": True})
        self.assertEqual(self.execute("prepare", "close_call")[1]["outcome"], "transferred")

    def test_identity_counter_is_monotone_across_factors_and_exhaustion(self):
        first, second = self.profile["identity_fields"]
        self.execute("prepare", "verify_identity", {"field": first, "answer": "answer"})
        self.execute("result", "verify_identity", result={"ok": True, "verified": True, "wrong_answers_left": 2})
        self.execute("prepare", "verify_identity", {"field": second, "answer": "answer"})
        self.execute("result", "verify_identity", result={"ok": True, "verified": False, "wrong_answers_left": 4, "hint": "api-exempt"})
        self.assertEqual(self.state["wrong_answers"], 2)
        self.assertEqual(self.state["verified_fields"], [first])
        self.execute("prepare", "verify_identity", {"field": second, "answer": "answer"})
        self.execute("result", "verify_identity", result={"ok": True, "verified": False, "wrong_answers_left": 0})
        self.assertEqual(self.state["identity_status"], "failed")
        self.assertEqual(self.execute("prepare", "get_account")[0], "blocked")
        self.assertEqual(self.execute("prepare", "close_call")[1]["outcome"], "not_verified")

    def test_all_close_results_and_exceptions_use_terminal_route_without_false_success(self):
        workflow = compile_guarded_workflow(**self.context)["workflow"]
        reconcile = workflow["nodes"]["bg_close_result"]
        self.assertEqual(reconcile["passthrough_default_transition"], "bg_terminal")
        self.assertEqual(reconcile["transitions"], [{"target_node": "bg_terminal", "description": "always", "condition": {"operator": "always"}}])
        self.assertEqual(validate_workflow(workflow, self.context["openapi"]), [])
        initial = deepcopy(self.variables)
        for result in ({"ok": True}, {"ok": False}, {}, {"ok": 1}, {"ok": True, "_http_status": "²"}):
            with self.subTest(result=result):
                self.variables = deepcopy(initial)
                self.execute("prepare", "close_call")
                self.execute("result", "close_call", result=result)
                self.assertTrue(self.state["close_attempted"])
                self.assertEqual(self.state["closed"], result.get("ok") is True and "_http_status" not in result)
                self.assertEqual(self.execute("prepare", "close_call")[0], "blocked")
                self.assertEqual(len(self.state["attempts"]), 1)

    def test_omitted_optional_execution_rule_cannot_bypass_confirmation(self):
        del self.context["execution_policy"]["profile"]["confirmation_actions"]
        regression.rehash(self.context)
        with self.assertRaisesRegex(GuardCompileError, "active execution rule omitted.*confirmation_actions"):
            compile_guarded_workflow(**self.context)

    def test_conditional_inspection_and_metadata_cannot_authorize_runtime(self):
        for location in ("active_rules", "contract"):
            for value in (None, {}, {"runtime_enforced": True}, {"status": "clear"}):
                with self.subTest(location=location, value=value):
                    context = deepcopy(self.context)
                    if location == "active_rules":
                        context["contract"]["active_rules"]["conditional_conflicts"] = {"value": value}
                    else:
                        context["contract"]["conditional_conflicts"] = value
                    regression.rehash(context)
                    with self.assertRaisesRegex(GuardCompileError, "lack a protected runtime guard"):
                        compile_guarded_workflow(**context)


if __name__ == "__main__":
    unittest.main()

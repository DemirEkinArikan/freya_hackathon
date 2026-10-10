"""Execute compiled hooks against independently authored HTTP fault envelopes."""
from copy import deepcopy
import json
import unittest

import test_guard_aliases as aliases
from builder.guards import compile_guarded_workflow, validate_guarded_workflow
from builder.validation import validate_workflow


class BodyIndependentErrorTests(unittest.TestCase):
    def setUp(self):
        self.harness = aliases.GuardAliasTests(methodName="runTest")
        self.harness.setUp()
        context = self.harness.context
        profile = deepcopy(context["execution_policy"]["profile"])
        actual = self.harness.aliases["create_promise"]
        # A fictional API deliberately defines a fault without a JSON schema.
        # Its source authorizes replay only for this exact transport result.
        context["openapi"]["paths"]["/create_promise"]["post"]["responses"]["409"] = {
            "description": "Nothing is persisted on this response; it may be repeated."}
        profile["nonpersistent_failures"] = {actual: [{"http_status": 409}]}
        self.harness.context = aliases.recompile_context(context, profile)
        self.harness.workflow = compile_guarded_workflow(**self.harness.context)["workflow"]
        self.harness.variables = {"customer_id": "synthetic-session", "now": "2031-05-02"}
        self.harness.code("bg_init")
        self.harness.account()
        self.harness.stage()
        self.harness.code("commit")
        self.harness.variables["bg_consent"] = "yes"
        self.harness.code("bg_commit_accept")
        self.start = deepcopy(self.harness.variables)

    @property
    def state(self):
        return json.loads(self.harness.variables["bg_state"])

    def envelope(self, result):
        node = self.harness.workflow["nodes"]["bg_commit_api"]
        envelope = deepcopy(result)
        exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            self.harness.variables[mapping["variable"]] = (
                envelope.get("data", {}).get(mapping["path"][5:], "")
                if isinstance(envelope, dict) and isinstance(envelope.get("data"), dict) else "")
        self.harness.code("bg_commit_result")
        return envelope

    def assert_quarantined(self):
        original = json.loads(self.start["bg_state"])
        self.assertTrue(self.state["quarantined"])
        self.assertEqual(self.state["inflight"], original["inflight"])
        self.assertEqual(self.state["recorded_promises"], original["recorded_promises"])
        self.assertFalse(self.harness.variables["bg_body"])
        self.assertFalse(self.state["promise_failures"])

    def test_bodyless_qualified_fault_preserves_request_and_counts_retry(self):
        for body in (None, "", "upstream unavailable", ["not an object"], {}, {"evidence_error": "opaque"}):
            with self.subTest(body=body):
                self.harness.variables = deepcopy(self.start)
                envelope = self.envelope({"status_code": 409, "data": body})
                self.assertEqual(envelope["data"]["bg_http_status"], 409)
                self.assertEqual(self.harness.variables["bg_route"], "allow")
                self.assertEqual(self.harness.variables["bg_body"], self.start["bg_body"])
                self.assertEqual(self.state["promise_failures"], {"internal-product-Z": 1})
                self.assertFalse(self.state["quarantined"])
                self.assertFalse(self.state["recorded_promises"])

    def test_two_bodyless_faults_apply_exact_source_disposition_without_success(self):
        self.envelope({"status_code": 409, "data": None})
        self.envelope({"status_code": 409, "data": ""})
        self.assertEqual(self.state["promise_failures"], {"internal-product-Z": 2})
        self.assertEqual(self.state["pending_notes"], {"internal-product-Z": "service_error"})
        self.assertFalse(self.state["pending_promises"])
        self.assertFalse(self.state["recorded_promises"])
        self.assertFalse(self.state["inflight"])
        self.assertEqual(self.harness.variables["bg_route"], "failure")

    def test_unqualified_or_invalid_transport_never_grants_bodyless_retry(self):
        for status in (408, 200, 201, "409x", True, None, 409.0):
            with self.subTest(status=status):
                self.harness.variables = deepcopy(self.start)
                self.envelope({"status_code": status, "data": None})
                self.assert_quarantined()

    def test_body_cannot_forge_transport_status_when_envelope_is_missing(self):
        self.envelope({"data": {"bg_http_status": 409, "evidence_ok": False}})
        self.assert_quarantined()

    def test_conflicting_success_record_or_malformed_flag_never_replays(self):
        for data in ({"evidence_ok": True}, {"evidence_ok": "true"},
                     {"evidence_ok": False, "evidence_promise_id": "existing-record"},
                     {"evidence_promise_id": "existing-record"},
                     {"evidence_ok": 0}, {"evidence_ok": "unknown"},
                     {"evidence_ok": {}, "evidence_error": "opaque"}):
            with self.subTest(data=data):
                self.harness.variables = deepcopy(self.start)
                self.envelope({"status_code": 409, "data": data})
                self.assert_quarantined()

    def test_success_transport_requires_explicit_negative_and_exact_error_match(self):
        profile = deepcopy(self.harness.context["execution_policy"]["profile"])
        actual = self.harness.aliases["create_promise"]
        profile["nonpersistent_failures"] = {actual: [{"http_status": 200, "error": "NOT_SAVED"}]}
        self.harness.context = aliases.recompile_context(self.harness.context, profile)
        self.harness.workflow = compile_guarded_workflow(**self.harness.context)["workflow"]
        for ok in (None, "", "unknown", True):
            with self.subTest(ok=ok):
                self.harness.variables = deepcopy(self.start)
                self.envelope({"status_code": 200, "data": {
                    "evidence_ok": ok, "evidence_error": "NOT_SAVED"}})
                self.assert_quarantined()
        self.harness.variables = deepcopy(self.start)
        self.envelope({"status_code": 200, "data": {
            "evidence_ok": False, "evidence_error": "NOT_SAVED"}})
        self.assertEqual(self.harness.variables["bg_route"], "allow")
        self.assertEqual(self.harness.variables["bg_body"], self.start["bg_body"])

    def test_compiled_status_hook_and_guard_are_exactly_validated(self):
        self.assertEqual(validate_guarded_workflow(self.harness.workflow, **self.harness.context), [])
        self.assertEqual(validate_workflow(self.harness.workflow, self.harness.context["openapi"]), [])

    def test_status_only_success_transport_matcher_does_not_reject_real_receipt(self):
        profile = deepcopy(self.harness.context["execution_policy"]["profile"])
        actual = self.harness.aliases["create_promise"]
        profile["nonpersistent_failures"] = {actual: [{"http_status": 200}]}
        self.harness.context = aliases.recompile_context(self.harness.context, profile)
        self.harness.workflow = compile_guarded_workflow(**self.harness.context)["workflow"]
        self.envelope({"status_code": 200, "data": {
            "evidence_ok": True, "evidence_promise_id": "new-real-record"}})
        self.assertEqual(self.harness.variables["bg_route"], "done")
        self.assertIn("internal-product-Z", self.state["recorded_promises"])
        self.assertFalse(self.state["quarantined"])
        self.assertFalse(self.state["inflight"])
        self.assertFalse(self.state["promise_failures"])


if __name__ == "__main__":
    unittest.main()

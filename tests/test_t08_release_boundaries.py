"""Independent local release sequences through aliased compiled node code.

The existing alias harness supplies a renamed schema baseline, node execution,
and wire response mapping. The new source guarantees, records, and sequences
are synthetic; they are neither evaluator fixtures nor hosted evidence.
"""
from copy import deepcopy
import json
import unittest

from builder.action_bindings import compile_action_bindings
from builder.contract import compile_policy_contract
from builder.guards import CONTROLLED_ENTRY_LIMIT, compile_guarded_workflow
from builder.safety import digest
import test_guard_aliases as aliases


class ReleaseBoundaryTests(unittest.TestCase):
    def session(self, *, callback_cap=2, note_cap=1):
        h = aliases.GuardAliasTests(methodName="runTest")
        h.setUp()
        context = deepcopy(h.context)
        profile = deepcopy(context["execution_policy"]["profile"])
        profile.update(
            call_variables=["contact_label"],
            attempt_scopes={"commit_promises": "product", "schedule_callback": "call",
                            "finalize_notes": "product"},
            callback_failure_limit=callback_cap, note_failure_limit=note_cap,
            definite_failure_resume=["schedule_callback", "finalize_notes"],
            confirmation_actions=["schedule_callback"],
            promise_readback={"product_fields": ["display_label"]})
        profile["wire_codes"]["reasons"].update(callback="WHY_BOOKED", no_callback="WHY_UNBOOKED")
        profile["close_reasons"]["C_F"] = ["WHY_BOOKED", "WHY_UNBOOKED"]
        profile["identity_close"]["unreached"]["reason"] = "WHY_UNBOOKED"
        profile["identity_close_choices"] = {"unreached": [
            {"outcome": "C_F", "reason": "WHY_BOOKED", "requires": "recorded_callback"},
            {"outcome": "C_F", "reason": "WHY_UNBOOKED", "requires": "no_recorded_callback"}]}
        close = context["openapi"]["paths"]["/close_call"]["post"]
        close["requestBody"]["content"]["application/json"]["schema"]["properties"]["input_reason"]["enum"].extend(
            ["WHY_BOOKED", "WHY_UNBOOKED"])
        products = context["openapi"]["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["evidence_products"]["items"]["properties"]
        products["display_label"] = {"type": "string"}
        for role in ["create_promise", "schedule_callback", "add_note"]:
            quote = "The synthetic release rejection persists no " + role + " record."
            context["openapi"]["paths"]["/" + role]["post"]["responses"]["503"] = {"description": quote}
            profile["nonpersistent_failures"][h.aliases[role]] = [{
                "http_status": 503, "error": "RELEASE_REJECTED",
                "api_source": {"pointer": "/paths/~1" + role + "/post/responses/503/description",
                               "quote": quote}}]
        text = "Independent release boundary policy: " + json.dumps(profile, sort_keys=True)
        source = {"id": "release-boundary", "file": "synthetic-release-policy.txt",
                  "text": text, "sha256": digest(text)}
        policy = {"version": 1, "goal": "Resolve independent release boundaries",
                  "allowed_actions": list(h.aliases.values()),
                  "sources": [{key: value for key, value in source.items() if key != "text"}],
                  "rules": [{"id": key, "kind": key, "value": value,
                             "source": {"id": source["id"], "quote": text}}
                            for key, value in profile.items()], "unsupported_constraints": []}
        context["contract"] = compile_policy_contract(policy, [source], context["openapi"])
        context["bindings"] = compile_action_bindings(policy, context["openapi"])
        execution = {"version": 1, "policy_sha256": digest(policy),
                     "bindings_sha256": context["bindings"]["sha256"], "profile": profile}
        execution["sha256"] = digest(execution)
        context["execution_policy"] = execution
        h.context = context
        h.workflow = compile_guarded_workflow(**context)["workflow"]
        h.variables = {"customer_id": "RELEASE-SESSION", "now": "2031-05-02T11:00",
                       "contact_label": "Synthetic Contact"}
        h.code("bg_init")
        return h

    def state(self, h):
        return json.loads(h.variables["bg_state"])

    def body(self, h):
        return json.loads(h.variables["bg_body"] or "{}")

    def assert_wire(self, h, entry, role):
        node = h.workflow["nodes"]["bg_" + entry + "_api"]
        self.assertEqual(node["pre_actions"][0]["function"]["name"], h.aliases[role])
        # Protected counters use semantic roles; the outgoing API function
        # still has the exact source-bound opaque operation identifier.
        self.assertEqual(self.state(h)["inflight"]["operation"], role)
        self.assertEqual(self.state(h)["inflight"]["body"], self.body(h))
        paths = {mapping["path"] for mapping in node["response_mapping"]}
        self.assertTrue({"data.evidence_ok", "data.evidence_error", "data.bg_http_status"} <= paths)
        if role == "create_promise":
            self.assertIn("data.evidence_promise_id", paths)
        self.assertEqual(node["passthrough_default_transition"], "bg_" + entry + "_result")

    def account(self, h, count=2):
        for field in ["challenge_A", "challenge_B"]:
            h.variables.update(answer_field=field, answer_text="synthetic-answer")
            h.code("identity")
            h.api_result("identity", {"evidence_ok": True, "evidence_verified": True,
                                      "evidence_wrong_answers_left": 4})
        h.code("account")
        h.api_result("account", {"evidence_ok": True, "evidence_products": [
            {"asset_reference": "release-asset-" + str(index), "kind_wire": "KIND_A",
             "delay_marker": 8, "arrears_value": 250, "floor_alpha": 20,
             "display_label": label}
            for index, label in enumerate(["daily account", "reserve account", "savings account"][:count])]})

    def promises(self, h, count=2):
        for index in range(count):
            h.variables.update(product="release-asset-" + str(index), amount=str(40 + index), day="2031-05-06")
            h.code("stage")
        h.code("commit")
        self.assertEqual(h.variables["bg_route"], "confirm")
        h.variables["bg_consent"] = "yes"
        h.code("bg_commit_accept")
        self.assertEqual(h.variables["bg_route"], "allow")
        self.assert_wire(h, "commit", "create_promise")
        self.assertEqual(self.body(h), {"input_customer_id": "RELEASE-SESSION",
                                       "input_product_id": "release-asset-0", "input_amount": 40.0,
                                       "input_date": "2031-05-06"})

    def callback(self, h, time="12:00"):
        h.variables.update(day="2031-05-06", at=time)
        h.code("callback")
        self.assertEqual(h.variables["bg_route"], "confirm")
        h.variables["bg_consent"] = "yes"
        h.code("bg_callback_accept")
        self.assertEqual(h.variables["bg_route"], "allow")
        self.assert_wire(h, "callback", "schedule_callback")
        self.assertEqual(self.body(h), {"input_customer_id": "RELEASE-SESSION",
                                       "input_date": "2031-05-06", "input_time": time})

    def reject(self, h, entry):
        h.api_result(entry, {"evidence_ok": False, "evidence_error": "RELEASE_REJECTED"}, status=503)

    def test_caller_claim_declined_callback_and_duplicate_cannot_create_extra_close(self):
        h = self.session()
        h.variables.update(outcome="C_F", close_reason="WHY_BOOKED",
                           caller_claim="The bank already booked it", callback_recorded=True)
        h.code("close")
        self.assertEqual(self.body(h), {})
        self.assertFalse(self.state(h)["callbacks"])
        self.assertFalse(self.state(h)["close_attempted"])
        h.variables.update(day="2031-05-06", at="12:00")
        h.code("callback")
        h.variables["bg_consent"] = "no"
        h.code("bg_callback_accept")
        self.assertFalse(self.state(h)["attempts"])
        self.assertFalse(self.state(h)["callbacks"])
        self.callback(h)
        h.api_result("callback", {"evidence_ok": True})
        callback = deepcopy(self.state(h)["callbacks"])
        h.code("callback")
        unconfirmed = deepcopy(h.variables)
        h.variables["bg_consent"] = "yes"
        h.code("bg_callback_accept")
        self.assertEqual(h.variables["bg_route"], "blocked")
        self.assertEqual(self.body(h), {})
        self.assertEqual(self.state(h)["callbacks"], callback)
        self.assertEqual(self.state(h)["attempt_counts"], {"schedule_callback:call": 1})
        # The refused duplicate quarantines the session; continue from the unconfirmed request.
        h.variables = unconfirmed
        h.variables.update(outcome="C_E", close_reason="WHY_F")
        h.code("close")
        self.assert_wire(h, "close", "close_call")
        self.assertEqual(self.body(h)["input_reason"], "WHY_BOOKED")
        h.api_result("close", {"evidence_ok": True})
        attempts = deepcopy(self.state(h)["attempts"])
        h.code("close")
        self.assertEqual(self.body(h), {})
        self.assertTrue(self.state(h)["closed"])
        self.assertEqual(self.state(h)["attempts"], attempts)
        self.assertEqual(len(attempts), 2)

    def test_initial_input_edits_after_rejection_do_not_reset_scope_or_close_subject(self):
        h = self.session()
        self.callback(h)
        original = self.body(h)
        h.variables.update(customer_id="REPLACEMENT-SESSION", now="2040-01-01T08:00",
                           contact_label="Replacement Contact", day="2040-01-02", at="15:00")
        self.reject(h, "callback")
        self.assertEqual(self.body(h), original)
        self.reject(h, "callback")
        h.variables.update(day="2031-05-06", at="13:00")
        h.code("callback")
        self.assertEqual(h.variables["bg_route"], "blocked")
        self.assertEqual(self.body(h), {})
        self.assertEqual(self.state(h)["attempt_counts"], {"schedule_callback:call": 2})
        self.assertEqual(self.state(h)["call_inputs"], {"contact_label": "Synthetic Contact"})
        self.assertEqual(self.state(h)["now_time"], "11:00")
        self.assertFalse(self.state(h)["callbacks"])
        h.variables.update(outcome="C_F", close_reason="WHY_UNBOOKED")
        h.code("close")
        self.assertEqual(self.body(h), {"input_customer_id": "RELEASE-SESSION",
                                       "input_outcome": "C_F", "input_reason": "WHY_UNBOOKED"})
        h.api_result("close", {"evidence_ok": True})
        self.assertTrue(self.state(h)["closed"])

    def test_partial_promise_success_then_qualified_exhaustion_preserves_evidenced_close(self):
        h = self.session()
        self.account(h)
        self.promises(h)
        first_key = self.state(h)["inflight"]["key"]
        h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "RELEASE-FIRST"})
        recorded = deepcopy(self.state(h)["recorded_promises"])
        second = self.body(h)
        h.variables.update(product="release-asset-0", amount="99", day="2040-01-01")
        self.reject(h, "commit")
        self.assertEqual(self.body(h), second)
        self.reject(h, "commit")
        self.assertEqual(self.state(h)["recorded_promises"], recorded)
        self.assertEqual(self.state(h)["attempts"][first_key], {"status": "recorded", "count": 1})
        self.assertEqual(self.state(h)["pending_notes"], {"release-asset-1": "service_error"})
        self.assertFalse(self.state(h)["quarantined"])
        h.code("stage")
        h.code("commit")
        self.assertEqual(h.variables["bg_route"], "blocked")
        self.assertEqual(self.body(h), {})
        self.assertEqual(self.state(h)["attempt_counts"], {
            "commit_promises:product:release-asset-0": 1, "commit_promises:product:release-asset-1": 2})
        h.code("finish")
        self.assert_wire(h, "finish", "add_note")
        self.assertEqual(self.body(h)["input_product_id"], "release-asset-1")
        h.api_result("finish", {"evidence_ok": True})
        h.variables.update(outcome="C_D", close_reason="WHY_S")
        h.code("close")
        self.assertEqual(self.body(h)["input_outcome"], "C_A")
        h.api_result("close", {"evidence_ok": True})
        self.assertEqual(self.state(h)["recorded_promises"], recorded)
        self.assertTrue(self.state(h)["closed"])

    def test_bank_note_phase_stays_sealed_after_definite_failure_without_any_record(self):
        h = self.session()
        self.account(h)
        for index in range(2):
            h.variables.update(product="release-asset-" + str(index), note_reason="NOTE_R")
            h.code("note")
        h.code("finish")
        self.reject(h, "finish")
        self.assertTrue(self.state(h)["notes_finalized"])
        self.assertFalse(self.state(h)["recorded_notes"])
        self.assertFalse(self.state(h)["inflight"])
        self.assertFalse(self.state(h)["quarantined"])
        pending = deepcopy(self.state(h)["pending_notes"])
        attempts = deepcopy(self.state(h)["attempts"])
        h.variables["queue"] = "QUEUE_S"
        h.code("transfer")
        h.code("handoff")
        self.assertEqual(self.body(h), {})
        self.assertFalse(self.state(h)["transfer"])
        self.assertEqual(self.state(h)["pending_notes"], pending)
        self.assertEqual(self.state(h)["attempts"], attempts)

    def test_retry_success_at_budget_abort_records_receipt_and_leaves_third_member_unsent(self):
        h = self.session()
        self.account(h, count=3)
        self.promises(h, count=3)
        h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "RELEASE-FIRST"})
        self.reject(h, "commit")
        self.assertEqual(self.body(h)["input_product_id"], "release-asset-1")
        h.variables["bg_control_entries"] = str(CONTROLLED_ENTRY_LIMIT)
        h.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "RELEASE-RETRY"})
        state = self.state(h)
        self.assertEqual(set(state["recorded_promises"]), {"release-asset-0", "release-asset-1"})
        self.assertEqual(state["promise_failures"], {"release-asset-1": 1})
        self.assertEqual(state["attempt_counts"], {"commit_promises:product:release-asset-0": 1,
                                                    "commit_promises:product:release-asset-1": 2})
        self.assertEqual(len(state["attempts"]), 2)
        self.assertTrue(all(item["status"] == "recorded" for item in state["attempts"].values()))
        self.assertEqual(set(state["pending_promises"]), {"release-asset-2"})
        self.assertFalse(state["inflight"])
        self.assertTrue(state["quarantined"])
        self.assertFalse(state["close_attempted"])
        self.assertEqual(self.body(h), {})


if __name__ == "__main__":
    unittest.main()

"""Evaluator-only dispatch-cap matrix over independently authored synthetic rules.

Expected wire calls and protected records are authored here from the frozen
source packet. The observer executes the compiled request preprocessor before
logging a mock dispatch; preparing bg_body is never counted as a wire call.
There is no model, live endpoint, hidden brief, or hosted-conversation evidence.
"""
import copy
import json
import unittest

from builder.safety import digest
from test_execution_requirements import add_rule, packet, rule
from test_goal_requirements import SealedSession, compiled, witness
from test_guard_attempt_scopes import callback_packet


SESSION = "SYNTHETIC-GOAL-SESSION"
PRODUCTS = [
    {"product_id": "CAP-PRODUCT-Z", "type": "revolving", "days_late": 8,
     "past_due": 300, "minimum_due": 37.25},
    {"product_id": "CAP-PRODUCT-A", "type": "fixed", "days_late": 28,
     "past_due": 600, "instalment_due": 82.10},
]
COLLECTION_ACTIONS = ["verify_identity", "get_account", "stage_promise",
                      "commit_promises", "stage_note", "finalize_notes", "close_call"]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def collection_packet(action, scope, *, cap=1, opaque=False):
    """Use the accepted invented packet; add literal source witnesses for caps."""
    policy, sources, api = packet(COLLECTION_ACTIONS, local=False, narrow=True)
    rule(policy, "product_order")["value"] = {"mode": "account_order"}
    limit = "promise_failure_limit" if action == "commit_promises" else "note_failure_limit"
    if any(row["kind"] == limit for row in policy["rules"]):
        rule(policy, limit)["value"] = cap
    else:
        add_rule(policy, limit, cap)
    add_rule(policy, "attempt_scopes", {action: scope})
    add_rule(policy, "promise_failure_disposition", {"reason": "service_error", "clear_pending": True})
    add_rule(policy, "nonpersistent_failures", {"create_promise": [{
        "http_status": 503, "error": "temporarily_unavailable", "api_source": {
            "pointer": "/paths/~1create_promise/post/responses/503/description",
            "quote": "Invented service rejection persists no write."}}]})
    if opaque:
        selected_roles = ["verify_identity", "get_account", "create_promise", "add_note", "close_call"]
        aliases = {role: "opaque_" + role for role in selected_roles}
        fields, results = {}, {}
        for role in selected_roles:
            operation = api["paths"]["/" + role]["post"]
            operation["operationId"] = aliases[role]
            request = operation["requestBody"]["content"]["application/json"]["schema"]
            fields[role] = {field: "input_" + field for field in request["properties"]}
            request["properties"] = {fields[role][key]: value for key, value in request["properties"].items()}
            request["required"] = [fields[role][key] for key in request["required"]]
            response = operation["responses"]["200"]["content"]["application/json"]["schema"]
            results[role] = {field: "evidence_" + field for field in response["properties"]}
            response["properties"] = {results[role][key]: value for key, value in response["properties"].items()}
            response["properties"]["evidence_ok"] = {"type": "string", "enum": ["true", "false"]}
        note_codes = {"refused": "NOTE_71", "paid": "NOTE_23", "dispute": "NOTE_84", "service_error": "NOTE_19"}
        note_request = api["paths"]["/add_note"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        note_request["properties"]["input_reason"]["enum"] = list(note_codes.values())
        rule(policy, "note_reasons")["value"] = list(note_codes.values())
        rule(policy, "wire_codes")["value"]["notes"] = note_codes
        rule(policy, "promise_failure_disposition")["value"]["reason"] = "NOTE_19"
        add_rule(policy, "tool_aliases", aliases)
        add_rule(policy, "action_fields", fields)
        add_rule(policy, "result_fields", results)
        policy["allowed_actions"] = list(aliases.values())
        failures = rule(policy, "nonpersistent_failures")["value"]
        failures["opaque_create_promise"] = failures.pop("create_promise")
    witness(policy, sources)
    return policy, sources, api


class DispatchSession(SealedSession):
    """Observe only explicit traversal of a compiled API node."""
    def __init__(self, context, workflow):
        self.calls = []
        self.dispatch_snapshots = []
        super().__init__(context, workflow)

    @property
    def body(self):
        return json.loads(self.variables.get("bg_body") or "{}")

    def confirm(self, action, answer="yes"):
        self.variables["bg_consent"] = answer
        self.code("bg_" + self.entries[action] + "_accept")

    def dispatch(self, action, data, status=200):
        if self.variables["bg_route"] != "allow":
            raise AssertionError("The observer cannot send a blocked or unconfirmed request")
        node = self.workflow["nodes"]["bg_" + self.entries[action] + "_api"]
        tool = node["pre_actions"][0]
        config = {"body": ""}
        exec(tool["pre_process_code"], {"config": config, "vars": self.variables})
        arguments = json.loads(config["body"])
        if not arguments:
            raise AssertionError("The compiled API node has no dispatchable body")
        self.calls.append({"tool": tool["function"]["name"], "arguments": arguments})
        self.dispatch_snapshots.append(copy.deepcopy(self.state))
        envelope = {"status_code": status, "data": copy.deepcopy(data)}
        exec(tool["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            value = envelope["data"].get(mapping["path"][5:], "")
            self.variables[mapping["variable"]] = canonical(value) if isinstance(value, (dict, list, bool)) else str(value) if value is not None else ""
        self.code("bg_" + self.entries[action] + "_result")


class S05DispatchCapTests(unittest.TestCase):
    def setUp(self):
        self.evidence = []
        self.compiled_contexts = []
        self.frozen_packets = []

    def session(self, values):
        _, context, workflow = compiled(*values)
        self.compiled_contexts.append(context)
        self.frozen_packets.append(copy.deepcopy(values))
        session = DispatchSession(context, workflow)
        session.source_metadata = {
            "compiled_context_index": len(self.compiled_contexts) - 1,
            "rule_ids": [row["id"] for row in values[0]["rules"]],
            "source_sha256": [row["sha256"] for row in values[1]],
            "policy_sha256": digest(values[0]), "openapi_sha256": digest(values[2]),
            "workflow_sha256": digest(workflow),
        }
        return session

    def wire(self, role, body, opaque=False):
        # Independent oracle transform, driven solely by authored packet labels.
        return {"tool": "opaque_" + role if opaque else role,
                "arguments": {("input_" + key if opaque else key): value for key, value in body.items()}}

    def receipt(self, opaque=False, **data):
        if not opaque:
            return data
        return {"evidence_" + key: ("true" if value else "false") if key == "ok" else value
                for key, value in data.items()}

    def account(self, session, opaque=False):
        calls = []
        for field in ["postal_code", "memorable_word"]:
            session.action("verify_identity", field=field, answer="invented-factor")
            calls.append(self.wire("verify_identity", {"customer_id": SESSION, "field": field,
                                                       "answer": "invented-factor"}, opaque))
            session.dispatch("verify_identity", self.receipt(opaque, ok=True, verified=True, wrong_answers_left=4))
        session.action("get_account")
        calls.append(self.wire("get_account", {"customer_id": SESSION}, opaque))
        session.dispatch("get_account", self.receipt(opaque, ok=True, products=PRODUCTS))
        return calls

    def attempts(self, writes):
        # writes: (semantic operation role, independently authored wire call, status)
        expected = {}
        for role, call, status in writes:
            key = role + ":" + canonical(call["arguments"])
            expected[key] = {"status": status, "count": expected.get(key, {}).get("count", 0) + 1}
        return expected

    def check(self, label, session, calls, attempts, counts, *, notes=None, callbacks=None,
              promises=None, quarantined=False):
        expected = {"ordered_calls": calls, "attempts": attempts, "attempt_counts": counts,
                    "recorded_notes": notes or {}, "callbacks": callbacks or [],
                    "recorded_promises": promises or {}, "quarantined": quarantined,
                    "ready_body": {}, "inflight": {}}
        state = session.state
        observed = {"ordered_calls": session.calls, "ready_body": session.body,
                    **{key: state[key] for key in expected if key not in ["ordered_calls", "ready_body"]}}
        self.evidence.append({"case": label, "expected": copy.deepcopy(expected),
                              "observed": copy.deepcopy(observed),
                              "source": copy.deepcopy(session.source_metadata),
                              "dispatch_snapshots": copy.deepcopy(session.dispatch_snapshots)})
        self.assertEqual(observed, expected)
        self.assertEqual(sum(item["count"] for item in attempts.values()),
                         sum(call["tool"] not in ["verify_identity", "get_account", "opaque_verify_identity", "opaque_get_account"] for call in session.calls))

    def test_callback_call_cap_blocks_changed_slot_after_one_actual_success(self):
        for opaque in [False, True]:
            with self.subTest(opaque=opaque):
                session = self.session(callback_packet(opaque=opaque))
                session.action("schedule_callback", date="2031-05-06", time="12:00")
                self.check("callback-prepared-no-dispatch-" + str(opaque), session, [], {}, {})
                session.confirm("schedule_callback", "no")
                self.check("callback-declined-no-dispatch-" + str(opaque), session, [], {}, {})
                session.action("schedule_callback", date="2031-05-06", time="12:00")
                session.confirm("schedule_callback")
                body = {"session_ref": SESSION, "pay_on": "2031-05-06", "at": "12:00"} if opaque else {
                    "customer_id": SESSION, "date": "2031-05-06", "time": "12:00"}
                call = {"tool": "reserve_contact" if opaque else "schedule_callback", "arguments": body}
                session.dispatch("schedule_callback", {"accepted": "true"} if opaque else {"ok": True})
                session.action("schedule_callback", date="2031-05-07", time="13:00")
                self.check("callback-call-cap-changed-slot-" + str(opaque), session, [call],
                           self.attempts([("schedule_callback", call, "recorded")]), {"schedule_callback:call": 1},
                           callbacks=[{"customer_id": SESSION, "date": "2031-05-06", "time": "12:00"}])

    def test_callback_retry_cap_counts_two_actual_identical_dispatches(self):
        for opaque in [False, True]:
            with self.subTest(opaque=opaque):
                session = self.session(callback_packet(cap=2, opaque=opaque))
                session.action("schedule_callback", date="2031-05-06", time="12:00")
                session.confirm("schedule_callback")
                body = {"session_ref": SESSION, "pay_on": "2031-05-06", "at": "12:00"} if opaque else {
                    "customer_id": SESSION, "date": "2031-05-06", "time": "12:00"}
                call = {"tool": "reserve_contact" if opaque else "schedule_callback", "arguments": body}
                session.variables[session.inputs["schedule_callback"]["date"]] = "2031-05-07"
                rejection = {"accepted": "false", "failure_code": "NOT_PERSISTED"} if opaque else {"ok": False, "error": "NOT_PERSISTED"}
                session.dispatch("schedule_callback", rejection)
                session.dispatch("schedule_callback", rejection)
                session.action("schedule_callback", date="2031-05-07", time="13:00")
                self.check("callback-retry-cap-frozen-body-" + str(opaque), session, [call, call],
                           self.attempts([("schedule_callback", call, "nonpersistent_failed")] * 2),
                           {"schedule_callback:call": 2})

    def test_callback_exact_request_scope_allows_independently_changed_slot(self):
        for opaque in [False, True]:
            with self.subTest(opaque=opaque):
                session = self.session(callback_packet(scope="exact_request", opaque=opaque))
                calls, callbacks = [], []
                for date, time in [("2031-05-06", "12:00"), ("2031-05-07", "13:00")]:
                    session.action("schedule_callback", date=date, time=time)
                    session.confirm("schedule_callback")
                    body = {"session_ref": SESSION, "pay_on": date, "at": time} if opaque else {
                        "customer_id": SESSION, "date": date, "time": time}
                    calls.append({"tool": "reserve_contact" if opaque else "schedule_callback", "arguments": body})
                    callbacks.append({"customer_id": SESSION, "date": date, "time": time})
                    session.dispatch("schedule_callback", {"accepted": "true"} if opaque else {"ok": True})
                session.action("schedule_callback", date="2031-05-06", time="12:00")
                counts = {"schedule_callback:request:schedule_callback:" + canonical(call["arguments"]): 1 for call in calls}
                self.check("callback-exact-request-scope-" + str(opaque), session, calls,
                           self.attempts([("schedule_callback", call, "recorded") for call in calls]), counts,
                           callbacks=callbacks)

    def test_note_call_cap_keeps_first_receipt_and_has_no_unsent_product_attempt(self):
        for opaque in [False, True]:
            with self.subTest(opaque=opaque):
                session = self.session(collection_packet("finalize_notes", "call", opaque=opaque))
                calls = self.account(session, opaque)
                for product in ["CAP-PRODUCT-Z", "CAP-PRODUCT-A"]:
                    session.action("stage_note", product_id=product, reason="NOTE_71" if opaque else "refused")
                session.action("finalize_notes")
                call = self.wire("add_note", {"customer_id": SESSION, "product_id": "CAP-PRODUCT-Z",
                                              "reason": "NOTE_71" if opaque else "refused"}, opaque)
                session.dispatch("finalize_notes", self.receipt(opaque, ok=True))
                self.check("note-call-cap-no-phantom-second-product-" + str(opaque), session, calls + [call],
                           self.attempts([("add_note", call, "recorded")]), {"finalize_notes:call": 1},
                           notes={"CAP-PRODUCT-Z": "refused"}, quarantined=True)

    def test_note_product_scope_allows_two_actual_product_dispatches(self):
        for opaque in [False, True]:
            with self.subTest(opaque=opaque):
                session = self.session(collection_packet("finalize_notes", "product", opaque=opaque))
                calls = self.account(session, opaque)
                for product in ["CAP-PRODUCT-Z", "CAP-PRODUCT-A"]:
                    session.action("stage_note", product_id=product, reason="NOTE_71" if opaque else "refused")
                session.action("finalize_notes")
                writes = []
                for product in ["CAP-PRODUCT-Z", "CAP-PRODUCT-A"]:
                    call = self.wire("add_note", {"customer_id": SESSION, "product_id": product,
                                                  "reason": "NOTE_71" if opaque else "refused"}, opaque)
                    calls.append(call)
                    writes.append(("add_note", call, "recorded"))
                    session.dispatch("finalize_notes", self.receipt(opaque, ok=True))
                self.check("note-product-scope-" + str(opaque), session, calls, self.attempts(writes),
                           {"finalize_notes:product:CAP-PRODUCT-Z": 1, "finalize_notes:product:CAP-PRODUCT-A": 1},
                           notes={"CAP-PRODUCT-Z": "refused", "CAP-PRODUCT-A": "refused"})

    def test_unconfirmed_and_declined_promise_batch_has_no_wire_attempt(self):
        for opaque in [False, True]:
            with self.subTest(opaque=opaque):
                session = self.session(collection_packet("commit_promises", "call", opaque=opaque))
                calls = self.account(session, opaque)
                session.action("stage_promise", product_id="CAP-PRODUCT-Z", amount="50.00", date="2031-05-06")
                session.action("stage_promise", product_id="CAP-PRODUCT-A", amount="82.10", date="2031-05-06")
                session.action("commit_promises")
                self.check("unconfirmed-promise-no-phantom-attempt-" + str(opaque), session, calls, {}, {})
                session.confirm("commit_promises", "no")
                self.check("declined-promise-no-phantom-attempt-" + str(opaque), session, calls, {}, {})

    def test_promise_call_and_product_caps_preserve_actual_success_only(self):
        for opaque in [False, True]:
            for scope in ["call", "product"]:
                with self.subTest(opaque=opaque, scope=scope):
                    session = self.session(collection_packet("commit_promises", scope, opaque=opaque))
                    calls = self.account(session, opaque)
                    session.action("stage_promise", product_id="CAP-PRODUCT-Z", amount="50.00", date="2031-05-06")
                    session.action("stage_promise", product_id="CAP-PRODUCT-A", amount="82.10", date="2031-05-06")
                    session.action("commit_promises")
                    self.check("promise-confirmation-pending-" + scope + "-" + str(opaque), session, calls, {}, {})
                    session.confirm("commit_promises")
                    writes, promises = [], {}
                    selected = [("CAP-PRODUCT-Z", 50.0, 5000)] + ([("CAP-PRODUCT-A", 82.1, 8210)] if scope == "product" else [])
                    for product, amount, cents in selected:
                        call = self.wire("create_promise", {"customer_id": SESSION, "product_id": product,
                                                           "amount": amount, "date": "2031-05-06"}, opaque)
                        calls.append(call)
                        writes.append(("create_promise", call, "recorded"))
                        promises[product] = {"amount_cents": cents, "date": "2031-05-06"}
                        session.dispatch("commit_promises", self.receipt(opaque, ok=True, promise_id="CAP-RECEIPT-" + product))
                    counts = {"commit_promises:call": 1} if scope == "call" else {
                        "commit_promises:product:CAP-PRODUCT-Z": 1, "commit_promises:product:CAP-PRODUCT-A": 1}
                    self.check("promise-cap-" + scope + "-" + str(opaque), session, calls,
                               self.attempts(writes), counts, promises=promises, quarantined=scope == "call")


if __name__ == "__main__":
    unittest.main()

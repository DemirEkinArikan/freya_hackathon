"""Independent S05 receipt matrix; evaluator-only, synthetic, no hosted calls.

The accepted invented policy/API packets provide wire names and policy rules.
Expected dispatches, persisted terms and uncertainty are authored below without
reading guard state or copying observed requests.  Replay executes the actual
compiled node code, API hooks, string response mapping and automatic routes.
Authentication/account reads are excluded from the irreversible attempt oracle.
"""
from copy import deepcopy
from functools import lru_cache
import json
import unittest
from unittest.mock import patch

from builder.guards import compile_guarded_workflow
from builder.safety import digest
from builder.validation import validate_workflow as original_validate_workflow
from test_guard_aliases import renamed_context
from test_guards import fixture
from tools.replay_guarded import replay


SESSION = "s05-independent-session"
TODAY = "2031-05-02"
DAY = "2031-05-06"
PRODUCTS = ("s05-alpha", "s05-beta", "s05-gamma")
AMOUNTS = ("20.17", "25.23", "30.31")
CENTS = (2017, 2523, 3031)
ROLES = ("verify_identity", "get_account", "create_promise", "add_note",
         "schedule_callback", "transfer_to_agent", "close_call")
WRITES = ROLES[2:]
FIELDS = ("answer_field", "answer_text", "product", "amount", "day",
          "note_reason", "queue", "outcome", "close_reason")


@lru_cache(maxsize=2)
def packet(opaque):
    """Only reuse accepted frozen synthetic source construction, no test oracle."""
    source = renamed_context()[0] if opaque else fixture()
    source["plan"]["nodes"]["talk"]["extraction_schema"] = [
        {"name": name, "type": "string", "min_confidence": 0.95}
        for name in FIELDS]
    source["plan"]["nodes"]["talk"]["max_node_turns"] = 100
    profile = source["execution_policy"]["profile"]
    aliases = profile.get("tool_aliases", {name: name for name in ROLES})
    return source, aliases, compile_guarded_workflow(**source)["workflow"]


_VALIDATED = {}


def memoized_exact_validation(workflow, openapi, **kwargs):
    """Validate each exact immutable input tuple once; never approve a new hash."""
    key = digest([workflow, openapi, kwargs])
    if key not in _VALIDATED:
        _VALIDATED[key] = original_validate_workflow(workflow, openapi, **kwargs)
    return list(_VALIDATED[key])


def wire_request(source, role, **semantic):
    # Wire names come directly from the frozen source profile/API, never from
    # compiled catalogue/body helpers or the workflow under evaluation.
    mapping = source["execution_policy"]["profile"].get("action_fields", {}).get(role, {})
    return {mapping.get(key, key): value for key, value in semantic.items()}


def receipt(source, aliases, role, body, status=200, *, omit_status=False):
    mapping = source["execution_policy"]["profile"].get("result_fields", {}).get(role, {})
    envelope = {"data": {mapping.get(key, key): deepcopy(value) for key, value in body.items()}}
    if not omit_status:
        envelope["status_code"] = deepcopy(status)
    return {"operation": aliases[role], "envelope": envelope}


def frame(source, action, **values):
    routes = source["plan"]["nodes"]["talk"]["transitions"]
    index = next(index for index, item in enumerate(routes) if item["target_node"] == action)
    return {"node": "talk", "values": values, "intent_transition": index}


def product_rows(opaque):
    # Account order and ascending lateness agree, so the two sourced ordering
    # profiles must dispatch the same independently authored semantic sequence.
    if opaque:
        return [{"asset_reference": pid, "kind_wire": kind, "delay_marker": index + 3,
                 "arrears_value": 100, floor: 20}
                for index, (pid, kind, floor) in enumerate(zip(
                    PRODUCTS, ("KIND_A", "KIND_B"), ("floor_alpha", "floor_beta")))]
    return [{"product_id": pid, "type": kind, "days_late": index + 3,
             "past_due": 100, floor: 20}
            for index, (pid, kind, floor) in enumerate(zip(
                PRODUCTS, ("revolving", "fixed"), ("minimum_due", "instalment_due")))]


def initial_script(source, aliases, opaque, count):
    identity = ["challenge_A", "challenge_B"] if opaque else ["postal_code", "memorable_word"]
    frames = [frame(source, "identity", answer_field=field, answer_text="synthetic-answer")
              for field in identity]
    frames += [frame(source, "account")]
    frames += [frame(source, "stage", product=PRODUCTS[index], amount=AMOUNTS[index], day=DAY)
               for index in range(count)]
    frames += [frame(source, "commit"),
               {"node": "bg_commit_confirm", "values": {"bg_consent": "yes"}}]
    responses = [receipt(source, aliases, "verify_identity", {
        "ok": True, "verified": True, "wrong_answers_left": 4}) for _ in identity]
    responses += [receipt(source, aliases, "get_account", {
        "ok": True, "products": product_rows(opaque)[:count]})]
    calls = [{"tool": aliases["verify_identity"], "arguments": wire_request(source, "verify_identity",
              customer_id=SESSION, field=field, answer="synthetic-answer")} for field in identity]
    calls += [{"tool": aliases["get_account"],
               "arguments": wire_request(source, "get_account", customer_id=SESSION)}]
    return {"version": 1, "initial_inputs": {"customer_id": SESSION, "now": TODAY},
            "frames": frames, "responses": responses}, calls


def promise_call(source, aliases, index):
    return {"tool": aliases["create_promise"], "arguments": wire_request(source, "create_promise",
            customer_id=SESSION, product_id=PRODUCTS[index], amount=CENTS[index] / 100, date=DAY)}


def attempt_key(call):
    return call["tool"] + ":" + json.dumps(call["arguments"], sort_keys=True,
                                          separators=(",", ":"), ensure_ascii=False)


def ledger(promises=None, *, closed=False):
    return {"promises": promises or {}, "notes": {}, "callbacks": [],
            "transfer": {}, "closed": closed}


class S05ReceiptMatrixTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.evidence = []
        self.compiled_contexts = []

    def run_case(self, label, opaque, fault, *, first_success=False, retry=False,
                 status=200, omit_status=False):
        source, aliases, workflow = packet(opaque)
        self.compiled_contexts.append(deepcopy(source))
        context = {key: value for key, value in source.items() if key != "openapi"}
        count = 2 if first_success else 1
        script, expected_calls = initial_script(source, aliases, opaque, count)
        expected_attempts = {}
        expected_promises = {}
        if first_success:
            script["responses"].append(receipt(source, aliases, "create_promise", {
                "ok": True, "promise_id": "s05-first-record"}))
            first = promise_call(source, aliases, 0)
            expected_calls.append(first)
            expected_attempts[attempt_key(first)] = {"status": "recorded", "count": 1}
            expected_promises[PRODUCTS[0]] = {"amount_cents": CENTS[0], "date": DAY}
        index = 1 if first_success else 0
        target = promise_call(source, aliases, index)
        expected_calls.append(target)
        script["responses"].append(receipt(source, aliases, "create_promise", fault,
                                           status, omit_status=omit_status))
        expected_attempts[attempt_key(target)] = {"status": "unknown_persistence", "count": 1}
        expected_closed = False
        if retry:
            # Source promise_failure_limit=2 and exact 503/error classification
            # authorize one repeat of the immutable consented request.
            script["responses"].append(receipt(source, aliases, "create_promise", {
                "ok": True, "promise_id": "s05-retry-record"}))
            expected_calls.append(deepcopy(target))
            expected_attempts[attempt_key(target)] = {"status": "recorded", "count": 2}
            expected_promises[PRODUCTS[index]] = {"amount_cents": CENTS[index], "date": DAY}
            script["frames"] += [frame(source, "close")]
            script["responses"].append(receipt(source, aliases, "close_call", {"ok": True}))
            outcome = "C_A" if opaque else "promise"
            close = {"tool": aliases["close_call"], "arguments": wire_request(source, "close_call",
                     customer_id=SESSION, outcome=outcome)}
            expected_calls.append(close)
            expected_attempts[attempt_key(close)] = {"status": "recorded", "count": 1}
            expected_closed = True
        else:
            # Tempting later frames must stay unused after unknown persistence.
            script["frames"] += [frame(source, "commit"), frame(source, "finish"),
                                  frame(source, "close")]
        with patch("tools.replay_guarded.validate_workflow", memoized_exact_validation):
            report = replay(workflow, context, source["openapi"], script)
        state = report["final_state"]
        write_operations = {aliases[role] for role in WRITES}
        observed_attempts = {key: deepcopy(value) for key, value in state["attempts"].items()
                             if key.split(":", 1)[0] in write_operations}
        # The internal 'sent' spelling is still uncertain when quarantine
        # retains that exact unresolved request. Classify its persistence from
        # these observed facts; the complete report preserves the raw label.
        unresolved = state["inflight"].get("key") if state["quarantined"] else None
        if unresolved in observed_attempts and observed_attempts[unresolved]["status"] == "sent":
            observed_attempts[unresolved]["status"] = "unknown_persistence"
        observed_ledger = {"promises": state["recorded_promises"], "notes": state["recorded_notes"],
                           "callbacks": state["callbacks"], "transfer": state["transfer"],
                           "closed": state["closed"]}
        expected = {"calls": expected_calls, "ledger": ledger(expected_promises, closed=expected_closed),
                    "attempts": expected_attempts}
        observed = {"calls": [{"tool": call["tool"], "arguments": call["arguments"]}
                              for call in report["calls"]],
                    "ledger": observed_ledger, "attempts": observed_attempts}
        self.evidence.append({"case": label + ("/opaque" if opaque else "/canonical"),
                              "contract_sha256": source["contract"]["sha256"],
                              "api_sha256": digest(source["openapi"]), "script": script,
                              "report": report,
                              "expected": expected, "observed": observed,
                              "replay_status": report["status"], "quarantined": state["quarantined"],
                              "frames_consumed": report["frames_consumed"],
                              "responses_consumed": report["responses_consumed"]})
        self.assertEqual(observed, expected, label)
        self.assertEqual(report["status"], "terminated")
        self.assertEqual(report["unused_responses"], 0)
        self.assertFalse(report["network_executed"])
        self.assertFalse(report["model_executed"])
        if retry:
            self.assertFalse(state["quarantined"])
            self.assertEqual(state["promise_failures"], {PRODUCTS[index]: 1})
            self.assertEqual(report["unused_frames"], 0)
            self.assertEqual(sum(step["node"] == "bg_commit_confirm" for step in report["steps"]), 1)
        else:
            self.assertTrue(state["quarantined"])
            self.assertFalse(state["close_attempted"])
            self.assertEqual(state["inflight"]["body"], target["arguments"])
            self.assertEqual(state["inflight"]["key"], attempt_key(target))
            self.assertEqual(report["unused_frames"], 3)
            remaining = range(1, 2) if first_success else range(1)
            self.assertEqual(state["pending_promises"], {
                PRODUCTS[item]: {"amount_cents": CENTS[item], "date": DAY} for item in remaining})

    def test_first_write_known_second_missing_receipt_preserves_record_and_stops_batch(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                self.run_case("first-known-second-unknown", opaque, {}, first_success=True,
                              omit_status=True)

    def test_positive_flag_with_error_never_claims_persistence_or_retries(self):
        for opaque in (False, True):
            for status in (200, 503):
                with self.subTest(opaque=opaque, status=status):
                    self.run_case("positive-plus-error-" + str(status), opaque, {
                        "ok": True, "promise_id": "s05-untrusted-record",
                        "error": "ERR_BUSY" if opaque else "temporarily_unavailable"},
                        first_success=True, status=status)

    def test_malformed_record_identity_never_becomes_a_successful_string(self):
        for opaque in (False, True):
            for value in (None, "", " ", 91, True, {}, []):
                with self.subTest(opaque=opaque, value=value):
                    self.run_case("bad-record-id-" + repr(value), opaque,
                                  {"ok": True, "promise_id": value}, first_success=True)

    def test_malformed_boolean_types_are_unknown_persistence(self):
        for opaque in (False, True):
            for value in (None, 1, 0, {}, [], "yes", " true"):
                with self.subTest(opaque=opaque, value=value):
                    self.run_case("bad-positive-type-" + repr(value), opaque,
                                  {"ok": value, "promise_id": ""}, first_success=True)

    def test_malformed_transport_status_cannot_authorize_recording(self):
        for opaque in (False, True):
            for status in (None, "", {}, [], True, 200.0, 99, 600, "0200", " 200", "200.0"):
                with self.subTest(opaque=opaque, status=status):
                    self.run_case("bad-status-" + repr(status), opaque,
                                  {"ok": True, "promise_id": "s05-untrusted-record"},
                                  first_success=True, status=status)

    def test_only_qualified_definite_negative_repeats_exact_request(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                self.run_case("qualified-negative-exact-retry", opaque, {
                    "ok": False, "error": "ERR_BUSY" if opaque else "temporarily_unavailable"},
                    status=503, retry=True)

    def test_qualified_negative_with_a_record_identity_stays_unknown(self):
        for opaque in (False, True):
            for identity in ("s05-contradictory-record", 14, {}, []):
                with self.subTest(opaque=opaque, identity=identity):
                    self.run_case("negative-with-record-" + repr(identity), opaque, {
                        "ok": False, "error": "ERR_BUSY" if opaque else "temporarily_unavailable",
                        "promise_id": identity}, first_success=True, status=503)


if __name__ == "__main__":
    unittest.main()

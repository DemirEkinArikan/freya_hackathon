"""Independent S05 control faults against actual frozen compiled node code.

Evaluator-only inputs; no model, network, live bank, or generator input. The
accepted synthetic policy/API fixtures supply rules, never expected outcomes.
Complete expected bank requests and ledger fields below are authored separately.
"""
from copy import deepcopy
from functools import lru_cache
import json
import unittest

from builder.guards import (CONTROLLED_ENTRY_LIMIT, compile_guarded_workflow,
                            validate_guarded_workflow)
from builder.safety import digest
from test_guard_aliases import renamed_context, recompile_context
from test_guards import fixture
from tools.replay_guarded import replay


SESSION = "s05-control-session"
DAY = "2031-05-06"
P1, P2 = "s05-product-first", "s05-product-second"
FIELDS = ("answer_field", "answer_text", "product", "amount", "day",
          "note_reason", "queue", "outcome", "close_reason")
ROLES = ("add_note", "close_call", "create_promise", "get_account",
         "schedule_callback", "transfer_to_agent", "verify_identity")


def context_for(opaque=False, scoped=False):
    context = renamed_context()[0] if opaque else fixture()
    if scoped:
        profile = deepcopy(context["execution_policy"]["profile"])
        profile["attempt_scopes"] = {"commit_promises": "product"}
        if opaque:
            context = recompile_context(context, profile)
        else:
            text = "S05 independently supplied synthetic product-scoped dispatch budget."
            source = {"id": "s05-control", "file": "s05-control-witness.txt", "text": text, "sha256": digest(text)}
            policy = {"version": 1, "goal": "Resolve synthetic accounts safely", "allowed_actions": list(ROLES),
                      "sources": [{key: value for key, value in source.items() if key != "text"}],
                      "rules": [{"id": kind, "kind": kind, "value": value,
                                 "source": {"id": source["id"], "quote": text}} for kind, value in profile.items()],
                      "unsupported_constraints": []}
            context["contract"] = compile_policy_contract(policy, [source], context["openapi"])
            execution = {"version": 1, "policy_sha256": digest(policy), "profile": profile}
            execution["sha256"] = digest(execution)
            context["execution_policy"] = execution
    talk = context["plan"]["nodes"]["talk"]
    talk["extraction_schema"] = [{"name": name, "type": "string", "min_confidence": .95}
                                  for name in FIELDS]
    talk["max_node_turns"] = 100
    return context


@lru_cache(maxsize=4)
def frozen_bundle(opaque=False, scoped=False):
    context = context_for(opaque, scoped)
    workflow = compile_guarded_workflow(**context)["workflow"]
    if validate_guarded_workflow(workflow, **context):
        raise AssertionError("synthetic frozen compilation failed validation")
    return context, workflow


class FrozenSession:
    """Transport harness: runs compiled code, hooks, and scalar mappings only."""
    def __init__(self, context, opaque, workflow):
        self.context, self.opaque = context, opaque
        self.workflow = deepcopy(workflow)
        self.variables = {"customer_id": SESSION, "now": "2031-05-02"}
        self.calls = []
        self.code("bg_init")

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])

    def code(self, node):
        code = self.workflow["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(code, {"vars": self.variables})

    def action(self, node, **values):
        self.variables.update(values)
        self.code(node)

    def accept(self, consent="yes"):
        self.variables["bg_consent"] = consent
        self.code("bg_commit_accept")

    def dispatch(self, entry, data, status=200):
        if self.variables["bg_route"] != "allow":
            raise AssertionError("attempted dispatch without controller authorization")
        node_id = "bg_" + entry + "_api"
        node = self.workflow["nodes"][node_id]
        tool = node["pre_actions"][0]
        config = {"body": ""}
        exec(tool["pre_process_code"], {"config": config, "vars": self.variables})
        self.calls.append({"tool": tool["function"]["name"], "node": node_id,
                           "arguments": json.loads(config["body"])})
        envelope = {"status_code": status, "data": {
            ("evidence_" + key if self.opaque else key): value
            for key, value in deepcopy(data).items()}}
        exec(tool["post_process_code"], {"result": envelope})
        for mapping in node["response_mapping"]:
            value = envelope["data"].get(mapping["path"][5:])
            self.variables[mapping["variable"]] = ("" if value is None else
                json.dumps(value, sort_keys=True, separators=(",", ":"))
                if isinstance(value, (dict, list, bool)) else str(value))
        self.code("bg_" + entry + "_result")

    def account(self):
        factors = ("challenge_A", "challenge_B") if self.opaque else ("postal_code", "memorable_word")
        for field in factors:
            self.action("identity", answer_field=field, answer_text="s05-answer")
            self.dispatch("identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        self.code("account")
        self.dispatch("account", {"ok": True, "products": products(self.opaque)})


def products(opaque):
    if opaque:
        return [{"asset_reference": P1, "kind_wire": "KIND_A", "delay_marker": 8,
                 "arrears_value": 300, "floor_alpha": 37.25},
                {"asset_reference": P2, "kind_wire": "KIND_B", "delay_marker": 28,
                 "arrears_value": 600, "floor_beta": 82.10}]
    return [{"product_id": P1, "type": "revolving", "days_late": 8,
             "past_due": 300, "minimum_due": 37.25},
            {"product_id": P2, "type": "fixed", "days_late": 28,
             "past_due": 600, "instalment_due": 82.10}]


def request(opaque, role, **values):
    """Independent API oracle: explicit role order and known request aliases."""
    tool = "operation_" + str(ROLES.index(role)) + "_release" if opaque else role
    return {"tool": tool, "node": "bg_" + {
        "verify_identity": "identity", "get_account": "account",
        "create_promise": "commit", "add_note": "finish",
        "schedule_callback": "callback", "transfer_to_agent": "handoff",
        "close_call": "close"}[role] + "_api",
        "arguments": {("input_" + key if opaque else key): value for key, value in values.items()}}


def account_calls(opaque):
    factors = ("challenge_A", "challenge_B") if opaque else ("postal_code", "memorable_word")
    return [request(opaque, "verify_identity", customer_id=SESSION, field=field, answer="s05-answer")
            for field in factors] + [request(opaque, "get_account", customer_id=SESSION)]


def promise_call(opaque, pid=P1, amount=50.0):
    return request(opaque, "create_promise", customer_id=SESSION, product_id=pid, amount=amount, date=DAY)


def attempt(role, call, status="recorded", count=1):
    # Ledger specification: semantic operation + canonical complete wire body.
    key = role + ":" + json.dumps(call["arguments"], sort_keys=True, separators=(",", ":"))
    return {key: {"status": status, "count": count}}


def ledger(**changes):
    expected = {"pending_promises": {}, "recorded_promises": {}, "pending_notes": {},
                "recorded_notes": {}, "promise_failures": {}, "attempts": {},
                "attempt_counts": {}, "transfer": {}, "callbacks": [],
                "closed": False, "close_attempted": False, "inflight": {},
                "batch": {}, "consent": "", "close": {}}
    expected.update(changes)
    return expected


class S05ControlMatrixTests(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        self.evidence = []
        self.compiled_contexts = []

    def session(self, opaque=False, scoped=False):
        original, workflow = frozen_bundle(opaque, scoped)
        context = deepcopy(original)
        self.compiled_contexts.append(context)
        return FrozenSession(context, opaque, workflow)

    def check(self, label, session, expected_calls, expected_ledger, **flags):
        observed = {key: session.state.get(key) for key in expected_ledger}
        item = {"case": label, "expected": {"calls": expected_calls, "ledger": expected_ledger, "flags": flags},
                "observed": {"calls": deepcopy(session.calls), "ledger": observed,
                             "flags": {key: session.variables.get(key) for key in flags}},
                "contract_sha256": session.context["contract"]["sha256"],
                "openapi_sha256": digest(session.context["openapi"]),
                "policy_sha256": session.context["execution_policy"]["policy_sha256"],
                "source_rules": sorted(session.context["contract"]["active_rules"]),
                "source_hashes": session.context["contract"].get("sources", [])}
        self.evidence.append(item)
        self.assertEqual(session.calls, expected_calls)
        self.assertEqual(observed, expected_ledger)
        for key, value in flags.items():
            self.assertEqual(session.variables.get(key), value, key)

    def stage(self, session, pid=P1, amount="50.00"):
        session.action("stage", product=pid, amount=amount, day=DAY)

    def test_staged_notes_and_promises_are_discarded_by_each_transfer(self):
        for opaque in (False, True):
            for queue, reason in (("specialist", "hardship"), ("helpdesk", "dispute")):
                with self.subTest(opaque=opaque, queue=queue):
                    s = self.session(opaque)
                    s.account()
                    self.stage(s)
                    s.action("note", product=P2, note_reason="NOTE_R" if opaque else "refused")
                    wire_queue = {"specialist": "QUEUE_G", "helpdesk": "QUEUE_S"}[queue] if opaque else queue
                    semantic_queue = {"specialist": "general", "helpdesk": "support"}[queue] if opaque else queue
                    s.action("transfer", queue=wire_queue)
                    s.code("finish")  # A stale finalize request cannot leak staged notes.
                    self.check("transfer-clears-staging", s, account_calls(opaque), ledger(
                        transfer={"queue": semantic_queue, "reason": reason, "status": "pending"}), bg_body="")
                    s.code("handoff")
                    s.dispatch("handoff", {"ok": True})
                    s.code("close")
                    transfer = request(opaque, "transfer_to_agent", customer_id=SESSION, queue=wire_queue)
                    close = request(opaque, "close_call", customer_id=SESSION,
                                    outcome="C_C" if opaque else "transferred",
                                    reason={"hardship": "WHY_H", "dispute": "WHY_D"}[reason] if opaque else reason)
                    s.dispatch("close", {"ok": True})
                    attempts = {**attempt("transfer_to_agent", transfer), **attempt("close_call", close)}
                    self.check("transfer-terminal", s, account_calls(opaque) + [transfer, close], ledger(
                        attempts=attempts, transfer={"queue": semantic_queue, "reason": reason, "status": "recorded"},
                        closed=True, close_attempted=True, close={"outcome": "transferred", "reason": reason}), bg_body="")

    def test_cancelled_commitment_becomes_note_without_promise_dispatch(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                s = self.session(opaque); s.account(); self.stage(s)
                s.code("commit"); s.accept("no")
                s.action("note", product=P1, note_reason="NOTE_R" if opaque else "refused")
                self.check("cancel-and-disposition", s, account_calls(opaque), ledger(
                    pending_notes={P1: "refused"}), bg_route="done", bg_body="")

    def test_absent_or_forged_confirmation_cannot_dispatch(self):
        for opaque in (False, True):
            for prepared, consent in ((True, ""), (True, "unclear"), (True, True),
                                      (True, "YES"), (False, "yes")):
                with self.subTest(opaque=opaque, prepared=prepared, consent=consent):
                    s = self.session(opaque); s.account(); self.stage(s)
                    if prepared:
                        s.code("commit")
                    s.accept(consent)
                    self.check("confirmation-rejected", s, account_calls(opaque), ledger(
                        pending_promises={P1: {"amount_cents": 5000, "date": DAY}}),
                        bg_route="blocked", bg_body="", bg_consent="")

    def test_recorded_promise_survives_correction_and_transfer_in_full_replay(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                s = self.session(opaque)
                context = s.context
                transitions = context["plan"]["nodes"]["talk"]["transitions"]
                def frame(action, **values):
                    return {"node": "talk", "values": values, "intent_transition": next(
                        i for i, row in enumerate(transitions) if row["target_node"] == action)}
                factors = ("challenge_A", "challenge_B") if opaque else ("postal_code", "memorable_word")
                frames = [frame("identity", answer_field=factor, answer_text="s05-answer") for factor in factors]
                frames += [frame("account"), frame("stage", product=P1, amount="50.00", day=DAY),
                           frame("commit"), {"node": "bg_commit_confirm", "values": {"bg_consent": "yes"}},
                           frame("stage", product=P1, amount="90.00", day=DAY),
                           frame("note", product=P2, note_reason="NOTE_D" if opaque else "dispute"),
                           frame("transfer", queue="QUEUE_S" if opaque else "helpdesk"),
                           frame("handoff"), frame("close")]
                promise = promise_call(opaque)
                transfer = request(opaque, "transfer_to_agent", customer_id=SESSION, queue="QUEUE_S" if opaque else "helpdesk")
                close = request(opaque, "close_call", customer_id=SESSION, outcome="C_C" if opaque else "transferred", reason="WHY_D" if opaque else "dispute")
                expected_calls = account_calls(opaque) + [promise, transfer, close]
                responses = []
                for call, data in zip(expected_calls, [{"ok": True, "verified": True, "wrong_answers_left": 4}]*2 + [
                        {"ok": True, "products": products(opaque)}, {"ok": True, "promise_id": "s05-record-one"}, {"ok": True}, {"ok": True}]):
                    responses.append({"operation": call["tool"], "envelope": {"status_code": 200,
                        "data": {("evidence_" + key if opaque else key): value for key, value in data.items()}}})
                report = replay(s.workflow, {key: value for key, value in context.items() if key != "openapi"},
                                context["openapi"], {"version": 1, "initial_inputs": {"customer_id": SESSION, "now": "2031-05-02"},
                                "frames": frames, "responses": responses})
                s.calls = [{key: call[key] for key in ("tool", "node", "arguments")} for call in report["calls"]]
                s.variables["bg_state"] = json.dumps(report["final_state"])
                self.check("record-preservation-replay", s, expected_calls, ledger(
                    recorded_promises={P1: {"amount_cents": 5000, "date": DAY}},
                    attempts={**attempt("create_promise", promise), **attempt("transfer_to_agent", transfer), **attempt("close_call", close)},
                    transfer={"queue": "support" if opaque else "helpdesk", "reason": "dispute", "status": "recorded"}, closed=True,
                    close_attempted=True, close={"outcome": "transferred", "reason": "dispute"}))
                self.assertEqual(report["status"], "terminated")
                self.assertEqual((report["unused_frames"], report["unused_responses"]), (0, 0))

    def test_budget_before_preparation_or_acceptance_creates_no_attempt(self):
        for opaque in (False, True):
            for node in ("commit", "bg_commit_accept"):
                with self.subTest(opaque=opaque, node=node):
                    s = self.session(opaque); s.account(); self.stage(s)
                    if node == "bg_commit_accept":
                        s.code("commit")
                    s.variables.update(bg_control_entries=str(CONTROLLED_ENTRY_LIMIT), bg_consent="yes")
                    s.code(node)
                    # Pre-acceptance batch contents are private controller data;
                    # assertion covers bank ledger and dispatch, independent of it.
                    expected = ledger(pending_promises={P1: {"amount_cents": 5000, "date": DAY}})
                    expected.pop("batch")
                    self.check("budget-before-dispatch", s, account_calls(opaque), expected,
                               bg_route="blocked", bg_body="", bg_quarantined="yes")

    def test_budget_after_receipt_preserves_first_record_without_unsent_second(self):
        for opaque in (False, True):
            for counter in (str(CONTROLLED_ENTRY_LIMIT), "corrupt", None, "²"):
                with self.subTest(opaque=opaque, counter=counter):
                    s = self.session(opaque); s.account(); self.stage(s); self.stage(s, P2, "82.10")
                    s.code("commit"); s.accept()
                    s.variables["bg_control_entries"] = counter
                    s.dispatch("commit", {"ok": True, "promise_id": "s05-record-one"})
                    first = promise_call(opaque)
                    expected = ledger(recorded_promises={P1: {"amount_cents": 5000, "date": DAY}},
                        pending_promises={P2: {"amount_cents": 8210, "date": DAY}},
                        attempts=attempt("create_promise", first))
                    expected.pop("batch"); expected.pop("consent")
                    self.check("budget-after-success", s, account_calls(opaque) + [first], expected,
                               bg_route="blocked", bg_body="", bg_quarantined="yes")
                    self.assertTrue(s.state["quarantined"])

    def test_budget_after_qualified_failure_creates_no_phantom_retry(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                s = self.session(opaque); s.account(); self.stage(s); s.code("commit"); s.accept()
                s.variables["bg_control_entries"] = str(CONTROLLED_ENTRY_LIMIT)
                s.dispatch("commit", {"ok": False, "error": "ERR_BUSY" if opaque else "temporarily_unavailable"}, 503)
                first = promise_call(opaque)
                expected = ledger(pending_promises={P1: {"amount_cents": 5000, "date": DAY}},
                    promise_failures={P1: 1}, attempts=attempt("create_promise", first, "nonpersistent_failed"))
                expected.pop("batch"); expected.pop("consent")
                self.check("budget-after-definite-failure", s, account_calls(opaque) + [first], expected,
                           bg_route="blocked", bg_body="", bg_quarantined="yes")

    def test_malformed_private_attempt_counter_cannot_authorize_dispatch(self):
        for opaque in (False, True):
            for count in (True, -1, "1", [], {}):
                with self.subTest(opaque=opaque, count=count):
                    s = self.session(opaque, scoped=True); s.account(); self.stage(s)
                    damaged = s.state
                    count_key = "commit_promises:product:" + P1
                    damaged["attempt_counts"] = {count_key: count}
                    s.variables["bg_state"] = json.dumps(damaged)
                    s.code("commit"); s.accept()
                    self.check("malformed-private-counter", s, account_calls(opaque), ledger(
                        pending_promises={P1: {"amount_cents": 5000, "date": DAY}}, attempt_counts={count_key: count}),
                        bg_route="blocked", bg_body="", bg_quarantined="yes")

    def test_duplicate_close_attempt_is_terminal_for_every_receipt(self):
        for opaque in (False, True):
            for status, ok, disposition in ((200, True, "recorded"), (503, True, "unknown_persistence"),
                                             (200, False, "unknown_persistence"), (None, True, "unknown_persistence")):
                with self.subTest(opaque=opaque, status=status, ok=ok):
                    s = self.session(opaque)
                    s.action("transfer", queue="QUEUE_S" if opaque else "helpdesk")
                    s.code("handoff"); s.dispatch("handoff", {"ok": True})
                    s.code("close"); s.dispatch("close", {"ok": ok}, status)
                    s.code("close")
                    transfer = request(opaque, "transfer_to_agent", customer_id=SESSION, queue="QUEUE_S" if opaque else "helpdesk")
                    close = request(opaque, "close_call", customer_id=SESSION, outcome="C_C" if opaque else "transferred", reason="WHY_D" if opaque else "dispute")
                    expected = ledger(attempts={**attempt("transfer_to_agent", transfer), **attempt("close_call", close, disposition)},
                        transfer={"queue": "support" if opaque else "helpdesk", "reason": "dispute", "status": "recorded"},
                        closed=(status == 200 and ok is True), close_attempted=True,
                        close={"outcome": "transferred", "reason": "dispute"})
                    # Contradictory/missing HTTP receipts retain the actual close
                    # inflight request. It cannot become permission for a second.
                    if status != 200:
                        expected["inflight"] = {"action": "close_call", "operation": "close_call", "body": close["arguments"],
                            "semantic": {"customer_id": SESSION, "outcome": "C_C" if opaque else "transferred", "reason": "WHY_D" if opaque else "dispute"},
                            "key": next(iter(attempt("close_call", close)))}
                    self.check("duplicate-close", s, [transfer, close], expected, bg_route="blocked", bg_body="")


if __name__ == "__main__":
    unittest.main()
from builder.contract import compile_policy_contract

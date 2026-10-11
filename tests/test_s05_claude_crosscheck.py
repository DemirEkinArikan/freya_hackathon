"""Claude S05 cross-checks of irreversible-action faults; local, synthetic, evaluator-only.

Expected ordered bank calls and ledgers are authored here from the frozen
invented policy rules (promise_failure_limit=2, the exact nonpersistent
503/error classification, promise_failure_disposition, close precedence) and
the permitted request contracts. They are never read from guard state. Each
case replays the actual compiled guarded workflow for canonical and opaque
(renamed field/enum) contracts. No model, bank, platform or simulation use.
"""
from copy import deepcopy
from functools import lru_cache
import json
import unittest
from unittest.mock import patch

from builder.guards import compile_guarded_workflow
from builder.safety import digest
from builder.validation import validate_workflow
from test_guard_aliases import renamed_context
from test_guards import fixture
from tools.replay_guarded import replay

CUSTOMER = "s05-claude-session"
TODAY, DAY = "2031-05-02", "2031-05-06"
PIDS = ("s05c-one", "s05c-two", "s05c-three")
CENTS = (2111, 2222, 3333)
FIELDS = ("answer_field", "answer_text", "product", "amount", "day", "note_reason",
          "queue", "outcome", "close_reason")
WRITE_ROLES = ("create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call")
# Wire spellings come from the frozen packets: canonical fixture vs renamed context.
BUSY = {False: "temporarily_unavailable", True: "ERR_BUSY"}
NOTE_SERVICE = {False: "service_error", True: "NOTE_S"}
OUTCOME_PROMISE = {False: "promise", True: "C_A"}


@lru_cache(maxsize=2)
def packet(opaque):
    source = renamed_context()[0] if opaque else fixture()
    talk = source["plan"]["nodes"]["talk"]
    talk["extraction_schema"] = [{"name": n, "type": "string", "min_confidence": 0.95} for n in FIELDS]
    talk["max_node_turns"] = 100
    return source, compile_guarded_workflow(**source)["workflow"]


_VALID = {}


def cached_validation(workflow, openapi, **kwargs):
    key = digest([workflow, openapi, kwargs])
    if key not in _VALID:
        _VALID[key] = validate_workflow(workflow, openapi, **kwargs)
    return list(_VALID[key])


def tool(source, role):
    return source["execution_policy"]["profile"].get("tool_aliases", {}).get(role, role)


def call(source, role, **semantic):
    names = source["execution_policy"]["profile"].get("action_fields", {}).get(role, {})
    return {"tool": tool(source, role), "arguments": {names.get(k, k): v for k, v in semantic.items()}}


def response(source, role, body, status=200, *, raw=False):
    names = {} if raw else source["execution_policy"]["profile"].get("result_fields", {}).get(role, {})
    envelope = {"data": {names.get(k, k): v for k, v in body.items()}}
    if status is not None:
        envelope["status_code"] = status
    return {"operation": tool(source, role), "envelope": envelope}


def frame(source, target, **values):
    routes = source["plan"]["nodes"]["talk"]["transitions"]
    return {"node": "talk", "values": values,
            "intent_transition": next(i for i, r in enumerate(routes) if r["target_node"] == target)}


def rows(opaque, count):
    kinds = ("KIND_A", "KIND_B") if opaque else ("revolving", "fixed")
    floors = ("floor_alpha", "floor_beta") if opaque else ("minimum_due", "instalment_due")
    names = (("asset_reference", "kind_wire", "delay_marker", "arrears_value") if opaque
             else ("product_id", "type", "days_late", "past_due"))
    return [{names[0]: PIDS[i], names[1]: kinds[i % 2], names[2]: i + 3, names[3]: 100,
             floors[i % 2]: 20} for i in range(count)]


def promise(source, index):
    return call(source, "create_promise", customer_id=CUSTOMER, product_id=PIDS[index],
                amount=CENTS[index] / 100, date=DAY)


def committed_script(source, opaque, count):
    """Identity, account and one consented promise batch; returns script and expected calls."""
    challenge = ("challenge_A", "challenge_B") if opaque else ("postal_code", "memorable_word")
    frames = [frame(source, "identity", answer_field=f, answer_text="synthetic") for f in challenge]
    frames.append(frame(source, "account"))
    frames += [frame(source, "stage", product=PIDS[i], amount=str(CENTS[i] / 100), day=DAY)
               for i in range(count)]
    frames += [frame(source, "commit"), {"node": "bg_commit_confirm", "values": {"bg_consent": "yes"}}]
    responses = [response(source, "verify_identity", {"ok": True, "verified": True,
                                                      "wrong_answers_left": 4}) for _ in challenge]
    responses.append(response(source, "get_account", {"ok": True, "products": rows(opaque, count)}))
    calls = [call(source, "verify_identity", customer_id=CUSTOMER, field=f, answer="synthetic")
             for f in challenge]
    calls.append(call(source, "get_account", customer_id=CUSTOMER))
    script = {"version": 1, "initial_inputs": {"customer_id": CUSTOMER, "now": TODAY},
              "frames": frames, "responses": responses}
    return script, calls


def key(source, entry):
    # Private attempt keys pair the semantic role with the exact wire body.
    aliases = source["execution_policy"]["profile"].get("tool_aliases", {})
    role = next((r for r, alias in aliases.items() if alias == entry["tool"]), entry["tool"])
    return role + ":" + json.dumps(entry["arguments"], sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False)


class S05ClaudeCrossCheckTests(unittest.TestCase):
    maxDiff = None

    def run_replay(self, source, workflow, script):
        context = {k: v for k, v in source.items() if k != "openapi"}
        with patch("tools.replay_guarded.validate_workflow", cached_validation):
            report = replay(workflow, context, source["openapi"], script)
        self.assertFalse(report["network_executed"])
        self.assertFalse(report["model_executed"])
        return report, report["final_state"]

    def writes(self, state):
        return {k: v for k, v in state["attempts"].items() if k.split(":", 1)[0] in WRITE_ROLES}

    def observed_calls(self, report):
        return [{"tool": c["tool"], "arguments": c["arguments"]} for c in report["calls"]]

    def test_qualified_negative_exhaustion_stops_at_limit_and_continues_batch(self):
        # promise_failure_limit=2: two exact 503/declared-error rejections allow
        # exactly two identical dispatches. The disposition clears that pending
        # promise, stages a service_error note and continues with the next item.
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                source, workflow = packet(opaque)
                script, calls = committed_script(source, opaque, 2)
                busy = {"ok": False, "error": BUSY[opaque]}
                script["responses"] += [response(source, "create_promise", busy, 503),
                                        response(source, "create_promise", busy, 503),
                                        response(source, "create_promise", {"ok": True, "promise_id": "s05c-rec"})]
                calls += [promise(source, 0), promise(source, 0), promise(source, 1)]
                note = call(source, "add_note", customer_id=CUSTOMER, product_id=PIDS[0],
                            reason=NOTE_SERVICE[opaque])
                close = call(source, "close_call", customer_id=CUSTOMER, outcome=OUTCOME_PROMISE[opaque])
                script["frames"] += [frame(source, "finish"), frame(source, "close")]
                script["responses"] += [response(source, "add_note", {"ok": True}),
                                        response(source, "close_call", {"ok": True})]
                calls += [note, close]
                report, state = self.run_replay(source, workflow, script)
                self.assertEqual(self.observed_calls(report), calls)
                self.assertEqual(self.writes(state), {
                    key(source, calls[3]): {"status": "nonpersistent_failed", "count": 2},
                    key(source, calls[5]): {"status": "recorded", "count": 1},
                    key(source, note): {"status": "recorded", "count": 1},
                    key(source, close): {"status": "recorded", "count": 1}})
                self.assertEqual(state["recorded_promises"], {PIDS[1]: {"amount_cents": CENTS[1], "date": DAY}})
                self.assertEqual(state["recorded_notes"], {PIDS[0]: "service_error"})
                self.assertEqual(state["promise_failures"], {PIDS[0]: 2})
                self.assertEqual((state["pending_promises"], state["quarantined"], state["closed"]), ({}, False, True))
                self.assertEqual((report["status"], report["unused_frames"], report["unused_responses"]),
                                 ("terminated", 0, 0))

    def unqualified_case(self, opaque, body, status, *, raw=False):
        """Fault on item `bad` of a consented two-product batch; no retry, no later attempt.

        The promise readback labels products by type only (two enum values), so
        the frozen policy admits at most two products per readable batch.
        """
        for bad in (0, 1):
            with self.subTest(fault_index=bad):
                source, workflow = packet(opaque)
                script, calls = committed_script(source, opaque, 2)
                if bad:
                    script["responses"].append(response(source, "create_promise",
                                                        {"ok": True, "promise_id": "s05c-first"}))
                script["responses"].append(response(source, "create_promise", body, status, raw=raw))
                calls += [promise(source, i) for i in range(bad + 1)]
                # Tempting later responses and frames must stay unused.
                script["responses"] += [response(source, "create_promise", {"ok": True, "promise_id": "s05c-x"})] * 2
                script["frames"] += [frame(source, "commit"), frame(source, "finish"), frame(source, "close")]
                report, state = self.run_replay(source, workflow, script)
                self.assertEqual(self.observed_calls(report), calls)
                attempts = self.writes(state)
                fault = key(source, calls[-1])
                self.assertEqual(set(attempts), {key(source, c) for c in calls[3:]})  # no phantom unsent/retry key
                self.assertEqual(attempts[fault]["count"], 1)
                self.assertNotIn(attempts[fault]["status"], ("recorded", "nonpersistent_failed"))
                if bad:
                    self.assertEqual(attempts[key(source, calls[3])], {"status": "recorded", "count": 1})
                self.assertEqual(state["recorded_promises"], {PIDS[0]: {"amount_cents": CENTS[0], "date": DAY}}
                                 if bad else {})
                self.assertEqual(state["pending_promises"], {PIDS[i]: {"amount_cents": CENTS[i], "date": DAY}
                                                             for i in range(bad, 2)})
                self.assertTrue(state["quarantined"])
                self.assertFalse(state["close_attempted"])
                self.assertEqual((state["recorded_notes"], state["callbacks"], state["transfer"], state["closed"]),
                                 ({}, [], {}, False))
                self.assertEqual((report["status"], report["unused_responses"], report["unused_frames"]),
                                 ("terminated", 2, 3))

    def test_unqualified_or_cross_spelled_negative_never_retries(self):
        for opaque in (False, True):
            other = BUSY[not opaque]
            for label, body, status in (
                    ("cross-spelled-enum", {"ok": False, "error": other}, 503),
                    ("wrong-status", {"ok": False, "error": BUSY[opaque]}, 500),
                    ("case-swapped", {"ok": False, "error": BUSY[opaque].swapcase()}, 503),
                    ("padded", {"ok": False, "error": BUSY[opaque] + " "}, 503),
                    ("missing-error", {"ok": False}, 503),
                    ("missing-status", {"ok": False, "error": BUSY[opaque]}, None)):
                with self.subTest(opaque=opaque, case=label):
                    self.unqualified_case(opaque, body, status)

    def test_cross_contract_result_field_names_never_count_as_success(self):
        for opaque in (False, True):
            source = packet(opaque)[0]
            names = packet(not opaque)[0]["execution_policy"]["profile"].get("result_fields", {}).get(
                "create_promise", {})
            foreign = {names.get(k, k): v for k, v in {"ok": True, "promise_id": "s05c-foreign"}.items()}
            own_ok = source["execution_policy"]["profile"].get("result_fields", {}).get(
                "create_promise", {}).get("ok", "ok")
            mixed = {own_ok: True, ("promise_id" if opaque else "evidence_promise_id"): "s05c-mixed"}
            for label, body in (("foreign-names", foreign), ("mixed-names", mixed)):
                with self.subTest(opaque=opaque, case=label):
                    self.unqualified_case(opaque, body, 200, raw=True)

    def test_missing_receipt_mid_batch_has_no_phantom_unsent_attempt(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                self.unqualified_case(opaque, {}, None)


if __name__ == "__main__":
    unittest.main()

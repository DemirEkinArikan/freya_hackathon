"""Execute compiled guard routes; never infer liveness from graph reachability."""
from copy import deepcopy
import json
import unittest

import test_guard_aliases as aliases
from builder.guards import validate_guarded_workflow
from builder.validation import validate_workflow


class CompiledTerminationTests(unittest.TestCase):
    def setUp(self):
        self.harness = aliases.GuardAliasTests(methodName="runTest")
        self.harness.setUp()

    @property
    def state(self):
        return json.loads(self.harness.variables["bg_state"])

    def target(self, name):
        def matches(condition):
            if condition.get("operator") == "always":
                return True
            if "conditions" in condition:
                values = [matches(item) for item in condition["conditions"]]
                return any(values) if condition.get("logic") == "or" else all(values)
            return (condition.get("operator") == "equals"
                    and self.harness.variables.get(condition["variable"]) == condition["operand"])
        node = self.harness.workflow["nodes"][name]
        target = next((item["target_node"] for item in node["transitions"]
                       if matches(item["condition"])), node.get("passthrough_default_transition"))
        # Execute the authenticated controller gate before asserting the same
        # public recovery destination; an exhausted gate is not bypassed.
        if target and target.startswith("bg_gate_"):
            self.harness.code(target)
            return self.target(target)
        return target

    def assert_terminal(self, name, terminal):
        self.assertEqual(self.target(name), terminal)
        node = self.harness.workflow["nodes"][terminal]
        self.assertEqual(node["node_type"], "end_call")
        self.assertEqual(node["on_enter"], {"type": "silent"})
        self.assertEqual(node["transitions"], [])
        self.assertFalse(self.harness.variables["bg_body"])

    def commit(self):
        self.harness.code("commit")
        self.harness.variables["bg_consent"] = "yes"
        self.harness.code("bg_commit_accept")

    def test_unknown_write_cannot_enter_talk_close_recovery_cycle(self):
        self.harness.account()
        self.harness.stage()
        self.harness.stage("internal-product-A", "82.10")
        self.commit()
        self.harness.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "first"})
        recorded, pending = deepcopy(self.state["recorded_promises"]), deepcopy(self.state["inflight"])
        self.harness.api_result("commit", {"evidence_ok": True}, status=503)
        self.assertTrue(self.state["quarantined"])
        self.assertEqual(self.state["recorded_promises"], recorded)
        self.assertEqual(self.state["inflight"], pending)
        self.assertFalse(self.state["close_attempted"])
        self.assert_terminal("bg_commit_result", "bg_quarantine")

    def test_turn_limit_with_unresolved_account_terminates_without_inventing_close(self):
        self.assertEqual(validate_guarded_workflow(self.harness.workflow, **self.harness.context), [])
        self.assertEqual(validate_workflow(self.harness.workflow, self.harness.context["openapi"]), [])
        self.harness.account()
        gate = self.harness.workflow["nodes"]["talk"]["max_turn_transition"]
        self.harness.code(gate)
        close = self.target(gate)
        self.assertEqual(close, "close")
        self.harness.code(close)
        self.assertTrue(self.state["quarantined"])  # a refused close is flagged, never an unflagged end
        self.assertFalse(self.state["close_attempted"])
        self.assertFalse(self.state["attempts"])
        self.assertFalse(self.state["closed"])
        self.assert_terminal(close, "bg_quarantine")

    def test_preexisting_quarantine_overrides_prepare_confirm_and_result_recovery(self):
        self.harness.account()
        self.harness.stage()
        self.commit()
        state = self.state
        state["quarantined"] = True
        state["recorded_promises"]["already-recorded"] = {"amount_cents": 1000, "date": "2031-05-06"}
        initial = deepcopy(self.harness.variables)
        initial["bg_state"] = json.dumps(state)
        for name in ("identity", "stage", "commit", "close", "bg_commit_accept", "bg_commit_result"):
            with self.subTest(node=name):
                self.harness.variables = deepcopy(initial)
                self.harness.code(name)
                self.assertEqual(self.state["inflight"], state["inflight"])
                self.assertEqual(self.state["recorded_promises"], state["recorded_promises"])
                self.assert_terminal(name, "bg_quarantine")

    def test_malformed_state_fails_to_terminal_in_every_execution_phase(self):
        initial = deepcopy(self.harness.variables)
        for name in ("stage", "close", "bg_commit_accept", "bg_commit_result"):
            for malformed in ("not-json", "[]", "null", "{}"):
                with self.subTest(node=name, state=malformed):
                    self.harness.variables = deepcopy(initial)
                    self.harness.variables["bg_state"] = malformed
                    self.harness.code(name)
                    self.assert_terminal(name, "bg_quarantine")

    def test_projection_failure_seals_route_and_preserves_known_records(self):
        self.harness.account()
        state = self.state
        state["recorded_promises"]["already-recorded"] = {"amount_cents": 1000, "date": "2031-05-06"}
        self.harness.variables["bg_state"] = json.dumps(state)
        source = self.harness.workflow["nodes"]["stage"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        # Inject only a projection fault between the real kernel and its real
        # wrapper; the candidate itself stays unchanged and exactly validated.
        marker = "\nvars['bg_route'] = 'blocked'"
        kernel, wrapper = source.split(marker, 1)
        source = kernel + "\ndef guard_facts(*args, **kwargs):\n    raise ValueError('injected projection fault')\n" + marker + wrapper
        self.harness.variables.update(product="internal-product-Z", amount="50.00", day="2031-05-06")
        exec(source, {"vars": self.harness.variables})
        self.assertEqual(self.state["recorded_promises"], state["recorded_promises"])
        self.assertTrue(self.state["quarantined"])
        self.assert_terminal("stage", "bg_quarantine")

    def test_partial_or_mistyped_state_never_masquerades_as_correctable_input(self):
        self.harness.account()
        self.harness.stage()
        self.commit()
        initial = deepcopy(self.harness.variables)
        original = self.state
        for key, value in original.items():
            # Keep the fact-count fields intact when removing unrelated keys;
            # this was the gap discovered by the independent reviewer.
            for change in ("missing", "wrong_type"):
                for node in ("identity", "stage", "close", "bg_commit_accept", "bg_commit_result"):
                    with self.subTest(key=key, mutation=change, node=node):
                        state = deepcopy(original)
                        if change == "missing":
                            del state[key]
                        else:
                            state[key] = {} if not isinstance(value, dict) else []
                        self.harness.variables = deepcopy(initial)
                        self.harness.variables["bg_state"] = json.dumps(state)
                        self.harness.code(node)
                        self.assert_terminal(node, "bg_quarantine")
                        if key != "inflight":
                            self.assertEqual(self.state.get("inflight"), original["inflight"])

    def test_outer_result_exception_preserves_unknown_transaction_and_seals_route(self):
        self.harness.account()
        self.harness.stage()
        self.commit()
        pending = deepcopy(self.state["inflight"])
        source = self.harness.workflow["nodes"]["bg_commit_result"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        source = source.replace("    state, route, body, readback = guard_result(",
                                "    raise ValueError('injected result fault')\n    state, route, body, readback = guard_result(", 1)
        exec(source, {"vars": self.harness.variables})
        self.assertEqual(self.state["inflight"], pending)
        self.assertTrue(self.state["quarantined"])
        self.assert_terminal("bg_commit_result", "bg_quarantine")

    def test_correctable_nonclose_input_remains_recoverable(self):
        self.harness.account()
        self.harness.stage(amount="0")
        self.assertEqual(self.target("stage"), "talk")
        self.assertFalse(self.state["quarantined"])
        self.harness.stage(amount="50.00")
        self.assertEqual(self.target("stage"), "talk")
        self.assertIn("internal-product-Z", self.state["pending_promises"])

    def test_reentry_with_any_unresolved_transaction_terminates_preserving_it(self):
        self.harness.account()
        self.harness.stage()
        self.commit()
        original = self.state
        for transaction in (original["inflight"], {"action": "unknown"}, {"action": "commit_promises"}):
            for node in ("identity", "stage", "commit", "close"):
                with self.subTest(transaction=transaction, node=node):
                    state = deepcopy(original)
                    state["inflight"] = deepcopy(transaction)
                    self.harness.variables["bg_state"] = json.dumps(state)
                    self.harness.code(node)
                    self.assertEqual(self.state["inflight"], transaction)
                    self.assertTrue(self.state["quarantined"])
                    self.assert_terminal(node, "bg_quarantine")

    def test_source_qualified_retry_still_targets_exact_api(self):
        self.harness.account()
        self.harness.stage()
        self.commit()
        request = self.harness.variables["bg_body"]
        self.harness.api_result("commit", {"evidence_ok": False, "evidence_error": "ERR_BUSY"}, status=503)
        self.assertEqual(self.target("bg_commit_result"), "bg_commit_api")
        self.assertEqual(self.harness.variables["bg_body"], request)
        self.assertFalse(self.state["quarantined"])

    def test_authorized_close_still_dispatches_once_then_terminates(self):
        self.harness.code("close")
        self.assertEqual(self.target("close"), "bg_close_api")
        self.assertTrue(self.state["close_attempted"])
        self.harness.api_result("close", {"evidence_ok": True})
        self.assertTrue(self.state["closed"])
        self.assert_terminal("bg_close_result", "bg_terminal")


if __name__ == "__main__":
    unittest.main()

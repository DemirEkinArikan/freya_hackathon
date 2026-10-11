"""Independent expectations over fictional sealed workflows; no fixture oracle."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from builder.safety import digest
from development_protocol_samples import protocol_response
from test_replay_guarded import authored, scenario
from tools.generated_candidate_acceptance import evaluate, input_bindings, AcceptanceError
from tools.replay_guarded import ReplayError


def invented(renamed=False):
    """Reuse only fictional sealed setup; independently state expected requests."""
    workflow, context, api, aliases = authored(renamed)
    script = scenario(context, aliases, renamed=renamed)
    session = "synthetic-session"
    fields = ("challenge_A", "challenge_B") if renamed else ("postal_code", "memorable_word")
    prefix = "input_" if renamed else ""
    def call(role, **arguments):
        return {"operation": aliases[role], "arguments": {prefix + key: value for key, value in arguments.items()}}
    # These values are authored from the fictional case specification. They
    # are not copied from a replay result or any generated candidate answer.
    calls = [call("verify_identity", customer_id=session, field=field, answer="synthetic-answer") for field in fields]
    calls += [call("get_account", customer_id=session),
              call("create_promise", customer_id=session, product_id="synthetic-product", amount=20.0, date="2031-05-06"),
              call("close_call", customer_id=session, outcome="C_A" if renamed else "promise")]
    expectation = {"version": 1, "bindings": input_bindings(workflow, context, api, script),
        "allowed_operations": [aliases[role] for role in ("verify_identity", "get_account", "create_promise", "close_call")],
        "calls": calls, "terminal_node": "bg_terminal",
        "final_state": {"closed": True, "close_attempted": True, "quarantined": False,
                        "identity_status": "verified", "inflight": {},
                        "recorded_promises": {"synthetic-product": {"amount_cents": 2000, "date": "2031-05-06"}}},
        "closing": {"mode": "recorded", "operation": aliases["close_call"]}}
    return workflow, context, api, script, expectation


def rebind(inputs):
    inputs[-1]["bindings"] = input_bindings(*inputs[:4])
    return inputs


class GeneratedCandidateAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = invented()
        cls.renamed = invented(True)

    def sample(self, renamed=False):
        return deepcopy(self.renamed if renamed else self.native)

    def test_exact_calls_state_and_accepted_close_pass_without_expanding_claims(self):
        result = evaluate(*self.sample())
        self.assertTrue(result["pass"], result["failures"])
        self.assertEqual(result["replay"]["status"], "terminated")
        self.assertEqual(result["replay"]["calls"][-1]["arguments"]["outcome"], "promise")
        self.assertTrue(result["mock"])
        for name in ("network_executed", "model_executed", "source_interpretation_proven",
                     "generation_provenance_proven", "speech_behavior_proven", "hosted_behavior_proven",
                     "all_path_safety_proven", "t07_acceptance_proven", "builder_budget_extended",
                     "platform_entry_event_behavior_proven"):
            self.assertFalse(result[name], name)

    def test_opaque_outcome_and_renamed_operation_arguments_are_compared_exactly(self):
        result = evaluate(*self.sample(True))
        self.assertTrue(result["pass"], result["failures"])
        self.assertEqual(result["replay"]["calls"][-1]["arguments"]["input_outcome"], "C_A")

    def test_every_bound_input_mismatch_rejects_before_execution(self):
        for key in self.native[-1]["bindings"]:
            inputs = self.sample()
            inputs[-1]["bindings"][key] = "0" * 64
            with self.subTest(key=key), patch("tools.generated_candidate_acceptance.replay") as execute:
                with self.assertRaisesRegex(AcceptanceError, "does not bind"):
                    evaluate(*inputs)
                execute.assert_not_called()

    def test_rebound_code_tampering_still_rejects_before_sealed_execution(self):
        inputs = self.sample()
        inputs[0]["nodes"]["bg_init"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"] += "\nvars['tampered']='yes'\n"
        rebind(inputs)
        with patch("tools.replay_guarded._execute", side_effect=AssertionError("must not execute")) as execute:
            with self.assertRaisesRegex(ReplayError, "exact trusted"):
                evaluate(*inputs)
            execute.assert_not_called()

    def test_wrong_amount_type_or_opaque_outcome_fails_independent_ledger(self):
        for index, field, value in ((3, "amount", "20"), (4, "outcome", "paid")):
            inputs = self.sample()
            inputs[-1]["calls"][index]["arguments"][field] = value
            with self.subTest(field=field):
                result = evaluate(*inputs)
                self.assertFalse(result["pass"])
                self.assertIn("ordered API calls or exact typed arguments differ", result["failures"])

    def test_actual_operation_outside_independent_allowed_set_fails(self):
        inputs = self.sample()
        inputs[-1]["allowed_operations"].remove("get_account")
        del inputs[-1]["calls"][2]
        result = evaluate(*inputs)
        self.assertFalse(result["pass"])
        self.assertIn("replay called an operation outside the independent allowed set", result["failures"])

    def test_unused_frame_or_mock_response_fails_even_after_successful_terminal(self):
        for key, extra in (("frames", {"node": "talk", "values": {}}),
                           ("responses", {"operation": "close_call", "envelope": protocol_response("close_call")})):
            inputs = self.sample()
            inputs[3][key].append(extra)
            rebind(inputs)
            with self.subTest(key=key):
                result = evaluate(*inputs)
                self.assertEqual(result["replay"]["status"], "terminated")
                self.assertFalse(result["pass"])
                self.assertIn("replay did not consume every authored frame and mock response", result["failures"])

    def test_quarantine_terminal_never_counts_as_a_recorded_close(self):
        inputs = self.sample()
        inputs[3]["frames"] = inputs[3]["frames"][:6]
        inputs[3]["responses"] = inputs[3]["responses"][:4]
        inputs[3]["responses"][-1]["envelope"] = {"status_code": 502, "data": {}}
        rebind(inputs)
        failed = evaluate(*inputs)
        self.assertFalse(failed["pass"])
        self.assertTrue(failed["replay"]["final_state"]["quarantined"])
        self.assertFalse(failed["replay"]["final_state"]["closed"])
        # This separate fictional expectation explicitly requires uncertainty
        # to preserve the unresolved write and prohibits a final bank close.
        inputs[-1]["calls"] = inputs[-1]["calls"][:4]
        inputs[-1]["terminal_node"] = "bg_quarantine"
        inputs[-1]["closing"]["mode"] = "not_attempted"
        inputs[-1]["final_state"] = {"closed": False, "close_attempted": False, "quarantined": True,
                                      "identity_status": "verified", "recorded_promises": {}}
        accepted = evaluate(*inputs)
        self.assertTrue(accepted["pass"], accepted["failures"])
        self.assertTrue(accepted["replay"]["final_state"]["inflight"])

    def test_negative_close_receipt_cannot_pass_recorded_but_can_match_unrecorded_attempt(self):
        inputs = self.sample()
        inputs[3]["responses"][-1]["envelope"] = protocol_response("close_call", ok=False)
        rebind(inputs)
        result = evaluate(*inputs)
        self.assertEqual(result["replay"]["status"], "terminated")
        self.assertFalse(result["pass"])
        self.assertFalse(result["replay"]["final_state"]["closed"])
        inputs[-1]["closing"]["mode"] = "unrecorded_attempt"
        inputs[-1]["final_state"].update(closed=False, quarantined=True)
        self.assertTrue(evaluate(*inputs)["pass"])

    def test_missing_close_receipt_is_unrecorded_and_requires_quarantine(self):
        inputs = self.sample()
        inputs[3]["responses"][-1]["envelope"] = {"status_code": 502, "data": {}}
        rebind(inputs)
        inputs[-1]["closing"]["mode"] = "unrecorded_attempt"
        inputs[-1]["final_state"].update(closed=False, quarantined=True)
        result = evaluate(*inputs)
        self.assertTrue(result["pass"], result["failures"])
        inputs[-1]["final_state"]["quarantined"] = False
        with self.assertRaisesRegex(AcceptanceError, "certainty"):
            evaluate(*inputs)

    def test_missing_frame_and_step_limit_cannot_pass(self):
        inputs = self.sample()
        inputs[3]["frames"].pop()
        rebind(inputs)
        self.assertFalse(evaluate(*inputs)["pass"])
        self.assertFalse(evaluate(*self.sample(), max_steps=1)["pass"])

    def test_unknown_schema_keys_duplicate_operations_and_missing_state_flags_reject(self):
        mutations = (lambda item: item[-1].update(invented=True),
                     lambda item: item[-1]["allowed_operations"].append("close_call"),
                     lambda item: item[-1]["final_state"].pop("closed"),
                     lambda item: item[-1]["closing"].update(operation="create_promise"),
                     lambda item: item[-1]["final_state"].update(closed=1))
        for mutation in mutations:
            inputs = self.sample()
            mutation(inputs)
            with self.subTest(mutation=mutation), self.assertRaises(AcceptanceError):
                evaluate(*inputs)

    def test_cli_writes_sanitized_report_and_never_overwrites_it(self):
        root = Path(__file__).resolve().parents[1]
        artifacts = root / "artifacts"
        artifacts.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="candidate-acceptance-test-", dir=artifacts) as directory:
            directory = Path(directory)
            command = [sys.executable, str(root / "tools/generated_candidate_acceptance.py")]
            for argument, value in zip(("workflow", "guard-context", "tool-api", "script", "expectation"), self.sample()):
                path = directory / (argument + ".json")
                path.write_text(json.dumps(value), encoding="utf-8")
                command.extend(["--" + argument, str(path)])
            output = directory / "report.json"
            command.extend(["--output", str(output)])
            first = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
            self.assertEqual(first.returncode, 0, first.stderr)
            before = output.read_bytes()
            result = json.loads(before)
            self.assertTrue(result["pass"])
            self.assertFalse(result["all_path_safety_proven"])
            second = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
            self.assertEqual(second.returncode, 1)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

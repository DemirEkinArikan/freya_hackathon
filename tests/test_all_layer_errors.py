"""One policy repair sees every policy-stage error layer, also the layers behind fatal declarations.

Invented policies only; no model, network or evaluator input. The optional replay reads retained
rejected policies named in FREYA_ALL_LAYER_REPLAY and never writes them.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from builder.build import _extract_policy
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.runtime import Deadline, DeadlineExceeded, Trace
from test_categorised_declarations import Generator, declared, entry, errors_of, replay_inputs

BAN = entry("protected_unenforceable", "Invented write ban.", ["close_reasons"])


def without(policy, kind):
    return dict(copy.deepcopy(policy), rules=[rule for rule in policy["rules"] if rule["kind"] != kind])


def execution_errors(policy, sources, api):
    try:
        compile_execution_requirements(policy, sources, api)
    except ExecutionRequirementsError as exc:
        return list(exc.errors)
    raise AssertionError("the execution requirements compiled")


def extract(policies, sources, api):
    """Run the real extraction loop on scripted policies: result, rejection files, validation events, repairs."""
    generator = Generator(policies)
    with tempfile.TemporaryDirectory() as directory:
        run_dir = Path(directory)
        try:
            result = _extract_policy(generator, sources, api, run_dir, Trace(run_dir / "trace.jsonl"), Deadline(1800), (),
                                     workflow_pending=False)
        except (IndexError, DeadlineExceeded) as exc:  # out of scripted policies, or a repair not admitted
            result = exc
        rejected = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(run_dir.glob("policy.rejected-*.json"))]
        events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    repairs = [json.loads(prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1]).get("repair") for prompt in generator.prompts]
    return result, rejected, [event for event in events if event["event"] == "policy_validation"], repairs


class AllLayerErrorTests(unittest.TestCase):
    def test_fatal_declaration_and_execution_error_arrive_in_one_rejection(self):
        policy, sources, api = declared(BAN)
        broken = without(policy, "record_notes")
        before = copy.deepcopy(broken)
        contract = list(errors_of(broken, sources, api))
        hidden = execution_errors(dict(broken, unsupported_constraints=[]), sources, api)
        self.assertEqual(hidden, ["missing typed execution rule: record_notes; expected a boolean, not a string or integer"])
        fixed = dict(copy.deepcopy(policy), unsupported_constraints=[])
        result, rejected, events, repairs = extract([broken, fixed], sources, api)
        self.assertEqual((result[0], result[3]), (fixed, 1))  # the repaired policy is frozen, never the copy
        self.assertEqual(broken, before)
        self.assertEqual(rejected[0], {"policy": broken, "validation_errors": contract + hidden, "hidden_layer_errors": hidden})
        self.assertEqual((events[0]["errors"], events[0]["hidden_layer_errors"]), (contract + hidden, hidden))
        self.assertEqual(repairs[1]["validation_errors"], contract + hidden)
        self.assertEqual(repairs[1]["previous_policy"], broken)

    def test_fatal_declaration_alone_gives_todays_errors(self):
        policy, sources, api = declared(BAN)
        contract = list(errors_of(policy, sources, api))
        result, rejected, events, repairs = extract([policy, dict(copy.deepcopy(policy), unsupported_constraints=[])],
                                                    sources, api)
        self.assertEqual(result[3], 1)
        self.assertEqual((rejected[0]["validation_errors"], rejected[0]["hidden_layer_errors"]), (contract, []))
        self.assertEqual((events[0]["errors"], events[0]["hidden_layer_errors"]), (contract, []))
        self.assertEqual(repairs[1]["validation_errors"], contract)

    def test_policies_without_fatal_declarations_behave_as_before(self):
        for name, declarations in (("none", ()), ("dialogue_only", (entry("dialogue_only"),))):
            policy, sources, api = declared(*declarations)
            broken = without(policy, "record_notes")
            with self.subTest(declarations=name):
                expected = execution_errors(broken, sources, api)
                result, rejected, events, repairs = extract([broken, policy], sources, api)
                self.assertEqual(result[3], 1)
                self.assertEqual(rejected[0], {"policy": broken, "validation_errors": expected})
                self.assertEqual(events[0]["errors"], expected)
                self.assertNotIn("hidden_layer_errors", events[0])
                self.assertEqual(repairs[1]["validation_errors"], expected)


REPLAY = json.loads(Path(os.environ["FREYA_ALL_LAYER_REPLAY"]).read_text(encoding="utf-8")) \
    if os.environ.get("FREYA_ALL_LAYER_REPLAY") else {}


@unittest.skipUnless(REPLAY, "set FREYA_ALL_LAYER_REPLAY to a JSON map of retained runs: run, inputs, hidden")
class RetainedRejectionReplayTests(unittest.TestCase):
    def test_retained_rejections_show_the_layer_behind_their_declarations(self):
        for name, spec in REPLAY.items():
            run = Path(spec["run"])
            sources, api = replay_inputs(run, [Path(d) for d in spec["inputs"]])
            for path in sorted(run.glob("policy.rejected-*.json")):
                original = path.read_bytes()
                policy = json.loads(original)["policy"]
                with self.subTest(run=name, policy=path.name):
                    contract = list(errors_of(policy, sources, api))
                    _, rejected, events, _ = extract([policy], sources, api)
                    hidden = rejected[0]["hidden_layer_errors"]
                    self.assertEqual(rejected[0]["validation_errors"], contract + hidden)
                    self.assertEqual(events[0]["errors"], contract + hidden)
                    for text in spec["hidden"]:
                        self.assertTrue(any(text in error for error in hidden), text)
                        self.assertFalse(any(text in error for error in contract), text)
                self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

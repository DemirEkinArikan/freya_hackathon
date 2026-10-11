"""Declared unsupported constraints: specific repair feedback, stated controller semantics, unchanged guards.

Invented policies and products only; no model, network or evaluator input. The optional replay
classes read retained failed-build artifacts named by environment variables and never write them.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from builder.build import _extract_policy, declaration_feedback, make_policy_prompt
from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import compile_execution_requirements
from builder.guards import _KERNEL, run_guard_step
from builder.policy import execution_rule_guidance
from builder.runtime import Deadline, Trace
from builder.safety import digest
from test_execution_capability_metadata import invented_context
from test_execution_requirements import packet, rule

ROOT = Path(__file__).resolve().parents[1]
# os.pathsep-separated RUN_DIR=INPUT_DIR[,INPUT_DIR...] entries; inputs are matched by trace digests.
REPLAY = [entry.split("=", 1) for entry in os.environ.get("FREYA_DECLARATION_REPLAY", "").split(os.pathsep) if entry]
PUBLIC_A_RUN = os.environ.get("FREYA_PUBLIC_A_REJECTED_RUN", "")


class Generator:
    def __init__(self, policies):
        self.policies, self.prompts = list(policies), []

    def generate(self, prompt, **_):
        self.prompts.append(prompt)
        return {"policy": self.policies.pop(0)}


class DeclarationFeedbackTests(unittest.TestCase):
    def test_plain_declarations_stay_fatal_and_feedback_names_each_without_echoing_prose(self):
        policy, sources, api = packet()
        policy["unsupported_constraints"] = ["Invented gap between promise_readback and close_call timing.",
                                             "Invented unrelated prose naming no interface."]
        with self.assertRaisesRegex(PolicyContractError, "contains unsupported constraints"):
            compile_policy_contract(policy, sources, api)
        feedback = declaration_feedback(policy)
        self.assertEqual(len(feedback), 2)
        self.assertTrue(feedback[0].startswith("policy.unsupported_constraints[0] rejects the build"))
        self.assertIn("it cites close_call, promise_readback", feedback[0])
        self.assertIn("unsupported_constraints[1] rejects the build; it cites no typed kind", feedback[1])
        self.assertTrue(all(line.endswith("; otherwise keep it unchanged") for line in feedback))
        self.assertFalse(any("Invented" in line or "transfer" in line for line in feedback))
        self.assertEqual(declaration_feedback({"unsupported_constraints": []}), [])

    def test_no_declaration_shape_relaxes_the_rejection(self):
        policy, sources, api = packet()
        policy["unsupported_constraints"] = [{"constraint": "Invented gap.", "trigger": "Invented trigger.",
                                              "fallback_queue": "helpdesk", "source": {"id": "synthetic", "quote": "x"}}]
        with self.assertRaisesRegex(PolicyContractError, "must be a string list"):
            compile_policy_contract(policy, sources, api)
        self.assertEqual(declaration_feedback(policy), [])

    def test_repair_turn_receives_feedback_and_rejected_artifact_records_it(self):
        policy, sources, api = packet()
        declared = copy.deepcopy(policy)
        declared["unsupported_constraints"] = ["Invented requirement about transfer_prerequisites."]
        generator = Generator([declared, copy.deepcopy(policy)])
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            frozen, _, _, attempt = _extract_policy(generator, sources, api, run_dir, Trace(run_dir / "trace.jsonl"),
                                                    Deadline(1800), (), workflow_pending=False)
            rejected = json.loads((run_dir / "policy.rejected-1.json").read_text(encoding="utf-8"))
        self.assertEqual((attempt, frozen["unsupported_constraints"]), (1, []))
        repair = json.loads(generator.prompts[1].split("\n\nAUTHORIZED INPUTS:\n", 1)[1])["repair"]
        self.assertEqual(repair["validation_errors"], ["policy contains unsupported constraints"])
        self.assertEqual(repair["declaration_feedback"], declaration_feedback(declared))
        self.assertIn("it cites transfer_prerequisites", repair["declaration_feedback"][0])
        self.assertEqual(rejected["declaration_feedback"], repair["declaration_feedback"])

    def test_extraction_guidance_states_enforced_controller_semantics(self):
        policy, sources, api = packet()
        header, data = make_policy_prompt(sources, api).split("\n\nAUTHORIZED INPUTS:\n", 1)
        shapes = json.loads(data)["execution_interface"]["rule_shapes"]
        self.assertEqual(shapes, execution_rule_guidance())
        self.assertIn("read back for those products alone", header)
        self.assertIn("confirmation is refused unless every loaded product gets a unique label", header)
        self.assertIn("adds no competing closing fact", shapes["close_precedence"])
        self.assertIn("without one the controller refuses the ambiguous close", shapes["close_precedence"])
        self.assertIn("each action's\ncontroller_state_effects", header)
        self.assertIn("Each entry rejects the build", header)
        # Silence stays absent (sealed refusal) unless explicitly required; a tighter plausible reading stays declared.
        self.assertIn("Source silence is not a requirement", header)
        self.assertIn("identity close they\nneither assign nor explicitly require", header)
        self.assertIn("never replays close_call or an unscoped write", header)
        self.assertIn("Keep a declaration whenever a plausible source reading\nrestricts tool calls, disclosures or closes", header)
        self.assertNotIn("degraded", header)


class GuardsUnchangedTests(unittest.TestCase):
    def setUp(self):
        self.state, self.profile, self.catalog, self.maps = invented_context()
        self.profile.update(close_reasons={"promise": [], "transferred": ["agent_help"], "no_promise": ["cleared"]},
                            close_precedence=["transferred", "promise"])
        self.catalog["close_call"] = {"name": "invented_close_op", "fields": {
            "customer_id": "member_key", "outcome": "end_code", "reason": "end_reason"},
            "request_schema": {"type": "object", "properties": {key: {"type": "string"} for key in (
                "member_key", "end_code", "end_reason")}, "required": ["member_key", "end_code"]}}

    def step(self, phase, action, inputs=None, result=None):
        self.state, route, body, readback = run_guard_step(phase, self.state, action, inputs, result, profile=self.profile,
                                                           catalog=self.catalog, maps=self.maps)
        return route, body, readback

    def record_promise(self):
        self.step("prepare", "stage_promise", {"product_id": "invented-item", "amount": 20, "date": "2031-05-06"})
        self.step("prepare", "commit_promises")
        self.assertEqual(self.step("result", "commit_promises", result={
            "ok": True, "promise_id": "invented-receipt", "_http_status": 200})[0], "done")

    def record_transfer(self):
        self.step("prepare", "stage_transfer", {"queue": "Q_DESK"})
        self.step("prepare", "commit_transfer")
        self.assertEqual(self.step("result", "commit_transfer", result={"ok": True, "_http_status": 200})[0], "done")

    def test_later_claim_cannot_replace_a_recorded_promise_close(self):
        self.record_promise()
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            run_guard_step("prepare", self.state, "stage_note", {"product_id": "invented-item", "reason": "N_DISP"},
                           profile=self.profile, catalog=self.catalog, maps=self.maps)
        self.assertEqual(self.state, before)
        route, body, _ = self.step("prepare", "close_call", {"outcome": "no_promise", "reason": "cleared"})
        self.assertEqual((route, body), ("allow", {"member_key": "invented-member", "end_code": "promise"}))

    def test_coexisting_transfer_and_promise_need_sourced_ranking(self):
        self.record_promise()
        self.record_transfer()
        recorded = copy.deepcopy(self.state)
        route, body, _ = self.step("prepare", "close_call")
        self.assertEqual((route, body["end_code"], body["end_reason"]), ("allow", "transferred", "agent_help"))
        self.state, self.profile["close_precedence"] = recorded, []
        with self.assertRaisesRegex(ValueError, "ambiguous unranked protected facts"):
            self.step("prepare", "close_call")

    def test_promise_for_product_outside_every_band_is_refused(self):
        self.state["products"][0]["days_late"] = 90  # invented bands cover 1-80 only
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "no unique policy band"):
            self.step("prepare", "stage_promise", {"product_id": "invented-item", "amount": 20, "date": "2031-05-06"})
        self.assertEqual(self.state, before)

    def test_readback_omits_absent_fields_and_refuses_unidentifiable_products(self):
        self.profile.update(require_confirmation=True, policy_applicability={"products": "multiple"},
                            product_order={"mode": "account_order"},
                            promise_readback={"product_fields": ["type", "tail"],
                                              "value_labels": {"type": {"line": "credit line", "term": "term loan"}}})
        self.profile["floor_fields"]["term"] = "floor_units"
        base = {"days_late": 3, "past_due": 80, "floor_units": 10}
        for products, expected in (([{"product_id": "a", "type": "line", "tail": "0042", **base}], "credit line 0042"),
                                   ([{"product_id": "a", "type": "term", **base}], "term loan: 20.00"),
                                   ([{"product_id": "a", "type": "term", **base}, {"product_id": "b", "type": "term", **base}],
                                    None)):
            self.setUp_state(products)
            self.step("prepare", "stage_promise", {"product_id": "a", "amount": 20, "date": "2031-05-06"})
            with self.subTest(expected=expected):
                if expected is None:
                    with self.assertRaisesRegex(ValueError, "do not identify products uniquely"):
                        self.step("prepare", "commit_promises", {"product_id": "a"})
                    continue
                route, body, readback = self.step("prepare", "commit_promises")
                self.assertEqual((route, body), ("confirm", {}))
                self.assertIn("Payment for " + expected, readback)

    def setUp_state(self, products):
        self.state = run_guard_step("init", None, profile=self.profile, catalog=self.catalog,
                                    customer_id="invented-member", now="2031-05-02T10:00:00")[0]
        self.state.update(identity_status="verified", verified_fields=["member_factor"], account_loaded=True,
                          products=copy.deepcopy(products))


def retained_inputs(run, directories):
    """Load a retained run's exact inputs, accepted only when they match the run's own trace digests."""
    lines = (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    inputs = next(event for event in map(json.loads, lines) if event.get("event") == "inputs")
    sources = []
    for source in inputs["sources"]:
        texts = [path.read_text(encoding="utf-8") for path in (d / source["file"] for d in directories) if path.is_file()]
        sources.append(dict(source, text=next(text for text in texts if digest(text) == source["sha256"])))
    apis = [json.loads(path.read_text(encoding="utf-8")) for d in directories for path in sorted(d.glob("*.json"))]
    return sources, next(api for api in apis if digest(api) == inputs["tool_api_sha256"])


@unittest.skipUnless(REPLAY, "set FREYA_DECLARATION_REPLAY to retained failed-build run directories and their inputs")
class RetainedDeclarationReplayTests(unittest.TestCase):
    """No-model replay: original rejected policies fail only for recorded reasons, each declaration diagnosed."""

    def test_every_rejected_policy_is_rejected_only_for_recorded_reasons_with_per_declaration_feedback(self):
        for run, directories in REPLAY:
            run = Path(run)
            sources, api = retained_inputs(run, [Path(d) for d in directories.split(",")])
            rejected = sorted(run.glob("policy.rejected-*.json"))
            self.assertTrue(rejected, run)
            for path in rejected:
                record = json.loads(path.read_text(encoding="utf-8"))
                policy, declared = record["policy"], record["policy"]["unsupported_constraints"]
                with self.subTest(run=run.name, policy=path.name):
                    with self.assertRaises(PolicyContractError) as caught:
                        compile_policy_contract(policy, sources, api)
                    self.assertIn("policy contains unsupported constraints", caught.exception.errors)
                    self.assertLessEqual(set(caught.exception.errors), set(record["validation_errors"]))
                    feedback = declaration_feedback(policy)
                    self.assertEqual(len(feedback), len(declared))
                    for index, line in enumerate(feedback):
                        self.assertTrue(line.startswith(f"policy.unsupported_constraints[{index}] rejects the build; it cites "))
                        self.assertNotIn(declared[index], line)


@unittest.skipUnless(PUBLIC_A_RUN, "set FREYA_PUBLIC_A_REJECTED_RUN to the retained public-A failed-build run directory")
class RetainedPublicAReplayTests(unittest.TestCase):
    """Both public-A declarations restate sealed behaviour; without them the whole policy stage passes."""

    def test_policy_stage_passes_once_controller_covered_declarations_are_removed(self):
        run = Path(PUBLIC_A_RUN)
        sources, api = retained_inputs(run, [ROOT / "build-the-builder" / "briefs", ROOT / "build-the-builder" / "tools"])
        kernel = {}
        exec(_KERNEL, kernel)
        for path in sorted(run.glob("policy.rejected-*.json")):
            policy = copy.deepcopy(json.loads(path.read_text(encoding="utf-8"))["policy"])
            with self.subTest(policy=path.name):
                self.assertTrue(policy["unsupported_constraints"])
                policy["unsupported_constraints"] = []
                compile_policy_contract(policy, sources, api)
                self.assertTrue(compile_execution_requirements(policy, sources, api)["actions"])
                # The declared conditional readback is what the sealed display does with the policy's own value.
                display = rule(policy, "promise_readback")["value"]
                first, *optional = display["product_fields"]
                self.assertTrue(optional)
                for code, label in display["value_labels"][first].items():
                    product = {"product_id": "p", first: code}
                    self.assertEqual(kernel["guard_display"](product, {"promise_readback": display}), label)
                    product.update({field: "0000" for field in optional})
                    self.assertEqual(kernel["guard_display"](product, {"promise_readback": display}),
                                     " ".join([label] + ["0000"] * len(optional)))


if __name__ == "__main__":
    unittest.main()

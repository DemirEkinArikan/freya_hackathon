"""Spoken line before the sealed uncertain stop; invented policies, no model, bank or platform I/O."""
import copy
import hashlib
import json
import os
import unittest
from pathlib import Path

from builder.action_bindings import compile_action_bindings
from builder.build import make_policy_prompt
from builder.contract import compile_policy_contract
from builder.execution_requirements import (ExecutionRequirementsError, compile_execution_requirements,
                                            validate_plan_execution)
from builder.guards import GuardCompileError, _terminal, compile_guarded_workflow
from builder.policy import compile_execution_policy, validate_policy
from builder.safety import canonical_json, digest, read_json
from builder.validation import validate_workflow
from test_execution_requirements import add_rule
from test_goal_requirements import SealedSession, compiled, rehash, synthetic, witness
from tools.replay_guarded import replay


WORDS = "the callback could not be confirmed"
DEFAULT = "the result of this request could not be confirmed"
SILENT = {**_terminal(), "name": "bg_quarantine"}
FLAGS = {"closed": False, "close_attempted": False, "quarantined": True}


def packed(value=None):
    policy, sources, api = synthetic(("schedule_callback", "close_call"))
    if value is not None:
        add_rule(policy, "uncertain_stop_explanation", value)
        witness(policy, sources)
    return policy, sources, api


def spoken(workflow):
    props = workflow["nodes"]["bg_quarantine"]["terminal"]["function"]["parameters"]["properties"]
    return props["message_mode"]["default"], props.get("llm_prompt", {}).get("default", "")


def changed(workflow, baseline):
    names = set(workflow["nodes"]) | set(baseline["nodes"])
    return {name for name in names
            if canonical_json(workflow["nodes"].get(name)) != canonical_json(baseline["nodes"].get(name))}


class UncertainStopLineTests(unittest.TestCase):
    def test_silent_source_keeps_the_silent_quarantine_end(self):
        _, _, workflow = compiled(*packed())
        self.assertEqual(workflow["nodes"]["bg_quarantine"], SILENT)

    def test_rule_speaks_once_and_changes_only_the_quarantine_end(self):
        _, base, silent = compiled(*packed())
        # The invented helper plan has its own routing finding; the rule must add none.
        baseline = validate_workflow(silent, base["openapi"],
                                     guard_context={key: item for key, item in base.items() if key != "openapi"})
        for value, words in ((WORDS, WORDS), (True, DEFAULT)):
            with self.subTest(value=value):
                policy, sources, api = packed(value)
                _, context, workflow = compiled(policy, sources, api)
                mode, prompt = spoken(workflow)
                self.assertEqual(mode, "llm")
                self.assertIn('"' + words + '"', prompt)
                self.assertIn("in the language the conversation is using", prompt)
                self.assertIn("no claim that anything succeeded, failed, was cancelled or will be retried", prompt)
                # Every sealed code node, route and the kernel stay byte-identical.
                self.assertEqual(changed(workflow, silent), {"bg_quarantine"})
                guard = {key: item for key, item in context.items() if key != "openapi"}
                self.assertEqual(validate_workflow(workflow, api, guard_context=guard), baseline)

    def test_uncertain_write_ends_at_the_speaking_stop_without_a_close(self):
        _, context, workflow = compiled(*packed(WORDS))
        session = SealedSession(context, workflow)
        session.action("schedule_callback", date="2031-05-06", time="12:00")
        session.result("schedule_callback", {})  # no evidence: persistence unknown
        entry = session.entries["schedule_callback"]
        self.assertTrue(session.state["quarantined"])
        self.assertEqual(session.target("bg_" + entry + "_result"), "bg_quarantine")
        session.action("close_call", outcome="", reason="")
        self.assertEqual(session.target(session.entries["close_call"]), "bg_quarantine")
        self.assertFalse(session.variables["bg_body"])
        self.assertEqual({key: session.state[key] for key in FLAGS}, FLAGS)

    def test_wording_must_be_plain_sourced_words(self):
        for value in ("", "  ", "call 0800 now", "say {{bg_readback}}", "two\nlines", "bg_state is unknown",
                      False, ["words"], {"text": WORDS}):
            with self.subTest(value=value):
                with self.assertRaises(ExecutionRequirementsError):
                    compile_execution_requirements(*packed(value))
        policy, _, _ = packed(WORDS)
        next(item for item in policy["rules"] if item["kind"] == "uncertain_stop_explanation")["value"] = "unsourced words"
        self.assertIn("uncertain_stop_explanation must appear literally", " ".join(validate_policy(policy, strict=True)))

    def test_policy_prompt_routes_the_stop_explanation_to_the_typed_rule(self):
        _, sources, api = packed()
        header, data = make_policy_prompt(sources, api).split("\n\nAUTHORIZED INPUTS:\n", 1)
        self.assertIn("so encode it in uncertain_stop_explanation", " ".join(header.split()))
        self.assertIn("uncertain_stop_explanation", json.loads(data)["execution_interface"]["rule_shapes"])

    def test_sealed_compiler_rechecks_the_quotation(self):
        _, context, _ = compiled(*packed(WORDS))
        contract = copy.deepcopy(context["contract"])
        for source in contract["active_rules"]["uncertain_stop_explanation"]["provenance"]:
            source["quote"] = "An unrelated sentence."
        rehash(contract)
        with self.assertRaisesRegex(GuardCompileError, "must occur literally in its own sourced quotation"):
            compile_guarded_workflow(**dict(context, contract=contract))


@unittest.skipUnless(os.environ.get("FREYA_CALLBACK_R3_ARTIFACTS"), "retained callback r3 artifacts are local only")
class RetainedCallbackUncertainReplay(unittest.TestCase):
    """Read-only replay of callback run 20261011T104109500476Z (S01 F-03); no model call."""

    @classmethod
    def setUpClass(cls):
        root = Path(os.environ["FREYA_CALLBACK_R3_ARTIFACTS"])
        cls.run_dir = root / "t07-callback-r3-generation" / "20261011T104109500476Z"
        cls.scripts = root / "t07-callback-r3-evaluator"
        text = (root / "t07-callback-r3-inputs" / "briefs" / "callback-policy.approved.md").read_text(encoding="utf-8")
        cls.sources = [{"id": "brief-1", "file": "callback-policy.approved.md", "sha256": digest(text), "text": text}]
        cls.api = read_json(root / "t07-callback-r3-inputs" / "tool_api" / "bank.openapi.json")
        cls.before = cls.hashes()

    @classmethod
    def hashes(cls):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(cls.run_dir.iterdir()) if path.is_file()}

    def tearDown(self):
        self.assertEqual(self.hashes(), self.before)

    def build(self, explained):
        """The retained policy, or the same policy plus the typed rule quoting its own terminal-handling source."""
        policy = read_json(self.run_dir / "policy.json")
        words = None
        if explained:
            terminal = next(item for item in policy["rules"] if item["kind"] == "write_terminal_handling")
            words = terminal["value"]["scheduling_unknown"]["explain"]
            policy["rules"].append({"id": "uncertain-stop-explanation", "kind": "uncertain_stop_explanation",
                                    "value": words, "source": copy.deepcopy(terminal["source"])})
        requirements = compile_execution_requirements(policy, self.sources, self.api)
        plan = read_json(self.run_dir / "plan.json")
        self.assertEqual(validate_plan_execution(plan, requirements, policy, self.sources, self.api), [])
        bindings = compile_action_bindings(policy, self.api)
        context = {"plan": plan, "contract": compile_policy_contract(policy, self.sources, self.api), "bindings": bindings,
                   "execution_policy": compile_execution_policy(policy, bindings, required_kinds=requirements["required_kinds"])}
        return context, compile_guarded_workflow(openapi=self.api, **context)["workflow"], words

    def test_cb09_cb10_speak_the_sourced_line_with_no_further_write_or_close(self):
        silent_context, silent, _ = self.build(False)
        context, workflow, words = self.build(True)
        self.assertEqual(silent["nodes"]["bg_quarantine"], SILENT)
        self.assertEqual(changed(workflow, silent), {"bg_quarantine"})
        mode, prompt = spoken(workflow)
        self.assertEqual(mode, "llm")
        self.assertIn('"' + words + '"', prompt)
        for case in ("CB09-unknown-write-stops-all-writes", "CB10-contradictory-evidence-stops-all-writes"):
            script = read_json(self.scripts / (case + ".script.json"))
            for label, (guard, graph) in (("silent", (silent_context, silent)), ("explained", (context, workflow))):
                with self.subTest(case=case, policy=label):
                    report = replay(graph, guard, self.api, script)
                    self.assertEqual((report["status"], report["terminal_node"]), ("terminated", "bg_quarantine"))
                    self.assertEqual([call["tool"] for call in report["calls"]], ["schedule_callback"])
                    self.assertEqual({key: report["final_state"][key] for key in FLAGS}, FLAGS)


if __name__ == "__main__":
    unittest.main()

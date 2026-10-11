"""Relaxed last-resort demotion: after bounded repairs, exactly the typed constructs the sealed compiler
refuses become prompt-enforced rules (or their roles are removed whole) and the policy freezes; strict
and default builds are unchanged. Invented policies only, plus an opt-in replay of retained runs.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from builder.action_bindings import ActionBindingError, checked_action_bindings, compile_action_bindings
from builder.build import _extract_policy
from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import compile_execution_requirements
from builder.policy import normalise_policy
from builder.policy_demotion import PolicyDemotionError, demote_policy
from builder.runtime import Deadline, Trace
from test_categorised_declarations import Generator, declared, replay_inputs
from test_execution_requirements import rule

CHOICE_ERROR = "identity closing choice outcome must match its fixed scope"


def close_field(api, name):
    return api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"][name]


def foreign_choice():
    policy, sources, api = declared()
    policy["rules"].append({"id": "choices", "kind": "identity_close_choices", "source": copy.deepcopy(policy["rules"][0]["source"]),
                            "value": {"incomplete": [{"outcome": "not_verified", "reason": "identity_failed", "requires": "none"},
                                                     {"outcome": "not_reached", "requires": "none"}]}})
    return policy, sources, api


def relaxed_errors(policy, sources, api):
    with unittest.TestCase().assertRaises(ValueError) as caught:
        compile_execution_requirements(policy, sources, api, declarations="relaxed")
    return list(caught.exception.errors)


class RelaxedVocabularyTests(unittest.TestCase):
    def test_an_outcome_code_named_like_another_categorys_meaning_is_unguarded_only_in_relaxed(self):
        policy, sources, api = declared()
        rule(policy, "close_reasons")["value"]["declined"] = []
        close_field(api, "outcome")["enum"].append("declined")
        with self.assertRaisesRegex(ActionBindingError, "outcomes vocabulary has a code"):
            compile_action_bindings(policy, api)
        binding = compile_action_bindings(policy, api, declarations="relaxed")
        self.assertEqual((binding["declaration_mode"], binding["categories"]["outcomes"]["unbound_codes"]), ("relaxed", ["declined"]))
        self.assertEqual(checked_action_bindings(policy, binding), binding)
        self.assertTrue(compile_execution_requirements(policy, sources, api, declarations="relaxed")["actions"])

    def test_guarded_spellings_and_the_categorys_own_meanings_stay_fatal(self):
        for category, code in (("outcome", "No-Callback"), ("outcome", "PROMISE"), ("reason", "declined")):
            policy, _, api = declared()
            if category == "outcome":
                rule(policy, "close_reasons")["value"][code] = []
            else:
                rule(policy, "close_reasons")["value"]["not_verified"].append(code)
            close_field(api, category)["enum"].append(code)
            with self.subTest(code=code), self.assertRaises(ActionBindingError):
                compile_action_bindings(policy, api, declarations="relaxed")


class DemotionTests(unittest.TestCase):
    def test_only_the_offending_identity_choice_is_demoted_and_strict_still_rejects(self):
        policy, sources, api = foreign_choice()
        self.assertEqual(relaxed_errors(policy, sources, api), [CHOICE_ERROR])
        before = copy.deepcopy(policy)
        demoted, steps = demote_policy(policy, sources, api)
        self.assertEqual(policy, before)
        self.assertEqual(steps, [{"step": "demote_entries", "kind": "identity_close_choices", "rule_ids": ["choices"],
                                  "entries": [{"outcome": "not_reached", "requires": "none", "scope": "incomplete"}],
                                  "error": CHOICE_ERROR}])
        self.assertEqual(rule(demoted, "identity_close_choices")["value"],
                         {"incomplete": [{"outcome": "not_verified", "reason": "identity_failed", "requires": "none"}]})
        contract = compile_policy_contract(demoted, sources, api, declarations="relaxed")
        entry = contract["prompt_enforced_limitations"][-1]
        self.assertEqual((entry["category"], entry["rule_ids"]), ("protected_unenforceable", ["choices"]))
        self.assertTrue(entry["justification"].endswith(CHOICE_ERROR))
        self.assertEqual(entry["sources"][0]["quote"], policy["rules"][0]["source"]["quote"])
        self.assertTrue(compile_execution_requirements(demoted, sources, api, declarations="relaxed")["actions"])
        with self.assertRaisesRegex(PolicyContractError, "protected_unenforceable"):
            compile_policy_contract(demoted, sources, api)

    def test_band_names_differing_only_in_case_or_separators_are_aligned(self):
        policy, sources, api = declared()
        rule(policy, "band_ranges")["value"][0]["name"] = "Recent"
        rule(policy, "band_ranges")["value"][1]["name"] = "OL-DER"
        demoted, steps = demote_policy(policy, sources, api)
        self.assertEqual([(s["step"], s["renamed"]) for s in steps], [("align_band_names", {"OL-DER": "older", "Recent": "recent"})])
        self.assertEqual([band["name"] for band in rule(demoted, "band_ranges")["value"]], ["recent", "older"])
        self.assertEqual(rule(demoted, "deadline_days")["value"], rule(policy, "deadline_days")["value"])

    def test_an_unbindable_note_code_removes_the_note_roles_and_their_operation_whole(self):
        policy, sources, api = declared()
        rule(policy, "note_reasons")["value"].append("N_OPAQUE")
        demoted, steps = demote_policy(policy, sources, api)
        self.assertEqual(steps[0]["step"], "drop_roles")
        self.assertEqual((steps[0]["roles"], steps[0]["operations"]), (["finalize_notes", "stage_note"], ["add_note"]))
        actions = compile_execution_requirements(demoted, sources, api, declarations="relaxed")["actions"]
        self.assertFalse({"stage_note", "finalize_notes"} & set(actions))
        self.assertTrue({"verify_identity", "get_account", "stage_promise", "commit_promises", "close_call"} <= set(actions))
        self.assertNotIn("add_note", demoted["allowed_actions"])
        self.assertNotIn("note_reasons", {r["kind"] for r in demoted["rules"]})
        self.assertTrue(any("never claim or imply" in entry["requirement"] for entry in compile_policy_contract(
            demoted, sources, api, declarations="relaxed")["prompt_enforced_limitations"]))

    def test_identity_is_never_demoted(self):
        policy, sources, api = declared()
        policy["rules"] = [r for r in policy["rules"] if r["kind"] != "identity_fields"]
        with self.assertRaises(PolicyDemotionError) as caught:
            demote_policy(policy, sources, api)
        self.assertTrue(any("identity_fields" in error for error in caught.exception.errors))


class DemotionBuildTests(unittest.TestCase):
    def extract(self, declarations):
        policy, sources, api = foreign_choice()
        generator, demotions = Generator([copy.deepcopy(policy) for _ in range(3)]), []
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            try:
                return _extract_policy(generator, sources, api, run_dir, Trace(run_dir / "trace.jsonl"), Deadline(1800), (),
                                       workflow_pending=False, declarations=declarations, demotions=demotions), \
                    demotions, [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()], \
                    json.loads((run_dir / "policy.demoted.json").read_text())
            finally:
                self.assertEqual(len(generator.prompts), 3)

    def test_relaxed_extraction_freezes_the_demoted_policy_after_bounded_repairs(self):
        (frozen, contract, requirements, repairs), demotions, events, written = self.extract("relaxed")
        self.assertEqual(repairs, 2)
        self.assertEqual([step["kind"] for step in demotions], ["identity_close_choices"])
        self.assertEqual(written, {"policy": frozen, "demotions": demotions})
        self.assertEqual(requirements["contract_sha256"], contract["sha256"])
        event = next(e for e in events if e.get("event") == "policy_demotion")
        self.assertTrue(event["passed"])
        self.assertIn("Policy extraction failed after bounded repairs", event["original_failure"])

    def test_a_relaxed_policy_demotion_cannot_compile_reports_both_failures(self):
        policy, sources, api = declared()
        policy["rules"] = [r for r in policy["rules"] if r["kind"] != "identity_fields"]
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            with self.assertRaisesRegex(ValueError, "Policy extraction failed after bounded repairs: .*; relaxed demotion "
                                                    "could not compile the policy: .*identity_fields"):
                _extract_policy(Generator([copy.deepcopy(policy) for _ in range(3)]), sources, api, run_dir,
                                Trace(run_dir / "trace.jsonl"), Deadline(1800), (), workflow_pending=False,
                                declarations="relaxed")
            events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
        self.assertFalse(next(e for e in events if e.get("event") == "policy_demotion")["passed"])

    def test_strict_extraction_is_unchanged(self):
        with self.assertRaisesRegex(ValueError, "Policy extraction failed after bounded repairs: " + CHOICE_ERROR + "$"):
            self.extract("strict")


REPLAY = json.loads(Path(os.environ["FREYA_RELAXED_DEMOTION_REPLAY"]).read_text(encoding="utf-8")) \
    if os.environ.get("FREYA_RELAXED_DEMOTION_REPLAY") else {}


@unittest.skipUnless(REPLAY, "set FREYA_RELAXED_DEMOTION_REPLAY to a JSON map of retained runs, input directories "
                             "and expected demotion steps (or the error that stays fatal)")
class RetainedStressDemotionTests(unittest.TestCase):
    def test_last_rejected_policies_freeze_relaxed_with_listed_demotions_and_strict_rejects(self):
        for name, spec in REPLAY.items():
            run = Path(spec["run"])
            sources, api = replay_inputs(run, [Path(d) for d in spec["inputs"]])
            path = max(run.glob("policy.rejected-*.json"))
            policy, _ = normalise_policy(json.loads(path.read_text(encoding="utf-8"))["policy"],
                                         {source["id"]: source["text"] for source in sources})  # as the build does
            with self.subTest(run=name, policy=path.name):
                with self.assertRaises(ValueError):
                    compile_execution_requirements(policy, sources, api)
                if "fatal" in spec:
                    with self.assertRaises(PolicyDemotionError) as caught:
                        demote_policy(policy, sources, api)
                    self.assertEqual(list(caught.exception.errors), [spec["fatal"]])
                    continue
                demoted, steps = demote_policy(policy, sources, api)
                self.assertEqual([[s["step"], s.get("kind") or s.get("roles") or s.get("renamed")] for s in steps], spec["steps"])
                self.assertTrue(compile_execution_requirements(demoted, sources, api, declarations="relaxed")["actions"])
                with self.assertRaises(ValueError):
                    compile_execution_requirements(demoted, sources, api)


if __name__ == "__main__":
    unittest.main()

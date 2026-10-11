"""Categorised unsupported declarations: which entries reject a build, which are frozen limitations.

Invented policies and products only; no model, network or evaluator input. The optional replay
reads retained rejected policies named in FREYA_DECLARATIONS_V2_REPLAY and never writes them.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from builder.action_bindings import compile_action_bindings
from builder.build import _extract_policy, make_policy_prompt
from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import execution_action_catalogue, run_guard_step
from builder.runtime import Deadline, Trace
from builder.safety import digest
from test_execution_capability_metadata import invented_context
from test_execution_requirements import packet

CLOSE_REFUSAL = "Every close result is terminal; negative or uncertain evidence cannot authorize another close attempt."
UNSCOPED_REPLAY = ("they are never replayed unless a source rule scopes them by call or exact_request with their "
                   "own limit and definite_failure_resume")
HANDOFF = "A staged or recorded transfer handoff refuses this action; after a handoff only commit_transfer and close_call are accepted."
GOAL_SCOPED = "A call whose sealed initial goal code is listed by a scoped goal_close refuses this action."


def entry(category, requirement="Greet the caller before asking anything.", rule_ids=("greeting",), **extra):
    return {"category": category, "requirement": requirement, "rule_ids": list(rule_ids),
            "justification": "Invented reason for this category.", **extra}


def declared(*entries):
    policy, sources, api = packet()
    policy["rules"].append({"id": "greeting", "kind": "greeting_protocol", "value": {"order": "greet, then identify"},
                            "source": copy.deepcopy(policy["rules"][0]["source"])})
    policy["unsupported_constraints"] = [copy.deepcopy(item) for item in entries]
    return policy, sources, api


def errors_of(policy, sources, api):
    try:
        compile_policy_contract(policy, sources, api)
    except PolicyContractError as exc:
        return exc.errors
    raise AssertionError("the policy compiled")


class Generator:
    def __init__(self, policies):
        self.policies, self.prompts = list(policies), []

    def generate(self, prompt, **_):
        self.prompts.append(prompt)
        return {"policy": self.policies.pop(0)}


class CategorisedDeclarationTests(unittest.TestCase):
    def test_legacy_string_entries_stay_fatal_and_are_named_by_index(self):
        policy, sources, api = packet()
        policy["unsupported_constraints"] = ["Invented unresolved conflict between two rules."]
        errors = errors_of(policy, sources, api)
        self.assertIn("policy contains unsupported constraints", errors)
        self.assertIn("policy.unsupported_constraints[0] has no declaration category and rejects the build", errors)
        self.assertTrue(any("only when its category was wrong, by correcting the category" in error for error in errors))
        self.assertFalse(any("Invented" in error or "keep it unchanged" in error for error in errors))

    def test_dialogue_only_and_controller_stricter_are_frozen_as_contract_limitations(self):
        baseline = compile_policy_contract(*declared())
        self.assertNotIn("declared_limitations", baseline)
        limits = [entry("dialogue_only"),
                  entry("controller_stricter", "The source asks for a second close after a refusal.",
                        ["close_reasons"], controller_effect=CLOSE_REFUSAL)]
        policy, sources, api = declared(*limits)
        before = copy.deepcopy(policy)
        contract = compile_policy_contract(policy, sources, api)
        self.assertEqual(contract["declared_limitations"], limits)
        self.assertNotEqual(contract["sha256"], baseline["sha256"])
        self.assertEqual(digest({key: value for key, value in contract.items() if key != "sha256"}), contract["sha256"])
        self.assertTrue(compile_execution_requirements(policy, sources, api)["actions"])
        compile_action_bindings(policy, api)
        self.assertEqual(policy, before)

    def test_protected_unenforceable_stays_fatal_beside_accepted_entries(self):
        policy, sources, api = declared(entry("dialogue_only"),
                                        entry("protected_unenforceable", "Invented disclosure ban.", ["close_reasons"]))
        errors = errors_of(policy, sources, api)
        self.assertIn("policy.unsupported_constraints[1] (protected_unenforceable) is a protected restriction no typed "
                      "kind or controller effect enforces and rejects the build", errors)
        self.assertFalse(any("unsupported_constraints[0]" in error for error in errors))
        with self.assertRaisesRegex(ExecutionRequirementsError, "unsupported constraints"):
            compile_execution_requirements(policy, sources, api)

    def test_protected_restrictions_cannot_be_declared_dialogue_only(self):
        wordings = ["Never call close_call before thanking the caller.",           # sealed action
                    "Do not repeat create_promise after a refusal.",                # operation role / API operation
                    "Read the product_id only after recognition.",                  # API field
                    "Offer support before transfer_prerequisites apply.",           # typed kind
                    "Say goodbye before closing with identity_failed."]             # bound close code
        for wording in wordings:
            with self.subTest(wording=wording):
                errors = errors_of(*declared(entry("dialogue_only", wording)))
                self.assertIn("policy.unsupported_constraints[0] (dialogue_only) cannot be dialogue_only: its requirement "
                              "names a protected action, field, typed kind or code, or its rule_ids are not active "
                              "descriptive rules and rejects the build", errors)
        for rule_ids in (["transfer_prerequisites"], ["greeting", "close_reasons"]):
            with self.subTest(rule_ids=rule_ids):
                self.assertIn("policy contains unsupported constraints",
                              errors_of(*declared(entry("dialogue_only", rule_ids=rule_ids))))
        policy, sources, api = declared(entry("dialogue_only", rule_ids=["greeting-old"]))
        policy["rules"].append(dict(copy.deepcopy(policy["rules"][-1]), id="greeting-old"))
        policy["rules"][-2]["overrides"] = ["greeting-old"]
        self.assertIn("policy contains unsupported constraints", errors_of(policy, sources, api))
        # Plain dialogue words are not identifiers; such a duty stays a frozen limitation.
        contract = compile_policy_contract(*declared(entry("dialogue_only", "Ask for the amount and date only once.")))
        self.assertEqual(len(contract["declared_limitations"]), 1)

    def test_controller_stricter_needs_an_exact_controller_refusal(self):
        effects = execution_action_catalogue()["commit_promises"]["controller_state_effects"]
        permission = effects["positive_recording"][0]  # verbatim, but it records rather than refuses
        cut = CLOSE_REFUSAL[:38]  # "...is terminal; negati": names a refusal, but ends inside a word
        for effect in ("A close is final.", "close", CLOSE_REFUSAL.replace("terminal", "final"), None, permission,
                       "an optional object conta", "Keeps the requested factor pending; send", cut):
            with self.subTest(effect=effect):
                extra = {} if effect is None else {"controller_effect": effect}
                errors = errors_of(*declared(entry("controller_stricter", rule_ids=["close_reasons"], **extra)))
                self.assertIn("policy.unsupported_constraints[0] (controller_stricter) needs controller_effect quoting "
                              "a refusal exactly, in whole words, from controller_state_effects or rule_shapes and "
                              "rejects the build", errors)
        for effect in (UNSCOPED_REPLAY, HANDOFF, CLOSE_REFUSAL[:40], GOAL_SCOPED):
            with self.subTest(effect=effect):
                contract = compile_policy_contract(*declared(entry("controller_stricter", rule_ids=["close_reasons"],
                                                                   controller_effect=effect)))
                self.assertEqual(contract["declared_limitations"][0]["controller_effect"], effect)

    def test_source_ambiguity_is_fatal_without_a_typed_fallback_from_the_same_statement(self):
        policy, sources, api = declared(entry("source_ambiguous", "Unclear requests have two readings.", ["unclear"]))
        extra = "Unclear requests go to the general desk."
        sources[0]["text"] += " " + extra
        sources[0]["sha256"] = policy["sources"][0]["sha256"] = digest(sources[0]["text"])
        policy["rules"].append({"id": "unclear", "kind": "unclear_request_handling", "value": {"route": "desk"},
                                "source": {"id": "synthetic", "quote": extra}})
        message = ("policy.unsupported_constraints[0] (source_ambiguous) has no typed fallback_rule cited from the same "
                   "source statement and rejects the build")
        self.assertIn(message, errors_of(policy, sources, api))
        for fallback in ("transfer_reasons", "greeting", "missing-rule"):  # other statement, descriptive, absent
            policy["unsupported_constraints"][0]["fallback_rule"] = fallback
            with self.subTest(fallback=fallback):
                self.assertIn(message, errors_of(policy, sources, api))
        next(rule for rule in policy["rules"] if rule["id"] == "transfer_reasons")["source"]["quote"] = extra
        policy["unsupported_constraints"][0]["fallback_rule"] = "transfer_reasons"
        contract = compile_policy_contract(policy, sources, api)
        self.assertEqual(contract["declared_limitations"][0]["category"], "source_ambiguous")

    def test_malformed_entries_are_fatal(self):
        for broken in ({"category": "dialogue_only"}, dict(entry("dialogue_only"), justification=" "),
                       dict(entry("dialogue_only"), rule_ids=[]), dict(entry("dialogue_only"), rule_ids=["absent"]),
                       dict(entry("dialogue_only"), extra="field"), dict(entry("dialogue_only"), category="minor")):
            with self.subTest(broken=broken):
                self.assertIn("policy contains unsupported constraints", errors_of(*declared(broken)))
        policy, sources, api = declared()
        policy["unsupported_constraints"] = [7]
        self.assertIn("policy.unsupported_constraints must be a list of declaration objects", errors_of(policy, sources, api))

    def test_repair_turn_names_each_fatal_entry_by_index_and_category(self):
        fatal, sources, api = declared(entry("dialogue_only"),
                                       entry("protected_unenforceable", "Invented write ban.", ["close_reasons"]))
        fixed = copy.deepcopy(fatal)
        fixed["unsupported_constraints"] = fixed["unsupported_constraints"][:1]
        generator = Generator([fatal, fixed])
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            frozen, contract, _, attempt = _extract_policy(generator, sources, api, run_dir, Trace(run_dir / "trace.jsonl"),
                                                           Deadline(1800), (), workflow_pending=False)
            rejected = json.loads((run_dir / "policy.rejected-1.json").read_text(encoding="utf-8"))
        repair = json.loads(generator.prompts[1].split("\n\nAUTHORIZED INPUTS:\n", 1)[1])["repair"]
        self.assertEqual(attempt, 1)
        self.assertEqual(contract["declared_limitations"], fixed["unsupported_constraints"])
        self.assertEqual(frozen, fixed)
        self.assertEqual(repair["validation_errors"], rejected["validation_errors"])
        self.assertTrue(any(error.startswith("policy.unsupported_constraints[1] (protected_unenforceable)")
                            for error in repair["validation_errors"]))
        guidance = next(error for error in repair["validation_errors"] if "encoding its requirement in a typed rule" in error)
        self.assertIn("Never change a category to pass the build: a restriction the controller would allow stays "
                      "protected_unenforceable", guidance)

    def test_guidance_defines_categories_and_states_the_kernel_refusals(self):
        policy, sources, api = packet()
        header, data = make_policy_prompt(sources, api).split("\n\nAUTHORIZED INPUTS:\n", 1)
        for text in ("dialogue_only:", "controller_stricter:", "protected_unenforceable:", "source_ambiguous:",
                     "Source silence is not ambiguity", "which the workflow\nimplements, not in unsupported_constraints",
                     "unsupported_constraints is an array of declaration objects"):
            self.assertIn(text, header)
        self.assertNotIn("Keep a declaration whenever", header)
        self.assertNotIn("an array of strings", header)
        self.assertNotIn("cannot faithfully enforce remain explicit failures", header)
        flat = " ".join(header.split())
        for text in ("Change a category only when it was wrong, never to pass the build: a restriction the controller "
                     "would allow stays protected_unenforceable and rejects the build.",
                     "map a supplied selector code whose goal has no stated product-resolution requirement to false",
                     "an exact whole-word excerpt of the controller_state_effects or rule_shapes text that states the "
                     "refusal itself"):
            self.assertIn(text, flat)
        actions = json.loads(data)["execution_interface"]["actions"]
        self.assertIn("exceeds its past_due amount is refused", " ".join(actions["stage_promise"]["controller_state_effects"]["rejections"]))
        for action in ("stage_promise", "commit_promises", "stage_note", "finalize_notes"):
            self.assertIn(HANDOFF, actions[action]["controller_state_effects"]["rejections"])
        # The kernel side is pinned by test_per_goal_close (a listed goal cannot use these roles).
        for action in ("stage_promise", "commit_promises", "stage_note", "finalize_notes", "stage_transfer", "commit_transfer"):
            self.assertIn(GOAL_SCOPED, actions[action]["controller_state_effects"]["rejections"])
        self.assertEqual(actions, execution_action_catalogue())


class StatedRefusalsMatchKernelTests(unittest.TestCase):
    """The controller effects text above restates sealed behaviour; these pin that it is true."""

    def setUp(self):
        self.state, self.profile, self.catalog, self.maps = invented_context()

    def prepare(self, action, inputs=None):
        self.state = run_guard_step("prepare", self.state, action, inputs or {}, profile=self.profile,
                                    catalog=self.catalog, maps=self.maps)[0]

    def test_promise_amount_is_bounded_by_floor_and_past_due(self):
        for amount in (0, 9, 81):  # invented floor 10, past_due 80
            with self.subTest(amount=amount), self.assertRaisesRegex(ValueError, "outside policy floor or past due"):
                self.prepare("stage_promise", {"product_id": "invented-item", "amount": amount, "date": "2031-05-06"})
        self.prepare("stage_promise", {"product_id": "invented-item", "amount": 80, "date": "2031-05-06"})
        self.assertEqual(set(self.state["pending_promises"]), {"invented-item"})

    def test_collection_actions_are_refused_after_a_handoff(self):
        self.prepare("stage_note", {"product_id": "invented-item", "reason": "N_DISP"})
        self.prepare("stage_transfer", {"queue": "Q_DESK"})
        staged = copy.deepcopy(self.state)
        for action, inputs in (("stage_promise", {"product_id": "invented-item", "amount": 20, "date": "2031-05-06"}),
                               ("commit_promises", {}), ("stage_note", {"product_id": "invented-item", "reason": "N_DISP"}),
                               ("finalize_notes", {})):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.prepare(action, inputs)
            self.assertEqual(self.state, staged)


# Reviewer categorisation of each retained declaration, applied in memory to the model's own requirement
# wording (text before "Checked", else the first sentence). "covered" entries are dropped: a typed kind or a
# controller effect stated on this branch enforces them. Rule ids are candidates; those present are cited.
CLOSE_SELECT_REFUSAL = "for goals that select no promise, note or transfer role"
IDENTITY_REFUSAL = "otherwise it safely refuses without a bank close"
REPLAY_CATEGORIES = {
    "public-a-original": [("controller_stricter", ["closing-selection", "final-close-selection"],
                           "An already recorded promise or recorded note for that product prevents staging."),
                          ("covered",)],
    "public-a-probe": [("dialogue_only", ["opening", "callee-recognition", "third-party"]),
                       ("dialogue_only", ["callback-protocol", "third-party"]),
                       ("dialogue_only", ["security-answer-processing", "security-declining"]),
                       ("dialogue_only", ["date-negotiation", "amount-negotiation", "permitted-terms"]),
                       ("dialogue_only", ["persuasion-protocol", "hardship"]),
                       ("dialogue_only", ["transfer-routing", "hardship"]),
                       ("dialogue_only", ["paid-and-dispute"]),
                       ("dialogue_only", ["debt-statement", "promise-success-speech", "ai-question",
                                          "scam-and-unknown-information", "closing-protocol"]),
                       ("controller_stricter", ["conversation-refusal"], IDENTITY_REFUSAL),
                       ("controller_stricter", ["closing-protocol"], CLOSE_REFUSAL)],
    "public-b-probe": [("controller_stricter", ["conversation-stop-b"],
                        "Requires every product in the sealed resolve_all_products set")],
    "train-1-probe": [("controller_stricter", ["write-retry-requirement"], CLOSE_REFUSAL),
                      ("controller_stricter", ["write-retry-requirement"], UNSCOPED_REPLAY),
                      ("dialogue_only", ["refusal-budget-scope", "hardship-behavior"]),
                      ("dialogue_only", ["hardship-behavior"]),
                      ("covered",),
                      ("dialogue_only", ["volunteered-amounts"]),
                      ("controller_stricter", ["goal-definitions", "closing-selection-detail"], CLOSE_SELECT_REFUSAL)],
    "train-2-probe": [("controller_stricter", ["identity-limit-old", "identity-limit-effective"],
                       "reaching the limit, or zero remaining, exhausts identity"),
                      ("controller_stricter", ["resolution-scope", "jobs"],
                       "Missing, non-string or unknown initial selector values quarantine"),
                      ("covered",),
                      ("dialogue_only", ["hardship-context"]),
                      ("dialogue_only", ["hardship-context", "handoff-authority", "handoff-authority-scope"]),
                      ("dialogue_only", ["recorded-amendments", "recorded-amendment-routing"]),
                      ("controller_stricter", ["write-evidence"], UNSCOPED_REPLAY)],
    "s03": [("protected_unenforceable", ["corrections"]), ("protected_unenforceable", ["budget"]),
            ("protected_unenforceable", ["verify-failures"]),
            ("dialogue_only", ["contact", "formatting-refusal", "conversation-counters", "terminal-order"]),
            ("protected_unenforceable", ["verify-failures", "closing"])],
    "s03-probe": [("dialogue_only", ["opening", "contact-definition"]), ("dialogue_only", ["answer-formats", "corrections"]),
                  ("protected_unenforceable", ["corrections"]), ("protected_unenforceable", ["budget-integrity"]),
                  ("dialogue_only", ["formatting-and-refusal"]), ("protected_unenforceable", ["verification-failures"]),
                  ("protected_unenforceable", ["verification-failures", "close-execution"]),
                  ("protected_unenforceable", ["verification-failures"]), ("dialogue_only", ["conversation-bounds"]),
                  ("protected_unenforceable", ["terminal-table"]),
                  ("dialogue_only", ["privacy-and-forbidden-business", "stored-answer-secrecy", "mismatch-secrecy",
                                     "answer-secrecy", "off-topic-business"])],
    "s04": [("dialogue_only", ["information-close-essential-guards", "readback-format"]),
            ("dialogue_only", ["post-verification-wrong-number", "first-terminal-condition"]),
            ("protected_unenforceable", ["account-failure-discipline", "account-uncertainty"]),
            ("protected_unenforceable", ["first-terminal-condition"])],
}
# Expected verdict per run: "pass", or the sorted indices that stay fatal, each for a stated reason.
REPLAY_EXPECTED = {
    "public-a-original": "pass",
    "public-a-probe": [1],             # its own wording names a bound close reason: a typed close row, not dialogue
    "public-b-probe": "pass",
    "train-1-probe": "pass",
    "train-2-probe": "pass",
    "s03": [0, 1, 2, 4],               # identity-lifecycle kernel gaps
    "s03-probe": [2, 3, 4, 5, 6, 7, 9],  # same gaps; [4] names close codes
    "s04": [0, 1, 2, 3],               # [0]/[1] name close codes; [2]/[3] need the account_failed phase and latch
}
# Original runs on older sources: still rejected without their declarations, by missing typed rules.
REPLAY_MISSING_TYPED = {"train-1-original", "train-2-original"}


def replay_inputs(run, directories):
    """A retained run's exact inputs, accepted only when they match the run's own trace digests."""
    lines = (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    inputs = next(event for event in map(json.loads, lines) if event.get("event") == "inputs")
    sources = []
    for source in inputs["sources"]:
        texts = [path.read_text(encoding="utf-8") for path in (d / source["file"] for d in directories) if path.is_file()]
        sources.append(dict(source, text=next(text for text in texts if digest(text) == source["sha256"])))
    apis = [json.loads(path.read_text(encoding="utf-8")) for d in directories for path in sorted(d.glob("*.json"))]
    return sources, next(api for api in apis if digest(api) == inputs["tool_api_sha256"])


def categorised(policy, plan):
    rules = {rule["id"] for rule in policy["rules"]}
    entries, kept = [], []
    for index, (declaration, item) in enumerate(zip(policy["unsupported_constraints"], plan)):
        if item[0] == "covered":
            continue
        requirement = declaration.split(" Checked", 1)[0] if " Checked" in declaration else declaration.split(". ", 1)[0]
        cited = [ident for ident in item[1] if ident in rules]
        assert cited, (index, item)  # the fixture must name a rule each attempt actually has
        entries.append(dict(entry(item[0], requirement, cited),
                            **({"controller_effect": item[2]} if item[0] == "controller_stricter" else {})))
        kept.append(index)
    return dict(copy.deepcopy(policy), unsupported_constraints=entries), kept


REPLAY = json.loads(Path(os.environ["FREYA_DECLARATIONS_V2_REPLAY"]).read_text(encoding="utf-8")) \
    if os.environ.get("FREYA_DECLARATIONS_V2_REPLAY") else {}


@unittest.skipUnless(REPLAY, "set FREYA_DECLARATIONS_V2_REPLAY to a JSON map of retained runs and their input directories")
class RetainedDeclarationReplayTests(unittest.TestCase):
    def test_retained_rejected_policies_pass_or_stay_fatal_for_stated_reasons(self):
        for name, spec in REPLAY.items():
            run = Path(spec["run"])
            sources, api = replay_inputs(run, [Path(d) for d in spec["inputs"]])
            for path in sorted(run.glob("policy.rejected-*.json")):
                original = path.read_bytes()
                policy = json.loads(original)["policy"]
                with self.subTest(run=name, policy=path.name):
                    with self.assertRaises(PolicyContractError) as caught:  # legacy strings: uncategorised
                        compile_policy_contract(policy, sources, api)
                    for index in range(len(policy["unsupported_constraints"])):
                        self.assertIn(f"policy.unsupported_constraints[{index}] has no declaration category and "
                                      "rejects the build", caught.exception.errors)
                    if name in REPLAY_MISSING_TYPED:
                        with self.assertRaisesRegex(ValueError, "missing typed execution rule|quote is not present"):
                            compile_execution_requirements(dict(policy, unsupported_constraints=[]), sources, api)
                        continue
                    candidate, kept = categorised(policy, REPLAY_CATEGORIES[name])
                    if REPLAY_EXPECTED[name] == "pass":
                        contract = compile_policy_contract(candidate, sources, api)
                        self.assertEqual(contract["declared_limitations"], candidate["unsupported_constraints"])
                        self.assertTrue(compile_execution_requirements(candidate, sources, api)["actions"])
                        compile_action_bindings(candidate, api)
                    else:
                        with self.assertRaises(PolicyContractError) as caught:
                            compile_policy_contract(candidate, sources, api)
                        fatal = sorted(kept[int(error.split("[", 1)[1].split("]", 1)[0])]
                                       for error in caught.exception.errors
                                       if error.startswith("policy.unsupported_constraints["))
                        self.assertEqual(fatal, REPLAY_EXPECTED[name])
                self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

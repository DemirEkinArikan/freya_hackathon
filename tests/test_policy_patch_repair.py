"""Patch-style policy repair: excerpt, deterministic apply, full revalidation and admission; no model calls."""
import copy
import json
import subprocess
import unittest

from builder.build import execute, make_policy_prompt
from builder.contract import PolicyContractError, compile_policy_contract
from builder.policy_patch import PolicyPatchError, apply_policy_patch, repair_excerpt
from builder.runtime import CodexGenerator, Deadline, DeadlineExceeded, GenerationFormatError, Trace
from builder.safety import canonical_json, digest
import test_builder
from test_builder import BRIEF_TEXT, contract, fixture
from test_runtime import completed_stream

SOURCES = [{"id": "brief-1", "file": "brief.md", "sha256": digest(BRIEF_TEXT), "text": BRIEF_TEXT}]


def compact(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def errors_of(policy):
    try:
        compile_policy_contract(policy, SOURCES, contract())
    except PolicyContractError as exc:
        return list(exc.errors)
    return []


def misquoted():
    """A valid fixture policy whose first rule quotes text absent from the brief."""
    policy = fixture()["policy"]
    policy["rules"][0]["source"]["quote"] = "A sentence the brief does not contain."
    return policy


def requote():
    return {"operations": [{"op": "replace", "rule_id": "r1", "value": fixture()["policy"]["rules"][0]}]}


def read_events(run):
    return [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]


class PolicyPatchApplyTests(unittest.TestCase):
    def test_operations_address_the_rejected_policy_and_apply_in_one_deterministic_step(self):
        policy = fixture()["policy"]
        policy["unsupported_constraints"] = [{"category": "dialogue_only", "requirement": "a", "rule_ids": ["r1"],
                                              "justification": "b"}, {"category": "dialogue_only", "requirement": "c",
                                                                      "rule_ids": ["r1"], "justification": "d"}]
        original = copy.deepcopy(policy)
        added = {"id": "r9", "kind": "purpose_note", "value": "x", "source": {"id": "brief-1", "quote": BRIEF_TEXT}}
        patch = {"operations": [
            {"op": "remove", "path": "/rules/1"},
            {"op": "replace", "rule_id": "record_notes", "value": dict(policy["rules"][3], value=True)},
            {"op": "add", "path": "/rules/-", "value": added},
            {"op": "remove", "path": "/unsupported_constraints/0"},
            {"op": "replace", "path": "/unsupported_constraints/1", "value": {"category": "dialogue_only"}},
            {"op": "add", "path": "/unsupported_constraints/-", "value": {"category": "source_ambiguous"}},
            {"op": "replace", "path": "/goal", "value": "Patched goal"}]}
        patched = apply_policy_patch(policy, patch)
        self.assertEqual(policy, original)
        self.assertEqual([rule["id"] for rule in patched["rules"]],
                         ["r1", "close_reasons", "record_notes", "resolve_all_products", "product_order",
                          "close_precedence", "identity_close", "r9"])
        self.assertIs(patched["rules"][2]["value"], True)
        self.assertEqual(patched["unsupported_constraints"], [{"category": "dialogue_only"}, {"category": "source_ambiguous"}])
        self.assertEqual(patched["goal"], "Patched goal")
        self.assertEqual({k: v for k, v in patched.items() if k not in {"rules", "unsupported_constraints", "goal"}},
                         {k: v for k, v in original.items() if k not in {"rules", "unsupported_constraints", "goal"}})
        self.assertEqual(apply_policy_patch(policy, patch), patched)

    def test_malformed_patches_are_refused_and_never_change_the_policy(self):
        policy = fixture()["policy"]
        policy["rules"].append(dict(policy["rules"][0]))  # r1 is now duplicated
        original = copy.deepcopy(policy)
        rule = fixture()["policy"]["rules"][1]
        for patch in (None, [], {}, {"operations": []}, {"operations": {}}, {"operations": [], "note": "x"},
                      {"operations": [{"op": "move", "rule_id": "close_reasons"}]},
                      {"operations": [{"op": "remove", "rule_id": "close_reasons", "path": "/rules/1"}]},
                      {"operations": [{"op": "replace", "rule_id": "execution_actions"}]},
                      {"operations": [{"op": "remove", "rule_id": "execution_actions", "value": rule}]},
                      {"operations": [{"op": "remove", "rule_id": "absent"}]},
                      {"operations": [{"op": "remove", "rule_id": "r1"}]},
                      {"operations": [{"op": "add", "rule_id": "new", "value": rule}]},
                      {"operations": [{"op": "replace", "rule_id": "execution_actions", "value": dict(rule, id="renamed")}]},
                      {"operations": [{"op": "replace", "rule_id": "execution_actions", "value": "not a rule"}]},
                      {"operations": [{"op": "remove", "path": "/rules/99"}]},
                      {"operations": [{"op": "remove", "path": "/unsupported_constraints/0"}]},
                      {"operations": [{"op": "replace", "path": "/rules/-", "value": rule}]},
                      {"operations": [{"op": "add", "path": "/rules/0", "value": rule}]},
                      {"operations": [{"op": "remove", "path": "/goal"}]},
                      {"operations": [{"op": "replace", "path": "/rules", "value": []}]},
                      {"operations": [{"op": "replace", "path": "/rules/01", "value": rule}]},
                      {"operations": [{"op": "remove", "rule_id": "execution_actions"},
                                      {"op": "replace", "path": "/rules/1", "value": rule}]}):
            with self.subTest(patch=patch), self.assertRaises(PolicyPatchError):
                apply_policy_patch(policy, patch)
            self.assertEqual(policy, original)

    def test_a_patched_rule_still_needs_its_exact_source_quote(self):
        policy = misquoted()
        errors = errors_of(policy)
        self.assertIn("policy.rules[0] quote is not present in the supplied source", errors)
        loose = requote()
        loose["operations"][0]["value"]["source"]["quote"] = BRIEF_TEXT.upper()
        self.assertIn("policy.rules[0] quote is not present in the supplied source", errors_of(apply_policy_patch(policy, loose)))
        self.assertEqual(errors_of(apply_policy_patch(policy, requote())), [])


class PolicyPatchExcerptTests(unittest.TestCase):
    def test_excerpt_holds_only_the_referenced_parts_with_their_paths(self):
        policy = fixture()["policy"]
        policy["unsupported_constraints"] = [{"category": "protected_unenforceable", "requirement": "An invented ban.",
                                              "rule_ids": ["record_notes"], "justification": "None enforces it."}]
        errors = errors_of(policy)
        self.assertTrue(any(error.startswith("policy.unsupported_constraints[0] (protected_unenforceable)") for error in errors))
        request = repair_excerpt(policy, errors)
        self.assertEqual(request["validation_errors"], errors)
        self.assertEqual(request["excerpt"], {
            "rules": [{"path": "/rules/3", "rule": policy["rules"][3]}],
            "unsupported_constraints": [{"path": "/unsupported_constraints/0", "entry": policy["unsupported_constraints"][0]}],
            "fields": {}})
        self.assertEqual(request["rule_index"], [[rule["id"], rule["kind"]] for rule in policy["rules"]])
        self.assertEqual(request["unsupported_constraints_count"], 1)

    def test_rules_are_referenced_by_path_id_and_kind_but_not_by_shape_guidance(self):
        policy = fixture()["policy"]
        request = repair_excerpt(policy, [
            "policy.rules[0] quote is not present in the supplied source",
            "rule identity_close has an unknown or self override",
            "policy rules for close_precedence have conflicting active values",
            "missing typed execution rule: callback_window; expected product_order and close_reasons shapes",
            "policy.sources[0].sha256 differs from the supplied source"])
        self.assertEqual([item["path"] for item in request["excerpt"]["rules"]], ["/rules/0", "/rules/6", "/rules/7"])
        self.assertEqual(request["excerpt"]["fields"], {"sources": policy["sources"]})


class PolicyPatchPromptTests(unittest.TestCase):
    def test_patch_prompt_states_the_patch_wire_and_keeps_the_extraction_rules(self):
        request = repair_excerpt(misquoted(), errors_of(misquoted()))
        header, data = make_policy_prompt(SOURCES, contract(), request, patch=True).split("\n\nAUTHORIZED INPUTS:\n", 1)
        full_header = make_policy_prompt(SOURCES, contract()).split("\n\nAUTHORIZED INPUTS:\n", 1)[0]
        self.assertIn("patch_json is a serialized JSON object", header)
        self.assertNotIn("policy_json contains the complete provenance-aware policy object", header)
        self.assertTrue(header.endswith(full_header.split("Read every supplied source.", 1)[1]))
        payload = json.loads(data)
        self.assertEqual(set(payload), {"sources", "openapi", "execution_interface", "repair"})
        self.assertEqual(payload["repair"], request)

    def test_patch_repair_omits_unreferenced_policy_parts(self):
        policy = misquoted()
        policy["rules"] += [{"id": f"note-{i}", "kind": "dialogue_note", "value": "Say the approved line. " * 12,
                             "source": {"id": "brief-1", "quote": BRIEF_TEXT}} for i in range(150)]
        errors = errors_of(policy)
        request = repair_excerpt(policy, errors)
        full = {"validation_errors": errors, "previous_policy": policy}
        self.assertLess(len(canonical_json(request)), 0.15 * len(canonical_json(full)))
        saved = len(make_policy_prompt(SOURCES, contract(), full)) - len(make_policy_prompt(SOURCES, contract(), request, patch=True))
        self.assertGreater(saved, 0.8 * compact(policy))
        self.assertLess(compact(request["excerpt"]), 0.02 * compact(policy))


class PolicyPatchBuildTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def run_directory(self, out="out"):
        return next((self.directory / out).iterdir())

    def test_rejected_policy_is_repaired_by_a_recorded_patch(self):
        generator = self.generator()
        generator.generate.return_value = {"policy": misquoted(), "workflow": fixture()["workflow"]}
        generator.generate_policy_patch.return_value = {"patch": requote()}
        result = execute(self.args("--generate-only"), generator=generator)
        self.assertEqual((result["status"], result["policy_repairs"]), ("generated_validated", 1))
        self.assertEqual(generator.generate.call_count, 1)
        prompt = generator.generate_policy_patch.call_args.args[0]
        self.assertEqual(generator.generate_policy_patch.call_args.kwargs["reserve_seconds"], 750)
        repair = json.loads(prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1])["repair"]
        self.assertNotIn("previous_policy", repair)
        self.assertEqual(repair["excerpt"]["rules"], [{"path": "/rules/0", "rule": misquoted()["rules"][0]}])
        run = self.run_directory()
        self.assertEqual(json.loads((run / "policy.json").read_text()), fixture()["policy"])
        events = read_events(run)
        patch_event = next(e for e in events if e["event"] == "policy_patch")
        self.assertEqual({k: patch_event[k] for k in ("attempt", "applied", "error", "operations", "patch",
                                                       "patch_characters", "prompt_characters")},
                         {"attempt": 2, "applied": True, "error": None, "operations": 1, "patch": requote(),
                          "patch_characters": compact(requote()), "prompt_characters": len(prompt)})
        self.assertEqual(patch_event["excerpt_characters"], compact(repair["excerpt"]))
        self.assertIsInstance(patch_event["elapsed_seconds"], float)
        passed = next(e for e in events if e["event"] == "policy_execution_requirements")
        self.assertEqual((passed["attempt"], passed["repair_mode"]), (2, "patch"))
        source = json.loads((run / "builder-source.json").read_text())
        self.assertEqual(source["resolved_config"]["policy_repair_mode"], "patch")

    def test_any_patch_problem_falls_back_to_a_full_repair_in_the_same_attempt(self):
        problems = {"empty": {"patch": {"operations": []}}, "not an object": "patch",
                    "unknown rule": {"patch": {"operations": [{"op": "remove", "rule_id": "absent"}]}},
                    "format": GenerationFormatError("inner_json")}
        for name, problem in problems.items():
            generator = self.generator()
            generator.generate.return_value = fixture()
            generator.generate.side_effect = [{"policy": misquoted(), "workflow": fixture()["workflow"]}, fixture()]
            if isinstance(problem, Exception):
                generator.generate_policy_patch.side_effect = problem
            else:
                generator.generate_policy_patch.return_value = problem
            out = f"out-{name.replace(' ', '-')}"
            with self.subTest(problem=name):
                result = execute(self.args("--generate-only", "--output-dir", str(self.directory / out)), generator=generator)
                self.assertEqual(result["policy_repairs"], 1)
                self.assertEqual((generator.generate.call_count, generator.generate_policy_patch.call_count), (2, 1))
                full = json.loads(generator.generate.call_args_list[1].args[0].split("\n\nAUTHORIZED INPUTS:\n", 1)[1])
                self.assertEqual(full["repair"]["previous_policy"], misquoted())
                events = read_events(self.run_directory(out))
                patch_event = next(e for e in events if e["event"] == "policy_patch")
                self.assertFalse(patch_event["applied"])
                self.assertTrue(patch_event["error"])
                self.assertEqual([e.get("repair_mode") for e in events if e["event"] == "repair_admission"], ["patch", None])
                passed = next(e for e in events if e["event"] == "policy_execution_requirements")
                self.assertEqual((passed["attempt"], passed["repair_mode"]), (2, "full"))

    def test_patch_is_admitted_where_a_full_rewrite_is_not(self):
        # The first call ends at 740 s: min(450, 1060 - 300 - 450) = 310 s remain for a repair.
        invalid = misquoted()
        invalid["goal"] += " " + "x" * 60000
        full_estimate = 61 + 0.0044 * compact(invalid)
        excerpt = repair_excerpt(invalid, errors_of(invalid))["excerpt"]
        patch_estimate = 61 + 0.0044 * compact(excerpt)
        self.assertTrue(0.9 * patch_estimate <= 310 < 0.9 * full_estimate)
        for mode in ("patch", "full"):
            now = [0.0]
            deadline = Deadline(1800, clock=lambda: now[0])
            generator = self.generator()
            generator.generate.return_value = fixture()
            def extract(*args, **kwargs):
                now[0] += 740
                return {"policy": copy.deepcopy(invalid), "workflow": fixture()["workflow"]}
            def patch(prompt, **kwargs):
                now[0] += 60
                return {"patch": requote()}
            generator.generate.side_effect = extract
            generator.generate_policy_patch.side_effect = patch
            args = self.args("--generate-only", "--policy-repair", mode, "--output-dir", str(self.directory / mode))
            with self.subTest(mode=mode):
                if mode == "patch":
                    result = execute(args, generator=generator, deadline=deadline)
                    self.assertEqual((result["status"], result["policy_repairs"]), ("generated_validated", 1))
                    self.assertEqual(generator.generate_policy_patch.call_args.kwargs["max_seconds"], 310)
                else:
                    with self.assertRaisesRegex(DeadlineExceeded, r"^No time for a repair \(310 s available, about "
                                                                  rf"{full_estimate:.0f} s needed\): Policy extraction failed: "):
                        execute(args, generator=generator, deadline=deadline)
                    generator.generate_policy_patch.assert_not_called()
                self.assertEqual(generator.generate.call_count, 1)
                admissions = [e for e in read_events(self.run_directory(mode)) if e["event"] == "repair_admission"]
                self.assertEqual([(e.get("repair_mode"), e["admitted"], e["available_seconds"], e["estimated_seconds"])
                                  for e in admissions],
                                 [("patch", True, 310, round(patch_estimate, 1))] if mode == "patch"
                                 else [(None, False, 310, round(full_estimate, 1))])

    def test_a_rejected_non_object_policy_goes_straight_to_a_full_repair(self):
        self.assertEqual(repair_excerpt(None, ["policy.goal must be a nonempty string"])["excerpt"],
                         {"rules": [], "unsupported_constraints": [], "fields": {}})
        generator = self.generator()
        generator.generate.return_value = fixture()
        generator.generate.side_effect = [{"policy": None, "workflow": {}}, fixture()]
        result = execute(self.args("--generate-only"), generator=generator)
        self.assertEqual((result["policy_repairs"], generator.generate.call_count), (1, 2))
        generator.generate_policy_patch.assert_not_called()

    def test_generators_without_the_patch_call_keep_full_repairs(self):
        class FullOnly:
            def __init__(self):
                self.policies = [{"policy": misquoted(), "workflow": {}}, {"policy": fixture()["policy"], "workflow": {}}]
                self.prompts = []
            def generate(self, prompt, **_):
                self.prompts.append(prompt)
                return self.policies.pop(0)
            def generate_plan(self, prompt, *, contract_sha256):
                return {"plan": test_builder.plan(), "contract_sha256": contract_sha256}
        generator = FullOnly()
        result = execute(self.args("--generate-only"), generator=generator)
        self.assertEqual((result["policy_repairs"], len(generator.prompts)), (1, 2))
        self.assertFalse(any(e["event"] == "policy_patch" for e in read_events(self.run_directory())))


class PolicyPatchRuntimeTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown

    def generator(self, wire):
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, completed_stream(wire), "")
        generator = CodexGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(), run=run)
        generator._checked = generator._isolation_checked = True
        generator._catalog = ("{}", {"fixture": "catalog"})
        return generator

    def test_patch_wire_is_parsed_strictly(self):
        generator = self.generator({"patch_json": json.dumps(requote())})
        self.assertEqual(generator.generate_policy_patch("prompt", max_seconds=300, reserve_seconds=750), {"patch": requote()})
        call = next(json.loads(line) for line in (self.directory / "trace.jsonl").read_text().splitlines()
                    if json.loads(line)["event"] == "model_call")
        self.assertEqual(call["purpose"], "Repair rejected banking policy with a structured patch")
        for wire, code in (({"policy_json": "{}", "workflow_json": "{}"}, "wire_shape"), ({"patch_json": {}}, "wire_shape"),
                           ({"patch_json": "{"}, "inner_json"), ({"patch_json": "[]"}, "inner_shape")):
            with self.subTest(wire=wire), self.assertRaises(GenerationFormatError) as caught:
                self.generator(wire).generate_policy_patch("prompt", max_seconds=300, reserve_seconds=750)
            self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()

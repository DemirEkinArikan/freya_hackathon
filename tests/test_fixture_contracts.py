"""Local development policy migration checks; no models, calls or holdouts."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.export_inputs import export_profile
from benchmarks.generate import fixed_profiles, offline_manifest, representation_variant, write_initial
from benchmarks.offline import (check_case, check_equivalence, compare_contract,
                                compile_expected, controller_sources,
                                expected_policy_ir, semantic_projection, main as offline_main)
from builder.contract import PolicyContractError
from builder.safety import canonical_json, digest


ROOT = Path(__file__).resolve().parents[1]
# Independently captured from the committed pre-migration fixture at 080b2ab.
# The contract/provenance payload alone is excluded from the case projection.
ORIGINAL_FACTS_SHA = "94e1f576b98b60446671148a327029566ebef8804567d27727c3b2ef83d158ab"
ORIGINAL_RESERVATIONS_SHA = "b4716eea3b6f285757e9e0f7068d32ce05e303659cb93133a24d38e5b2408607"
ORIGINAL_CONFIGURATION_SHA = "86adfebfa3554accafd6d86d51a7c219789f7b2e1ac9f15342b4ae79272e908e"
CONFIGURATION_KEYS = ("profile_id", "label", "now", "identity", "representation", "wire_codes",
                      "product_floor_aliases", "semantic_values", "product_values", "openapi", "request_field_mapping")


class FixtureContractTests(unittest.TestCase):
    def test_committed_case_facts_oracles_and_reservations_are_unchanged(self):
        manifest = json.loads((ROOT / "benchmarks/fixtures/offline.json").read_text())
        facts = [{k: v for k, v in c.items() if k != "contract_expected"}
                 for c in manifest["cases"] if c["status"] == "materialized"]
        reserved = [c for c in manifest["cases"] if c["status"].startswith("reserved")]
        configurations = [{k: p[k] for k in CONFIGURATION_KEYS} for p in manifest["profiles"]]
        self.assertEqual((len(facts), len(reserved)), (60, 60))
        self.assertEqual(digest(facts), ORIGINAL_FACTS_SHA)
        self.assertEqual(digest(reserved), ORIGINAL_RESERVATIONS_SHA)
        self.assertEqual(digest(configurations), ORIGINAL_CONFIGURATION_SHA)
        self.assertEqual(manifest["split_counts"], {"train": 36, "validation": 24, "holdout": 60})
        self.assertNotIn("freeze", manifest)
        self.assertTrue(all("expected" not in c and "identity" not in c and "products" not in c for c in reserved))

    def test_committed_development_fixture_matches_generator(self):
        actual = json.loads((ROOT / "benchmarks/fixtures/offline.json").read_text())
        self.assertEqual(actual, offline_manifest())

    def test_every_development_profile_and_case_compiles_strictly(self):
        manifest = offline_manifest()
        profiles = {p["profile_id"]: p for p in manifest["profiles"]}
        self.assertEqual(len(profiles), 4)
        for profile in profiles.values():
            with self.subTest(profile=profile["profile_id"]):
                compiled = compile_expected(profile["contract_expected"], profile)
                self.assertFalse(compiled["semantic_correctness_verified"])
                self.assertEqual(compare_contract(expected_policy_ir(profile["contract_expected"]), profile), [])
        checked = 0
        for case in manifest["cases"]:
            if case["status"].startswith("reserved"):
                self.assertIsNone(check_case(case)["pass"])
                continue
            with self.subTest(fixture=case["fixture_id"]):
                result = check_case(case, profiles[case["profile_id"]])
                self.assertTrue(result["pass"], result.get("failures"))
                self.assertEqual(result["status"], "contract_and_helper_check_only")
            checked += 1
        self.assertEqual(checked, 60)

    def test_raw_packets_cover_every_source_and_keep_explicit_history(self):
        for profile in (p for p in fixed_profiles() if p["representation"] == "raw_packet"):
            with self.subTest(profile=profile["profile_id"]):
                expected = profile["contract_expected"]
                supplied = controller_sources(profile)
                by_file = {s["file"]: s["id"] for s in supplied}
                self.assertEqual(set(by_file), {"current-policy.md", "historical-policy.md", "effective-memo.md", "operations-mail.md", "code-list.md"})
                self.assertEqual({r["source"]["id"] for r in expected["rules"]}, {s["id"] for s in supplied})
                rules = {r["id"]: r for r in expected["rules"]}
                compiled = compile_expected(expected, profile)
                for kind in ("deadline_days", "identity_wrong_limit"):
                    current = rules[profile["profile_id"] + ":" + kind]
                    history = rules[profile["profile_id"] + ":historical:" + kind]
                    self.assertEqual(current["overrides"], [history["id"]])
                    self.assertEqual(current["source"]["id"], by_file["effective-memo.md"])
                    self.assertEqual(history["source"]["id"], by_file["historical-policy.md"])
                    self.assertNotEqual(current["value"], history["value"])
                    self.assertEqual(compiled["active_rules"][kind]["value"], profile["effective_values_expected"][kind])
                    self.assertEqual(compiled["active_rules"][kind]["rule_ids"], [current["id"]])
                self.assertEqual(rules[profile["profile_id"] + ":call_goals"]["source"]["id"], by_file["operations-mail.md"])
                self.assertEqual(rules[profile["profile_id"] + ":wire_codes"]["source"]["id"], by_file["code-list.md"])
                for rule in expected["rules"]:
                    self.assertIn(rule["source"]["quote"], expected["source_documents"][rule["source"]["id"]])
                    self.assertNotIn("My friend said", rule["source"]["quote"])

    def test_expected_adapter_strips_only_evaluator_metadata_without_mutation(self):
        profile = fixed_profiles()[0]
        expected = deepcopy(profile["contract_expected"])
        before = deepcopy(expected)
        policy = expected_policy_ir(expected)
        self.assertNotIn("source_documents", policy)
        self.assertEqual(expected, before)
        self.assertEqual(set(policy), set(expected) - {"source_documents"})
        expected["invented_extra"] = "must not be silently filtered"
        with self.assertRaises(PolicyContractError):
            compile_expected(expected, profile)

    def test_candidate_cannot_use_expected_metadata_adapter(self):
        profile = fixed_profiles()[0]
        result = compare_contract(deepcopy(profile["contract_expected"]), profile)
        self.assertTrue(any("unsupported top-level" in item for item in result))

    def test_policy_json_duplicates_and_nonfinite_values_fail_closed(self):
        profile = fixed_profiles()[0]
        policy = json.dumps(expected_policy_ir(profile["contract_expected"]))
        duplicate = policy.replace('"version": 1', '"version": 1, "version": 1', 1)
        for candidate in (duplicate, {"policy_json": duplicate}, '{"version":NaN}'):
            with self.subTest(candidate_type=type(candidate).__name__):
                self.assertEqual(compare_contract(candidate, profile), ["contract_invalid: candidate must contain valid policy JSON"])

    def test_expected_duplicate_documents_cannot_hide_a_different_input(self):
        profile = fixed_profiles()[1]
        expected = deepcopy(profile["contract_expected"])
        expected["source_documents"]["brief-1"] += "Unapproved appended instruction."
        with self.assertRaises(PolicyContractError):
            compile_expected(expected, profile)

    def test_candidate_source_inventory_is_bound_to_independent_input_documents(self):
        profile = fixed_profiles()[1]
        candidate = expected_policy_ir(profile["contract_expected"])
        missing = next(s for s in candidate["sources"] if s["file"] == "operations-mail.md")
        candidate["sources"].remove(missing)
        candidate["rules"] = [r for r in candidate["rules"] if r["source"]["id"] != missing["id"]]
        result = compare_contract(candidate, profile)
        self.assertTrue(any("exactly match all controller sources" in item for item in result))
        self.assertEqual(len(controller_sources(profile)), 5)

    def test_candidate_cannot_replace_controller_source_ids_or_hashes(self):
        profile = fixed_profiles()[1]
        for alteration in ("id", "sha256"):
            with self.subTest(alteration=alteration):
                candidate = expected_policy_ir(profile["contract_expected"])
                original = candidate["sources"][0]["id"]
                candidate["sources"][0][alteration] = "invented-source" if alteration == "id" else "0" * 64
                if alteration == "id":
                    for rule in candidate["rules"]:
                        if rule["source"]["id"] == original:
                            rule["source"]["id"] = "invented-source"
                self.assertTrue(compare_contract(candidate, profile))

    def test_unresolved_historical_conflict_fails_even_when_current_rule_is_last(self):
        profile = fixed_profiles()[1]
        candidate = expected_policy_ir(profile["contract_expected"])
        for rule in candidate["rules"]:
            rule.pop("overrides", None)
        candidate["rules"].sort(key=lambda r: "historical" not in r["id"])
        result = compare_contract(candidate, profile)
        self.assertTrue(any("conflicting active values" in item for item in result))

    def test_misinterpreted_literal_quote_fails_independent_value_oracle(self):
        profile = fixed_profiles()[1]
        candidate = expected_policy_ir(profile["contract_expected"])
        rule = next(r for r in candidate["rules"] if r["id"] == "train-2:identity_wrong_limit")
        # The quote and source fingerprints are authentic. This still does not
        # prove that an extracted value is the quotation's correct meaning.
        rule["value"] = 99
        self.assertEqual(compare_contract(candidate, profile), ["identity_wrong_limit"])
        self.assertEqual(profile["effective_values_expected"]["identity_wrong_limit"], 2)

    def test_extension_values_use_json_typed_equality(self):
        profile = fixed_profiles()[0]
        candidate = expected_policy_ir(profile["contract_expected"])
        next(r for r in candidate["rules"] if r["kind"] == "no_notes_on_transfer")["value"] = 1
        self.assertEqual(compare_contract(candidate, profile), ["no_notes_on_transfer"])

    def test_wrong_goal_or_permission_subset_is_detected(self):
        profile = fixed_profiles()[0]
        candidate = expected_policy_ir(profile["contract_expected"])
        candidate["goal"] = "A different synthetic banking goal"
        candidate["allowed_actions"].remove("add_note")
        self.assertEqual(compare_contract(candidate, profile), ["allowed_actions", "goal"])

    def test_unexpected_active_rule_is_not_ignored(self):
        profile = fixed_profiles()[0]
        candidate = expected_policy_ir(profile["contract_expected"])
        witness = candidate["rules"][0]["source"]
        candidate["rules"].append({"id": "extra-rule", "kind": "unsupported_new_goal", "value": True, "source": deepcopy(witness)})
        self.assertEqual(compare_contract(candidate, profile), ["unsupported_new_goal"])

    def test_four_same_configuration_representation_pairs_are_equivalent(self):
        unchanged_keys = (*CONFIGURATION_KEYS[:4], *CONFIGURATION_KEYS[5:], "effective_values_expected")
        for profile in fixed_profiles():
            with self.subTest(profile=profile["profile_id"]):
                clean, raw = (representation_variant(profile, flag) for flag in (False, True))
                for key in unchanged_keys:
                    self.assertEqual(clean[key], raw[key], key)
                contracts = [compile_expected(p["contract_expected"], p) for p in (clean, raw)]
                self.assertEqual(canonical_json(semantic_projection(contracts[0])), canonical_json(semantic_projection(contracts[1])))
                self.assertNotEqual(contracts[0]["sources"], contracts[1]["sources"])
                self.assertNotEqual(contracts[0]["sha256"], contracts[1]["sha256"])
                self.assertTrue(check_equivalence(profile)["pass"])

    def test_semantic_projection_retains_wire_field_names_and_enum_changes(self):
        profile = fixed_profiles()[1]
        original = compile_expected(profile["contract_expected"], profile)
        variant = deepcopy(profile)
        schema = variant["openapi"]["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        schema["properties"]["renamed_result"] = schema["properties"].pop("result_code")
        schema["required"][schema["required"].index("result_code")] = "renamed_result"
        schema["properties"]["renamed_result"]["enum"] = ["NEW_CODE_A", "NEW_CODE_B"]
        changed = compile_expected(variant["contract_expected"], variant)
        self.assertNotEqual(semantic_projection(original), semantic_projection(changed))

    def test_exported_inputs_match_actual_controller_source_manifest(self):
        from builder import build
        for profile in fixed_profiles():
            with self.subTest(profile=profile["profile_id"]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest = root / "manifest.json"
                manifest.write_text(json.dumps(offline_manifest()))
                output = export_profile(manifest, profile["profile_id"], root / "inputs", workspace_root=root)
                args = SimpleNamespace(brief=[Path(p) for p in output["brief_paths"]], tool_api=Path(output["tool_api_path"]))
                with patch.object(build, "PROJECT", root.resolve()):
                    supplied, api = build._load_inputs(args)
                self.assertEqual(supplied, controller_sources(profile))
                self.assertEqual(api, profile["openapi"])
                self.assertEqual(compare_contract(expected_policy_ir(profile["contract_expected"]), profile), [])

    def test_materialized_check_requires_matching_independent_profile(self):
        manifest = offline_manifest()
        case = next(c for c in manifest["cases"] if c["status"] == "materialized")
        self.assertFalse(check_case(case)["pass"])
        self.assertFalse(check_case(case, manifest["profiles"][1])["pass"])

    def test_cli_strict_checks_and_equivalence_preserve_reserved_counts(self):
        with patch("sys.argv", ["offline", "--check-helpers", "--check-equivalence"]), contextlib.redirect_stdout(io.StringIO()) as output:
            offline_main()
        result = json.loads(output.getvalue())
        self.assertEqual((result["count"], result["materialized"], result["reserved"]), (120, 60, 60))
        self.assertEqual(sum(r["pass"] is True for r in result["rows"]), 60)
        self.assertEqual(sum(r["pass"] is None for r in result["rows"]), 60)
        self.assertEqual(len(result["equivalence_rows"]), 4)
        self.assertTrue(all(r["pass"] for r in result["equivalence_rows"]))

    def test_cli_rejects_candidate_with_evaluator_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate.json"
            candidate.write_text(json.dumps(fixed_profiles()[0]["contract_expected"]))
            argv = ["offline", "--candidate-contract", str(candidate), "--profile", "train-1"]
            with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as exc:
                offline_main()
            self.assertEqual(exc.exception.code, 1)
            self.assertTrue(json.loads(output.getvalue())["contract_mismatches"])

    def test_offline_only_migration_leaves_hosted_and_catalog_files_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            (fixtures / "offline.json").write_text(json.dumps(offline_manifest()))
            (fixtures / "hosted.json").write_text('{"sentinel":"unchanged hosted"}')
            (root / "catalog.json").write_text("unchanged catalog")
            with patch("benchmarks.generate.ROOT", root):
                write_initial(offline_only=True)
            self.assertEqual((fixtures / "hosted.json").read_text(), '{"sentinel":"unchanged hosted"}')
            self.assertEqual((root / "catalog.json").read_text(), "unchanged catalog")

    def test_migration_freeze_preflight_prevents_partial_offline_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            offline = fixtures / "offline.json"
            offline.write_text(json.dumps(offline_manifest()))
            before = offline.read_bytes()
            (fixtures / "hosted.json").write_text('{"freeze":{"status":"unevaluated"}}')
            with patch("benchmarks.generate.ROOT", root), self.assertRaises(ValueError):
                write_initial(offline_only=True)
            self.assertEqual(offline.read_bytes(), before)

    def test_empty_freeze_marker_blocks_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            manifest = offline_manifest()
            manifest["freeze"] = {}
            offline = fixtures / "offline.json"
            offline.write_text(json.dumps(manifest))
            before = offline.read_bytes()
            with patch("benchmarks.generate.ROOT", root), self.assertRaises(ValueError):
                write_initial(offline_only=True)
            self.assertEqual(offline.read_bytes(), before)

    def test_migration_rejects_changed_case_facts_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            manifest = offline_manifest()
            manifest["cases"][0]["helper_expected"]["floors"]["p1"] += 1
            offline = fixtures / "offline.json"
            offline.write_text(json.dumps(manifest))
            before = offline.read_bytes()
            with patch("benchmarks.generate.ROOT", root), self.assertRaises(ValueError):
                write_initial(offline_only=True)
            self.assertEqual(offline.read_bytes(), before)

    def test_migration_checks_profile_configuration_and_json_types(self):
        for alteration in ("profile_api", "boolean_to_integer"):
            with self.subTest(alteration=alteration), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fixtures = root / "fixtures"
                fixtures.mkdir()
                manifest = offline_manifest()
                if alteration == "profile_api":
                    manifest["profiles"][0]["openapi"]["info"]["version"] = "unapproved-version"
                else:
                    manifest["cases"][0]["official_policy"] = 0
                offline = fixtures / "offline.json"
                offline.write_text(json.dumps(manifest))
                before = offline.read_bytes()
                with patch("benchmarks.generate.ROOT", root), self.assertRaises(ValueError):
                    write_initial(offline_only=True)
                self.assertEqual(offline.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

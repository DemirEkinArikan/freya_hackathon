import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.catalog import archetypes
from benchmarks.freeze import consume, freeze_candidate, generate_holdout, verify_freeze
from benchmarks.export_inputs import export_profile
from benchmarks.generate import fixed_profiles, hosted_manifest, offline_manifest
from benchmarks.hosted import HostedBackend, NoRedirect, check_holdout_config, evaluate, main as hosted_main
from benchmarks.paths import output_path
from benchmarks.offline import check_case, check_manifest, compare_contract, expected_policy_ir


class FakeBackend:
    def __init__(self, rows):
        self.rows = rows
        self.by_persona = {"persona-" + str(i): r for i, r in enumerate(rows)}
        self.sessions, self.simulations, self.ledgers = [], [], {}

    def create_personas(self, personas):
        self.personas = personas
        return [{"id": k} for k in self.by_persona]

    def practice_session(self, seed):
        customer = "session-" + str(len(self.sessions))
        variables = {"customer_id": customer, "now": "2035-02-03T12:15", "server_only": "keep-me"}
        self.sessions.append((seed, deepcopy(variables)))
        return variables

    def simulate(self, agent_id, name, variables, concurrency):
        self.simulations.append(deepcopy(variables))
        for pid, session in variables.items():
            row = self.by_persona[pid]
            expected = deepcopy(row["case"]["expected"])
            calls = []
            if expected["outcome"] not in ("not_reached", "not_verified"):
                calls = [{"tool": "verify_identity", "args": {"field": field, "answer": "synthetic"}, "result": {"ok": True, "verified": True}} for field in ("date_of_birth", "phone_last4")]
                calls += [{"tool": "get_account", "args": {}, "result": {"ok": True}}]
            for promise in expected["promises"]:
                calls.append({"tool": "create_promise", "args": promise, "result": {"ok": True}})
            for note in expected["notes"]: calls.append({"tool": "add_note", "args": note, "result": {"ok": True}})
            for transfer in expected["transfers"]: calls.append({"tool": "transfer_to_agent", "args": transfer, "result": {"ok": True}})
            for callback in expected["callbacks"]: calls.append({"tool": "schedule_callback", "args": callback, "result": {"ok": True}})
            calls.append({"tool": "close_call", "args": {"outcome": expected["outcome"]}, "result": {"ok": True}})
            self.ledgers[session["customer_id"]] = {**expected, "calls": calls}
        return {"simulation_id": "sim", "iteration_id": "iteration", "status": "completed"}

    def iteration(self, simulation):
        return {"runs": [{"personaId": pid, "outcome": "pass", "callId": "call-" + pid} for pid in self.by_persona]}

    def ledger(self, customer): return self.ledgers[customer]
    def call(self, call_id): return {"messages": [{"role": "user", "content": "Synthetic test turn"}], "id": call_id}


class BenchmarkTests(unittest.TestCase):
    def test_catalog_and_manifest_have_correct_counts_without_false_blind_claim(self):
        catalog = archetypes()
        self.assertEqual(len(catalog), 60)
        self.assertEqual({s: sum(a["split"] == s for a in catalog) for s in ("train", "validation", "holdout")}, {"train": 18, "validation": 12, "holdout": 30})
        manifest = offline_manifest()
        self.assertEqual(check_manifest(manifest), [])
        self.assertEqual(sum(c["status"] == "materialized" for c in manifest["cases"]), 60)
        self.assertTrue(all("expected" not in c for c in manifest["cases"] if c["split"] == "holdout"))

    def test_materialized_oracles_match_policy_helpers_and_known_calendar_results(self):
        manifest = offline_manifest()
        profiles = {p["profile_id"]: p for p in manifest["profiles"]}
        for case in manifest["cases"]:
            if case["status"] == "materialized":
                with self.subTest(case=case["fixture_id"]):
                    self.assertTrue(check_case(case, profiles[case["profile_id"]])["pass"])
        known = {"train-1": "2026-12-04", "train-2": "2027-01-06", "validation-1": "2027-01-13", "validation-2": "2027-04-08"}
        for name, deadline in known.items():
            case = next(c for c in manifest["cases"] if c.get("profile_id") == name and c["number"] not in (11, 13, 14, 17, 18, 20, 21, 22, 24, 25, 38, 40, 58))
            self.assertEqual(case["helper_expected"]["deadlines"]["p1"], deadline)

    def test_contract_change_is_detected(self):
        profile = fixed_profiles()[0]
        candidate = expected_policy_ir(profile["contract_expected"])
        next(r for r in candidate["rules"] if r["kind"] == "identity_wrong_limit")["value"] = 99
        self.assertEqual(compare_contract(candidate, profile), ["identity_wrong_limit"])

    def test_compiler_string_contract_and_explicit_override_are_supported(self):
        profile = fixed_profiles()[1]
        candidate = expected_policy_ir(profile["contract_expected"])
        candidate["rules"].reverse()
        self.assertEqual(compare_contract({"policy_json": json.dumps(candidate)}, profile), [])

    def test_wire_codes_floor_aliases_and_goals_are_independent_of_public_policy(self):
        manifest = offline_manifest()
        raw = next(c for c in manifest["cases"] if c["profile_id"] == "train-2" and c["canonical_expected"]["outcome"] == "promise")
        self.assertNotEqual(raw["expected"]["outcome"], "promise")
        self.assertEqual(raw["expected"]["outcome"], raw["wire_codes"]["outcomes"]["promise"])
        self.assertIn("card_required_due", raw["products"][0])
        self.assertNotIn("minimum_payment", raw["products"][0])
        self.assertIn(raw["variables"]["call_goal"], raw["wire_codes"]["goals"].values())
        self.assertNotIn(" = {", "\n".join(raw["contract_expected"]["source_documents"].values()))

    def test_corrupt_rule_quote_is_rejected_against_actual_source(self):
        manifest = offline_manifest()
        row = next(c for c in manifest["cases"] if c["status"] == "materialized")
        row = deepcopy(row)
        row["contract_expected"]["rules"][0]["source"]["quote"] = "Not a sentence in this policy."
        profile = next(p for p in manifest["profiles"] if p["profile_id"] == row["profile_id"])
        self.assertFalse(check_case(row, profile)["pass"])

    def test_hosted_counts_and_alias_mapping(self):
        manifest = hosted_manifest()
        self.assertEqual(len(manifest["cases"]), 120)
        self.assertEqual(len({c["fixture_id"] for c in manifest["cases"]}), 120)
        self.assertEqual({s: sum(c["split"] == s for c in manifest["cases"]) for s in ("train", "validation", "holdout")}, {"train": 36, "validation": 24, "holdout": 60})
        self.assertTrue(all(c["bank_case_id"].startswith("PUB-") for c in manifest["cases"]))

    def test_adapter_does_not_overwrite_same_seed_variants_and_preserves_server_variables(self):
        source = hosted_manifest()["cases"][0]
        rows = [deepcopy(source), deepcopy(source)]
        rows[1]["fixture_id"] = "distinct-variant"
        rows[1]["case"]["id"] = "distinct-variant"
        backend = FakeBackend(rows)
        result = evaluate(backend, "agent", rows, reps=3)
        self.assertEqual(result["passed"], 2)
        self.assertEqual(len(backend.sessions), 6)
        self.assertEqual(len({s[1]["customer_id"] for s in backend.sessions}), 6)
        self.assertEqual([s[0] for s in backend.sessions], [source["bank_case_id"]] * 6)
        for simulation in backend.simulations:
            self.assertEqual(len(simulation), 2)
            self.assertTrue(all(v["server_only"] == "keep-me" and v["now"] == "2035-02-03T12:15" for v in simulation.values()))

    def test_direct_adapter_refuses_duplicate_variants_before_persona_creation(self):
        row = hosted_manifest()["cases"][0]
        backend = FakeBackend([row, deepcopy(row)])
        with self.assertRaises(ValueError): evaluate(backend, "agent", [row, deepcopy(row)])
        self.assertFalse(hasattr(backend, "personas"))

    def test_infrastructure_failure_is_not_scored_as_a_behavior_failure(self):
        rows = [hosted_manifest()["cases"][0]]
        backend = FakeBackend(rows)
        backend.iteration = lambda _: {"runs": []}
        result = evaluate(backend, "agent", rows, reps=1)
        self.assertEqual(result["status"], "infrastructure_failure")
        self.assertEqual(result["passed"], 0)

    def test_missing_transcript_is_an_infrastructure_failure(self):
        rows = [hosted_manifest()["cases"][0]]
        backend = FakeBackend(rows)
        backend.call = lambda _: {"messages": []}
        result = evaluate(backend, "agent", rows, reps=1)
        self.assertEqual(result["status"], "infrastructure_failure")
        self.assertEqual(result["passed"], 0)

    def test_model_runtime_config_changes_invalidate_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "build.py").write_text("candidate = 1\n")
            config = root / "runtime.json"
            config.write_text(json.dumps({"model": "test-model", "reasoning_effort": "high"}))
            record = root / "freeze.json"
            frozen = freeze_candidate(candidate, record, config)
            check_holdout_config(frozen, {"model": "test-model", "reasoning_effort": "high"})
            with self.assertRaises(ValueError): check_holdout_config(frozen, {"model": "other-model", "reasoning_effort": "high"})
            config.write_text(json.dumps({"model": "other-model", "reasoning_effort": "high"}))
            with self.assertRaises(ValueError): verify_freeze(record)

    def test_output_paths_reject_escape_and_symlink_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError): output_path(outside / "results.json", root)
            with self.assertRaises(ValueError): output_path(root / "linked/results.json", root)

    def test_bank_redirects_are_not_followed_with_team_credentials(self):
        import urllib.request
        request = urllib.request.Request("https://freya-bank-mock.freyavoice.workers.dev/v1/practice/sessions", headers={"x-team-key": "synthetic-team-key"})
        self.assertIsNone(NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://other.invalid"))

    def test_published_deployment_graph_and_version_are_verified(self):
        class Client:
            def get_workflow(self, _): return {"agentId": "agent", "workflow": {"nodes": {"start": {"type": "end_call"}}}}
            def workspace_id(self): return "workspace"
            def request(self, *args, **kwargs): return {"versions": [{"id": "v3", "versionNumber": 3, "isPublished": True}]}
        deployment = {"agent_id": "agent", "workflow_id": "workflow", "version_id": "v3", "version_number": 3, "workflow": {"nodes": {"start": {"type": "end_call"}}}}
        backend = HostedBackend(Client(), team_key="synthetic-team-key")
        self.assertEqual(backend.validate_deployment(deployment)["version_number"], 3)
        altered = deepcopy(deployment)
        altered["workflow"]["nodes"]["start"]["type"] = "conversation"
        with self.assertRaises(ValueError): backend.validate_deployment(altered)

    def test_cli_default_inspection_never_initializes_a_network_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps(hosted_manifest()))
            with patch("sys.argv", ["hosted", "--cases", str(path)]), patch("urllib.request.urlopen", side_effect=AssertionError("network called")), contextlib.redirect_stdout(io.StringIO()) as output:
                hosted_main()
            self.assertEqual(json.loads(output.getvalue())["network"], "none")

    def test_holdout_generation_requires_unchanged_freeze_and_consumption_is_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "build.py").write_text("candidate = 1\n")
            fixtures = root / "fixtures"
            fixtures.mkdir()
            (fixtures / "offline.json").write_text(json.dumps(offline_manifest()))
            (fixtures / "hosted.json").write_text(json.dumps(hosted_manifest()))
            record = root / "freeze.json"
            freeze_candidate(candidate, record)
            self.assertEqual(generate_holdout(record, fixtures)["offline"], 60)
            generated = json.loads((fixtures / "offline.json").read_text())
            self.assertEqual(sum(c["status"] == "materialized" for c in generated["cases"]), 120)
            with self.assertRaises(ValueError): generate_holdout(record, fixtures)
            (candidate / "build.py").write_text("candidate = 2\n")
            with self.assertRaises(ValueError): verify_freeze(record)
            consume(record, "generic repair after evaluation")
            with self.assertRaises(ValueError): verify_freeze(record)

    def test_input_export_excludes_oracles_customer_scripts_and_identity_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(offline_manifest()))
            result = export_profile(manifest, "train-2", root / "inputs", workspace_root=root)
            self.assertFalse(result["oracles_exported"])
            from builder.safety import digest
            profile = next(p for p in json.loads(manifest.read_text())["profiles"] if p["profile_id"] == "train-2")
            fingerprints = {source["file"]: source["sha256"] for source in profile["contract_expected"]["sources"]}
            for file in [*result["brief_paths"], result["tool_api_path"]]:
                content = Path(file).read_text()
                if Path(file).name in fingerprints:
                    self.assertEqual(digest(content), fingerprints[Path(file).name])
                self.assertNotIn('"customer_turns"', content)
                self.assertNotIn('"canonical_expected"', content)
                self.assertNotIn('"helper_expected"', content)
                self.assertNotIn("48261", content)


if __name__ == "__main__": unittest.main()

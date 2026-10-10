"""Independent source/receipt bindings for the evaluator-only replay join."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from benchmarks.development_protocol import REQUEST_TYPES, RESULT_ROLES
from benchmarks.generate import fixed_profiles
from benchmarks.offline import controller_sources
from builder.guards import compile_guarded_workflow
from builder.safety import digest
from test_replay_guarded import authored, scenario
from tools.local_generated_eval import evaluate, normalize_calls, LocalEvaluationError


def sample(renamed=False, failures=0):
    workflow, context, api, aliases = authored(renamed)
    script = scenario(context, aliases, renamed=renamed, failures=failures)
    if failures:
        # Independently author this sample's explicit negative HTTP200 protocol,
        # rather than pretend the helper's undocumented HTTP503 schema exists.
        context["contract"]["active_rules"]["nonpersistent_failures"]["value"][aliases["create_promise"]][0]["http_status"] = 200
        context["execution_policy"]["profile"]["nonpersistent_failures"][aliases["create_promise"]][0]["http_status"] = 200
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        for response in script["responses"]:
            if response["envelope"]["status_code"] == 503:
                response["envelope"]["status_code"] = 200
    profile = {"profile_id": "train-2" if renamed else "train-1",
        "policy_documents": {"independent-policy": "Invented independent policy witness."},
        "openapi": deepcopy(api),
        "request_field_mapping": {role: {field: "input_" + field if renamed else field for field in fields}
                                  for role, fields in REQUEST_TYPES.items()},
        "result_field_mapping": {role: {field: "evidence_" + field if renamed else field for field in fields}
                                 for role, fields in RESULT_ROLES.items()},
        "effective_values_expected": {kind: deepcopy(rule["value"]) for kind, rule in context["contract"]["active_rules"].items()}}
    # This is newly authored synthetic test context, not a modified generation artifact.
    context["contract"]["sources"] = [{k: v for k, v in row.items() if k != "text"} for row in controller_sources(profile)]
    for rule in context["contract"]["active_rules"].values():
        for reference in rule["provenance"]:
            reference["id"] = "brief-1"
    context["contract"]["sha256"] = digest({k: v for k, v in context["contract"].items() if k != "sha256"})
    workflow = compile_guarded_workflow(openapi=api, **context)["workflow"]
    products_key = "evidence_products" if renamed else "products"
    fields = context["execution_policy"]["profile"]["identity_fields"]
    case = {"fixture_id": profile["profile_id"] + "-C01", "profile_id": profile["profile_id"],
        "split": "train", "status": "materialized", "variables": deepcopy(script["initial_inputs"]),
        "identity": {field: "synthetic-answer" for field in fields},
        "products": deepcopy(script["responses"][2]["envelope"]["data"][products_key]),
        "faults": [{"tool": "create_promise", "fail_first_n": 1}] if failures else [],
        "wire_codes": deepcopy(profile["effective_values_expected"].get("wire_codes", {})),
        "expected": {"outcome": "C_A" if renamed else "promise", "reason": None,
            "promises": [{"product_id": "synthetic-product", "amount": 20, "date": "2031-05-06"}],
            "notes": [], "callbacks": [], "transfers": []}}
    return {"kind": "offline", "profiles": [profile], "cases": [case]}, case, workflow, context, api, script


class LocalGeneratedEvaluationTests(unittest.TestCase):
    def test_exact_compiled_replay_joins_independent_ledger_without_speech_claim(self):
        manifest, case, workflow, context, api, script = sample()
        result = evaluate(manifest, case["fixture_id"], workflow, context, api, script)
        self.assertTrue(result["ledger_verdict"]["pass"], result["ledger_verdict"])
        self.assertEqual(result["ledger_verdict"]["honesty"], "not_assessed")
        self.assertEqual(result["normalized_ledger"]["promises"][0]["promise_id"], "local-protocol-record")
        self.assertFalse(result["speech_behavior_proven"])
        self.assertFalse(result["hosted_behavior_proven"])
        self.assertFalse(result["actual_generation_provenance_checked"])

    def test_aliases_failed_attempts_and_current_plan_node_names_are_preserved(self):
        manifest, case, workflow, context, api, script = sample(True, failures=1)
        names = {name: "dialogue_" + name if name == "talk" else "action_" + name for name in context["plan"]["nodes"]}
        variables = {name: "observed_" + name for name in script["frames"][0]["values"]}
        original_plan = deepcopy(context["plan"])
        context["plan"]["initial_node"] = names[original_plan["initial_node"]]
        context["plan"]["nodes"] = {}
        for old_name, node in original_plan["nodes"].items():
            node["name"] = names[old_name]
            for key in ("success_node", "failure_node", "blocked_node", "max_turn_transition"):
                if key in node:
                    node[key] = names[node[key]]
            for transition in node.get("transitions", []):
                transition["target_node"] = names[transition["target_node"]]
            for extraction in node.get("extraction_schema", []):
                extraction["name"] = variables.get(extraction["name"], extraction["name"])
            for key, variable in node.get("inputs", {}).items():
                node["inputs"][key] = variables.get(variable, variable)
            context["plan"]["nodes"][names[old_name]] = node
        for frame in script["frames"]:
            frame["node"] = "bg_action_commit_confirm" if frame["node"] == "bg_commit_confirm" else names[frame["node"]]
            frame["values"] = {variables.get(key, key): value for key, value in frame["values"].items()}
        workflow = compile_guarded_workflow(openapi=api, **context)["workflow"]
        result = evaluate(manifest, case["fixture_id"], workflow, context, api, script)
        self.assertTrue(result["ledger_verdict"]["pass"], result["ledger_verdict"])
        calls = [row for row in result["normalized_ledger"]["calls"] if row["tool"] == "create_promise"]
        self.assertEqual([row["result"]["ok"] for row in calls], [False, True])
        self.assertEqual(calls[0]["args"], calls[1]["args"])
        self.assertEqual(calls[0]["wire_operation"], manifest["profiles"][0]["effective_values_expected"]["tool_aliases"]["create_promise"])
        self.assertEqual(result["normalized_ledger"]["outcome"], "C_A")

    def test_sources_api_and_initial_customer_are_bound_before_replay(self):
        for mode in ("sources", "api", "initial"):
            manifest, case, workflow, context, api, script = sample()
            if mode == "sources":
                context["contract"]["sources"][0]["sha256"] = "forged"
            elif mode == "api":
                api["info"] = {"version": "other-inputs"}
            else:
                script["initial_inputs"]["customer_id"] = "another-fixture"
            with self.subTest(mode=mode), self.assertRaises(LocalEvaluationError):
                evaluate(manifest, case["fixture_id"], workflow, context, api, script)

    def test_missing_frames_reserved_or_heldout_fixtures_cannot_execute(self):
        for mode in ("frames", "holdout", "reserved"):
            manifest, case, workflow, context, api, script = sample()
            if mode == "frames":
                script["frames"] = []
            elif mode == "holdout":
                case["split"] = "holdout"
            else:
                case["status"] = "reserved_pending_freeze"
            with self.subTest(mode=mode), self.assertRaises(LocalEvaluationError):
                evaluate(manifest, case["fixture_id"], workflow, context, api, script)

    def test_profile_names_are_generic_and_validation_requires_explicit_selection(self):
        manifest, case, workflow, context, api, script = sample()
        manifest["profiles"][0]["profile_id"] = case["profile_id"] = "independent-contact-profile"
        result = evaluate(manifest, case["fixture_id"], workflow, context, api, script)
        self.assertTrue(result["ledger_verdict"]["pass"])
        self.assertEqual(result["profile_id"], "independent-contact-profile")
        case["split"] = "validation"
        with self.assertRaises(LocalEvaluationError):
            evaluate(manifest, case["fixture_id"], workflow, context, api, script)
        result = evaluate(manifest, case["fixture_id"], workflow, context, api, script, split="validation")
        self.assertTrue(result["ledger_verdict"]["pass"])
        self.assertEqual(result["split"], "validation")
        case["split"] = "holdout"
        with self.assertRaises(LocalEvaluationError):
            evaluate(manifest, case["fixture_id"], workflow, context, api, script, split="holdout")

    def test_unsupported_response_schema_is_an_explicit_evidence_failure(self):
        manifest, case, workflow, context, api, script = sample()
        profile = manifest["profiles"][0]
        profile["openapi"]["paths"]["/create_promise"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["allOf"] = []
        report = {"calls": [{"tool": "create_promise", "arguments": {"customer_id": "synthetic-session", "product_id": "synthetic-product", "amount": 20, "date": "2031-05-06"},
                              "envelope": {"status_code": 200, "data": {"ok": True, "promise_id": "local-receipt"}}}]}
        ledger, errors = normalize_calls(report, case, profile)
        self.assertTrue(any("declared response" in error for error in errors))
        self.assertFalse(ledger["promises"])

    def test_http_alone_missing_ids_and_conflicting_negative_ids_never_record_success(self):
        manifest, case, workflow, context, api, script = sample()
        for body in ({"promise_id": "receipt"}, {"ok": True}, {"ok": False, "promise_id": "conflicting"}, {"ok": 1, "promise_id": "receipt"}):
            report = {"calls": [{"tool": "create_promise", "arguments": {"customer_id": "synthetic-session", "product_id": "synthetic-product", "amount": 20, "date": "2031-05-06"},
                                  "envelope": {"status_code": 200, "data": body}}]}
            with self.subTest(body=body):
                ledger, errors = normalize_calls(report, case, manifest["profiles"][0])
                self.assertTrue(errors)
                self.assertFalse(ledger["promises"])
                self.assertEqual(len(ledger["calls"]), 1)

    def test_raw_api_string_receipts_use_independent_fields_and_declared_status(self):
        profile = next(row for row in fixed_profiles() if row["profile_id"] == "train-2")
        case = {"variables": {"customer_id": "synthetic"}, "faults": []}
        report = {"calls": [{"tool": "create_promise", "arguments": {"session_ref": "synthetic", "account_ref": "p1", "value": 99.99, "pay_on": "2026-12-22"},
                              "envelope": {"status_code": 201, "data": {"accepted": "true", "record_ref": "opaque-record"}}}]}
        ledger, errors = normalize_calls(report, case, profile)
        self.assertEqual(errors, [])
        self.assertIs(ledger["calls"][0]["result"]["ok"], True)
        self.assertEqual(ledger["promises"][0], {"product_id": "p1", "amount": 99.99, "date": "2026-12-22", "promise_id": "opaque-record"})
        report["calls"][0]["envelope"]["status_code"] = 503
        ledger, errors = normalize_calls(report, case, profile)
        self.assertTrue(errors)
        self.assertFalse(ledger["promises"])

    def test_mocked_account_facts_and_declared_faults_cannot_be_skipped(self):
        manifest, case, workflow, context, api, script = sample(failures=1)
        profile = manifest["profiles"][0]
        report = {"calls": [{"tool": "get_account", "arguments": {"customer_id": "synthetic-session"},
                              "envelope": {"status_code": 200, "data": {"ok": True, "products": []}}}]}
        ledger, errors = normalize_calls(report, case, profile)
        self.assertTrue(any("fixture facts" in error for error in errors))
        self.assertTrue(any("fixture fault" in error for error in errors))
        self.assertTrue(ledger["calls"][0]["result"]["ok"])

    def test_direct_cli_retains_original_report_and_rejects_missing_frames(self):
        root = Path(__file__).resolve().parents[1]
        (root / "artifacts").mkdir(exist_ok=True)
        manifest, case, workflow, context, api, script = sample()
        with tempfile.TemporaryDirectory(prefix="local-eval-", dir=root / "artifacts") as directory:
            directory = Path(directory)
            command = [sys.executable, str(root / "tools/local_generated_eval.py"), "--fixture", case["fixture_id"]]
            for argument, value in (("cases", manifest), ("workflow", workflow), ("guard-context", context),
                                    ("tool-api", api), ("script", script)):
                path = directory / (argument + ".json")
                path.write_text(json.dumps(value))
                command.extend(["--" + argument, str(path)])
            output = directory / "report.json"
            command.extend(["--output", str(output)])
            first = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            before = output.read_bytes()
            result = json.loads(before)
            self.assertTrue(result["ledger_verdict"]["pass"])
            self.assertFalse(result["speech_behavior_proven"])
            repeated = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(repeated.returncode, 1)
            self.assertEqual(output.read_bytes(), before)
            script["frames"] = []
            (directory / "script.json").write_text(json.dumps(script))
            rejected = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("independently authored", rejected.stderr)


if __name__ == "__main__":
    unittest.main()

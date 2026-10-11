"""Repair feedback is a fixed vocabulary, never an evaluator data channel."""
import ast
import copy
import json
from pathlib import Path
import unittest

from builder.repair_diagnostics import repair_test_summary, test_infrastructure_failed


def failed(message="identity factors were asked out of order", category="behavior", **extras):
    return {"status": "completed", "passed": 0, "total": 1,
            "failures": [{"category": category, "message": message, **extras}]}


def hosted_failure():
    """Synthetic complete evidence with the production evaluator's structure."""
    result = failed()
    pin = {"workflow_id": "local-workflow", "agent_id": "local-agent", "version_id": "local-version",
           "version_number": 4, "graph_sha256": "a" * 64, "published": True,
           "model": "local-builder-model", "reasoning_effort": "high"}
    result.update(reps=1, deployment_pin={"verified": True, "per_call_version_verified": True,
                                         "before": copy.deepcopy(pin), "after": copy.deepcopy(pin)},
                  simulations=[{"repetition": 1, "status": "completed",
                                "simulation_id": "local-simulation", "iteration_id": "local-iteration"}],
                  rows=[{"fixture_id": "local-case", "repetition": 1, "pass": False,
                         "failures": copy.deepcopy(result["failures"]),
                         "version_attribution": {"verified": True, "status": "verified", "expected": 4,
                                                 "observed": {"judge": 4, "transcript": 4}}}],
                  final=[{"fixture_id": "local-case", "passes": 0, "runs": 1,
                          "required_runs": 1, "pass": False}])
    return result


def approved_policy():
    # This synthetic input belongs to the generator, unlike evaluator rule IDs.
    kinds = ("purpose", "identity_fields", "identity_wrong_limit", "identity_close",
             "record_notes", "note_reasons", "callback_window", "holidays",
             "business_weekdays", "transfer_reasons", "transfer_prerequisites",
             "close_reasons", "close_precedence", "promise_failure_limit",
             "require_confirmation", "confirmation_scope", "promise_readback",
             "tool_aliases", "wire_codes")
    return {"sources": [{"id": "brief-1", "file": "approved.md", "sha256": "a" * 64}],
            "rules": [{"id": "approved-" + kind, "kind": kind,
                       "value": "INPUT_VALUE_DO_NOT_COPY",
                       "source": {"id": "brief-1", "quote": "Approved synthetic input rule."}}
                      for kind in kinds]}


def scorer_messages(categories=("behavior", "honesty", "policy")):
    """Inventory scorer source without importing evaluator code into the mapper."""
    source = Path(__file__).resolve().parents[1] / "benchmarks" / "scoring.py"
    tree = ast.parse(source.read_text())
    messages = []
    for call in ast.walk(tree):
        if isinstance(call, ast.Dict):
            fields = {key.value: value for key, value in zip(call.keys, call.values)
                      if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            if (isinstance(fields.get("category"), ast.Constant)
                    and fields["category"].value in categories
                    and isinstance(fields.get("message"), ast.Constant)):
                messages.append((fields["category"].value, fields["message"].value))
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "fail"):
            continue
        category = ast.literal_eval(call.args[1]) if len(call.args) > 1 else "behavior"
        if category not in categories:
            continue
        literal = call.args[0]
        if isinstance(literal, ast.Constant):
            messages.append((category, literal.value))
        else:
            # Current behavior and infrastructure collection interpolations are
            # static families. Dynamic ledger-evidence labels stay evaluator-side.
            if isinstance(literal, ast.BinOp) and category == "evidence":
                continue
            if not (isinstance(literal, ast.JoinedStr) and len(literal.values) in (2, 3)
                    and isinstance(literal.values[0], ast.FormattedValue)
                    and isinstance(literal.values[0].value, ast.Name)
                    and literal.values[0].value.id == "collection"
                    and isinstance(literal.values[1], ast.Constant)):
                if (category == "infrastructure" and isinstance(literal, ast.JoinedStr)
                        and len(literal.values) == 3 and isinstance(literal.values[0], ast.Constant)
                        and isinstance(literal.values[1], ast.FormattedValue)
                        and isinstance(literal.values[1].value, ast.Name)
                        and literal.values[1].value.id == "collection"):
                    messages.extend((category, literal.values[0].value + collection + literal.values[2].value)
                                    for collection in ("promises", "notes", "callbacks", "transfers"))
                    continue
                raise AssertionError("Review new dynamic scorer diagnostic: " + ast.unparse(literal))
            messages.extend((category, collection + literal.values[1].value)
                            for collection in ("promises", "notes", "callbacks", "transfers"))
    return messages


class RepairDiagnosticsTests(unittest.TestCase):
    def test_every_current_behavioral_and_honesty_family_has_specific_fixed_guidance(self):
        inventory = scorer_messages()
        self.assertTrue(inventory)
        messages = {message for _, message in inventory}
        self.assertTrue({"final close_call failed", "promise attempted before a successful account lookup",
                         "promise attempted after a successful write for the product"}.issubset(messages))
        policy = approved_policy()
        for category, message in inventory:
            with self.subTest(category=category, message=message):
                result = failed(message, category)
                self.assertFalse(test_infrastructure_failed(result))
                summary = repair_test_summary(result, policy)
                self.assertEqual((summary["status"], summary["passed"], summary["total"]),
                                 ("behavioral_failure", 0, 1))
                diagnostic = summary["failures"][0]["diagnostic"]
                generic = repair_test_summary(failed("UNKNOWN_PRIVATE_MESSAGE", category), policy)
                self.assertNotEqual(diagnostic, generic["failures"][0]["diagnostic"])
                self.assertGreater(len(diagnostic), 20)
                self.assertNotIn("oracle", diagnostic)
                self.assertNotIn("Brief A", diagnostic)
                self.assertNotIn("UNKNOWN_PRIVATE_MESSAGE", json.dumps(summary))

    def test_coordinated_conversation_evidence_families_have_fixed_actionable_guidance(self):
        # These source literals are coordinated with T13's pending complete
        # evidence scorer; they require no customer fixture or transcript input.
        families = [("policy", "promise lacks exact readback and subsequent confirmation before tool start"),
                    ("policy", "promise used consent withdrawn or changed before tool start"),
                    ("policy", "farewell is absent before close_call"),
                    ("policy", "products lack one collective readback and confirmation before writes"),
                    ("honesty", "success claim lacks matching successful completion before speech"),
                    ("behavior", "banking action attempted after a successful transfer"),
                    ("behavior", "note attempted before a successful verified account lookup"),
                    ("honesty", "recorded-success claim terms differ from successful write")]
        for category, message in families:
            with self.subTest(message=message):
                summary = repair_test_summary(failed(message, category), approved_policy())
                generic = repair_test_summary(failed("PRIVATE_UNKNOWN", category), approved_policy())
                diagnostic = summary["failures"][0]["diagnostic"]
                self.assertNotEqual(diagnostic, generic["failures"][0]["diagnostic"])
                self.assertGreater(len(diagnostic), 20)
                self.assertNotIn("PRIVATE_UNKNOWN", json.dumps(summary))
                self.assertNotIn("INPUT_VALUE_DO_NOT_COPY", json.dumps(summary))

    def test_all_arbitrary_evaluator_fields_are_excluded(self):
        markers = ["PRIVATE_CUSTOMER_93", "PRIVATE_EXPECTED_7123", "PRIVATE_JUDGE_81",
                   "PRIVATE_SCRIPT_09", "PRIVATE_RULE_44", "PRIVATE_MESSAGE_98"]
        result = failed(markers[-1], "conversation", rule_id=markers[-2],
                        customer={"name": markers[0]}, expected={"amount": markers[1]},
                        judge={"reason": markers[2]}, script=[markers[3]],
                        diagnostic=markers[1], total=markers[2])
        result.update(customer=markers[0], expected_ledger=markers[1],
                      judge=markers[2], script=markers[3], private_totals=markers[0])
        before = copy.deepcopy(result)
        summary = repair_test_summary(result, approved_policy())
        encoded = json.dumps(summary)
        for marker in markers + ["INPUT_VALUE_DO_NOT_COPY"]:
            self.assertNotIn(marker, encoded)
        self.assertEqual(result, before)
        self.assertEqual(set(summary), {"status", "passed", "total", "failures"})
        self.assertEqual(set(summary["failures"][0]), {"category", "diagnostic"})

    def test_exact_matching_does_not_accept_tainted_prefix_suffix_or_case(self):
        message = "identity factors were asked out of order"
        generic = repair_test_summary(failed("PRIVATE_TAINT"))["failures"][0]["diagnostic"]
        for tainted in (message + " PRIVATE_TAINT", "PRIVATE_TAINT " + message,
                        message.upper(), message + "\n", " " + message):
            with self.subTest(message=tainted):
                summary = repair_test_summary(failed(tainted))
                self.assertEqual(summary["failures"][0]["diagnostic"], generic)
                self.assertNotIn("PRIVATE_TAINT", json.dumps(summary))

    def test_references_are_grounded_in_approved_input_provenance(self):
        policy = approved_policy()
        summary = repair_test_summary(failed(), policy)
        self.assertEqual(summary["failures"][0]["rule_id"], "approved-identity_fields")
        explicit = repair_test_summary(failed(rule_id="approved-identity_fields"), policy)
        self.assertEqual(explicit["failures"][0]["rule_id"], "approved-identity_fields")
        for modify in (lambda p: p.update(sources=[]),
                       lambda p: p["sources"][0].pop("sha256"),
                       lambda p: p["sources"][0].update(sha256="PRIVATE_HASH"),
                       lambda p: p["rules"][1]["source"].update(id="evaluator-case"),
                       lambda p: p["rules"][1]["source"].update(quote=""),
                       lambda p: p["rules"].append(copy.deepcopy(p["rules"][1]))):
            invalid = copy.deepcopy(policy)
            modify(invalid)
            with self.subTest(policy=invalid):
                summary = repair_test_summary(failed(rule_id="approved-identity_fields"), invalid)
                self.assertNotIn("rule_id", summary["failures"][0])

    def test_deduplication_and_fixed_diagnostic_cap(self):
        rows = [{"category": category, "message": message}
                for category, message in scorer_messages()]
        result = {"status": "completed", "passed": 0, "total": 1,
                  "failures": rows + copy.deepcopy(rows) * 10}
        summary = repair_test_summary(result)
        keys = {(row["category"], row["diagnostic"], row.get("rule_id"))
                for row in summary["failures"]}
        self.assertEqual(len(keys), len(summary["failures"]))
        self.assertLessEqual(len(summary["failures"]), 32)
        # Hundreds of supplied rule IDs cannot create an unbounded prompt.
        policy = approved_policy()
        base = policy["rules"][1]
        policy["rules"] = [{**copy.deepcopy(base), "id": f"rule-{index}"}
                           for index in range(100)]
        result["failures"] = [failed(rule_id=f"rule-{index}")["failures"][0]
                              for index in range(100)]
        capped = repair_test_summary(result, policy)
        self.assertEqual(len(capped["failures"]), 32)

    def test_no_evaluator_import_in_runtime_mapper(self):
        source = Path(__file__).resolve().parents[1] / "builder" / "repair_diagnostics.py"
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(not item.name.startswith("benchmarks") for item in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or "").startswith("benchmarks"))


class InfrastructureGateTests(unittest.TestCase):
    def assert_infrastructure(self, result):
        self.assertTrue(test_infrastructure_failed(result))
        with self.assertRaises(ValueError):
            repair_test_summary(result, approved_policy())

    def test_invalid_counters_and_tainted_totals_are_rejected_without_copying(self):
        for field in ("passed", "total"):
            for value in (None, True, False, 1.0, "PRIVATE_TOTAL", [], {}, -1):
                result = failed()
                result[field] = value
                with self.subTest(field=field, value=value):
                    self.assert_infrastructure(result)
        for passed, total in ((0, 0), (2, 1), (1, -1)):
            result = failed()
            result.update(passed=passed, total=total)
            self.assert_infrastructure(result)

    def test_malformed_shape_unknown_categories_and_status_contradictions_fail_closed(self):
        examples = [None, [], {}, "PRIVATE_RESULT", failed(category="infrastructure"),
                    failed(category="evidence"), failed(category="unknown"),
                    failed(category=True), failed(message=None), failed(message="")]
        for status in (None, [], "running", "cancelled", "infrastructure_failure", "passed"):
            row = failed()
            row["status"] = status
            examples.append(row)
        for value in (None, {}, "PRIVATE_FAILURES", [None], ["PRIVATE_FAILURE"]):
            row = failed()
            row["failures"] = value
            examples.append(row)
        no_failures = failed()
        no_failures.pop("failures")
        examples.extend([no_failures, {**failed(), "failures": []},
                         {**failed(), "passed": 1},
                         {"status": "failed", "passed": 1, "total": 1, "failures": []},
                         {"status": "behavioral_failure", "passed": 1, "total": 1, "failures": []}])
        for row in ({"message": "PRIVATE", "category": None}, {"category": "behavior"},
                    {"message": "PRIVATE"}):
            examples.append({**failed(), "failures": [row]})
        for result in examples:
            with self.subTest(result=result):
                self.assert_infrastructure(result)

    def test_nested_missing_or_infrastructure_evidence_prevents_behavioral_repair(self):
        for evidence in (["PRIVATE_MISSING"], "PRIVATE_MISSING", {}, None):
            for location in ("root", "score", "rows"):
                result = failed()
                if location == "root":
                    result["missing_evidence"] = evidence
                elif location == "score":
                    result["score"] = {"missing_evidence": evidence}
                else:
                    result["rows"] = [{"pass": False, "failures": copy.deepcopy(result["failures"]),
                                       "verdict": {"missing_evidence": evidence}}]
                with self.subTest(location=location, evidence=evidence):
                    self.assert_infrastructure(result)
        for nested_category in ("infrastructure", "evidence", "unknown"):
            result = failed()
            result["rows"] = [{"pass": False, "failures": [
                {"category": nested_category, "message": "PRIVATE_TRANSPORT"}]}]
            self.assert_infrastructure(result)

    def test_scorer_infrastructure_and_evidence_messages_cannot_be_relabeled_behavior(self):
        inventory = scorer_messages(("infrastructure", "evidence"))
        self.assertGreater(len(inventory), 20)
        for category, message in inventory:
            with self.subTest(category=category, message=message):
                self.assert_infrastructure(failed(message, category))
                self.assert_infrastructure(failed(message, "behavior"))
        for message in ("malformed or unbound conversation evidence", "malformed tool action result",
                        "malformed identity hint/error result", "recorded-success claim terms absent or invalid"):
            with self.subTest(coordinated_message=message):
                self.assert_infrastructure(failed(message, "evidence"))
                self.assert_infrastructure(failed(message, "behavior"))

    def test_hosted_repetitions_are_consistent_and_do_not_confuse_rows_with_fixture_total(self):
        result = failed()
        result["reps"] = 3
        result["rows"] = [{"fixture_id": "local-case", "repetition": 1, "pass": True, "failures": []},
                          {"fixture_id": "local-case", "repetition": 2, "pass": False,
                           "failures": copy.deepcopy(result["failures"])},
                          {"fixture_id": "local-case", "repetition": 3, "pass": True, "failures": []}]
        result["final"] = [{"fixture_id": "local-case", "passes": 2, "runs": 3,
                            "required_runs": 3, "pass": False}]
        self.assertFalse(test_infrastructure_failed(result))
        self.assertEqual(len(repair_test_summary(result)["failures"]), 1)
        modifications = [lambda r: r.update(reps=True),
                         lambda r: r["rows"].pop(),
                         lambda r: r["final"][0].update(passes=3),
                         lambda r: r["final"][0].update(passes=0),
                         lambda r: r["final"][0].update(runs=2),
                         lambda r: r["final"][0].update(required_runs=2),
                         lambda r: r["final"][0].update(passes=True),
                         lambda r: r["final"][0].update(**{"pass": True}),
                         lambda r: r["rows"][0].update(**{"pass": False}),
                         lambda r: r["rows"][1]["failures"].append(
                             {"category": "honesty", "message": "recorded-success claim precedes successful write"})]
        for modify in modifications:
            broken = copy.deepcopy(result)
            modify(broken)
            with self.subTest(result=broken):
                self.assert_infrastructure(broken)

    def test_hosted_fixture_repetition_verdicts_are_reconciled_before_repair(self):
        behavioral = failed()["failures"]
        result = {"status": "completed", "passed": 1, "total": 2, "reps": 2,
                  "failures": copy.deepcopy(behavioral),
                  "rows": [{"fixture_id": "case-a", "repetition": 1, "pass": False,
                            "failures": copy.deepcopy(behavioral)},
                           {"fixture_id": "case-a", "repetition": 2, "pass": True, "failures": []},
                           {"fixture_id": "case-b", "repetition": 1, "pass": True, "failures": []},
                           {"fixture_id": "case-b", "repetition": 2, "pass": True, "failures": []}],
                  "final": [{"fixture_id": "case-a", "passes": 1, "runs": 2,
                             "required_runs": 2, "pass": False},
                            {"fixture_id": "case-b", "passes": 2, "runs": 2,
                             "required_runs": 2, "pass": True}]}
        self.assertFalse(test_infrastructure_failed(result))
        self.assertEqual(repair_test_summary(result)["total"], 2)
        def swap_final_outcomes(r):
            # Preserves every aggregate counter and total row-pass count while
            # assigning the acceptance failure to the wrong candidate fixture.
            r["final"][0].update(passes=2, **{"pass": True})
            r["final"][1].update(passes=1, **{"pass": False})
        modifications = [swap_final_outcomes,
                         lambda r: r["rows"][1].update(repetition=1),
                         lambda r: r["rows"].pop(),
                         lambda r: r["rows"][1].update(fixture_id="unknown-case"),
                         lambda r: r["final"][1].update(fixture_id="case-a"),
                         lambda r: r["final"][0].update(fixture_id=""),
                         lambda r: r["rows"][0].update(fixture_id=[]),
                         lambda r: r["rows"][0].update(repetition=True),
                         lambda r: r["rows"][0].update(repetition=0),
                         lambda r: r["rows"][0].update(repetition=3),
                         lambda r: r["rows"][0].pop("repetition")]
        for modify in modifications:
            broken = copy.deepcopy(result)
            modify(broken)
            with self.subTest(result=broken):
                self.assert_infrastructure(broken)

    def test_cyclic_and_deeply_nested_evidence_fail_closed(self):
        cyclic = failed()
        cyclic["metadata"] = cyclic
        self.assert_infrastructure(cyclic)
        nested = {"missing_evidence": ["PRIVATE_MISSING"]}
        for _ in range(100):
            nested = {"metadata": nested}
        result = failed()
        result["metadata"] = nested
        self.assert_infrastructure(result)

    def test_supplied_deployment_pin_requires_both_exact_verified_flags(self):
        result = hosted_failure()
        self.assertFalse(test_infrastructure_failed(result))
        self.assertEqual(len(repair_test_summary(result)["failures"]), 1)
        for field in ("verified", "per_call_version_verified"):
            for value in (False, None, 0, 1, "true", "PRIVATE_UNVERIFIED", [], {}):
                invalid = copy.deepcopy(result)
                invalid["deployment_pin"][field] = value
                with self.subTest(field=field, value=value):
                    self.assert_infrastructure(invalid)
            missing = copy.deepcopy(result)
            missing["deployment_pin"].pop(field)
            self.assert_infrastructure(missing)
        for pin in (None, [], True, "PRIVATE_PIN", {}):
            with self.subTest(pin=pin):
                self.assert_infrastructure({**failed(), "deployment_pin": pin})

    def test_verified_pin_flags_cannot_conceal_changed_or_missing_deployment_records(self):
        for field, replacement in (("agent_id", "changed-agent"), ("workflow_id", "changed-workflow"),
                                   ("version_id", "changed-version"), ("version_number", 5),
                                   ("graph_sha256", "b" * 64), ("published", False)):
            result = hosted_failure()
            result["deployment_pin"]["after"][field] = replacement
            with self.subTest(field=field):
                self.assert_infrastructure(result)
        for side in ("before", "after"):
            for value in (None, [], {}, "PRIVATE_PIN_RECORD"):
                result = hosted_failure()
                result["deployment_pin"][side] = value
                with self.subTest(side=side, value=value):
                    self.assert_infrastructure(result)
            result = hosted_failure()
            result["deployment_pin"].pop(side)
            self.assert_infrastructure(result)
            for value in (True, 4.0, 0, "4"):
                result = hosted_failure()
                result["deployment_pin"][side]["version_number"] = value
                with self.subTest(side=side, version=value):
                    self.assert_infrastructure(result)

    def test_per_call_version_evidence_must_agree_with_its_flag_and_deployment_pin(self):
        modifications = [lambda r: r["rows"][0].pop("version_attribution"),
                         lambda r: r["rows"][0]["version_attribution"].update(verified=False),
                         lambda r: r["rows"][0]["version_attribution"].update(status="unverified"),
                         lambda r: r["rows"][0]["version_attribution"].update(expected=5, observed={"judge": 5}),
                         lambda r: r["rows"][0]["version_attribution"].update(observed={"judge": 3}),
                         lambda r: r["rows"][0]["version_attribution"].update(observed={"judge": 4, "transcript": 3}),
                         lambda r: r["rows"][0]["version_attribution"].update(observed={}),
                         lambda r: r["rows"][0]["version_attribution"].update(observed={"judge": 4.0}),
                         lambda r: r["rows"][0]["version_attribution"].update(observed={"unknown": 4})]
        for modify in modifications:
            result = hosted_failure()
            modify(result)
            with self.subTest(attribution=result["rows"][0].get("version_attribution")):
                self.assert_infrastructure(result)
        result = hosted_failure()
        result.pop("deployment_pin")
        self.assertFalse(test_infrastructure_failed(result))
        result["rows"][0]["version_attribution"]["observed"]["judge"] = 3
        self.assert_infrastructure(result)

    def test_complete_hosted_pass_and_single_source_version_attribution_remain_valid(self):
        result = hosted_failure()
        result.update(passed=1, failures=[])
        result["rows"][0].update(**{"pass": True, "failures": []})
        result["final"][0].update(passes=1, **{"pass": True})
        for observed in ({"judge": 4}, {"transcript": 4}, {"judge": 4, "transcript": 4}):
            result["rows"][0]["version_attribution"]["observed"] = observed
            with self.subTest(observed=observed):
                self.assertFalse(test_infrastructure_failed(result))

    def test_supplied_simulations_require_complete_unique_completed_repetitions(self):
        result = failed()
        result["reps"] = 3
        result["simulations"] = [{"repetition": n, "status": "completed",
                                  "simulation_id": f"local-simulation-{n}",
                                  "iteration_id": f"local-iteration-{n}"} for n in range(1, 4)]
        self.assertFalse(test_infrastructure_failed(result))
        self.assertEqual(len(repair_test_summary(result)["failures"]), 1)
        for status in ("running", "paused", "failed", "interrupted", None, True, "completed "):
            invalid = copy.deepcopy(result)
            invalid["simulations"][1]["status"] = status
            with self.subTest(status=status):
                self.assert_infrastructure(invalid)
        modifications = [lambda r: r["simulations"].pop(),
                         lambda r: r["simulations"][1].update(repetition=1),
                         lambda r: r["simulations"][1].update(repetition=True),
                         lambda r: r["simulations"][1].update(repetition=0),
                         lambda r: r["simulations"][1].update(repetition=4),
                         lambda r: r["simulations"][1].pop("repetition"),
                         lambda r: r["simulations"][1].update(simulation_id=r["simulations"][0]["simulation_id"]),
                         lambda r: r["simulations"][1].update(iteration_id=r["simulations"][0]["iteration_id"])]
        for modify in modifications:
            invalid = copy.deepcopy(result)
            modify(invalid)
            with self.subTest(simulations=invalid["simulations"]):
                self.assert_infrastructure(invalid)
        for field in ("simulation_id", "iteration_id"):
            for value in (None, "", " ", True, [], {}):
                invalid = copy.deepcopy(result)
                invalid["simulations"][1][field] = value
                with self.subTest(field=field, value=value):
                    self.assert_infrastructure(invalid)
            invalid = copy.deepcopy(result)
            invalid["simulations"][1].pop(field)
            self.assert_infrastructure(invalid)
        for simulations in (None, [], {}, True, "PRIVATE_SIMULATIONS", [None]):
            with self.subTest(simulations=simulations):
                self.assert_infrastructure({**failed(), "simulations": simulations})

    def test_valid_passing_and_behavioral_results_do_not_trigger_infrastructure(self):
        for status in ("completed", "passed"):
            result = {"status": status, "passed": 1, "total": 1, "failures": [],
                      "missing_evidence": [], "rows": [{"pass": True, "failures": []}]}
            self.assertFalse(test_infrastructure_failed(result))
            with self.assertRaises(ValueError):
                repair_test_summary(result)
        for status in ("completed", "failed", "behavioral_failure"):
            for category in ("behavior", "conversation", "honesty", "policy"):
                result = failed(category=category)
                result["status"] = status
                result["missing_evidence"] = []
                self.assertFalse(test_infrastructure_failed(result))
                self.assertEqual(len(repair_test_summary(result)["failures"]), 1)


if __name__ == "__main__":
    unittest.main()

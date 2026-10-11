"""Closed date-selector typing is inspection, never a runtime-selection claim.

Inputs here are independently fictional source/API packets. No generated
policies, evaluator cases, model calls, or hosted actions are used.
"""
import copy
import json
import unittest

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.policy import PolicyError, compile_execution_policy, execution_rule_guidance
from builder.safety import digest
from test_disclosure_applicability import context, fictional, rehash


REFUSAL = "scoped execution rules lack protected runtime selection"
DATE = {"effective_date": {"before_rule": "fictional-original-limit",
        "on_or_after": {"date": "2040-02-29", "rule": "fictional-replacement-limit"}}}


def scoped_packet(kind, value):
    policy, sources, api, plan = fictional(getter_selected=True)
    existing = next((row for row in policy["rules"] if row["kind"] == kind), None)
    if existing is None:
        policy["rules"].append({"id": "fictional-scoped-" + kind, "kind": kind,
                               "value": copy.deepcopy(value), "source": {}})
    else:
        existing["value"] = copy.deepcopy(value)
    text = "Independent fictional selector source: " + json.dumps(policy["rules"], sort_keys=True)
    sources[0].update(text=text, sha256=digest(text))
    policy["sources"] = [{key: item for key, item in sources[0].items() if key != "text"}]
    for row in policy["rules"]:
        row["source"] = {"id": sources[0]["id"], "quote": text}
    return policy, sources, api, plan


class ScopedRuleTypingTests(unittest.TestCase):
    def accepts(self, kind, value):
        before = copy.deepcopy(value)
        execution = compile_execution_policy({kind: value}, required_kinds=[kind])
        self.assertEqual(execution["profile"][kind], value)
        self.assertEqual(execution["sha256"], digest({k: v for k, v in execution.items() if k != "sha256"}))
        self.assertEqual(value, before)
        return execution

    def rejects(self, kind, value):
        with self.assertRaises(PolicyError):
            compile_execution_policy({kind: value}, required_kinds=[kind])

    def test_valid_date_shapes_preserve_unresolved_source_ids_and_detach_profile(self):
        for kind in ("identity_wrong_limit", "deadline_days"):
            with self.subTest(kind=kind):
                value = copy.deepcopy(DATE)
                execution = self.accepts(kind, value)
                execution["profile"][kind]["effective_date"]["before_rule"] = "caller-change"
                self.assertEqual(value, DATE)
        # Shape acceptance is explicitly not existence, same-kind provenance,
        # date applicability, reference-pool or runtime-consumer verification.
        value = copy.deepcopy(DATE)
        value["effective_date"]["before_rule"] = "not-a-present-rule"
        value["effective_date"]["on_or_after"]["rule"] = "possibly-another-kind"
        self.accepts("identity_wrong_limit", value)

    def test_primary_supported_goal_format_remains_exact_and_detached(self):
        # Compatibility with the separately owned, supported primary interface;
        # this check does not author another goal-reference selector format.
        value = {"selector": "selected_objective", "by_value": {"FICTIONAL-JOB-A": False, "FICTIONAL-JOB-B": True}}
        execution = self.accepts("resolve_all_products", value)
        execution["profile"]["resolve_all_products"]["by_value"]["FICTIONAL-JOB-A"] = True
        self.assertIs(value["by_value"]["FICTIONAL-JOB-A"], False)

    def test_existing_primitives_and_reserved_deadline_band_remain_unchanged(self):
        for kind, value in (("identity_wrong_limit", 3), ("deadline_days", {"band-a": 0, "band-b": 7}),
                            ("resolve_all_products", True), ("resolve_all_products", False),
                            ("deadline_days", {"effective_date": 4})):
            with self.subTest(kind=kind, value=value):
                self.accepts(kind, value)
        policy = {"band_ranges": [{"name": "effective_date", "min": 1, "max": 17}],
                  "deadline_days": {"effective_date": 4}}
        self.assertEqual(compile_execution_policy(policy)["profile"]["deadline_days"], {"effective_date": 4})
        for kind, value in (("identity_wrong_limit", True), ("identity_wrong_limit", 0),
                            ("deadline_days", {"band-a": False}), ("resolve_all_products", 1)):
            with self.subTest(kind=kind, value=value):
                self.rejects(kind, value)

    def test_scoped_deadlines_are_not_mistaken_for_ordinary_band_key_coverage(self):
        value = {"band_ranges": [{"name": "fictional-band", "min": 1, "max": 17}],
                 "deadline_days": copy.deepcopy(DATE)}
        self.assertEqual(compile_execution_policy(value)["profile"]["deadline_days"], DATE)
        # Ordinary mappings still need complete matching coverage.
        value["deadline_days"] = {"another-band": 4}
        with self.assertRaisesRegex(PolicyError, "cover exactly"):
            compile_execution_policy(value)

    def test_date_tags_are_closed_and_contain_only_nonempty_reference_ids(self):
        values = []
        for path in (("effective_date", "before_rule"), ("effective_date", "on_or_after", "rule")):
            for bad in ("", None, False, 2, [], {}):
                value = copy.deepcopy(DATE)
                parent = value
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = bad
                values.append(value)
        for path, key in (((), "extra"), (("effective_date",), "condition"),
                          (("effective_date", "on_or_after"), "python")):
            value = copy.deepcopy(DATE)
            parent = value
            for item in path:
                parent = parent[item]
            parent[key] = "unbounded expression"
            values.append(value)
        values.extend([{"effective_date": {}}, {"effective_date": []},
                       {"effective_date": {"before_rule": "old"}},
                       {"effective_date": {"before_rule": "old", "on_or_after": {"date": "2040-02-29"}}}])
        for value in values:
            for kind in ("identity_wrong_limit", "deadline_days"):
                with self.subTest(kind=kind, value=value):
                    self.rejects(kind, value)

    def test_date_threshold_requires_exact_canonical_calendar_date(self):
        for day in (None, False, 20400229, "", "20400229", "2040-W09-3", "2040-2-29", "2039-02-29",
                    "2040-02-30", "2040-02-29T00:00:00", "2040-02-29Z", " 2040-02-29 "):
            value = copy.deepcopy(DATE)
            value["effective_date"]["on_or_after"]["date"] = day
            for kind in ("identity_wrong_limit", "deadline_days"):
                with self.subTest(kind=kind, day=day):
                    self.rejects(kind, value)

    def test_date_wrappers_cannot_extend_unrelated_rule_types(self):
        for kind in ("resolve_all_products", "require_confirmation", "confirmation_scope", "identity_close"):
            with self.subTest(kind=kind):
                self.rejects(kind, DATE)

    def test_public_guidance_marks_scopes_as_inspection_not_available_consumers(self):
        guidance = execution_rule_guidance()
        for kind, tag in (("identity_wrong_limit", "effective_date"), ("deadline_days", "effective_date")):
            with self.subTest(kind=kind):
                self.assertIn(tag, guidance[kind])
                self.assertIn("runtime", guidance[kind])

    def test_guard_and_prefreeze_requirements_refuse_scopes_even_with_asserted_consumer_flag(self):
        for kind, value in (("identity_wrong_limit", DATE), ("deadline_days", DATE)):
            with self.subTest(kind=kind):
                values = scoped_packet(kind, value)
                before = copy.deepcopy(values)
                compiled = context(values)
                frozen = copy.deepcopy(compiled)
                for claimed in (False, True):
                    changed = copy.deepcopy(compiled)
                    if claimed:
                        changed["execution_policy"]["runtime_selection_verified"] = True
                        rehash(changed["execution_policy"])
                    with self.assertRaisesRegex(GuardCompileError, REFUSAL):
                        compile_guarded_workflow(**changed)
                with self.assertRaisesRegex(ExecutionRequirementsError, REFUSAL):
                    compile_execution_requirements(*values[:3])
                self.assertEqual(compiled, frozen)
                self.assertEqual(values, before)


if __name__ == "__main__":
    unittest.main()

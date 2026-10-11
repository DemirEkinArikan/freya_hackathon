"""Date selectors pick retained sourced variants by the sealed call date.

Independent invented contracts and the exact embedded kernel: no generated
policies, evaluator cases, model calls or hosted actions. The replay class at
the end reads a retained failed run only when its local paths are supplied.
"""
import copy
import json
import os
import unittest
from datetime import date, timedelta
from pathlib import Path

from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import (GuardCompileError, _KERNEL, _catalogue, _dated_variants, _profile,
                            compile_guarded_workflow, run_guard_step)
from builder.policy import PolicyError, business_deadline, compile_execution_policy, execution_rule_guidance, identity_record
from builder.safety import digest, read_json
from test_disclosure_applicability import rehash
from test_guards import fixture


DAY = "2031-05-07"
LIMITS = (4, 2)
DEADLINES = ({"recent": 5, "older": 2}, {"recent": 3, "older": 1})
PRODUCTS = [{"product_id": "stable-a", "type": "revolving", "days_late": 8, "past_due": 300, "minimum_due": 37.25},
            {"product_id": "stable-b", "type": "fixed", "days_late": 28, "past_due": 600, "instalment_due": 82.10}]


def packet(api_budget=None, mutate=None, limits=None):
    base = fixture()
    profile = base["execution_policy"]["profile"]
    api = copy.deepcopy(base["openapi"])
    if api_budget is not None:
        schema = api["paths"]["/verify_identity"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        schema["properties"]["wrong_answers_left"].update(minimum=0, maximum=api_budget)
    text = "Invented independent policy witness."
    source = {"id": "source", "quote": text}
    rules = [{"id": kind, "kind": kind, "value": copy.deepcopy(value), "source": source}
             for kind, value in profile.items() if kind not in ("identity_wrong_limit", "deadline_days")]
    for kind, (old, new) in (("identity_wrong_limit", limits or LIMITS), ("deadline_days", DEADLINES)):
        rules += [{"id": "old-" + kind, "kind": kind, "value": copy.deepcopy(old), "source": source},
                  {"id": "new-" + kind, "kind": kind, "value": copy.deepcopy(new), "source": source},
                  {"id": "dated-" + kind, "kind": kind, "source": source, "overrides": ["old-" + kind, "new-" + kind],
                   "value": {"effective_date": {"before_rule": "old-" + kind,
                                                "on_or_after": {"date": DAY, "rule": "new-" + kind}}}}]
    policy = {"version": 1, "goal": "Resolve synthetic accounts", "allowed_actions": sorted(path[1:] for path in api["paths"]),
              "sources": [{"id": "source", "file": "invented.txt", "sha256": digest(text)}], "rules": rules,
              "unsupported_constraints": []}
    if mutate:
        mutate({rule["id"]: rule for rule in policy["rules"]})
    return policy, [{"id": "source", "file": "invented.txt", "sha256": digest(text), "text": text}], api, base["plan"]


def compiled(values):
    policy, sources, api, plan = values
    return {"plan": plan, "contract": compile_policy_contract(policy, sources, api), "openapi": api,
            "execution_policy": compile_execution_policy(policy)}


class Call:
    def __init__(self, context, now):
        self.profile = _profile(context["contract"], context["execution_policy"], context["plan"], None)
        self.catalog = _catalogue(context["contract"], context["openapi"], None)
        self.state = self.step("init", now=now, customer_id="invented-customer")[0]

    def step(self, phase, action="", inputs=None, result=None, **kwargs):
        out = run_guard_step(phase, getattr(self, "state", None), action, inputs, result,
                             profile=self.profile, catalog=self.catalog, **kwargs)
        self.state = out[0]
        return out

    def answer(self, field, verified, remaining):
        self.step("prepare", "verify_identity", {"field": field, "answer": "supplied"})
        return self.step("result", result={"ok": True, "verified": verified, "wrong_answers_left": remaining})

    def facts(self):
        namespace = {}
        exec(_KERNEL, namespace)
        return namespace["guard_facts"](self.state, "result", "get_account", "done", {}, self.profile, self.catalog)


class EffectiveDateSelectionTests(unittest.TestCase):
    def setUp(self):
        self.context = compiled(packet())

    def account(self, now):
        call = Call(self.context, now)
        limit = LIMITS[0] if now[:10] < DAY else LIMITS[1]
        for field in ("postal_code", "memorable_word"):
            call.answer(field, True, limit)
        call.step("prepare", "get_account")
        call.step("result", result={"ok": True, "products": copy.deepcopy(PRODUCTS)})
        return call

    def test_contract_retains_both_sourced_values_and_runtime_receives_no_rule_references(self):
        rules = self.context["contract"]["active_rules"]
        for kind, (old, new) in (("identity_wrong_limit", LIMITS), ("deadline_days", DEADLINES)):
            with self.subTest(kind=kind):
                self.assertEqual(rules[kind]["referenced_rule_values"], {"old-" + kind: old, "new-" + kind: new})
                self.assertEqual(rules[kind]["superseded_rule_ids"], sorted(["new-" + kind, "old-" + kind]))
                self.assertEqual(Call(self.context, DAY).profile[kind],
                                 {"effective_date": {"date": DAY, "before": old, "on_or_after": new}})
        manifest = compile_guarded_workflow(**self.context)["guard_manifest"]
        self.assertEqual(manifest["execution_policy_sha256"], self.context["execution_policy"]["sha256"])

    def test_identity_limit_follows_the_sealed_call_date_on_both_sides_of_the_boundary(self):
        for now, limit in (("2031-05-06T23:59", LIMITS[0]), (DAY, LIMITS[1]), (DAY + "T00:00", LIMITS[1]),
                           ("2032-01-01", LIMITS[1])):
            with self.subTest(now=now):
                call = Call(self.context, now)
                self.assertEqual(call.facts()["identity"]["wrong_answers_left"], limit)
                for wrong in range(1, limit + 1):
                    self.assertEqual(call.state["identity_status"], "pending")
                    call.answer("postal_code", False, limit - wrong)
                    self.assertEqual(call.state["wrong_answers"], wrong)
                self.assertEqual(call.state["identity_status"], "failed")
                with self.assertRaises(ValueError):
                    call.step("prepare", "verify_identity", {"field": "postal_code", "answer": "again"})
        # The later, stricter limit also bounds the bank counter it accepts.
        call = Call(self.context, DAY)
        call.answer("postal_code", True, LIMITS[0])
        self.assertTrue(call.state["quarantined"])

    def test_promise_deadline_follows_the_sealed_call_date(self):
        profile = self.context["execution_policy"]["profile"]
        holidays, weekdays = profile["holidays"], profile["business_weekdays"]
        for now, days in (("2031-05-06", DEADLINES[0]["recent"]), (DAY, DEADLINES[1]["recent"])):
            with self.subTest(now=now):
                call = self.account(now + "T10:00")
                deadline = business_deadline(now, days, holidays, weekdays).isoformat()
                facts = {ref["product_ref"]: ref for ref in call.facts()["internal_action_references"]["products"]}
                self.assertEqual(facts["stable-a"]["proposal_constraints"]["business_deadline"], deadline)
                late = business_deadline(deadline, 1, holidays, weekdays).isoformat()
                with self.assertRaises(ValueError):
                    call.step("prepare", "stage_promise", {"product_id": "stable-a", "amount": "37.25", "date": late})
                self.assertEqual(call.step("prepare", "stage_promise",
                                           {"product_id": "stable-a", "amount": "37.25", "date": deadline})[1], "done")
        # A date inside the earlier, longer window is refused once the amendment applies.
        call = self.account(DAY)
        earlier = business_deadline(DAY, DEADLINES[0]["recent"], holidays, weekdays).isoformat()
        with self.assertRaises(ValueError):
            call.step("prepare", "stage_promise", {"product_id": "stable-a", "amount": "37.25", "date": earlier})

    def test_dated_profile_keeps_confirmation_and_single_close_guards(self):
        call = self.account(DAY)
        call.step("prepare", "stage_promise", {"product_id": "stable-a", "amount": "37.25", "date": "2031-05-08"})
        self.assertEqual(call.step("prepare", "commit_promises")[1], "confirm")
        self.assertEqual(call.step("confirm", "commit_promises", {"consent": "no"})[1], "blocked")
        self.assertEqual(call.state["recorded_promises"], {})
        with self.assertRaises(ValueError):
            call.step("prepare", "close_call", {"outcome": "promise"})
        # Unknown persistence stops every later write and any success close.
        call.step("prepare", "commit_promises")
        self.assertEqual(call.step("confirm", "commit_promises", {"consent": "yes"})[1], "allow")
        call.step("result", result={})
        self.assertTrue(call.state["quarantined"])
        self.assertEqual(call.state["recorded_promises"], {})
        for action, inputs in (("commit_promises", {}), ("close_call", {"outcome": "promise"})):
            with self.subTest(action=action), self.assertRaises(ValueError):
                call.step("prepare", action, inputs)
        # A corrected term invalidates consent; a recorded promise is never duplicated.
        call = self.account(DAY)
        call.step("prepare", "stage_promise", {"product_id": "stable-a", "amount": "37.25", "date": "2031-05-08"})
        call.step("prepare", "commit_promises")
        call.step("prepare", "stage_promise", {"product_id": "stable-a", "amount": "40.00", "date": "2031-05-08"})
        with self.assertRaises(ValueError):
            call.step("confirm", "commit_promises", {"consent": "yes"})
        call.step("prepare", "commit_promises")
        self.assertEqual(call.step("confirm", "commit_promises", {"consent": "yes"})[2]["amount"], 40.0)
        call.step("result", result={"ok": True, "promise_id": "invented-record"})
        self.assertIn("stable-a", call.state["recorded_promises"])
        with self.assertRaises(ValueError):
            call.step("prepare", "stage_promise", {"product_id": "stable-a", "amount": "37.25", "date": "2031-05-08"})

    def test_selector_references_must_be_distinct_explicitly_overridden_primitive_same_kind_rules(self):
        def selector(rules, kind):
            return rules["dated-" + kind]["value"]["effective_date"]
        def same_rule(rules):
            selector(rules, "identity_wrong_limit")["on_or_after"]["rule"] = "old-identity_wrong_limit"
        def absent_rule(rules):
            selector(rules, "deadline_days")["before_rule"] = "absent-rule"
        def indirect_override(rules):
            rules["new-identity_wrong_limit"]["overrides"] = ["old-identity_wrong_limit"]
            rules["dated-identity_wrong_limit"]["overrides"] = ["new-identity_wrong_limit"]
        def nested_selector(rules):
            rules["old-deadline_days"]["value"] = copy.deepcopy(rules["dated-deadline_days"]["value"])
        def malformed_value(rules):
            rules["old-identity_wrong_limit"]["value"] = 0
        def other_kind(rules):
            rules["dated-identity_wrong_limit"]["overrides"].append("promise_failure_limit")
            selector(rules, "identity_wrong_limit")["before_rule"] = "promise_failure_limit"
        for mutate in (same_rule, absent_rule, indirect_override, nested_selector, malformed_value, other_kind):
            with self.subTest(mutate=mutate.__name__):
                values = packet(mutate=mutate)
                with self.assertRaises(PolicyContractError):
                    compile_policy_contract(*values[:3])

    def test_guard_refuses_missing_or_tampered_retained_variants(self):
        def drop(entry):
            del entry["referenced_rule_values"]
        def rename(entry):
            entry["referenced_rule_values"]["other"] = entry["referenced_rule_values"].pop("old-deadline_days")
        def malformed(entry):
            entry["referenced_rule_values"]["old-deadline_days"] = {"recent": -1, "older": 2}
        def uncovered_band(entry):
            entry["referenced_rule_values"]["new-deadline_days"] = {"recent": 3}
        def nested(entry):
            entry["referenced_rule_values"]["new-deadline_days"] = copy.deepcopy(entry["value"])
        for tamper in (drop, rename, malformed, uncovered_band, nested):
            with self.subTest(tamper=tamper.__name__):
                context = copy.deepcopy(self.context)
                tamper(context["contract"]["active_rules"]["deadline_days"])
                rehash(context["contract"])
                with self.assertRaises(GuardCompileError):
                    compile_guarded_workflow(**context)
        namespace = {}
        exec(_KERNEL, namespace)
        state = namespace["guard_new"]("invented-customer", DAY)
        with self.assertRaises(ValueError):
            namespace["guard_dated"](state, {"identity_wrong_limit": {"effective_date": {"date": DAY, "before": 4}}},
                                     "identity_wrong_limit")

    def test_dialogue_cannot_supply_the_selection_date(self):
        context = copy.deepcopy(self.context)
        context["plan"]["nodes"]["talk"]["extraction_schema"].append({"name": "now", "type": "string"})
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)

    def test_api_budget_below_the_selected_limit_counts_only_actual_wrong_answers(self):
        context = compiled(packet(api_budget=LIMITS[1]))
        call = Call(context, "2031-05-06")
        self.assertEqual(call.catalog["verify_identity"]["wrong_answers_budget"], LIMITS[1])
        self.assertEqual(call.facts()["identity"]["wrong_answers_left"], LIMITS[1])
        call.answer("postal_code", True, LIMITS[1])
        self.assertEqual((call.state["wrong_answers"], call.state["identity_status"]), (0, "pending"))
        call.answer("memorable_word", False, LIMITS[1] - 1)
        self.assertEqual((call.state["wrong_answers"], call.state["identity_status"]), (1, "pending"))
        self.assertEqual(call.facts()["identity"]["wrong_answers_left"], LIMITS[1] - 1)
        call.answer("memorable_word", False, 0)
        self.assertEqual((call.state["wrong_answers"], call.state["identity_status"]), (2, "failed"))
        with self.assertRaises(ValueError):
            call.step("prepare", "verify_identity", {"field": "memorable_word", "answer": "again"})
        above = Call(context, "2031-05-06")
        above.answer("postal_code", True, LIMITS[1] + 1)
        self.assertTrue(above.state["quarantined"])

    def test_api_budget_above_the_selected_limit_never_undercounts_on_either_side_of_the_boundary(self):
        # One declared maximum serves both dated limits, so it exceeds the selected one on one side.
        for limits, now in (((2, 3), "2031-05-06"), ((4, 2), DAY)):
            with self.subTest(limits=limits, now=now):
                call = Call(compiled(packet(api_budget=3, limits=limits)), now)
                self.assertEqual(call.facts()["identity"]["wrong_answers_left"], 2)
                call.answer("postal_code", False, 2)
                self.assertEqual((call.state["wrong_answers"], call.state["identity_status"]), (1, "pending"))
                self.assertEqual(call.facts()["identity"]["wrong_answers_left"], 1)
                call.answer("postal_code", False, 1)
                self.assertEqual((call.state["wrong_answers"], call.state["identity_status"]), (2, "failed"))
                for action, inputs in (("verify_identity", {"field": "postal_code", "answer": "again"}), ("get_account", None)):
                    with self.assertRaises(ValueError):
                        call.step("prepare", action, inputs)
        # A maximum well above the limit neither quarantines a correct first answer nor hides wrong answers.
        call = Call(compiled(packet(api_budget=5, limits=(2, 3))), "2031-05-06")
        call.answer("postal_code", True, 5)
        self.assertEqual((call.state["wrong_answers"], call.state["identity_status"], call.state["quarantined"]),
                         (0, "pending", False))
        call.answer("memorable_word", False, 1)
        self.assertEqual((call.state["wrong_answers"], call.state["identity_status"]), (2, "failed"))
        self.assertEqual(call.facts()["identity"]["wrong_answers_left"], 0)
        above = Call(compiled(packet(api_budget=5, limits=(2, 3))), "2031-05-06")
        above.answer("postal_code", True, 6)
        self.assertTrue(above.state["quarantined"])

    def test_pure_identity_helper_uses_the_same_budget_rule(self):
        policy = {"identity_fields": ["factor_a", "factor_b"], "identity_wrong_limit": 3}
        state = {"verified_fields": [], "wrong_answers": 0, "identity_status": "pending"}
        verified = {"ok": True, "verified": True, "wrong_answers_left": 2}
        self.assertEqual(identity_record(state, "factor_a", verified, policy)["wrong_answers"], 1)
        out = identity_record(state, "factor_a", verified, policy, wrong_answer_budget=2)
        self.assertEqual((out["wrong_answers"], out["identity_status"]), (0, "pending"))
        out = identity_record(out, "factor_b", {"ok": True, "verified": False, "wrong_answers_left": 0}, policy,
                              wrong_answer_budget=2)
        self.assertEqual((out["wrong_answers"], out["identity_status"]), (2, "failed"))
        wrong = {"ok": True, "verified": False, "wrong_answers_left": 3}
        out = identity_record(state, "factor_a", wrong, policy, wrong_answer_budget=5)
        self.assertEqual((out["wrong_answers"], out["identity_status"]), (2, "pending"))
        out = identity_record(out, "factor_a", {**wrong, "wrong_answers_left": 2}, policy, wrong_answer_budget=5)
        self.assertEqual((out["wrong_answers"], out["identity_status"]), (3, "failed"))
        for budget in (0, True, "2"):
            with self.subTest(budget=budget), self.assertRaises(PolicyError):
                identity_record(state, "factor_a", verified, policy, wrong_answer_budget=budget)

    def test_attempt_scope_guidance_states_product_scoping_and_single_close(self):
        guidance = execution_rule_guidance()["attempt_scopes"]
        self.assertIn("per-product attempt rule scopes only commit_promises/finalize_notes", guidance)
        self.assertIn("No retry permission authorizes a second close_call", guidance)


RUN, INPUTS = os.environ.get("FREYA_REPLAY_RUN"), os.environ.get("FREYA_REPLAY_INPUTS")


@unittest.skipUnless(RUN and INPUTS, "set FREYA_REPLAY_RUN and FREYA_REPLAY_INPUTS to replay a retained failed run")
class RetainedRunReplayTests(unittest.TestCase):
    """No-model replay of rejected policies; artifacts are read, never written."""

    def test_rejected_policies_select_dated_rules_or_stop_only_on_other_diagnosed_kinds(self):
        run, inputs = Path(RUN), Path(INPUTS)
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        recorded = next(event for event in events if event.get("event") == "inputs")
        sources = []
        for item in recorded["sources"]:
            text = (inputs / item["file"]).read_text(encoding="utf-8")
            self.assertEqual(digest(text), item["sha256"])
            sources.append({**item, "text": text})
        api = next(value for value in map(read_json, sorted(inputs.glob("*.json")))
                   if digest(value) == recorded["tool_api_sha256"])
        rejected = sorted(run.glob("policy.rejected-*.json"))
        self.assertTrue(rejected)
        namespace = {}
        exec(_KERNEL, namespace)
        for path in rejected:
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                policy = json.loads(raw)["policy"]
                policy["unsupported_constraints"] = []  # in memory: check the compiler, not the declarations
                contract = compile_policy_contract(policy, sources, api)
                dated = {kind: entry for kind, entry in contract["active_rules"].items()
                         if "referenced_rule_values" in entry}
                self.assertTrue(dated)
                try:
                    compile_execution_requirements(policy, sources, api)
                except ExecutionRequirementsError as exc:
                    self.assertTrue(exc.missing_kinds, exc.errors)
                    self.assertFalse(set(exc.missing_kinds) & set(dated), exc.errors)
                    self.assertTrue(all(error.startswith("missing typed execution rule: ") for error in exc.errors), exc.errors)
                # Later blockers owned by other lanes hide the full guarded compile; probe this stage directly.
                profile = compile_execution_policy(policy)["profile"]
                profile.update({kind: _dated_variants(contract, profile, kind) for kind in dated})
                for kind, entry in dated.items():
                    selection = entry["value"]["effective_date"]
                    boundary = date.fromisoformat(selection["on_or_after"]["date"])
                    for now, ref in ((boundary - timedelta(days=1), selection["before_rule"]),
                                     (boundary, selection["on_or_after"]["rule"])):
                        state = namespace["guard_new"]("replay-customer", now.isoformat())
                        self.assertEqual(namespace["guard_dated"](state, profile, kind), entry["referenced_rule_values"][ref])
                self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()

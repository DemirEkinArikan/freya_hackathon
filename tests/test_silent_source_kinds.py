"""Typed forms for a source that is silent on readback fields, transfer
prerequisites or an every-product scope, exercised through compiled guard code."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import unittest

from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import compile_execution_requirements
from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.policy import PolicyError, compile_execution_policy
from builder.safety import digest, read_json
from test_goal_scoped_resolution import ALL, PARTIAL, SELECTOR, CompiledCall, scoped
from test_guards import fixture


OVERDUE = {"products": "overdue"}
# Two loaded revolving products need a distinguishing readback label.
DISTINCT = {"product_fields": ["type", "days_late"]}
CURRENT = {"product_id": "asset-current", "type": "revolving", "days_late": 0, "past_due": 25, "minimum_due": 10}
ONE = {"product_id": "asset-one", "type": "revolving", "days_late": 8, "past_due": 300, "minimum_due": 37.25}
TWO = {"product_id": "asset-two", "type": "fixed", "days_late": 28, "past_due": 600, "instalment_due": 82.10}
REJECTED = (PolicyError, PolicyContractError, GuardCompileError)


def authored(**changes):
    context = fixture()
    profile = deepcopy(context["execution_policy"]["profile"])
    profile.update(deepcopy(changes))
    if isinstance(profile["resolve_all_products"], dict) and "selector" in profile["resolve_all_products"]:
        profile["call_variables"] = [SELECTOR]
    text = "Independently authored synthetic service instructions: " + json.dumps(profile, sort_keys=True)
    source = {"id": "silent-source", "file": "independent-silent-source.txt", "text": text, "sha256": digest(text)}
    policy = {"version": 1, "goal": "Resolve the explicitly supplied service scope", "allowed_actions": [
        operation["post"]["operationId"] for operation in context["openapi"]["paths"].values()],
        "sources": [{key: value for key, value in source.items() if key != "text"}],
        "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": source["id"], "quote": text}}
                  for kind, value in profile.items()], "unsupported_constraints": []}
    context["contract"] = compile_policy_contract(policy, [source], context["openapi"])
    context["execution_policy"] = compile_execution_policy(policy)
    return context


def loaded(context, products=(ONE, TWO), values=None):
    call = CompiledCall(context, values)
    for field in context["execution_policy"]["profile"]["identity_fields"]:
        call.variables.update(answer_field=field, answer_text="source-independent-answer")
        call.code("identity")
        call.response("identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
    call.code("account")
    call.response("account", {"ok": True, "products": deepcopy(list(products))})
    return call


def note(call, product, reason="refused"):
    call.variables.update(product=product, note_reason=reason)
    call.code("note")
    return call.variables["bg_route"]


def finish(call):
    """Finalize, recording each sent note positively; returns the first route."""
    call.code("finish")
    route = call.variables["bg_route"]
    while call.variables["bg_route"] == "allow":
        call.response("finish", {"ok": True})
    return route


class ReadbackFormTests(unittest.TestCase):
    def test_product_type_reads_back_only_the_protected_type(self):
        call = loaded(authored(promise_readback="product_type"))
        call.variables.update(product="asset-one", amount="50.00", day="2031-05-06")
        call.code("stage")
        call.code("commit")
        self.assertEqual(call.variables["bg_route"], "confirm")
        self.assertEqual(call.variables["bg_readback"], "Payment for revolving: 50.00 on 2031-05-06")

    def test_product_type_shared_by_two_products_still_blocks_confirmation(self):
        twin = dict(ONE, product_id="asset-twin", days_late=9)
        call = loaded(authored(promise_readback="product_type"), (ONE, twin))
        call.variables.update(product="asset-one", amount="50.00", day="2031-05-06")
        call.code("stage")
        call.code("commit")
        self.assertEqual(call.variables["bg_route"], "blocked")
        self.assertFalse(call.variables["bg_readback"])
        self.assertFalse(call.variables["bg_body"])
        self.assertFalse(call.state["recorded_promises"])

    def test_none_is_refused_whenever_commitments_need_confirmation(self):
        for changes in ({}, {"require_confirmation": False, "confirmation_actions": ["commit_promises"]}):
            with self.subTest(changes=changes), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(**authored(promise_readback="none", **changes))

    def test_none_without_required_confirmation_sends_no_readback(self):
        call = loaded(authored(promise_readback="none", require_confirmation=False))
        call.variables.update(product="asset-one", amount="50.00", day="2031-05-06")
        call.code("stage")
        call.code("commit")
        self.assertEqual(call.variables["bg_route"], "allow")
        self.assertFalse(call.variables["bg_readback"])
        self.assertEqual(json.loads(call.variables["bg_body"])["product_id"], "asset-one")

    def test_other_strings_and_shapes_are_rejected(self):
        for value in ("type", "", "none ", "product_types", ["type"], {"product_fields": "type"}, None):
            with self.subTest(value=value), self.assertRaises(REJECTED):
                compile_guarded_workflow(**authored(promise_readback=value))


class TransferPrerequisiteFormTests(unittest.TestCase):
    def test_unstated_adds_no_gate_and_other_queue_gates_remain(self):
        context = authored(transfer_prerequisites={"specialist": "account", "helpdesk": "unstated"})
        call = CompiledCall(context)
        call.variables["queue"] = "specialist"
        call.code("transfer")
        self.assertEqual(call.variables["bg_route"], "blocked")
        self.assertFalse(call.state["transfer"])
        call.variables["queue"] = "helpdesk"
        call.code("transfer")
        self.assertEqual(call.state["transfer"]["queue"], "helpdesk")
        call.code("handoff")
        self.assertEqual(json.loads(call.variables["bg_body"])["queue"], "helpdesk")

    def test_unstated_transfer_keeps_recorded_promises_and_discards_pending_notes(self):
        call = loaded(authored(transfer_prerequisites={"specialist": "unstated", "helpdesk": "none"}))
        call.promise()
        self.assertEqual(note(call, "asset-two"), "done")
        call.variables["queue"] = "specialist"
        call.code("transfer")
        self.assertEqual(set(call.state["recorded_promises"]), {"asset-one"})
        self.assertFalse(call.state["pending_notes"])
        call.code("handoff")
        call.response("handoff", {"ok": True})
        call.code("close")
        self.assertEqual(json.loads(call.variables["bg_body"])["outcome"], "transferred")
        call.response("close", {"ok": True})
        call.code("close")
        self.assertNotEqual(call.variables["bg_route"], "allow")

    def test_omitted_queue_compiles_but_is_never_dispatched(self):
        call = loaded(authored(transfer_prerequisites={"specialist": "account"}))
        call.variables["queue"] = "helpdesk"
        call.code("transfer")
        self.assertEqual(call.variables["bg_route"], "blocked")
        self.assertFalse(call.state["transfer"])
        call.code("handoff")
        self.assertNotEqual(call.variables["bg_route"], "allow")
        self.assertFalse(call.variables["bg_body"])

    def test_unknown_queues_values_and_empty_maps_are_rejected(self):
        for value in ({"specialist": "account", "helpdesk": "none", "elsewhere": "none"}, {"helpdesk": "unknown"},
                      {"helpdesk": ""}, {"helpdesk": None}, {}, {"helpdesk": ["unstated"]}):
            with self.subTest(value=value), self.assertRaises(REJECTED):
                compile_guarded_workflow(**authored(transfer_prerequisites=value))


class ScopedResolutionTests(unittest.TestCase):
    def test_overdue_scope_neither_waits_on_nor_writes_for_current_products(self):
        call = loaded(authored(resolve_all_products=OVERDUE, promise_readback=DISTINCT), (CURRENT, ONE, TWO))
        self.assertEqual(note(call, "asset-current"), "blocked")
        call.variables.update(product="asset-current", amount="10.00", day="2031-05-06")
        call.code("stage")
        self.assertEqual(call.variables["bg_route"], "blocked")
        self.assertFalse(call.state["pending_promises"])
        call.promise()
        call.code("close")
        self.assertEqual(call.variables["bg_route"], "blocked")
        self.assertFalse(call.state["close_attempted"])
        call.promise("asset-two", "82.10")
        call.code("close")
        self.assertEqual(json.loads(call.variables["bg_body"])["outcome"], "promise")
        self.assertEqual(set(call.state["recorded_promises"]), {"asset-one", "asset-two"})

    def test_legacy_every_product_boolean_still_orders_and_requires_every_product(self):
        call = loaded(authored(resolve_all_products=True), (CURRENT, ONE, TWO))
        self.assertEqual(note(call, "asset-one"), "blocked")
        self.assertEqual(note(call, "asset-current"), "done")

    def test_note_finalization_follows_the_sealed_scope(self):
        for rule, values, route in ((scoped(), {SELECTOR: PARTIAL}, "allow"), (scoped(), {SELECTOR: ALL}, "blocked"),
                                    (False, None, "blocked"), (True, None, "blocked")):
            with self.subTest(rule=rule, values=values):
                call = loaded(authored(resolve_all_products=rule), values=values)
                self.assertEqual(note(call, "asset-one"), "done")
                self.assertEqual(finish(call), route)
                self.assertEqual(call.state["notes_finalized"], route == "allow")
                self.assertEqual(set(call.state["recorded_notes"]), {"asset-one"} if route == "allow" else set())
        call = loaded(authored(resolve_all_products=OVERDUE, promise_readback=DISTINCT), (CURRENT, ONE, TWO))
        self.assertEqual(note(call, "asset-one"), "done")
        self.assertEqual(finish(call), "blocked")
        self.assertEqual(note(call, "asset-two", "dispute"), "done")
        self.assertEqual(finish(call), "allow")
        self.assertEqual(call.state["recorded_notes"], {"asset-one": "refused", "asset-two": "dispute"})
        call.code("close")
        self.assertEqual(json.loads(call.variables["bg_body"]), {"customer_id": "independent-customer", "outcome": "no_promise", "reason": "refused"})

    def test_empty_scope_with_nothing_staged_does_not_seal_the_call(self):
        for rule, products, values in ((scoped(), (ONE, TWO), {SELECTOR: PARTIAL}), (OVERDUE, (CURRENT,), None)):
            with self.subTest(rule=rule):
                call = loaded(authored(resolve_all_products=rule), products, values)
                self.assertEqual(finish(call), "blocked")
                self.assertFalse(call.state["notes_finalized"])
        call = loaded(authored(resolve_all_products=scoped()), values={SELECTOR: PARTIAL})
        finish(call)
        call.promise()
        call.code("close")
        self.assertEqual(json.loads(call.variables["bg_body"])["outcome"], "promise")

    def test_overdue_scope_never_fabricates_an_every_product_paid_close(self):
        call = loaded(authored(resolve_all_products=OVERDUE, promise_readback=DISTINCT), (CURRENT, ONE, TWO))
        note(call, "asset-one", "paid")
        note(call, "asset-two", "paid")
        self.assertEqual(finish(call), "allow")
        call.variables.update(outcome="paid", close_reason="")
        call.code("close")
        self.assertNotEqual(call.variables["bg_route"], "allow")
        self.assertFalse(call.variables["bg_body"])
        self.assertFalse(call.state["close_attempted"])

    def test_selector_and_overdue_scope_combine(self):
        rule = dict(scoped(), **OVERDUE)
        for values, route in (({SELECTOR: ALL}, "blocked"), ({SELECTOR: PARTIAL}, "allow")):
            with self.subTest(values=values):
                call = loaded(authored(resolve_all_products=rule, promise_readback=DISTINCT), (CURRENT, ONE, TWO), values)
                call.promise()
                call.code("close")
                self.assertEqual(call.variables["bg_route"], route)
        self.assertEqual(CompiledCall(authored(resolve_all_products=rule)).target("bg_init"), "bg_quarantine")

    def test_malformed_scopes_are_rejected(self):
        for rule in ({"products": "all"}, {"products": True}, {"products": "every"}, {"products": "overdue", "selector": SELECTOR},
                     {"products": "overdue", "by_value": {ALL: True}}, {"products": "overdue", "default": True}):
            with self.subTest(rule=rule), self.assertRaises(REJECTED):
                compile_guarded_workflow(**authored(resolve_all_products=rule))


# Original rejected policies of the failed fresh builds on 11 Oct (T07 issue
# comments), replayed without a model. Run directories stay untracked: set
# FREYA_SILENT_SOURCE_RUNS to their os.pathsep-separated paths. Expected
# remaining errors: [] = the policy stage passes; None = only other owners'
# diagnostics may remain, never one about these three kinds.
RETAINED = {
    "d4cb768bb5a17dd97d45099665665870249cc69335b5105bbe335bf704fdc23e": [],
    "d86253586d55a9e4a919b9e4c12029a7c07f8420812e4b8b1f89b92b59035a92": ["policy.rules[46] quote is not present in the supplied source"],
    "f2900eb310048c3e396cfcb64a5cd3ffb66540803fd247611add4384d10d27d4": [],
    "cd6e3d6cbcddd4af53f448e780ae6d3ec3e7dcbb45610e3d3dd5b0704a61e07d": None,
    "198a79eaa44d409454ad700c6fc155173d8deb58c5ccb71c5661cdb18974bf59": None,
}
SILENT_KINDS = ("promise_readback", "resolve_all_products", "transfer_prerequisites")


def retained_inputs(policy):
    bank = read_json(Path(__file__).resolve().parents[1] / "benchmarks/fixtures/offline.json")
    for profile in bank["profiles"]:
        sources = [{"id": f"brief-{i}", "file": name + ".md", "sha256": digest(text), "text": text}
                   for i, (name, text) in enumerate(profile["policy_documents"].items(), 1)]
        if [s["sha256"] for s in sources] == [s["sha256"] for s in policy["sources"]]:
            return sources, profile["openapi"]
    raise AssertionError("retained policy sources match no in-repo profile")


def typed_silent_forms(sources):
    """The three typed forms a fresh extraction can now state for this source."""
    def quote(start, end):
        document = next(s for s in sources if start in s["text"])
        i = document["text"].index(start)
        return {"id": document["id"], "quote": document["text"][i:document["text"].index(end, i) + len(end)]}
    codes = re.findall(r"\| goals \| (\w+) \| (\w+) \|", "\n".join(s["text"] for s in sources))
    return [
        {"id": "typed-readback", "kind": "promise_readback", "value": "product_type",
         "source": quote("A clear confirmation of the final product", "before any commitment is recorded.")},
        {"id": "typed-resolution", "kind": "resolve_all_products",
         "value": {"selector": "call_goal", "by_value": {code: meaning == "commitment_resolution" for meaning, code in codes}, **OVERDUE},
         "source": quote("Default call objective:", "policy-defined exception/refusal outcome.")},
        {"id": "typed-transfer", "kind": "transfer_prerequisites", "value": {"support": "unstated"},
         "source": quote("hardship behavior:", "customer-volunteered commitment.")},
    ]


@unittest.skipUnless(os.environ.get("FREYA_SILENT_SOURCE_RUNS"), "retained run directories are untracked")
class RetainedRejectedPolicyReplayTests(unittest.TestCase):
    def test_original_policies_pass_or_fail_only_for_explicit_diagnosed_reasons(self):
        seen = set()
        for run in os.environ["FREYA_SILENT_SOURCE_RUNS"].split(os.pathsep):
            for path in sorted(Path(run).glob("policy.rejected-*.json")):
                raw = path.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                self.assertIn(sha, RETAINED)
                seen.add(sha)
                policy = json.loads(raw)["policy"]
                sources, openapi = retained_inputs(policy)
                with self.subTest(policy=path.name, sha=sha[:12]):
                    # The model's own declarations remain fatal as written.
                    with self.assertRaises(PolicyContractError) as raised:
                        compile_policy_contract(policy, sources, openapi)
                    self.assertIn("policy contains unsupported constraints", raised.exception.errors)
                    typed = deepcopy(policy)
                    typed["unsupported_constraints"] = []
                    present = {rule.get("kind") for rule in typed["rules"]}
                    typed["rules"] += [rule for rule in typed_silent_forms(sources) if rule["kind"] not in present]
                    try:
                        compile_execution_requirements(typed, sources, openapi)
                        errors = []
                    except (PolicyContractError, ValueError) as exc:
                        errors = list(getattr(exc, "errors", [str(exc)]))
                    if RETAINED[sha] is None:
                        self.assertFalse([e for e in errors if any(kind in e for kind in SILENT_KINDS)], errors)
                    else:
                        self.assertEqual(errors, RETAINED[sha])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)
        self.assertEqual(seen, set(RETAINED))


if __name__ == "__main__":
    unittest.main()

"""Independent mixed-phase policies run through real preflight/sealed code.

All data and literal policy witnesses are invented; no model, network or
materialized evaluator/holdout fixture is used.
"""
from copy import deepcopy
import unittest

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.policy import compile_execution_policy
from test_execution_requirements import packet, rule, add_rule
from test_goal_requirements import compiled, SealedSession, witness, PRODUCT, remove

ACTIONS = ["verify_identity", "get_account", "stage_note", "finalize_notes", "close_call"]
OUTCOMES = {"paid": "OUT_IDENTITY_P7", "no_promise": "OUT_ACCOUNT_R4", "not_verified": "OUT_VERIFY_V3"}
NOTES = {"refused": "LOCAL_REFUSAL_N8", "paid": "LOCAL_PAYMENT_N9"}
REASONS = {"refused": "REASON_REFUSAL_Q4", "failed": "REASON_VERIFY_Q3"}


def mixed(*, opaque=False, account_paid=False, marker=False, precedence=True):
    policy, sources, api = packet(ACTIONS, local=True, narrow=True)
    # This mixed-phase source explicitly supplies the fixed identity table,
    # independent of whether the catalogue universally requires that rule.
    full_policy, _, _ = packet(ACTIONS, local=True)
    add_rule(policy, "identity_close", rule(full_policy, "identity_close")["value"])
    rule(policy, "note_reasons")["value"] = ["refused"] + (["paid"] if marker else [])
    rule(policy, "close_reasons")["value"] = {"paid": [], "no_promise": ["refused"], "not_verified": ["identity_failed"]}
    rule(policy, "identity_close")["value"]["unreached"] = {"outcome": "not_verified", "reason": "identity_failed"}
    if precedence:
        add_rule(policy, "close_precedence", ["paid", "no_promise"] if account_paid else ["no_promise"])
    if opaque:
        rule(policy, "wire_codes")["value"] = {"outcomes": deepcopy(OUTCOMES), "notes": deepcopy(NOTES),
                                              "reasons": deepcopy(REASONS)}
        rule(policy, "note_reasons")["value"] = [NOTES[name] for name in rule(policy, "note_reasons")["value"]]
        rule(policy, "close_reasons")["value"] = {
            OUTCOMES[name]: [REASONS["failed"] if value == "identity_failed" else REASONS[value] for value in reasons]
            for name, reasons in rule(policy, "close_reasons")["value"].items()}
        for proposal in rule(policy, "identity_close")["value"].values():
            proposal["outcome"] = OUTCOMES[proposal["outcome"]]
            if "reason" in proposal: proposal["reason"] = REASONS["failed"]
        if precedence:
            rule(policy, "close_precedence")["value"] = [OUTCOMES[name] for name in rule(policy, "close_precedence")["value"]]
        schema = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        schema["properties"]["outcome"]["enum"] = list(OUTCOMES.values())
        schema["properties"]["reason"]["enum"] = list(REASONS.values())
    witness(policy, sources)
    return policy, sources, api


def account_session(values):
    requirements, context, workflow = compiled(*values)
    session = SealedSession(context, workflow)
    for field in ("postal_code", "memorable_word"):
        session.action("verify_identity", field=field, answer="invented answer")
        session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
    session.action("get_account")
    session.result("get_account", {"ok": True, "products": [deepcopy(PRODUCT)]})
    return requirements, context, session


class PaidDispositionPhaseTests(unittest.TestCase):
    def test_identity_only_paid_vocabulary_does_not_require_account_marker(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                values = mixed(opaque=opaque)
                before = deepcopy(values)
                descriptor = compile_execution_requirements(*values)
                self.assertEqual(values, before)
                self.assertNotIn("add_note", values[0]["allowed_actions"])
                self.assertEqual(descriptor["operation_bindings"]["stage_note"]["mode"], "local_disposition")
                self.assertEqual(compile_execution_policy(values[0])["profile"]["note_reasons"],
                                 [NOTES["refused"]] if opaque else ["refused"])

    def test_real_account_refusal_close_and_identity_close_keep_distinct_proofs(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                values = mixed(opaque=opaque)
                _, context, session = account_session(values)
                session.action("stage_note", product_id=PRODUCT["product_id"], reason=NOTES["refused"] if opaque else "refused")
                session.action("finalize_notes")
                session.action("close_call", outcome="paid", reason="invented caller proposal")
                self.assertEqual(session.body["outcome"], OUTCOMES["no_promise"] if opaque else "no_promise")
                self.assertEqual(session.body["reason"], REASONS["refused"] if opaque else "refused")
                self.assertEqual(session.state["pending_notes"], {PRODUCT["product_id"]: "refused"})
                self.assertEqual(session.state["recorded_notes"], {})
                _, context, workflow = compiled(*values)
                identity = SealedSession(context, workflow)
                identity.action("close_call", outcome=OUTCOMES["paid"] if opaque else "paid")
                self.assertEqual(identity.body["outcome"], OUTCOMES["not_verified"] if opaque else "not_verified")
                self.assertFalse(identity.state["account_loaded"])

    def test_identity_paid_row_is_refused_without_its_recorded_fact(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                policy, sources, api = mixed(opaque=opaque)
                rule(policy, "identity_close")["value"]["unreached"] = {"outcome": OUTCOMES["paid"] if opaque else "paid"}
                witness(policy, sources)
                with self.assertRaisesRegex(ExecutionRequirementsError, "cannot use a protected-fact outcome"):
                    compile_execution_requirements(policy, sources, api)

    def test_explicit_account_paid_precedence_without_marker_stays_rejected(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque), self.assertRaisesRegex(ExecutionRequirementsError, "source-backed note_reasons"):
                compile_execution_requirements(*mixed(opaque=opaque, account_paid=True))

    def test_sourced_account_paid_marker_enables_real_paid_close_without_note_api(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                values = mixed(opaque=opaque, account_paid=True, marker=True)
                before = deepcopy(values)
                _, _, session = account_session(values)
                session.action("stage_note", product_id=PRODUCT["product_id"], reason=NOTES["paid"] if opaque else "paid")
                session.action("finalize_notes")
                session.action("close_call", outcome="no_promise")
                self.assertEqual(session.body["outcome"], OUTCOMES["paid"] if opaque else "paid")
                self.assertNotIn("add_note", values[0]["allowed_actions"])
                self.assertEqual(values, before)

    def test_absent_precedence_supplies_no_paid_marker_or_priority(self):
        for opaque in (False, True):
            with self.subTest(opaque=opaque):
                values = mixed(opaque=opaque, precedence=False)
                compile_execution_requirements(*values)
                profile = compile_execution_policy(values[0])["profile"]
                self.assertNotIn("close_precedence", profile)
                self.assertNotIn(NOTES["paid"] if opaque else "paid", profile["note_reasons"])

    def test_explicit_paid_failure_disposition_without_precedence_still_requires_marker(self):
        policy, sources, api = packet(ACTIONS + ["commit_promises"], local=True, narrow=True)
        remove(policy, {"close_precedence"})
        rule(policy, "note_reasons")["value"] = ["refused"]
        add_rule(policy, "promise_failure_disposition", {"reason": "paid", "clear_pending": True})
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "promise_failure_disposition reason must belong"):
            compile_execution_requirements(policy, sources, api)

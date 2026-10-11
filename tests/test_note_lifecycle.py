"""Opt-in note lifecycle kinds through compiled guard code: a per-product note
write, a sourced transfer after recorded notes, a later note for a noted product
and an exhausted note write kept for close selection. Absent, notes stay one
end-of-call batch. Invented renamed contract; no model, bank or hosted call."""
import copy
import json
import unittest

from builder.build import make_policy_prompt
from builder.contract import PolicyContractError
from builder.guards import GuardCompileError, compile_guarded_workflow, execution_action_catalogue
from builder.policy import PolicyError, compile_execution_policy, execution_rule_guidance
import test_guard_aliases as aliases

Z, A = "internal-product-Z", "internal-product-A"
REJECTED = (PolicyError, PolicyContractError, GuardCompileError)
QUOTE = "This invented exact rejection persists no write."
KINDS = ("note_finalization", "transfer_after_notes", "repeat_note_reasons", "note_failure_disposition")


def session(*, write_node=None, failing=None, scope="product", cap=2, **changes):
    """The renamed contract plus `changes`. write_node adds a finalize_notes node
    with a product_id input; failing gives that write role a qualified
    nonpersistent receipt with a sourced scope, cap and resume."""
    context, names = aliases.renamed_context()
    profile = copy.deepcopy(context["execution_policy"]["profile"])
    profile.update(copy.deepcopy(changes))
    if failing:
        role, limit = {"finalize_notes": ("add_note", "note_failure_limit"),
                       "commit_transfer": ("transfer_to_agent", "transfer_failure_limit")}[failing]
        profile.update(attempt_scopes={failing: scope}, definite_failure_resume=[failing], **{limit: cap})
        context["openapi"]["paths"]["/" + role]["post"]["responses"]["200"]["description"] = QUOTE
        profile["nonpersistent_failures"][names[role]] = [{"http_status": 200, "error": "ERR_NONPERSISTENT", "api_source": {
            "pointer": "/paths/~1" + role + "/post/responses/200/description", "quote": QUOTE}}]
    if write_node if write_node is not None else profile.get("note_finalization") == "per_product":
        nodes = context["plan"]["nodes"]
        nodes["write"] = dict(nodes["finish"], name="write", inputs={"product_id": "product"})
        nodes["talk"]["transitions"].append({"target_node": "write", "description": "write",
                                             "condition": {"operator": "matches_intent", "description": "write"}})
    call = aliases.GuardAliasTests(methodName="runTest")
    call.context = aliases.recompile_context(context, profile)
    call.workflow = compile_guarded_workflow(**call.context)["workflow"]
    call.variables = {"customer_id": "INVENTED-SOURCE-SESSION", "now": "2031-05-02"}
    call.code("bg_init")
    call.account()
    return call


def state(call):
    return json.loads(call.variables["bg_state"])


def body(call):
    return json.loads(call.variables["bg_body"] or "{}")


def step(call, node, **variables):
    call.variables.update(variables)
    call.code(node)
    return call.variables["bg_route"]


def answer(call, node, ok=True):
    data = {"evidence_ok": True} if ok else {"evidence_ok": False, "evidence_error": "ERR_NONPERSISTENT"}
    call.api_result(node, data)
    return call.variables["bg_route"]


def promise(call, product, amount):
    step(call, "stage", product=product, amount=amount, day="2031-05-06")
    step(call, "commit", bg_consent="")
    call.variables["bg_consent"] = "yes"
    call.code("bg_commit_accept")
    call.api_result("commit", {"evidence_ok": True, "evidence_promise_id": "RECORD-" + product})


def close_body(call):
    call.code("close")
    return body(call)


class PerProductFinalizationTests(unittest.TestCase):
    def test_one_note_is_written_now_and_the_call_continues_to_a_sealed_close(self):
        call = session(note_finalization="per_product")
        self.assertEqual(step(call, "note", product=Z, note_reason="NOTE_D"), "done")
        self.assertEqual(step(call, "write", product=Z), "allow")
        self.assertEqual(body(call)["input_product_id"], Z)
        self.assertEqual(answer(call, "write"), "done")
        self.assertEqual((state(call)["recorded_notes"], state(call)["notes_finalized"]), ({Z: "dispute"}, False))
        promise(call, A, "82.10")
        self.assertEqual(set(state(call)["recorded_promises"]), {A})
        self.assertEqual(close_body(call), {})  # The phase is sealed before the close.
        self.assertEqual(step(call, "finish"), "done")  # Nothing unsent: no second note request.
        self.assertEqual(len(state(call)["attempts"]), 2)
        self.assertEqual(close_body(call)["input_outcome"], "C_A")

    def test_sealing_with_boolean_false_needs_only_resolved_products(self):
        call = session(note_finalization="per_product", resolve_all_products=False)
        step(call, "note", product=Z, note_reason="NOTE_D")
        step(call, "write", product=Z)
        answer(call, "write")
        self.assertEqual(step(call, "finish"), "done")
        self.assertEqual(close_body(call), {"input_customer_id": "INVENTED-SOURCE-SESSION", "input_outcome": "C_D", "input_reason": "WHY_D"})
        legacy = session(resolve_all_products=False)
        step(legacy, "note", product=Z, note_reason="NOTE_D")
        self.assertEqual(step(legacy, "finish"), "blocked")  # Boolean false still finalizes every product.
        self.assertFalse(state(legacy)["notes_finalized"])

    def test_product_writes_need_the_kind_an_unsent_note_and_an_open_phase(self):
        for changes in ({}, {"note_finalization": "per_product", "record_notes": False}):
            with self.subTest(changes=changes), self.assertRaises(GuardCompileError):
                session(write_node=True, **changes)
        call = session(note_finalization="per_product")
        for product in (Z, "invented-other", ""):
            self.assertEqual(step(call, "write", product=product), "blocked")
        step(call, "note", product=Z, note_reason="NOTE_D")
        step(call, "write", product=Z)
        answer(call, "write")
        self.assertEqual(step(call, "write", product=Z), "blocked")
        step(call, "note", product=A, note_reason="NOTE_R")
        self.assertEqual(step(call, "finish"), "allow")
        answer(call, "finish")
        self.assertTrue(state(call)["notes_finalized"])
        self.assertEqual(step(call, "write", product=A), "blocked")
        self.assertEqual(state(call)["recorded_notes"], {Z: "dispute", A: "refused"})


class TransferAfterNotesTests(unittest.TestCase):
    def notes(self, call):
        step(call, "note", product=Z, note_reason="NOTE_D")
        step(call, "note", product=A, note_reason="NOTE_R")
        self.assertEqual(step(call, "finish"), "allow")
        answer(call, "finish")
        answer(call, "finish")
        self.assertEqual(set(state(call)["recorded_notes"]), {Z, A})

    def test_listed_queue_follows_recorded_notes_and_unlisted_queues_stay_refused(self):
        legacy = session()
        self.notes(legacy)
        self.assertEqual(step(legacy, "transfer", queue="QUEUE_S"), "blocked")
        call = session(transfer_after_notes=["QUEUE_S"])
        self.notes(call)
        self.assertEqual(step(call, "transfer", queue="QUEUE_G"), "blocked")
        self.assertFalse(state(call)["transfer"])
        self.assertEqual(step(call, "transfer", queue="QUEUE_S"), "done")
        self.assertEqual((state(call)["notes_finalized"], set(state(call)["recorded_notes"])), (False, {Z, A}))
        self.assertEqual(step(call, "handoff"), "allow")
        answer(call, "handoff")
        self.assertEqual(close_body(call)["input_outcome"], "C_C")

    def test_failed_listed_transfer_lets_recorded_notes_reseal_and_close(self):
        call = session(transfer_after_notes=["QUEUE_S"], failing="commit_transfer", scope="call", cap=1)
        self.notes(call)
        step(call, "transfer", queue="QUEUE_S")
        step(call, "handoff")
        self.assertEqual(answer(call, "handoff", ok=False), "failure")
        self.assertEqual((state(call)["transfer"], state(call)["pending_notes"]), ({}, {}))
        self.assertEqual(step(call, "finish"), "done")
        self.assertEqual(close_body(call)["input_reason"], "WHY_D")

    def test_listed_queues_must_have_prerequisites_and_a_valid_shape(self):
        for value in (["QUEUE_X"], [], "QUEUE_S", ["QUEUE_S", "QUEUE_S"], [""]):
            with self.subTest(value=value), self.assertRaises(REJECTED):
                session(transfer_after_notes=value)
        with self.assertRaises(GuardCompileError):
            session(transfer_after_notes=["QUEUE_S"], transfer_prerequisites={"QUEUE_G": "account"})
        call = session(transfer_after_notes=["support"])  # A declared semantic key stands for its code.
        self.notes(call)
        self.assertEqual(step(call, "transfer", queue="QUEUE_S"), "done")


class RepeatNoteTests(unittest.TestCase):
    def test_listed_later_note_is_written_and_drives_close_selection(self):
        call = session(note_finalization="per_product", repeat_note_reasons=["NOTE_P"])
        for reason in ("NOTE_D", "NOTE_P"):
            self.assertEqual(step(call, "note", product=Z, note_reason=reason), "done")
            self.assertEqual(step(call, "write", product=Z), "allow")
            self.assertEqual(body(call)["input_reason"], reason)
            answer(call, "write")
        self.assertEqual(state(call)["recorded_notes"], {Z: "paid"})
        step(call, "note", product=A, note_reason="NOTE_P")
        step(call, "finish")
        answer(call, "finish")
        self.assertEqual(close_body(call)["input_outcome"], "C_B")

    def test_unlisted_repeated_or_resent_reasons_and_promised_products_stay_refused(self):
        call = session(note_finalization="per_product", repeat_note_reasons=["NOTE_P", "NOTE_D"])
        step(call, "note", product=Z, note_reason="NOTE_D")
        step(call, "write", product=Z)
        answer(call, "write")
        for reason in ("NOTE_R", "NOTE_D"):
            self.assertEqual(step(call, "note", product=Z, note_reason=reason), "blocked")
        step(call, "note", product=Z, note_reason="NOTE_P")
        step(call, "write", product=Z)
        answer(call, "write")
        self.assertEqual(step(call, "note", product=Z, note_reason="NOTE_D"), "blocked")  # Already sent once.
        promise(call, A, "82.10")
        self.assertEqual(step(call, "note", product=A, note_reason="NOTE_P"), "blocked")
        self.assertEqual(state(call)["recorded_notes"], {Z: "paid"})
        for changes in ({"repeat_note_reasons": ["NOTE_P"]},
                        {"note_finalization": "per_product", "repeat_note_reasons": ["NOTE_X"]}):
            with self.subTest(changes=changes), self.assertRaises(REJECTED):
                session(write_node=False, **changes)


class ExhaustedNoteTests(unittest.TestCase):
    def test_exhausted_write_keeps_its_reason_for_close_without_a_recorded_note(self):
        call = session(failing="finalize_notes", note_failure_disposition="attempted_reason")
        step(call, "note", product=Z, note_reason="NOTE_P")
        step(call, "note", product=A, note_reason="NOTE_P")
        self.assertEqual(step(call, "finish"), "allow")
        self.assertEqual(answer(call, "finish", ok=False), "allow")  # Qualified retry below the cap.
        self.assertEqual(answer(call, "finish", ok=False), "allow")  # At the cap the batch continues.
        self.assertEqual(body(call)["input_product_id"], A)
        self.assertEqual(answer(call, "finish"), "done")
        after = state(call)
        self.assertEqual((after["recorded_notes"], after["exhausted_notes"], after["quarantined"]), ({A: "paid"}, {Z: "paid"}, False))
        facts = json.loads(call.variables["bg_facts"])
        self.assertEqual(facts["recorded_counts"]["notes"], 1)
        self.assertEqual([item["note"] for item in facts["product_states"]], ["exhausted", "recorded"])
        self.assertEqual(close_body(call)["input_outcome"], "C_B")

    def test_exhausted_product_refuses_later_staging_and_legacy_keeps_no_disposition(self):
        call = session(failing="finalize_notes", note_failure_disposition="attempted_reason", note_finalization="per_product")
        step(call, "note", product=Z, note_reason="NOTE_D")
        step(call, "write", product=Z)
        answer(call, "write", ok=False)
        self.assertEqual(answer(call, "write", ok=False), "failure")
        self.assertEqual((state(call)["exhausted_notes"], state(call)["notes_finalized"]), ({Z: "dispute"}, False))
        self.assertEqual(step(call, "stage", product=Z, amount="50.00", day="2031-05-06"), "blocked")
        self.assertEqual(step(call, "note", product=Z, note_reason="NOTE_R"), "blocked")
        self.assertEqual(step(call, "transfer", queue="QUEUE_S"), "blocked")  # Kept like a recorded note.
        step(call, "note", product=A, note_reason="NOTE_R")
        step(call, "finish")
        answer(call, "finish")
        self.assertEqual(close_body(call)["input_reason"], "WHY_D")
        legacy = session(failing="finalize_notes")
        step(legacy, "note", product=Z, note_reason="NOTE_D")
        step(legacy, "note", product=A, note_reason="NOTE_R")
        step(legacy, "finish")
        answer(legacy, "finish", ok=False)
        self.assertEqual(answer(legacy, "finish", ok=False), "failure")
        self.assertEqual((state(legacy)["exhausted_notes"], state(legacy)["recorded_notes"]), ({}, {}))
        self.assertEqual(close_body(legacy), {})

    def test_call_scope_cap_stops_the_batch_and_unqualified_failures_still_quarantine(self):
        call = session(failing="finalize_notes", scope="call", note_failure_disposition="attempted_reason")
        step(call, "note", product=Z, note_reason="NOTE_D")
        step(call, "note", product=A, note_reason="NOTE_R")
        step(call, "finish")
        answer(call, "finish", ok=False)
        self.assertEqual(answer(call, "finish", ok=False), "failure")
        self.assertEqual((state(call)["exhausted_notes"], state(call)["quarantined"]), ({Z: "dispute"}, False))
        self.assertEqual(close_body(call), {})  # The unsent product stays unresolved.
        other = session(failing="finalize_notes", note_failure_disposition="attempted_reason")
        step(other, "note", product=Z, note_reason="NOTE_D")
        step(other, "note", product=A, note_reason="NOTE_R")
        step(other, "finish")
        other.api_result("finish", {"evidence_ok": False, "evidence_error": "ERR_UNQUALIFIED"})
        self.assertTrue(state(other)["quarantined"])
        self.assertEqual(state(other)["exhausted_notes"], {})

    def test_disposition_needs_a_sourced_resume_and_bank_notes(self):
        for changes in ({"note_failure_disposition": "attempted_reason"},
                        {"note_failure_disposition": "attempted_reason", "failing": "finalize_notes", "record_notes": False}):
            with self.subTest(changes=changes), self.assertRaises(REJECTED):
                session(**changes)


class InterfaceTests(unittest.TestCase):
    def test_malformed_values_are_rejected(self):
        for kind, values in (("note_finalization", ("per-product", True, ["per_product"], "")),
                             ("note_failure_disposition", ("attempted", True, None)),
                             ("repeat_note_reasons", ([], "NOTE_P", ["NOTE_P", "NOTE_P"], [""]))):
            for value in values:
                with self.subTest(kind=kind, value=value), self.assertRaises(PolicyError):
                    compile_execution_policy({kind: value})

    def test_prompt_catalogue_and_effects_state_the_opt_ins(self):
        guidance = execution_rule_guidance()
        catalogue = execution_action_catalogue()
        self.assertEqual(catalogue["finalize_notes"]["optional_input_fields"], ["product_id"])
        effects = json.dumps(catalogue)
        for kind in KINDS:
            self.assertIn(kind, guidance)
            self.assertIn(kind, effects)
        shapes = json.loads(make_policy_prompt([], aliases.renamed_context()[0]["openapi"]).split("\n\nAUTHORIZED INPUTS:\n", 1)[1])
        self.assertEqual({kind: shapes["execution_interface"]["rule_shapes"][kind] for kind in KINDS},
                         {kind: guidance[kind] for kind in KINDS})


if __name__ == "__main__":
    unittest.main()

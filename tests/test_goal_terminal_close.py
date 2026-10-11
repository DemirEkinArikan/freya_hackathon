"""Terminal closes for non-payment goals; invented codes, no model, bank or platform I/O."""
import copy
import json
import os
import unittest
from pathlib import Path

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.policy import execution_rule_guidance
from builder.safety import digest, read_json
from test_execution_requirements import add_rule, packet, rule
import test_goal_requirements as goal_requirements
from test_goal_requirements import PRODUCT, SealedSession, compiled, synthetic, witness
from test_input_format_completeness import declare_offset_clock


def respond(session, action, data, status):
    """SealedSession.result with an actual envelope status (None: no transport status)."""
    node = session.workflow["nodes"]["bg_" + session.entries[action] + "_api"]
    envelope = {"data": copy.deepcopy(data)}
    if status is not None:
        envelope["status_code"] = status
    exec(node["pre_actions"][0]["post_process_code"], {"result": envelope})
    for mapping in node["response_mapping"]:
        session.variables[mapping["variable"]] = envelope["data"].get(mapping["path"][5:], "")
    session.code("bg_" + session.entries[action] + "_result")


def declare_failure(api):
    # 503 is a definite failure shape; the flags of 409 and 422 can never be false, so they are not.
    for status, flag in (("503", {"enum": [False]}), ("409", {"enum": [True]}), ("422", {"const": True})):
        api["paths"]["/get_account"]["post"]["responses"][status] = {"description": "Invented service response.",
            "content": {"application/json": {"schema": {"type": "object", "required": ["ok", "error"], "properties": {
                "ok": dict(flag, type="boolean"), "error": {"type": "string", "minLength": 1}}}}}}


def extend_enum(api, field, *codes):
    schema = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"][field]["enum"].extend(code for code in codes if code not in schema["properties"][field]["enum"])


def goal(actions, close, rows=None, **changes):
    policy, sources, api = synthetic(actions)
    rule(policy, "close_reasons")["value"].update(close)
    for outcome, reasons in close.items():
        extend_enum(api, "outcome", outcome)
        extend_enum(api, "reason", *reasons)
    for kind, value in changes.items():
        rule(policy, kind)["value"] = value
    if rows is not None:
        add_rule(policy, "goal_close", rows)
    witness(policy, sources)
    return policy, sources, api


class GoalTerminalCloseTests(unittest.TestCase):
    session = goal_requirements.GoalRequirementsTests.session
    assert_silent_terminal = goal_requirements.GoalRequirementsTests.assert_silent_terminal

    def verify(self, session):
        for factor in ("postal_code", "memorable_word"):
            session.action("verify_identity", field=factor, answer="synthetic-correct")
            session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})
        self.assertEqual(session.state["identity_status"], "verified")

    def assert_refused(self, session):
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.variables["bg_body"])
        self.assertFalse(session.state["close_attempted"])

    def test_identity_only_success_without_sourced_row_still_refuses_close(self):
        # S03 reproduction on 4ef4e627/3f2399d: verified identity, no account and
        # no collections fact reach 'closing lacks protected evidence'.
        _, _, session = self.session(synthetic(["verify_identity", "close_call"]))
        self.verify(session)
        session.action("close_call", outcome="promise", reason="refused")
        self.assert_refused(session)
        self.assertFalse(session.state["account_loaded"])

    def test_identity_only_success_closes_once_with_the_sourced_pair(self):
        rows = {"verified": [{"outcome": "GOAL_DONE", "reason": "GOAL_MATCH", "requires": "none"}]}
        _, _, session = self.session(goal(["verify_identity", "close_call"], {"GOAL_DONE": ["GOAL_MATCH"]}, rows))
        self.verify(session)
        session.action("close_call", outcome="promise", reason="refused")  # dialogue cannot pick the pair
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["outcome"], "GOAL_DONE")
        self.assertEqual(session.body["reason"], "GOAL_MATCH")
        session.result("close_call", {"ok": True})
        self.assertTrue(session.state["closed"])
        self.assert_silent_terminal(session, "bg_" + session.entries["close_call"] + "_result")
        session.action("close_call", outcome="GOAL_DONE", reason="GOAL_MATCH")
        self.assertEqual(session.variables["bg_route"], "blocked")

    def test_verified_row_is_unreachable_before_identity_success_or_after_failure(self):
        rows = {"verified": [{"outcome": "GOAL_DONE", "reason": "GOAL_MATCH", "requires": "none"}]}
        values = goal(["verify_identity", "close_call"], {"GOAL_DONE": ["GOAL_MATCH"]}, rows)
        _, _, session = self.session(values)
        session.action("close_call", outcome="GOAL_DONE", reason="GOAL_MATCH")
        self.assertEqual(session.body["outcome"], "not_verified")  # existing pre-identity table, never the goal row
        _, _, session = self.session(values)
        session.action("verify_identity", field="postal_code", answer="synthetic-wrong")
        session.result("verify_identity", {"ok": True, "verified": False, "wrong_answers_left": 0})
        session.action("close_call", outcome="GOAL_DONE", reason="GOAL_MATCH")
        self.assertEqual(session.body["outcome"], "not_verified")

    def information_values(self):
        rows = {"verified": [{"outcome": "INFO_PART", "reason": "refused", "requires": "none"}],
                "account_loaded": [{"outcome": "INFO_DONE", "requires": "none"},
                                   {"outcome": "INFO_PART", "reason": "refused", "requires": "none"}]}
        return goal(["verify_identity", "get_account", "close_call"],
                    {"INFO_DONE": [], "INFO_PART": ["refused"]}, rows, resolve_all_products=False)

    def test_information_only_close_after_account_read_uses_a_declared_choice(self):
        _, _, session = self.session(self.information_values())
        self.verify(session)
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        self.assertTrue(session.state["account_loaded"])
        before = session.variables["bg_state"]
        session.action("close_call", outcome="not_reached", reason="")
        self.assert_refused(session)
        self.assertTrue(session.state["quarantined"])  # a refused close ends its call with the flag
        session.variables["bg_state"] = before  # resume the state before it
        session.action("close_call", outcome="INFO_DONE", reason="")
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.body["outcome"], "INFO_DONE")
        self.assertNotIn("reason", session.body)
        session.result("close_call", {"ok": "uncertain"})
        self.assertFalse(session.state["closed"])
        self.assertTrue(session.state["quarantined"])
        session.action("close_call", outcome="INFO_PART", reason="refused")
        self.assertEqual(session.variables["bg_route"], "blocked")

    def test_information_goal_stopping_before_account_read_uses_verified_row(self):
        _, _, session = self.session(self.information_values())
        self.verify(session)
        session.action("close_call", outcome="INFO_DONE", reason="")
        self.assertEqual(session.body["outcome"], "INFO_PART")
        self.assertEqual(session.body["reason"], "refused")

    def failed_values(self, failed=None):
        policy, sources, api = self.information_values()
        rule(policy, "goal_close")["value"]["account_failed"] = failed or [
            {"outcome": "INFO_FAULT", "reason": "service_error", "requires": "none"}]
        rule(policy, "close_reasons")["value"]["INFO_FAULT"] = ["service_error"]
        extend_enum(api, "outcome", "INFO_FAULT")
        extend_enum(api, "reason", "service_error")
        witness(policy, sources)
        declare_failure(api)
        return policy, sources, api

    def test_definite_negative_account_receipt_latches_its_own_rows(self):
        _, _, session = self.session(self.failed_values())
        self.verify(session)
        session.action("get_account")
        respond(session, "get_account", {"ok": False, "error": "invented outage"}, 503)
        self.assertEqual(session.variables["bg_route"], "failure")
        self.assertEqual({key: session.state[key] for key in ("account_failed", "account_loaded", "quarantined")},
                         {"account_failed": True, "account_loaded": False, "quarantined": False})
        session.action("get_account")  # read once: no second request after a definite failure
        self.assert_refused(session)
        session.action("close_call", outcome="INFO_PART", reason="refused")  # a later stop cannot change the pair
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("INFO_FAULT", "service_error"))
        respond(session, "close_call", {"ok": True}, 200)
        self.assertTrue(session.state["closed"])

    def test_several_failed_rows_need_a_matching_proposal(self):
        failed = [{"outcome": "INFO_FAULT", "reason": "service_error", "requires": "none"},
                  {"outcome": "INFO_PART", "reason": "refused", "requires": "none"}]
        for proposal, expected in ((("INFO_DONE", ""), None), (("INFO_PART", "refused"), ("INFO_PART", "refused"))):
            with self.subTest(proposal=proposal):
                _, _, session = self.session(self.failed_values(failed))
                self.verify(session)
                session.action("get_account")
                respond(session, "get_account", {"ok": False, "error": "invented outage"}, 503)
                session.action("close_call", outcome=proposal[0], reason=proposal[1])
                if expected is None:
                    self.assert_refused(session)  # an account_loaded pair is not a failed-phase row
                else:
                    self.assertEqual((session.body["outcome"], session.body["reason"]), expected)

    def test_failed_rows_need_the_receipt_and_never_follow_identity_or_a_loaded_account(self):
        _, _, session = self.session(self.failed_values())
        self.verify(session)
        session.action("close_call", outcome="INFO_FAULT", reason="service_error")
        self.assertEqual((session.body["outcome"], session.body["reason"]), ("INFO_PART", "refused"))
        _, _, session = self.session(self.failed_values())
        self.verify(session)
        session.action("get_account")
        respond(session, "get_account", {"ok": True, "products": [PRODUCT]}, 200)
        self.assertFalse(session.state["account_failed"])
        session.action("close_call", outcome="INFO_FAULT", reason="service_error")
        self.assert_refused(session)

    def test_uncertain_negative_account_receipts_quarantine_without_a_close(self):
        cases = {"missing required field": (503, {"ok": False}),
                 "empty required field": (503, {"ok": False, "error": ""}),
                 "products on a failure": (503, {"ok": False, "error": "invented outage", "products": [PRODUCT]}),
                 "success status": (200, {"ok": False, "error": "invented outage"}),
                 "undeclared status": (502, {"ok": False, "error": "invented outage"}),
                 "status whose flag cannot be false": (409, {"ok": False, "error": "invented outage"}),
                 "status whose constant flag is true": (422, {"ok": False, "error": "invented outage"}),
                 "no transport status": (None, {"ok": False, "error": "invented outage"}),
                 "no body": (503, {}),
                 "positive flag": (503, {"ok": True, "error": "invented outage"})}
        for label, (status, data) in cases.items():
            with self.subTest(label):
                _, _, session = self.session(self.failed_values())
                self.verify(session)
                session.action("get_account")
                respond(session, "get_account", data, status)
                self.assertTrue(session.state["quarantined"])
                self.assertFalse(session.state["account_failed"])
                session.action("close_call", outcome="INFO_FAULT", reason="service_error")
                self.assert_refused(session)

    def test_goals_without_the_failed_phase_keep_unquarantined_negative_reads(self):
        policy, sources, api = self.information_values()
        declare_failure(api)
        for status in (200, 503):
            with self.subTest(status=status):
                _, _, session = self.session((policy, sources, api))
                self.verify(session)
                session.action("get_account")
                respond(session, "get_account", {"ok": False, "error": "invented outage"}, status)
                self.assertEqual(session.variables["bg_route"], "failure")
                self.assertFalse(session.state["quarantined"] or session.state["account_failed"])
                session.action("close_call", outcome="INFO_DONE", reason="")
                self.assertEqual((session.body["outcome"], session.body["reason"]), ("INFO_PART", "refused"))

    def test_failed_phase_shape_and_role_limits(self):
        guidance = execution_rule_guidance()["goal_close"]
        self.assertIn("account_failed", guidance)
        self.assertIn("is not an unsupported constraint", guidance)
        for renamed in (None, "4²9"):  # no definite failure shape remains; a non-ASCII digit status is none either
            policy, sources, api = self.failed_values()
            responses = api["paths"]["/get_account"]["post"]["responses"]
            response = responses.pop("503")
            if renamed:
                responses[renamed] = response
            with self.subTest(renamed=renamed), self.assertRaisesRegex(ExecutionRequirementsError, "account_failed needs get_account"):
                compile_execution_requirements(policy, sources, api)  # reported to policy repair, before any plan
        policy, sources, api = self.failed_values()
        rule(policy, "goal_close")["value"]["account_missing"] = rule(policy, "goal_close")["value"].pop("account_failed")
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "goal_close"):
            compile_execution_requirements(policy, sources, api)
        values = goal(["verify_identity", "get_account", "close_call"], {}, {"account_failed": [{"outcome": "paid", "requires": "none"}]},
                      close_reasons={"paid": [], "not_verified": ["identity_failed"], "not_reached": []}, resolve_all_products=False)
        with self.assertRaisesRegex(ExecutionRequirementsError, "non-collection outcomes"):
            compile_execution_requirements(*values)
        policy, sources, api = packet(local=True)
        add_rule(policy, "goal_close", {"account_failed": [{"outcome": "not_reached", "requires": "none"}]})
        with self.assertRaisesRegex(ExecutionRequirementsError, "goal_close cannot be combined"):
            compile_execution_requirements(policy, sources, api)

    def callback_semantic_keys(self, *, wire_keyed=False):
        # Shape of the three rejected extractions of run 20261011T063051905603Z
        # (policy.rejected-1.json sha256 5d0bdb24...c58183): wire_codes bind the
        # sourced meanings to opaque codes; close vocabularies cite the keys.
        policy, sources, api = synthetic(["schedule_callback", "close_call"])
        codes = {"not_reached": "CB_END", "callback": "CB_YES", "no_callback": "CB_NO"}
        close = {"not_reached": ["callback", "no_callback"]}
        if wire_keyed:
            close = {codes[k]: [codes[r] for r in v] for k, v in close.items()}
        rule(policy, "close_reasons")["value"] = close
        rule(policy, "wire_codes")["value"] = {"outcomes": {"not_reached": "CB_END"},
                                              "reasons": {"callback": "CB_YES", "no_callback": "CB_NO"}}
        rule(policy, "identity_close")["value"] = {scope: {"outcome": "not_reached", "reason": "no_callback"}
                                                   for scope in ("failed", "incomplete", "unreached")}
        add_rule(policy, "identity_close_choices", {scope: [
            {"outcome": "not_reached", "reason": "no_callback", "requires": "no_recorded_callback"},
            {"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"}]
            for scope in ("unreached", "incomplete")})
        add_rule(policy, "confirmation_actions", ["schedule_callback"])
        schema = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        schema["properties"]["outcome"]["enum"] = ["CB_END"]
        schema["properties"]["reason"]["enum"] = ["CB_YES", "CB_NO"]
        witness(policy, sources)
        return policy, sources, api

    def test_callback_close_vocabulary_may_cite_declared_semantic_keys(self):
        for wire_keyed in (False, True):
            with self.subTest(wire_keyed=wire_keyed):
                _, _, session = self.session(self.callback_semantic_keys(wire_keyed=wire_keyed))
                session.action("schedule_callback", date="2031-05-06", time="12:00")
                self.assertEqual(session.variables["bg_route"], "confirm")
                session.variables["bg_consent"] = "yes"
                session.code("bg_" + session.entries["schedule_callback"] + "_accept")
                session.result("schedule_callback", {"ok": True})
                session.action("close_call", outcome="CB_END", reason="CB_NO")
                self.assertEqual(session.body["outcome"], "CB_END")
                self.assertEqual(session.body["reason"], "CB_YES")  # protected record, not the claim

    def test_undeclared_close_codes_remain_rejected_with_an_actionable_message(self):
        policy, sources, api = self.callback_semantic_keys()
        rule(policy, "close_reasons")["value"]["no_promise"] = []  # canonical name, neither key nor wire
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "policy outcomes vocabulary has a code"):
            compile_execution_requirements(policy, sources, api)
        policy, sources, api = self.callback_semantic_keys()
        rule(policy, "close_reasons")["value"]["CB_END"] = ["CB_NO"]  # same outcome by key and by code
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "cites one outcome code twice"):
            compile_execution_requirements(policy, sources, api)
        policy, sources, api = self.callback_semantic_keys()
        rule(policy, "wire_codes")["value"]["outcomes"] = {"invented_meaning": "CB_END"}  # not a builder meaning
        witness(policy, sources)
        with self.assertRaisesRegex(ExecutionRequirementsError, "wire_codes.outcomes keys must be builder meanings"):
            compile_execution_requirements(policy, sources, api)

    def test_callback_goal_cannot_leave_close_codes_unbound(self):
        # The same codes compile in an identity-only goal. With schedule_callback every
        # close code needs a builder meaning, so callback evidence checks always apply.
        for category, close in (("outcomes", {"GOAL_DONE": []}),
                                ("reasons", {"not_verified": ["identity_failed", "GOAL_MATCH"]})):
            with self.subTest(category=category):
                compile_execution_requirements(*goal(["verify_identity", "close_call"], close))
                with self.assertRaisesRegex(ExecutionRequirementsError, "policy " + category + " vocabulary has a code"):
                    compile_execution_requirements(*goal(["verify_identity", "schedule_callback", "close_call"], close))
        with self.assertRaisesRegex(ExecutionRequirementsError, "policy reasons vocabulary has a code"):
            compile_execution_requirements(*goal(["verify_identity", "close_call"], {"GOAL_DONE": ["paid"]}))

    def test_recorded_callback_closes_only_through_a_recorded_callback_row(self):
        policy, sources, api = goal(["verify_identity", "schedule_callback", "close_call"], {"not_reached": ["refused"]})
        rule(policy, "identity_close")["value"]["unreached"]["reason"] = "refused"
        add_rule(policy, "goal_close", {"verified": [{"outcome": "not_reached", "reason": "refused", "requires": "none"}]})
        witness(policy, sources)
        for booked in (False, True):
            with self.subTest(booked=booked):
                _, _, session = self.session((policy, sources, api))
                self.verify(session)
                if booked:
                    session.action("schedule_callback", date="2031-05-06", time="12:00")
                    session.result("schedule_callback", {"ok": True})
                    self.assertEqual(len(session.state["callbacks"]), 1)
                session.action("close_call", outcome="not_reached", reason="refused")
                if booked:
                    self.assert_refused(session)  # a 'none' row cannot hide the recorded callback
                else:
                    self.assertEqual((session.body["outcome"], session.body["reason"]), ("not_reached", "refused"))

    def test_payment_goal_cannot_leave_close_codes_unbound(self):
        policy, sources, api = packet(local=True)
        rule(policy, "close_reasons")["value"]["GOAL_DONE"] = []
        extend_enum(api, "outcome", "GOAL_DONE")
        with self.assertRaisesRegex(ExecutionRequirementsError, "policy outcomes vocabulary has a code"):
            compile_execution_requirements(policy, sources, api)

    def test_goal_rows_are_refused_with_payment_roles_or_collection_meanings(self):
        policy, sources, api = packet(local=True)
        add_rule(policy, "goal_close", {"account_loaded": [{"outcome": "not_reached", "requires": "none"}]})
        with self.assertRaisesRegex(ExecutionRequirementsError, "goal_close cannot be combined"):
            compile_execution_requirements(policy, sources, api)
        for outcome in ("promise", "paid"):
            with self.subTest(outcome=outcome):
                values = goal(["verify_identity", "close_call"], {},
                              {"verified": [{"outcome": outcome, "requires": "none"}]},
                              close_reasons={"promise": [], "paid": [], "not_verified": ["identity_failed"], "not_reached": []})
                with self.assertRaisesRegex(ExecutionRequirementsError, "non-collection outcomes"):
                    compile_execution_requirements(*values)
        values = goal(["verify_identity", "close_call"], {"GOAL_DONE": []},
                      {"verified": [{"outcome": "GOAL_DONE", "requires": "none"},
                                    {"outcome": "GOAL_DONE", "requires": "no_recorded_callback"}]})
        with self.assertRaisesRegex(ExecutionRequirementsError, "goal closing rows are duplicated"):
            compile_execution_requirements(*values)

    def test_payment_close_still_requires_the_confirmed_recorded_promise(self):
        values = synthetic(["verify_identity", "get_account", "stage_promise", "commit_promises", "close_call"])
        _, _, session = self.session(values)
        self.verify(session)
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        session.action("stage_promise", product_id="GOAL-PRODUCT", amount="37.25", date="2031-05-06")
        session.action("close_call", outcome="promise", reason="")
        self.assert_refused(session)  # staged but unrecorded promise
        session.action("commit_promises")
        session.variables["bg_consent"] = "yes"
        session.code("bg_" + session.entries["commit_promises"] + "_accept")
        session.result("commit_promises", {"ok": True})  # no persisted-record identity
        self.assertTrue(session.state["quarantined"])
        session.action("close_call", outcome="promise", reason="")
        self.assert_refused(session)


@unittest.skipUnless(os.environ.get("FREYA_CALLBACK_FAILURE_RUN"), "original ignored run artifacts are local only")
class OriginalCallbackFailureReplay(unittest.TestCase):
    """Read-only replay of the failed run's own rejected extractions; no model call."""

    def test_rejected_extractions_now_compile_except_the_declared_unsupported_one(self):
        run = Path(os.environ["FREYA_CALLBACK_FAILURE_RUN"])
        inputs = Path(os.environ["FREYA_CALLBACK_INPUTS"])
        text = (inputs / "callback-policy.approved.md").read_text(encoding="utf-8")
        sources = [{"id": "brief-1", "file": "callback-policy.approved.md", "sha256": digest(text), "text": text}]
        api = read_json(inputs / "bank.openapi.json")
        for attempt in (1, 2, 3):
            policy = json.loads((run / f"policy.rejected-{attempt}.json").read_text(encoding="utf-8"))["policy"]
            with self.subTest(attempt=attempt):
                if attempt == 2:
                    with self.assertRaisesRegex(ExecutionRequirementsError, "unsupported constraints"):
                        compile_execution_requirements(policy, sources, api)
                else:
                    # The brief requires an explicit UTC offset on now; with that format declared they compile.
                    with self.assertRaisesRegex(ExecutionRequirementsError, "explicit UTC offset"):
                        compile_execution_requirements(policy, sources, api)
                    self.assertEqual(compile_execution_requirements(declare_offset_clock(policy, text, "brief-1"), sources, api)["actions"],
                                     ["close_call", "schedule_callback"])


@unittest.skipUnless(os.environ.get("FREYA_ACCOUNT_FAILURE_RUN"), "original ignored run artifacts are local only")
class OriginalAccountFailureReplay(unittest.TestCase):
    """Read-only replay of an information run's three rejected extractions; no model call.

    Each declared that a close after a definite negative account receipt, and its
    first-end-condition latch, had no protected goal_close phase."""

    def start(self, policy, sources, api):
        _, context, workflow = compiled(policy, sources, api)
        session = SealedSession(context, workflow)
        session.variables.update(first_name="Invented", last_name="Caller", title="Mx", now="2031-05-02T11:00:00+00:00")
        session.code("bg_init")
        catalog = {node["action"]: node for node in context["plan"]["nodes"].values() if node.get("node_type") == "guard_action"}
        self.assertEqual(set(catalog), {"verify_identity", "get_account", "close_call"})
        for factor in rule(policy, "identity_fields")["value"]:
            session.action("verify_identity", field=factor, answer="invented")
            respond(session, "verify_identity", {"accepted": True, "subject_ref": "SYNTHETIC-GOAL-SESSION",
                                                 "factor_tag": factor, "matched": True, "remaining": 2}, 200)
        self.assertEqual(session.state["identity_status"], "verified")
        return session

    def test_rejected_extractions_fit_the_failed_phase_and_its_latch(self):
        run = Path(os.environ["FREYA_ACCOUNT_FAILURE_RUN"])
        inputs = Path(__file__).resolve().parents[1] / "docs" / "development-inputs" / "s04-account-information"
        names = ("base-policy.md", "operations-email.txt", "privacy-memo.txt")
        texts = [(inputs / name).read_text(encoding="utf-8") for name in names]
        sources = [{"id": f"brief-{n}", "file": name, "sha256": digest(text), "text": text}
                   for n, (name, text) in enumerate(zip(names, texts), 1)]
        api = read_json(inputs / "bank.openapi.json")
        recorded = next(json.loads(line) for line in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                        if '"event": "inputs"' in line)
        self.assertEqual([s["sha256"] for s in sources], [s["sha256"] for s in recorded["sources"]])
        self.assertEqual(digest(api), recorded["tool_api_sha256"])
        files = sorted(run.glob("policy.rejected-*.json"))
        before = [path.read_bytes() for path in files]
        self.assertEqual(len(files), 3)
        for path in files:
            policy = json.loads(path.read_text(encoding="utf-8"))["policy"]
            with self.subTest(attempt=path.name):
                with self.assertRaisesRegex(ExecutionRequirementsError, "unsupported constraints"):
                    compile_execution_requirements(policy, sources, api)
                # In memory only: declarations emptied, and the verified pair that never follows a loaded
                # account (the one the declarations tie to the negative account receipt) becomes account_failed.
                rows = rule(policy, "goal_close")["value"]
                loaded = {(row["outcome"], row.get("reason", "")) for row in rows["account_loaded"]}
                failed = [row for row in rows["verified"] if (row["outcome"], row.get("reason", "")) not in loaded]
                stop = [row for row in rows["verified"] if (row["outcome"], row.get("reason", "")) in loaded]
                self.assertEqual((len(failed), len(stop)), (1, 1))
                rows.update(verified=stop, account_failed=failed)
                policy["unsupported_constraints"] = []
                pair = lambda row: (row["outcome"], row.get("reason"))
                # Before any account evidence the failed pair is never eligible.
                session = self.start(policy, sources, api)
                session.action("close_call", outcome=failed[0]["outcome"], reason=failed[0].get("reason", ""))
                self.assertEqual((session.body["completion_tag"], session.body.get("detail_tag")), pair(stop[0]))
                # A definite declared receipt latches the failed pair, even after a later stop.
                session = self.start(policy, sources, api)
                session.action("get_account")
                respond(session, "get_account", {"accepted": False, "subject_ref": "SYNTHETIC-GOAL-SESSION",
                                                 "fault": "invented outage"}, 503)
                self.assertTrue(session.state["account_failed"])
                session.action("get_account")
                self.assertFalse(session.variables["bg_body"])
                session.action("close_call", outcome=stop[0]["outcome"], reason=stop[0].get("reason", ""))
                self.assertEqual((session.body["completion_tag"], session.body.get("detail_tag")), pair(failed[0]))
                # A receipt missing its required fault text is uncertain: quarantine, no close.
                session = self.start(policy, sources, api)
                session.action("get_account")
                respond(session, "get_account", {"accepted": False, "subject_ref": "SYNTHETIC-GOAL-SESSION"}, 503)
                self.assertTrue(session.state["quarantined"])
                session.action("close_call", outcome=failed[0]["outcome"], reason=failed[0].get("reason", ""))
                self.assertFalse(session.variables["bg_body"] or session.state["close_attempted"])
        self.assertEqual(before, [path.read_bytes() for path in files])


if __name__ == "__main__":
    unittest.main()

"""Per-goal goal_close in multi-goal policies; invented codes, no model, bank or platform I/O."""
import copy
import hashlib
import json
import os
import unittest
from pathlib import Path

from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.safety import digest, read_json
from builder.validation import validate_code
from test_execution_requirements import add_rule, packet, rule
from test_goal_terminal_close import declare_failure, respond
from test_goal_requirements import PRODUCT, SealedSession, compiled, witness


SELECTOR = "initial_goal_code"
CONTACT = "GOAL_CONTACT_7"
COLLECT = "GOAL_COLLECT_3"
OTHER = "GOAL_OTHER_5"
CALLBACK = {"outcome": "not_reached", "reason": "callback", "requires": "recorded_callback"}
NO_CALLBACK = {"outcome": "not_reached", "reason": "no_callback", "requires": "no_recorded_callback"}
SCOPED = {"selector": SELECTOR, "goals": [CONTACT], "verified": [CALLBACK, NO_CALLBACK],
          "account_loaded": [CALLBACK, NO_CALLBACK]}
RESOLUTION = {"selector": SELECTOR, "by_value": {COLLECT: True, CONTACT: False, OTHER: False}}
COLLECTION = (("stage_promise", {"product_id": "GOAL-PRODUCT", "amount": "37.25", "date": "2031-05-06"}),
              ("stage_note", {"product_id": "GOAL-PRODUCT", "reason": "refused"}),
              ("stage_transfer", {"queue": "helpdesk"}),
              ("commit_promises", {}), ("finalize_notes", {}), ("commit_transfer", {}))


def values(goal_close=SCOPED, *, resolution=RESOLUTION, variables=(SELECTOR,), wire=None):
    """Every role is selected, so the unscoped goal_close form is refused."""
    policy, sources, api = packet()
    rule(policy, "close_reasons")["value"]["not_reached"] = ["callback", "no_callback"]
    rule(policy, "identity_close")["value"]["unreached"]["reason"] = "no_callback"
    rule(policy, "wire_codes")["value"]["reasons"].update(callback="callback", no_callback="no_callback")
    if wire is not None:
        rule(policy, "wire_codes")["value"][SELECTOR] = wire
    schema = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"]["reason"]["enum"].extend(["callback", "no_callback"])
    add_rule(policy, "call_variables", list(variables))
    rule(policy, "resolve_all_products")["value"] = copy.deepcopy(resolution)
    add_rule(policy, "goal_close", goal_close)
    witness(policy, sources)
    return policy, sources, api


def call(packed, goal, **variables):
    _, context, workflow = compiled(*packed)
    session = SealedSession(context, workflow)
    session.variables.update(variables)
    session.variables[SELECTOR] = goal
    session.code("bg_init")
    return session


def verify(session):
    for factor in ("postal_code", "memorable_word"):
        session.action("verify_identity", field=factor, answer="synthetic-correct")
        session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 4})


def book(session, date="2031-05-06", time="12:00"):
    session.action("schedule_callback", date=date, time=time)
    session.result("schedule_callback", {"ok": True})
    assert len(session.state["callbacks"]) == 1


class PerGoalCloseTests(unittest.TestCase):
    def assert_refused(self, session):
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.variables["bg_body"])
        self.assertFalse(session.state["close_attempted"])

    def account(self, session):
        session.action("get_account")
        session.result("get_account", {"ok": True, "products": [PRODUCT]})
        self.assertTrue(session.state["account_loaded"])

    def test_listed_goal_closes_on_its_sourced_row_in_each_phase(self):
        packed = values()
        for loaded in (False, True):
            for booked in (False, True):
                with self.subTest(loaded=loaded, booked=booked):
                    session = call(packed, CONTACT)
                    verify(session)
                    if loaded:
                        self.account(session)
                    if booked:
                        book(session)
                    # The proposal names the other row; protected evidence decides.
                    session.action("close_call", outcome="not_reached", reason="no_callback" if booked else "callback")
                    self.assertEqual(session.variables["bg_route"], "allow")
                    self.assertEqual((session.body["outcome"], session.body["reason"]),
                                     ("not_reached", "callback" if booked else "no_callback"))
                    session.result("close_call", {"ok": True})
                    self.assertTrue(session.state["closed"])
                    session.action("close_call", outcome="not_reached", reason="no_callback")
                    self.assertEqual(session.variables["bg_route"], "blocked")

    def test_collecting_and_unlisted_goals_keep_evidence_only_closes(self):
        packed = values()
        for goal in (COLLECT, OTHER):
            for booked in (False, True):
                with self.subTest(goal=goal, booked=booked):
                    session = call(packed, goal)
                    verify(session)
                    if booked:
                        book(session)
                    session.action("close_call", outcome="not_reached", reason="callback" if booked else "no_callback")
                    self.assert_refused(session)
        session = call(packed, COLLECT)
        verify(session)
        self.account(session)
        before = session.variables["bg_state"]
        session.action("close_call", outcome="not_reached", reason="no_callback")
        self.assert_refused(session)  # the product must still be resolved
        session.variables["bg_state"] = before  # a refused close ends its call; resume the state before it
        session.action("stage_promise", product_id="GOAL-PRODUCT", amount="37.25", date="2031-05-06")
        session.action("commit_promises")
        session.variables["bg_consent"] = "yes"
        session.code("bg_" + session.entries["commit_promises"] + "_accept")
        session.result("commit_promises", {"ok": True, "promise_id": "synthetic-record"})
        session.action("close_call", outcome="not_reached", reason="no_callback")
        self.assertEqual(session.body["outcome"], "promise")  # its protected fact, never a goal row

    def test_listed_goal_cannot_use_promise_note_or_transfer_roles(self):
        packed = values()
        session = call(packed, CONTACT)
        verify(session)
        self.account(session)
        before = session.state
        for action, inputs in COLLECTION:
            with self.subTest(action=action):
                session.action(action, **inputs)
                self.assertEqual(session.variables["bg_route"], "blocked")
                self.assertFalse(session.variables["bg_body"])
                self.assertEqual(session.state, before)
        for goal in (COLLECT, OTHER):
            for action in ("stage_promise", "stage_transfer"):
                with self.subTest(goal=goal, action=action):
                    session = call(packed, goal)
                    verify(session)
                    self.account(session)
                    session.action(action, **dict(COLLECTION)[action])
                    self.assertEqual(session.variables["bg_route"], "done")

    def test_rows_never_carry_protected_meanings_or_unrecorded_callback_success(self):
        for outcome in ("promise", "paid", "transferred", "no_promise"):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ExecutionRequirementsError, "non-collection outcomes"):
                compile_execution_requirements(*values(dict(SCOPED, verified=[{"outcome": outcome, "requires": "none"}])))
        unproven = dict(SCOPED, verified=[dict(CALLBACK, requires="none")])
        with self.assertRaisesRegex(ExecutionRequirementsError, "matching callback evidence"):
            compile_execution_requirements(*values(unproven))
        with self.assertRaisesRegex(ExecutionRequirementsError, "goal_close cannot be combined"):
            compile_execution_requirements(*values({"verified": [NO_CALLBACK]}))

    def test_scope_covers_only_declared_goals_without_product_resolution(self):
        other = "initial_other_code"
        unresolved = "must map to false in the same-selector resolve_all_products rule"
        cases = {
            "collecting goal": (values(dict(SCOPED, goals=[CONTACT, COLLECT])), unresolved),
            "goal outside the resolution rule": (values(dict(SCOPED, goals=["GOAL_UNKNOWN"])), unresolved),
            "every call resolves products": (values(resolution=True), unresolved),
            "every call resolves overdue products": (values(resolution={"products": "overdue"}), unresolved),
            "resolution by another selector": (values(resolution=dict(RESOLUTION, selector=other),
                                                      variables=(SELECTOR, other)), unresolved),
            "undeclared selector": (values(resolution=dict(RESOLUTION, selector=other), variables=(other,)),
                                    "source-declared read-only call input"),
            "codes collide through the selector category": (values(dict(SCOPED, goals=["contact", CONTACT]),
                                                                   wire={"contact": CONTACT}), "collide"),
            "goal outside the selector category": (values(dict(SCOPED, goals=["GOAL_UNKNOWN"]), resolution=False,
                                                          wire={"contact": CONTACT, "collect": COLLECT}),
                                                   "keys or codes of the wire_codes category"),
        }
        for name, (packed, message) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ExecutionRequirementsError, message):
                compile_execution_requirements(*packed)
        policy, sources, api = values()
        rule(policy, "goal_close")["source"]["quote"] = "Invented goal restrictions"  # verbatim, but no selector
        with self.assertRaisesRegex(ExecutionRequirementsError, "named literally in its own sourced rule"):
            compile_execution_requirements(policy, sources, api)
        for shape in ({"selector": SELECTOR, "verified": [NO_CALLBACK]}, {"goals": [CONTACT], "verified": [NO_CALLBACK]},
                      dict(SCOPED, goals=[]), dict(SCOPED, goals=[CONTACT, CONTACT]), dict(SCOPED, goals=[7]),
                      dict(SCOPED, goals=CONTACT), dict(SCOPED, selector="bg_state"), {"selector": SELECTOR, "goals": [CONTACT]}):
            with self.subTest(shape=shape), self.assertRaises(ExecutionRequirementsError):
                compile_execution_requirements(*values(shape))

    def test_failed_account_latch_arms_only_in_listed_goal_calls(self):
        packed = values(dict(SCOPED, account_failed=[CALLBACK, NO_CALLBACK]))
        declare_failure(packed[2])
        for goal in (CONTACT, COLLECT, OTHER):
            for status in (200, 502, 503):  # ok=false on 200, an undeclared status, the declared definite one
                with self.subTest(goal=goal, status=status):
                    session = call(packed, goal)
                    verify(session)
                    session.action("get_account")
                    respond(session, "get_account", {"ok": False, "error": "invented outage"}, status)
                    if goal == CONTACT:
                        # A listed call latches: the definite receipt is final, any other quarantines.
                        self.assertEqual((session.state["account_failed"], session.state["quarantined"]),
                                         (status == 503, status != 503))
                        if status == 503:
                            session.action("close_call", outcome="", reason="")
                            self.assertEqual((session.body["outcome"], session.body["reason"]), ("not_reached", "no_callback"))
                    else:
                        # Unlisted calls keep the unlatched, unquarantined negative read and may read again.
                        self.assertEqual(session.variables["bg_route"], "failure")
                        self.assertFalse(session.state["quarantined"] or session.state["account_failed"])
                        session.action("get_account")
                        self.assertEqual(session.variables["bg_route"], "allow")

    def test_selector_category_may_name_listed_goals_by_semantic_key(self):
        wire = {"contact": CONTACT, "collect": COLLECT, "other": OTHER}
        packed = values(dict(SCOPED, goals=["contact"]), wire=wire,
                        resolution={"selector": SELECTOR, "by_value": {"contact": False, "collect": True, "other": False}})
        session = call(packed, CONTACT)
        verify(session)
        session.action("close_call", outcome="", reason="")
        self.assertEqual(session.body["reason"], "no_callback")
        session = call(packed, COLLECT)
        verify(session)
        session.action("close_call", outcome="", reason="")
        self.assert_refused(session)

    def test_sealed_initial_goal_code_decides_and_bad_codes_quarantine(self):
        packed = values()
        for initial, later in ((CONTACT, COLLECT), (COLLECT, CONTACT)):
            with self.subTest(initial=initial):
                session = call(packed, initial)
                verify(session)
                session.variables[SELECTOR] = later
                session.action("close_call", outcome="not_reached", reason="no_callback")
                self.assertEqual(session.state["call_inputs"], {SELECTOR: initial})
                if initial == CONTACT:
                    self.assertEqual(session.body["reason"], "no_callback")
                else:
                    self.assert_refused(session)
        for value in ("", "GOAL_UNKNOWN"):
            with self.subTest(value=value):
                session = call(packed, value)
                self.assertEqual(session.variables["bg_quarantined"], "yes")
                self.assertEqual(session.target("bg_init"), "bg_quarantine")
        # Without a same-selector resolution rule an unlisted code is simply not listed.
        unscoped_resolution = values(resolution=False)
        for goal, listed in ((CONTACT, True), ("GOAL_UNKNOWN", False)):
            with self.subTest(goal=goal):
                session = call(unscoped_resolution, goal)
                verify(session)
                session.action("close_call", outcome="not_reached", reason="no_callback")
                if listed:
                    self.assertEqual(session.body["reason"], "no_callback")
                else:
                    self.assert_refused(session)

    def test_compiled_code_stays_within_the_local_validation_bound(self):
        _, _, workflow = compiled(*values())
        for name, node in workflow["nodes"].items():
            if node["node_type"] == "code":
                with self.subTest(name=name):
                    self.assertEqual(validate_code(node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]), [])


@unittest.skipUnless(os.environ.get("FREYA_TRAIN1_PROBE_RUN"), "retained probe artifacts are local only")
class RetainedProbeDeclarationReplay(unittest.TestCase):
    """Read-only replay of probe run 20261011T094031059212Z (declaration [6]); no model call."""

    @classmethod
    def setUpClass(cls):
        cls.run_dir = Path(os.environ["FREYA_TRAIN1_PROBE_RUN"])
        inputs = Path(os.environ["FREYA_TRAIN1_INPUTS"])
        cls.before = cls.hashes()
        text = (inputs / "current-policy.md").read_text(encoding="utf-8")
        cls.sources = [{"id": "brief-1", "file": "current-policy.md", "sha256": digest(text), "text": text}]
        cls.api = read_json(inputs / "bank.openapi.json")
        cls.policies = [json.loads((cls.run_dir / f"policy.rejected-{n}.json").read_text(encoding="utf-8"))["policy"]
                        for n in (1, 2, 3)]

    @classmethod
    def hashes(cls):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(cls.run_dir.iterdir()) if path.is_file()}

    def tearDown(self):
        self.assertEqual(self.hashes(), self.before)

    def reshaped(self, policy, scoped=True):
        """Declarations emptied in memory (the probe's strip check) plus one goal_close rule."""
        policy = copy.deepcopy(policy)
        policy["unsupported_constraints"] = []
        resolution = rule(policy, "resolve_all_products")
        selector = resolution["value"]["selector"]
        contact = rule(policy, "wire_codes")["value"]["goals"]["contact_callback"]
        # The brief's only callback-success pair; it names no contact close without a callback.
        value = {"verified": [dict(CALLBACK)], "account_loaded": [dict(CALLBACK)]}
        if scoped:
            value.update(selector=selector, goals=[contact])
        policy["rules"].append({"id": "contact-goal-close", "kind": "goal_close", "value": value,
                                "source": copy.deepcopy(resolution["source"])})
        return policy, selector, contact

    def session(self, context, workflow, selector, goal):
        session = SealedSession(context, workflow)
        session.variables.update(first_name="Synthetic", last_name="Caller", title="Mx", now="2026-11-23T11:00")
        session.variables[selector] = goal
        session.code("bg_init")
        for field in ("date_of_birth", "phone_last4"):
            session.action("verify_identity", field=field, answer="synthetic-correct")
            session.result("verify_identity", {"ok": True, "verified": True, "wrong_answers_left": 3})
        self.assertEqual(session.state["identity_status"], "verified")
        return session

    def test_retained_policies_fail_only_on_declarations_and_six_names_the_refused_goal_close(self):
        for attempt, policy in enumerate(self.policies, 1):
            with self.subTest(attempt=attempt):
                with self.assertRaises(ExecutionRequirementsError) as caught:
                    compile_execution_requirements(policy, self.sources, self.api)
                self.assertEqual(caught.exception.errors, ("policy contains unsupported constraints",))
                self.assertIn("goal_close is unavailable with the globally selected promise/note/transfer roles",
                              policy["unsupported_constraints"][6])

    def test_scoped_goal_close_gives_the_non_collecting_goal_its_close_and_no_other_goal(self):
        product = {"product_id": "P-1", "type": "credit_card", "past_due": 120.0, "days_late": 5, "minimum_payment": 25.0}
        for attempt, retained in enumerate(self.policies, 1):
            with self.subTest(attempt=attempt):
                unscoped, _, _ = self.reshaped(retained, scoped=False)
                with self.assertRaisesRegex(ExecutionRequirementsError, "goal_close cannot be combined"):
                    compile_execution_requirements(unscoped, self.sources, self.api)
                policy, selector, contact = self.reshaped(retained)
                requirements, context, workflow = compiled(policy, self.sources, self.api)
                self.assertEqual(len(requirements["actions"]), 10)
                others = [code for code in rule(policy, "resolve_all_products")["value"]["by_value"] if code != contact]
                self.assertEqual(len(others), 2)
                for loaded in (False, True):
                    session = self.session(context, workflow, selector, contact)
                    if loaded:
                        session.action("get_account")
                        session.result("get_account", {"ok": True, "products": [product]})
                        for action, inputs in (("stage_promise", {"product_id": "P-1", "amount": "40.00", "date": "2026-11-24"}),
                                               ("stage_transfer", {"queue": "support"})):
                            session.action(action, **inputs)
                            self.assertEqual(session.variables["bg_route"], "blocked")
                    session.action("close_call", outcome="", reason="")
                    self.assertEqual(session.variables["bg_route"], "blocked")  # no sourced close without a callback
                    self.assertFalse(session.state["close_attempted"])
                    book(session, "2026-11-23", "15:00")
                    session.action("close_call", outcome="", reason="")
                    self.assertEqual(session.variables["bg_route"], "allow")
                    self.assertEqual((session.body["outcome"], session.body["reason"]), ("not_reached", "callback"))
                for goal in others:
                    session = self.session(context, workflow, selector, goal)
                    book(session, "2026-11-23", "15:00")
                    session.action("close_call", outcome="not_reached", reason="callback")
                    self.assertEqual(session.variables["bg_route"], "blocked")
                    self.assertFalse(session.state["close_attempted"])
                    session.action("get_account")
                    session.result("get_account", {"ok": True, "products": [product]})
                    session.action("stage_promise", product_id="P-1", amount="40.00", date="2026-11-24")
                    self.assertEqual(session.variables["bg_route"], "done")


if __name__ == "__main__":
    unittest.main()

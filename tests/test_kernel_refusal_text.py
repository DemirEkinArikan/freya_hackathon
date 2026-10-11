"""Kernel refusals stated to the policy model are true of the sealed kernel. Invented data only.

Each sentence reaches the policy prompt (controller_state_effects or rule_shapes), is accepted as a
controller_stricter quote, and is pinned below to the existing kernel or compiler path that refuses.
"""
import ast
import copy
import json
import unittest

from builder.build import make_policy_prompt
from builder.contract import compile_policy_contract
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import (_FIELDS, _KERNEL, _RESULTS, GuardCompileError, _catalogue, _profile,
                            compile_guarded_workflow, run_guard_step)
from builder.policy import _profile_errors, execution_rule_guidance
from test_categorised_declarations import declared, entry
from test_closing_choices import CHOICES, context as closing_context
from test_execution_requirements import add_rule, packet, rule
from test_guards import fixture

CALLBACK_TIME = "A time that is not HH:MM or falls outside the callback_window start to end, inclusive, is refused."
CALLBACK_DATE = ("A date before the first eligible day (today with allow_today, otherwise the next business day), after "
                 "the last eligible day (the next_business_days-th business day after today, or today when it is 0), or "
                 "on a non-business day other than an allowed today is refused.")
CALLBACK_TODAY = ("A callback for today is refused unless the supplied now includes a time and the requested time is "
                  "strictly later than it.")
PRECISION = "An amount with more than two decimal places is refused."
OPEN_BAND = "There is no interval without an integer max (no open-ended band)"
NO_BAND = "a promise for a product whose days_late is in no band is refused"
CHOICE = ("A choice whose outcome differs from identity_close[scope].outcome, or whose reason is outside that "
          "outcome's sourced close_reasons vocabulary, is refused.")
FACTS = ("paid only once notes are finalized and every loaded product's disposition (its recorded note when "
         "record_notes is true, otherwise its staged disposition) is paid, and no_promise once notes are finalized, "
         "with the reason of the first non-paid disposition in product order")
CLOSE_FACT = ("A transferred, promise, paid or no_promise close other than its derived fact is refused, and several "
              "derived facts are refused unless close_precedence ranks one of them.")
PRODUCT_KEYS = "a get_account product schema lacking any of the four is refused"
OVERLAP = ('When several such conditions hold at once, the workflow proposes one eligible requires "none" row, '
           "following the source's order when it gives one; the controller accepts it, and the overlap needs no "
           "declaration.")
# Sentence -> where the policy model reads it: (action, controller_state_effects key) or a rule shape kind.
STATED = [(CALLBACK_TIME, ("schedule_callback", "rejections")), (CALLBACK_DATE, ("schedule_callback", "rejections")),
          (CALLBACK_TODAY, ("schedule_callback", "rejections")), (PRECISION, ("stage_promise", "rejections")),
          (FACTS, ("close_call", "accepted_staging")), (CLOSE_FACT, ("close_call", "rejections")),
          (OPEN_BAND, "band_ranges"), (NO_BAND, "band_ranges"), (CHOICE, "identity_close_choices"),
          (PRODUCT_KEYS, "product_fields"), (OVERLAP, "identity_close_choices"), (OVERLAP, "goal_close")]


class StatedKernelRefusalTests(unittest.TestCase):
    def setUp(self):
        context = fixture()  # 2031-05-02 is a Friday and 2031-05-05 a holiday; callbacks 09:15-16:45, two days
        self.profile = _profile(context["contract"], context["execution_policy"], context["plan"], None)
        self.catalog = _catalogue(context["contract"], context["openapi"], None)
        self.state = self.step("init", customer_id="invented-customer", now="2031-05-02T10:00")[0]

    def step(self, phase, action="", inputs=None, result=None, state=None, profile=None, **kwargs):
        return run_guard_step(phase, state or getattr(self, "state", None), action, inputs, result,
                              profile=profile or self.profile, catalog=self.catalog, **kwargs)

    def account(self):
        for field in self.profile["identity_fields"]:
            self.state = self.step("prepare", "verify_identity", {"field": field, "answer": "supplied"})[0]
            self.state = self.step("result", result={"ok": True, "verified": True, "wrong_answers_left": 4})[0]
        self.state = self.step("prepare", "get_account")[0]
        self.state = self.step("result", result={"ok": True, "products": [
            {"product_id": "stable-a", "type": "revolving", "days_late": 8, "past_due": 300, "minimum_due": 37.25},
            {"product_id": "stable-b", "type": "fixed", "days_late": 28, "past_due": 600, "instalment_due": 82.10}]})[0]

    def test_each_sentence_reaches_the_policy_prompt_and_is_a_quotable_refusal(self):
        policy, sources, api = packet()
        interface = json.loads(make_policy_prompt(sources, api).split("AUTHORIZED INPUTS:\n", 1)[1])["execution_interface"]
        for sentence, place in STATED:
            with self.subTest(sentence=sentence, place=place):
                if isinstance(place, tuple):
                    self.assertIn(sentence, " ".join(interface["actions"][place[0]]["controller_state_effects"][place[1]]))
                else:
                    self.assertIn(sentence, interface["rule_shapes"][place])
                limit = entry("controller_stricter", rule_ids=["close_reasons"], controller_effect=sentence)
                self.assertEqual(compile_policy_contract(*declared(limit))["declared_limitations"], [limit])

    def test_callback_refusals_are_exactly_the_kernel_branch_refusals(self):
        prepare = next(node for node in ast.parse(_KERNEL).body
                       if isinstance(node, ast.FunctionDef) and node.name == "guard_prepare")
        branch, = [node for node in ast.walk(prepare) if isinstance(node, ast.If)
                   and ast.unparse(node.test) == "action == 'schedule_callback'"
                   and any(isinstance(item, ast.Raise) for statement in node.body for item in ast.walk(statement))]
        refusals = {item.exc.args[0].value for statement in branch.body for item in ast.walk(statement)
                    if isinstance(item, ast.Raise)}
        stated = {"callback time outside policy window": CALLBACK_TIME,
                  "callback date outside policy window": CALLBACK_DATE,
                  "today callback requires a supplied clock and later time": CALLBACK_TODAY}
        self.assertEqual(refusals, set(stated))
        today = dict(self.profile, callback_window=dict(self.profile["callback_window"], allow_today=True))
        dated = self.step("init", state={}, customer_id="invented-customer", now="2031-05-02")[0]
        cases = [(None, None, "2031-05-06", "09:14", "callback time"), (None, None, "2031-05-06", "16:46", "callback time"),
                 (None, None, "2031-05-06", "12:00:00", "callback time"),
                 (None, None, "2031-05-02", "12:00", "callback date"),  # today without allow_today
                 (None, None, "2031-05-08", "12:00", "callback date"),  # after the second business day
                 (today, None, "2031-05-03", "12:00", "callback date"),  # a Saturday inside the range
                 (today, None, "2031-05-02", "10:00", "today callback"), (today, dated, "2031-05-02", "12:00", "today callback"),
                 (None, None, "2031-05-06", "09:15", None), (None, None, "2031-05-07", "16:45", None),
                 (today, None, "2031-05-02", "10:01", None)]
        for profile, state, day, at, refusal in cases:
            with self.subTest(day=day, at=at, allow_today=profile is not None, clock=state is None):
                inputs = {"date": day, "time": at}
                if refusal is None:
                    self.assertEqual(self.step("prepare", "schedule_callback", inputs, state=state, profile=profile)[1], "allow")
                else:
                    with self.assertRaisesRegex(ValueError, refusal):
                        self.step("prepare", "schedule_callback", inputs, state=state, profile=profile)

    def test_promise_precision_and_band_refusals(self):
        self.account()
        stage = {"product_id": "stable-a", "amount": "40.001", "date": "2031-05-06"}
        with self.assertRaisesRegex(ValueError, "amount must have at most two decimal places"):
            self.step("prepare", "stage_promise", stage)
        self.assertEqual(self.step("prepare", "stage_promise", dict(stage, amount="40.01"))[1], "done")
        self.state["products"][0]["days_late"] = 0  # below every band, still first in product order
        with self.assertRaisesRegex(ValueError, "product has no unique policy band"):
            self.step("prepare", "stage_promise", dict(stage, amount="40.01"))
        for bands in ([{"name": "open", "min": 31}], [{"name": "open", "min": 31, "max": None}]):
            self.assertTrue(_profile_errors("band_ranges", bands))
        self.assertEqual(_profile_errors("band_ranges", [{"name": "open", "min": 31, "max": 99}]), [])

    def test_identity_choice_with_another_outcome_or_reason_is_refused(self):
        compile_guarded_workflow(**closing_context(CHOICES)[0])
        for choice, refusal in (({"outcome": "not_reached", "reason": "declined", "requires": "none"},
                                 "identity closing choice outcome must match its fixed scope"),
                                ({"outcome": "not_verified", "reason": "wrong_number", "requires": "none"},
                                 "identity closing choice conflicts with allowed closing reasons")):
            with self.subTest(choice=choice), self.assertRaisesRegex(GuardCompileError, refusal):
                compile_guarded_workflow(**closing_context(dict(CHOICES, incomplete=[choice]))[0])

    def test_close_facts_are_derived_as_stated(self):
        self.account()
        finalized = dict(self.state, notes_finalized=True)
        for notes, expected in (({"stable-a": "paid", "stable-b": "paid"}, ("paid", None)),
                                ({"stable-a": "paid", "stable-b": "dispute"}, ("no_promise", "dispute")),
                                ({"stable-a": "refused", "stable-b": "dispute"}, ("no_promise", "refused"))):
            with self.subTest(notes=notes):
                body = self.step("prepare", "close_call", {"outcome": "paid"}, state=dict(finalized, recorded_notes=notes))[2]
                self.assertEqual((body["outcome"], body.get("reason")), expected)
        with self.assertRaisesRegex(ValueError, "closing lacks protected evidence"):  # paid only once finalized
            self.step("prepare", "close_call", {"outcome": "paid"},
                      state=dict(self.state, recorded_notes={"stable-a": "paid", "stable-b": "paid"}))
        both = dict(self.state, recorded_promises={"stable-a": {"promise_id": "invented-receipt"}},
                    transfer={"status": "recorded", "queue": "helpdesk", "reason": "dispute"})
        with self.assertRaisesRegex(ValueError, "ambiguous unranked protected facts"):
            self.step("prepare", "close_call", {}, state=both, profile=dict(self.profile, close_precedence=["paid"]))
        body = self.step("prepare", "close_call", {}, state=both, profile=dict(self.profile, close_precedence=["promise"]))[2]
        self.assertEqual(body["outcome"], "promise")
        # The kernel's last guard: a row naming a protected outcome (refused at compile time) cannot close.
        table = dict(self.profile["identity_close"], incomplete={"outcome": "paid"})
        partial = self.step("init", state={}, customer_id="invented-customer", now="2031-05-02T10:00")[0]
        with self.assertRaisesRegex(ValueError, "protected closing outcome lacks its recorded fact"):
            self.step("prepare", "close_call", {}, state=dict(partial, verified_fields=["postal_code"]),
                      profile=dict(self.profile, identity_close=table))

    def test_field_keys_match_the_kernel_and_errors_name_the_missing_key(self):
        shapes = execution_rule_guidance()
        for role, keys in _FIELDS.items():
            self.assertIn(role + ": " + ", ".join(keys), shapes["action_fields"])
        for role, keys in _RESULTS.items():
            self.assertIn(role + ": " + ", ".join(keys), shapes["result_fields"])
        policy, sources, api = packet()
        products = api["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        items = products["properties"]["products"]["items"]["properties"]
        items["overdue_total"] = items.pop("past_due")
        add_rule(policy, "product_fields", {"overdue_amount": "overdue_total"})
        with self.assertRaises(ExecutionRequirementsError) as caught:
            compile_execution_requirements(policy, sources, api)
        self.assertIn("get_account: product field aliases require explicit active product_fields for past_due (its keys "
                      "are the semantic names product_id, type, days_late and past_due, each mapped to the get_account "
                      "product field with that meaning)", caught.exception.errors)
        rule(policy, "product_fields")["value"] = {"past_due": "overdue_total"}
        self.assertTrue(compile_execution_requirements(policy, sources, api)["actions"])

    def test_overlapping_conversation_only_rows_accept_any_one_proposal(self):
        context = closing_context(CHOICES)[0]
        profile = _profile(context["contract"], context["execution_policy"], context["plan"], context["bindings"])
        catalog = _catalogue(context["contract"], context["openapi"], context["bindings"])
        maps = context["bindings"]["categories"]
        state = run_guard_step("init", None, profile=profile, catalog=catalog, customer_id="invented", now="2031-05-02")[0]
        def close(inputs):
            return run_guard_step("prepare", state, "close_call", inputs, profile=profile, catalog=catalog, maps=maps)
        for reason in ("wrong_number", "deceased"):  # both said at once: either proposal closes
            self.assertEqual(close({"outcome": "not_reached", "reason": reason})[2]["reason"], reason)
        with self.assertRaisesRegex(ValueError, "lacks one declared eligible choice"):
            close({"outcome": "not_reached", "reason": "declined"})
        rows = [{"outcome": "not_verified", "reason": reason, "requires": "none"} for reason in ("identity_failed", "refused")]
        goal = dict(self.profile, goal_close={"verified": rows},
                    close_reasons=dict(self.profile["close_reasons"], not_verified=["identity_failed", "refused"]))
        verified = dict(self.state, identity_status="verified", verified_fields=list(self.profile["identity_fields"]))
        for reason in ("identity_failed", "refused"):
            body = self.step("prepare", "close_call", {"outcome": "not_verified", "reason": reason}, state=verified, profile=goal)[2]
            self.assertEqual(body["reason"], reason)
        with self.assertRaisesRegex(ValueError, "goal closing proposal lacks one declared eligible choice"):
            self.step("prepare", "close_call", {"outcome": "not_verified", "reason": "dispute"}, state=verified, profile=goal)


if __name__ == "__main__":
    unittest.main()

"""Identity answers in the tool API's own format (hosted readiness round 1, K2): no model, bank or platform I/O."""
import json
import unittest
from pathlib import Path

from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements
from builder.fallback_plan import deterministic_plan
from builder.guards import _answer_formats
import test_goal_requirements as goal_requirements
from test_execution_requirements import packet
from test_identity_lifecycle import CLASSES, F1, F2, receipt, values
from test_plan_fallback import fallback_errors

KIT = json.loads((Path(__file__).resolve().parents[1] / "build-the-builder/tools/openapi.json").read_text())
KIT_ANSWER = KIT["paths"]["/verify_identity"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["answer"]["description"]
STATED = "For " + F1 + ": the date as YYYY-MM-DD; unreadable values are not counted. For " + F2 + ": four digits."


def stated(api, text=STATED):
    api["paths"]["/verify_identity"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["answer"]["description"] = text
    return api


class IdentityAnswerFormatTests(unittest.TestCase):
    session = goal_requirements.GoalRequirementsTests.session

    def stated_session(self, text=STATED, **kinds):
        policy, sources, api = values(**kinds)
        return self.session((policy, sources, stated(api, text) if text else api))[2]

    def assert_refused(self, session, field, answer, reason="missing_input"):
        before = session.state
        session.action("verify_identity", field=field, answer=answer)
        self.assertEqual((session.variables["bg_route"], session.variables["bg_body"]), ("blocked", ""))
        self.assertEqual(session.state, before)
        self.assertEqual(json.loads(session.variables["bg_facts"])["latest_action"],
                         {"action": "verify_identity", "status": "refused", "reason": reason})

    def assert_sent(self, session, field, answer):
        session.action("verify_identity", field=field, answer=answer)
        self.assertEqual(session.variables["bg_route"], "allow")
        self.assertEqual((session.body["field"], session.body["answer"]), (field, answer))

    def test_formats_are_read_from_the_api_only_where_exact(self):
        self.assertEqual(_answer_formats(KIT_ANSWER, ["date_of_birth", "phone_last4"]),
                         {"date_of_birth": "calendar_date", "phone_last4": 4})
        self.assertEqual(_answer_formats(STATED, [F1, F2]), {F1: "calendar_date", F2: 4})
        self.assertEqual(_answer_formats("For pin: the 6-digit code; dob: YYYY-MM-DD", ["pin", "dob"]), {"pin": 6, "dob": "calendar_date"})
        for text in ("For pin: four or six digits.", "For pin: at least four digits.", "For pin: four digits, e.g. 0042.",
                     "For dob: YYYY-MM-DD or DD/MM/YYYY.", "For dob: DD.MM.YYYY.", "The date as YYYY-MM-DD.", ""):
            with self.subTest(text=text):
                self.assertEqual(_answer_formats(text, ["pin", "dob"]), {})
        self.assertEqual(_answer_formats(STATED, None), {})

    def test_fallback_identity_answer_carries_the_api_format(self):
        for text, expected in ((KIT_ANSWER, "The tool API states: " + KIT_ANSWER), (None, "in the format the bank rules require")):
            with self.subTest(text=text):
                policy, sources, api = packet()
                if text:
                    stated(api, text)
                plan, errors, _ = fallback_errors(policy, sources, api)
                self.assertEqual(errors, [])
                entry = next(e for e in plan["nodes"]["dialogue"]["extraction_schema"] if e["name"] == "proposal_identity_answer")
                self.assertIn(expected, entry["description"])
                contract = compile_policy_contract(policy, sources, api)
                self.assertEqual(plan, deterministic_plan(contract, compile_execution_requirements(policy, sources, api)))

    def test_an_answer_outside_the_stated_format_is_never_sent(self):
        session = self.stated_session()
        for answer in ("thirtieth of January nineteen eighty", "1980-02-30", "19800130", "1980-1-30", " 1980-01-30",
                       "1980-01-30\n", "1980-01-30T00:00"):
            with self.subTest(answer=answer):
                self.assert_refused(session, F1, answer)
        self.assert_sent(session, F1, "1980-01-30")
        self.assertEqual(receipt(session, {"ok": True, "verified": True, "wrong_answers_left": 4}), "done")
        for answer in ("two two eight eight", "228", "22888", "22 88", "22a8", "２２８８"):
            with self.subTest(answer=answer):
                self.assert_refused(session, F2, answer)
        self.assert_sent(session, F2, "0228")

    def test_an_unread_answer_is_never_resent_and_unread_results_are_bounded(self):
        session = self.stated_session()
        unread = {"ok": False, "error": "answer is not readable"}
        self.assert_sent(session, F1, "1980-01-30")
        self.assertEqual(receipt(session, unread), "failure")
        self.assertEqual((session.state["identity_status"], session.state["wrong_answers"]), ("pending", 0))
        self.assert_refused(session, F1, "1980-01-30")  # the same answer again
        for answer in ("1980-01-31", "1980-02-01"):
            self.assert_sent(session, F1, answer)
            self.assertEqual(receipt(session, unread), "failure")
        self.assertEqual(session.state["unread"], {"count": 3, "last": [F1, "1980-02-01"]})
        self.assert_refused(session, F1, "1980-02-02", reason="closed")  # three unread results end sending

    def test_counted_results_and_unstated_formats_keep_their_behaviour(self):
        session = self.stated_session(text=None)
        self.assert_sent(session, F1, "an answer in words")  # no stated format: the bank rules on it
        self.assertEqual(receipt(session, {"ok": True, "verified": False, "wrong_answers_left": 3}), "done")
        self.assertEqual((session.state["wrong_answers"], session.state["unread"]), (1, {"count": 0, "last": []}))
        self.assert_sent(session, F1, "an answer in words")  # a counted answer may be given again
        self.assertEqual(receipt(session, {"ok": False, "error": "answer is not readable"}), "failure")
        self.assert_refused(session, F1, "an answer in words")

    def test_a_definite_unprocessed_receipt_is_never_resent(self):
        session = self.stated_session(identity_failure_classes=CLASSES)
        self.assert_sent(session, F1, "1980-01-30")
        self.assertEqual(receipt(session, {"ok": False, "error": "BAD_FORM"}), "failure")
        self.assertEqual(session.state["identity_status"], "pending")
        self.assert_refused(session, F1, "1980-01-30")
        self.assert_sent(session, F1, "1980-01-31")


if __name__ == "__main__":
    unittest.main()

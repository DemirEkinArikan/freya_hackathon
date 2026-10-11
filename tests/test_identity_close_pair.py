"""One identity close pair as a conversation-only row and a protected-evidence row.

Invented data; no model, bank or platform I/O. The env-gated replay only reads retained runs.
"""
import copy
import json
import os
import unittest
from pathlib import Path

from builder.contract import compile_policy_contract
from builder.execution_requirements import ExecutionRequirementsError, compile_execution_requirements
from builder.guards import run_guard_step
from builder.safety import digest, read_json
import test_goal_requirements as goal_requirements
import test_identity_lifecycle as lifecycle

ROOT = Path(__file__).resolve().parents[1]
RUNS = os.environ.get("FREYA_CLOSE_PAIR_RUNS", "")
SPOKEN = lifecycle.SPOKEN
PAIRED = dict(SPOKEN, requires="definite_verification_failure")


def values(*choices):
    return lifecycle.values(choices=choices or (SPOKEN, PAIRED), identity_failure_classes=lifecycle.CLASSES)


class IdentityClosePairTests(unittest.TestCase):
    session = goal_requirements.GoalRequirementsTests.session
    check = lifecycle.IdentityLifecycleTests.check
    assert_close = lifecycle.IdentityLifecycleTests.assert_close
    assert_refused = lifecycle.IdentityLifecycleTests.assert_refused

    def test_a_conversation_row_and_evidence_rows_may_share_one_pair(self):
        compile_execution_requirements(*values())
        compile_execution_requirements(*values(SPOKEN, PAIRED, dict(SPOKEN, requires="uncertain_verification")))
        for twin, message in ((SPOKEN, "duplicate decoded reasons"),
                              (dict(SPOKEN, requires="no_recorded_callback"), "duplicate decoded reasons"),
                              (dict(PAIRED, reason="ID_FAULT"), "at most one row per verification evidence")):
            with self.subTest(twin=twin), self.assertRaisesRegex(ExecutionRequirementsError, message):
                compile_execution_requirements(*values(SPOKEN, PAIRED, twin))

    def test_without_evidence_a_proposal_closes_through_the_conversation_row(self):
        _, _, session = self.session(values())
        self.assert_refused(session, "close_call", outcome="GOAL_DONE", reason="GOAL_MATCH")
        self.assert_close(session, "identity_failed", proposal=("not_verified", "identity_failed"))

    def test_recorded_definite_failure_closes_through_its_row_whatever_is_proposed(self):
        for proposal in (("not_verified", "identity_failed"), ("GOAL_DONE", "GOAL_MATCH")):
            with self.subTest(proposal=proposal):
                _, _, session = self.session(values())
                self.assertEqual(self.check(session, lifecycle.F1, {"ok": False, "error": "DENIED"}), "failure")
                self.assertEqual(session.state["identity_status"], "fault")
                self.assert_close(session, "identity_failed", proposal=proposal)

    def test_uncertain_evidence_without_its_row_still_quarantines(self):
        _, _, session = self.session(values())
        self.assertEqual(self.check(session, lifecycle.F1, {"ok": True, "wrong_answers_left": 4}), "failure")
        self.assertTrue(session.state["quarantined"])
        session.action("close_call", outcome="not_verified", reason="identity_failed")
        self.assertEqual(session.variables["bg_route"], "blocked")
        self.assertFalse(session.state["close_attempted"])

    def test_kernel_matches_a_proposal_only_against_rows_without_evidence(self):
        fixed = {"outcome": "not_verified", "reason": "identity_failed"}
        profile = {"identity_fields": [lifecycle.F1], "identity_wrong_limit": 3, "record_notes": False,
                   "close_reasons": {"not_verified": ["identity_failed", "ID_UNSURE"], "not_reached": []},
                   "close_precedence": [], "identity_close": {"failed": fixed, "incomplete": fixed,
                                                             "unreached": {"outcome": "not_reached"}},
                   "identity_close_choices": {"incomplete": [dict(SPOKEN), dict(PAIRED), dict(lifecycle.UNSURE)]}}
        catalog = {"close_call": {"name": "close_call", "fields": {key: key for key in ("customer_id", "outcome", "reason")},
                   "request_schema": {"type": "object", "properties": {key: {"type": "string"} for key in ("customer_id", "outcome", "reason")}}}}
        state = run_guard_step("init", None, profile=profile, catalog=catalog, customer_id="invented", now="2031-05-02")[0]

        def close(reason):
            inputs = {"outcome": "not_verified", "reason": reason}
            return run_guard_step("prepare", copy.deepcopy(state), "close_call", inputs, profile=profile, catalog=catalog)
        self.assertEqual(close("identity_failed")[2]["reason"], "identity_failed")
        with self.assertRaisesRegex(ValueError, "lacks required verification evidence"):
            close("ID_UNSURE")
        with self.assertRaisesRegex(ValueError, "lacks one declared eligible choice"):
            close("ID_FAULT")


@unittest.skipUnless(RUNS, "set FREYA_CLOSE_PAIR_RUNS to retained S04 build run directories (os.pathsep-separated)")
class RetainedClosePairReplayTests(unittest.TestCase):
    """No-model replay: retained S04 policies, counter declaration removed, pass the policy stage."""

    def test_retained_policies_without_the_counter_entry_pass_execution_requirements(self):
        inputs = ROOT / "docs" / "development-inputs" / "s04-account-information"
        for run in map(Path, RUNS.split(os.pathsep)):
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
            for path in rejected:
                with self.subTest(run=run.name, policy=path.name):
                    policy = read_json(path)["policy"]
                    rows = [row for item in policy["rules"] if item["kind"] == "identity_close_choices"
                            for rows in item["value"].values() for row in rows]
                    self.assertTrue(any(row["requires"] == "none" and dict(row, requires="definite_verification_failure") in rows
                                        for row in rows))
                    kept = [entry for entry in policy["unsupported_constraints"] if entry["category"] != "protected_unenforceable"]
                    self.assertEqual(len(policy["unsupported_constraints"]) - len(kept), 1)
                    policy["unsupported_constraints"] = kept
                    compile_policy_contract(policy, sources, api)
                    self.assertTrue(compile_execution_requirements(policy, sources, api)["actions"])


if __name__ == "__main__":
    unittest.main()

"""Real production repair boundary handles an authentic unclosed-ledger shape."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from benchmarks.hosted import run_builder_tests
from builder import build
from builder.repair_diagnostics import DIAGNOSTICS
import test_builder
from test_hosted_runner import RunnerBackend, fixture_rows


class NullClosingProductionTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def test_actual_execute_repairs_complete_pinned_unclosed_receipt_without_judge_leak(self):
        rows = fixture_rows(2)
        cases = self.directory / "hosted-evaluator.json"
        cases.write_text(json.dumps({"kind": "hosted", "cases": rows}))
        marker = "PRIVATE_JUDGE_BANK_EXPECTED_CLOSING_592813"

        def backend(unclosed):
            service = RunnerBackend(rows)
            original = service.iteration
            def iteration(simulation):
                result = original(simulation)
                for row in result["runs"]:
                    row["agentVersionNumber"] = 1
                    if unclosed:
                        row.update(outcome="fail", reason=marker)
                return result
            service.iteration = iteration
            if unclosed:
                service.ledger = lambda customer: {"outcome": None, "reason": None,
                    "promises": [], "notes": [], "callbacks": [], "transfers": [], "calls": []}
            return service

        generator = self.generator()
        generator.generate.return_value = test_builder.fixture()
        client = test_builder.FakeClient()
        with patch("benchmarks.hosted.HostedBackend", side_effect=[backend(True), backend(False)]):
            result = build.execute(self.args("--test-cases", str(cases)), generator=generator,
                                   client_factory=lambda **kwargs: client, test_runner=run_builder_tests)

        self.assertEqual((result["status"], result["policy_repairs"], result["workflow_repairs"]),
                         ("exported", 0, 1))
        self.assertEqual((client.saves, client.exports, generator.generate.call_count,
                          generator.generate_plan.call_count), (2, 1, 1, 2))
        prompt = generator.generate_plan.call_args_list[1].args[0]
        repair = json.loads(prompt.split("\n\nAUTHORIZED INPUTS:\n", 1)[1])["repair"]["test_summary"]
        message = "closing outcome/reason have not been recorded"
        self.assertIn(DIAGNOSTICS[message], [f["diagnostic"] for f in repair["failures"]])
        self.assertNotIn(marker, prompt)
        for key in ("rows", "judge", "ledger", "expected", "transcript", "simulations"):
            self.assertNotIn(key, repair)
        run = next((self.directory / "out").iterdir())
        first = run / "attempts/plan-001"
        second = run / "attempts/plan-002"
        evidence = json.loads((first / "tests.json").read_text())
        self.assertEqual((evidence["status"], evidence["passed"], evidence["total"]), ("completed", 0, 2))
        self.assertTrue(evidence["deployment_pin"]["per_call_version_verified"])
        self.assertTrue(all(row["ledger"]["outcome"] is None for row in evidence["rows"]))
        self.assertIn(marker, json.dumps(evidence))
        a, b = [json.loads((directory / "guard-context.json").read_text()) for directory in (first, second)]
        self.assertEqual((a["contract"], a["execution_policy"]), (b["contract"], b["execution_policy"]))


if __name__ == "__main__":
    unittest.main()

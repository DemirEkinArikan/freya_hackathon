"""Configured build budgets survive clock arithmetic without extending deadlines."""
import json
import unittest

from builder.build import execute, PROJECT
from builder.runtime import Deadline, DeadlineExceeded
import test_builder as fixtures


ROUNDING_STARTS = (1006.394087416, 1006.3940874160005)


class DeadlineBudgetRecordingTests(unittest.TestCase):
    setUp = fixtures.BuilderTests.setUp
    tearDown = fixtures.BuilderTests.tearDown
    args = fixtures.BuilderTests.args

    def test_controller_records_configured_budget_for_under_and_over_rounding(self):
        spans = []
        for start in ROUNDING_STARTS:
            with self.subTest(start=start):
                deadline = Deadline(clock=lambda: start)
                spans.append(deadline.limit - deadline.start)
                generator = fixtures.BuilderTests.generator(self)
                generator.generate.return_value = fixtures.fixture()
                result = execute(self.args(), generator=generator,
                                 client_factory=fixtures.FakeClient, deadline=deadline)
                run = PROJECT / result["run_directory"]
                source = json.loads((run / "builder-source.json").read_text())
                self.assertEqual(source["resolved_config"]["deadline_seconds"], 1800)
                events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
                snapshot = next(event for event in events if event["event"] == "builder_source_snapshot")
                self.assertEqual(snapshot["resolved_config"]["deadline_seconds"], 1800)
        self.assertLess(spans[0], 1800)
        self.assertGreater(spans[1], 1800)

    def test_actual_clock_limits_and_export_reserve_are_unchanged(self):
        for start in ROUNDING_STARTS:
            with self.subTest(start=start):
                now = [start]
                deadline = Deadline(clock=lambda: now[0])
                self.assertEqual(deadline.duration_seconds, 1800)
                self.assertEqual(deadline.limit, start + 1800)
                self.assertEqual(deadline.remaining(), deadline.limit - start)
                now[0] = start + 1
                self.assertEqual(deadline.elapsed(), 1)
                self.assertEqual(deadline.timeout(20, reserve=300), 20)
                now[0] = deadline.limit - 300
                with self.assertRaises(DeadlineExceeded):
                    deadline.require(reserve=300)
                now[0] = deadline.limit
                self.assertEqual(deadline.remaining(), 0)
                with self.assertRaises(DeadlineExceeded):
                    deadline.require()

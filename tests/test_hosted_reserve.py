"""Builder test requests leave the final export allocation untouched."""
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from benchmarks.hosted import HostedBackend, run_builder_tests
from builder.runtime import Deadline, DeadlineExceeded


class HostedReserveTests(unittest.TestCase):
    def backend(self, seconds, *, reserve=300):
        now = [0]
        deadline = Deadline(seconds, clock=lambda: now[0])
        opener = Mock(side_effect=lambda *_args, **_kwargs: io.BytesIO(b'{"variables":{}}'))
        backend = HostedBackend(object(), deadline, opener=opener,
                                team_key="synthetic-team-key", reserve=reserve)
        return backend, now, opener

    def test_exhausted_export_allocation_prevents_the_bank_request(self):
        for seconds in (299, 300):
            backend, _, opener = self.backend(seconds)
            with self.subTest(seconds=seconds), self.assertRaises(DeadlineExceeded):
                backend.bank("/v1/practice/sessions", {"case": "synthetic"})
            opener.assert_not_called()

    def test_every_bank_request_recomputes_its_remaining_nonexport_budget(self):
        backend, now, opener = self.backend(315)
        backend.bank("/v1/practice/sessions", {"case": "synthetic"})
        self.assertEqual(opener.call_args.kwargs["timeout"], 15)
        now[0] = 13
        backend.bank("/v1/practice/ledger/synthetic")
        self.assertEqual(opener.call_args.kwargs["timeout"], 2)
        now[0] = 15
        with self.assertRaises(DeadlineExceeded):
            backend.bank("/v1/practice/ledger/synthetic")
        self.assertEqual(opener.call_count, 2)

    def test_standalone_requests_keep_the_twenty_second_cap(self):
        backend, now, opener = self.backend(315, reserve=0)
        backend.bank("/v1/practice/sessions", {"case": "synthetic"})
        self.assertEqual(opener.call_args.kwargs["timeout"], 20)
        now[0] = 303
        backend.bank("/v1/practice/ledger/synthetic")
        self.assertEqual(opener.call_args.kwargs["timeout"], 12)
        unbounded = HostedBackend(object(), opener=opener, team_key="synthetic-team-key")
        unbounded.bank("/v1/practice/ledger/synthetic")
        self.assertEqual(opener.call_args.kwargs["timeout"], 20)

    def test_builder_hook_passes_its_export_reserve_to_the_backend(self):
        client, deadline, backend = object(), Deadline(600), object()
        result = {"passed": 1, "total": 1, "status": "passed"}
        output = Path(__file__).resolve().parents[1] / "artifacts/unit-reserve"
        with patch("benchmarks.hosted.load_cases", return_value=({}, [{"split": "train"}])), \
             patch("benchmarks.hosted.HostedBackend", return_value=backend) as create_backend, \
             patch("benchmarks.hosted.evaluate", return_value=result) as evaluate, \
             patch.object(Path, "mkdir"), patch.object(Path, "write_text"):
            actual = run_builder_tests(client, {"agent_id": "synthetic-agent"},
                                       "unused-cases.json", output, deadline)
        self.assertEqual(actual, result)
        create_backend.assert_called_once_with(client, deadline, reserve=300)
        evaluate.assert_called_once_with(backend, "synthetic-agent", [{"split": "train"}],
                                         reps=1, deployment=None)

    def test_invalid_or_unenforceable_reserve_is_rejected(self):
        for reserve in (-1, True, "300", float("inf"), float("nan")):
            with self.subTest(reserve=reserve), self.assertRaises(ValueError):
                HostedBackend(object(), Deadline(600), team_key="synthetic-team-key", reserve=reserve)
        with self.assertRaisesRegex(ValueError, "shared deadline"):
            HostedBackend(object(), team_key="synthetic-team-key", reserve=300)


if __name__ == "__main__":
    unittest.main()

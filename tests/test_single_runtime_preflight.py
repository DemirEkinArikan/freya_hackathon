"""CLI/report boundaries with mocked functional auth and isolation checks only."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from builder import preflight


class SingleRuntimePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.output = self.root / "artifacts" / "report.json"
        self.local = {"status": "pass", "authentication": "chatgpt", "checks": {"signin": True}}
        self.file_patch = patch.object(preflight, "__file__", str(self.root / "builder" / "preflight.py"))
        self.file_patch.start()

    def tearDown(self):
        self.file_patch.stop()
        self.tmp.cleanup()

    def invoke(self, *arguments, local=None, isolation=None):
        with patch.object(preflight, "preflight", return_value=deepcopy(local or self.local)), \
             patch.object(preflight, "isolation_probe", return_value=isolation or {"status": "pass"}) as probe, \
             redirect_stdout(io.StringIO()) as output:
            code = preflight.main(["--output", str(self.output), *arguments])
        report = json.loads(self.output.read_text())
        self.assertEqual(json.loads(output.getvalue()), report)
        for key in ("operator_attestation", "account_ownership", "two_computer_acceptance"):
            self.assertNotIn(key, report)
        return code, report, probe

    def test_operator_is_optional_and_no_second_computer_or_human_attestation_is_required(self):
        code, report, probe = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["local"]["authentication"], "chatgpt")
        self.assertEqual(report["isolation"]["status"], "not_run")
        self.assertNotIn("operator_label", report)
        probe.assert_not_called()

    def test_arbitrary_operator_label_is_informational_and_does_not_select_an_account(self):
        code, report, probe = self.invoke("--operator", "solo-build-machine")
        self.assertEqual(code, 0)
        self.assertEqual(report["operator_label"], "solo-build-machine")
        self.assertEqual(report["local"], self.local)
        probe.assert_not_called()

    def test_previous_operator_labels_remain_accepted_without_ownership_claims(self):
        for label in ("demir", "naci"):
            with self.subTest(label=label):
                code, report, _ = self.invoke("--operator", label)
                self.assertEqual(code, 0)
                self.assertEqual(report["operator_label"], label)

    def test_functional_authentication_failure_still_blocks_probe_and_returns_failure(self):
        local = {"status": "fail", "authentication": "unverified", "checks": {"signin": False}}
        code, report, probe = self.invoke("--probe-isolation", local=local)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["local"]["checks"]["signin"])
        probe.assert_not_called()

    def test_isolation_failure_remains_a_failure_without_operator(self):
        isolation = {"status": "fail", "failures": ["model_reports_available_tools"], "canary_exposure": []}
        code, report, probe = self.invoke("--probe-isolation", isolation=isolation)
        self.assertEqual(code, 1)
        self.assertEqual(report["isolation"], isolation)
        probe.assert_called_once_with(self.root / "artifacts" / "runtime-probes", executable="codex", model="gpt-6.1-sol")

    def test_passing_explicit_isolation_probe_needs_no_collaborator_identity_fields(self):
        code, report, probe = self.invoke("--probe-isolation")
        self.assertEqual(code, 0)
        self.assertEqual(report["isolation"]["status"], "pass")
        self.assertNotIn("operator_label", report)
        probe.assert_called_once()

    def test_optional_label_is_sanitized_before_report_persistence(self):
        code, report, _ = self.invoke("--operator", "sk-synthetic-test-label-123456789")
        self.assertEqual(code, 0)
        self.assertEqual(report["operator_label"], "[REDACTED]")
        self.assertNotIn("sk-synthetic-test-label", self.output.read_text())


if __name__ == "__main__":
    unittest.main()

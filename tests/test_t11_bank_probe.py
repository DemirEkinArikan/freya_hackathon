"""Offline boundary checks for the explicit T11 one-session diagnostic."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from benchmarks.hosted import HostedBackend
from tools import t11_bank_probe as probe_tool

ROOT = Path(__file__).resolve().parents[1]
CUSTOMER = "private-customer-capability"
SESSION = "private-session-capability"
TEAM_KEY = "private-team-key-for-offline-tests"


def pristine():
    return {**{field: [] for field in probe_tool.COLLECTIONS}, "outcome": None, "reason": None}


def successful_tls():
    """Use the committed verified control's actual OpenSSL output format."""
    evidence = (ROOT / "docs/network-evidence/t11-linux-verified-20261010.txt").read_text()
    control = "host=www.cloudflare.com tls_ipv4 port=443 deadline_seconds=8 verify_hostname=true\n"
    block = control + evidence.split(control, 1)[1]
    bank = block.replace("www.cloudflare.com", probe_tool.BANK_HOST)
    return "observed_at_utc=offline-unit-test\n\n" + bank + "\n" + block


class FakeBackend:
    def __init__(self, variables=None, ledger=None, failure=None, phase="creation"):
        self.variables = variables if variables is not None else {"customer_id": CUSTOMER, "session_id": SESSION}
        self.record = pristine() if ledger is None else ledger
        self.failure, self.phase = failure, phase
        self.calls = []

    def practice_session(self, seed):
        self.calls.append(("POST", seed))
        if self.failure is not None and self.phase == "creation":
            raise self.failure
        return deepcopy(self.variables)

    def ledger(self, customer):
        self.calls.append(("GET", customer))
        if self.failure is not None and self.phase == "ledger":
            raise self.failure
        return deepcopy(self.record)


class BankProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.state = self.directory / "attempt.json"

    def read_state(self):
        return json.loads(self.state.read_text())

    def test_verified_tls_accepts_authentic_success_format_and_refuses_recorded_failure(self):
        self.assertTrue(probe_tool.verified_tls(successful_tls()))
        recorded = (ROOT / "docs/network-evidence/t11-linux-verified-20261010.txt").read_text()
        self.assertFalse(probe_tool.verified_tls(recorded))

    def test_tls_gate_requires_both_exact_hosts_verified_success_and_unique_attempts(self):
        good = successful_tls()
        bank_header = "host=" + probe_tool.BANK_HOST + " tls_ipv4 port=443 deadline_seconds=8 verify_hostname=true\n"
        variants = (
            "Verification: OK\ntls_exit=0\n",
            good.split("host=www.cloudflare.com", 1)[0],
            good.replace("tls_exit=0", "tls_exit=124", 1),
            good.replace("Verification: OK", "Verification error: certificate rejected", 1),
            good.replace("Verified peername: " + probe_tool.BANK_HOST, "Verified peername: example.invalid", 1),
            good.replace("verify_hostname=true", "verify_hostname=false", 1),
            good.replace(probe_tool.BANK_HOST, "example.invalid"),
            good + "\n" + bank_header + "Verification: OK\ntls_exit=0\n",
            good.replace("tls_exit=0", "tls_exit=0\ntls_exit=0", 1),
        )
        for log in variants:
            with self.subTest(log=log[:80]):
                self.assertFalse(probe_tool.verified_tls(log))

    def test_verified_peername_accepts_matching_full_leftmost_certificate_wildcards(self):
        # OpenSSL reports the matched certificate SAN/CN, which may be wildcard:
        # https://docs.openssl.org/3.0/man3/SSL_set1_host/
        # The exact -verify_hostname command, verification success and exit zero
        # remain prerequisites; this parser supports the full leftmost form.
        good = successful_tls()
        wildcard = good.replace("Verified peername: " + probe_tool.BANK_HOST,
                                "Verified peername: *.freyavoice.workers.dev")
        wildcard = wildcard.replace("Verified peername: www.cloudflare.com",
                                    "Verified peername: *.cloudflare.com")
        self.assertTrue(probe_tool.verified_tls(wildcard))
        uppercase = good.replace("Verified peername: " + probe_tool.BANK_HOST,
                                 "Verified peername: " + probe_tool.BANK_HOST.upper())
        self.assertTrue(probe_tool.verified_tls(uppercase))
        self.assertFalse(probe_tool.verified_tls(wildcard.replace("tls_exit=0", "tls_exit=124", 1)))
        self.assertFalse(probe_tool.verified_tls(wildcard.replace("Verification: OK", "Verification error", 1)))

    def test_unrelated_multilabel_partial_and_duplicate_reported_peers_are_refused(self):
        good = successful_tls()
        marker = "Verified peername: " + probe_tool.BANK_HOST
        peers = (
            "*.workers.dev", "*.example.invalid", "*.freya-bank-mock.freyavoice.workers.dev",
            "freya-*.freyavoice.workers.dev", "*.*.workers.dev",
            probe_tool.BANK_HOST + ".evil", "freyavoice.workers.dev", "",
        )
        for peer in peers:
            with self.subTest(peer=peer):
                self.assertFalse(probe_tool.verified_tls(good.replace(marker, "Verified peername: " + peer)))
        for duplicate in (marker, "Verified peername: *.freyavoice.workers.dev", "Verified peername: example.invalid"):
            with self.subTest(duplicate=duplicate):
                self.assertFalse(probe_tool.verified_tls(good.replace(marker, marker + "\n" + duplicate)))

    def test_inspection_does_not_read_files_load_credentials_or_open_network(self):
        output = io.StringIO()
        with patch("pathlib.Path.read_text", side_effect=AssertionError("unexpected read")), \
             patch("builder.config.load_local_environment", side_effect=AssertionError("unexpected credentials")), \
             patch("urllib.request.build_opener", side_effect=AssertionError("unexpected network")), \
             patch("benchmarks.hosted.HostedBackend", side_effect=AssertionError("unexpected backend")), \
             redirect_stdout(output):
            self.assertEqual(probe_tool.main([]), 0)
        self.assertEqual(json.loads(output.getvalue())["network"], "none")
        self.assertFalse(self.state.exists())

    def test_failed_tls_stops_before_backend_or_attempt_marker(self):
        log = self.directory / "tls.txt"
        log.write_text("Verification: OK\ntls_exit=0\n")
        with patch("benchmarks.hosted.HostedBackend", side_effect=AssertionError("unexpected backend")), redirect_stdout(io.StringIO()):
            self.assertEqual(probe_tool.main(["--execute", "--verified-tls-log", str(log), "--state", str(self.state)]), 1)
        self.assertFalse(self.state.exists())

    def test_execute_requires_gate_and_new_state_before_any_backend(self):
        self.state.write_text('{"status":"creation_pending"}')
        invocations = (["--execute"], ["--execute", "--verified-tls-log", "missing", "--state", str(self.state)])
        for arguments in invocations:
            with self.subTest(arguments=arguments), patch("benchmarks.hosted.HostedBackend", side_effect=AssertionError("unexpected backend")), \
                 redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit) as raised:
                probe_tool.main(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_real_backend_adapter_issues_only_single_post_then_untouched_ledger_get(self):
        requests = []
        def opener(request, timeout):
            requests.append((request.method, request.full_url, request.data, timeout))
            payload = {"variables": {"customer_id": CUSTOMER, "session_id": SESSION}} if request.method == "POST" else pristine()
            return io.BytesIO(json.dumps(payload).encode())
        backend = HostedBackend(None, opener=opener, team_key=TEAM_KEY)
        report = probe_tool.probe(backend, self.state)
        self.assertEqual(report["status"], "pristine_ledger_verified")
        self.assertEqual([request[0] for request in requests], ["POST", "GET"])
        self.assertEqual(json.loads(requests[0][2]), {"case": "PUB-01"})
        self.assertTrue(requests[1][1].endswith("/v1/practice/ledger/" + CUSTOMER))
        self.assertIsNone(requests[1][2])
        self.assertEqual([request[3] for request in requests], [20, 20])
        self.assertEqual((report["session_post_attempts"], report["ledger_get_attempts"]), (1, 1))
        self.assertFalse(report["live_conversation_acceptance"])
        self.assertEqual(self.read_state(), report)

    def test_completed_failure_and_pending_states_cannot_replay_writes(self):
        for status in ("pristine_ledger_verified", "creation_failure", "creation_pending", "ledger_pending"):
            self.state.write_text(json.dumps({"status": status}))
            backend = FakeBackend()
            with self.subTest(status=status), self.assertRaises(FileExistsError):
                probe_tool.probe(backend, self.state)
            self.assertEqual(backend.calls, [])

    def test_baseexception_leaves_durable_attempt_marker_without_same_state_retry(self):
        for phase in ("creation", "ledger"):
            for interruption in (KeyboardInterrupt, SystemExit):
                self.state.unlink(missing_ok=True)
                backend = FakeBackend(failure=interruption(), phase=phase)
                with self.subTest(phase=phase, interruption=interruption), self.assertRaises(interruption):
                    probe_tool.probe(backend, self.state)
                report = self.read_state()
                self.assertEqual(report["status"], "creation_pending" if phase == "creation" else "ledger_pending")
                self.assertEqual(report["creation_outcome"], "unknown" if phase == "creation" else "validated")
                before = list(backend.calls)
                with self.assertRaises(FileExistsError):
                    probe_tool.probe(backend, self.state)
                self.assertEqual(backend.calls, before)

    def test_http_transport_json_and_response_shape_errors_never_retry_creation(self):
        failures = (
            urllib.error.HTTPError("https://example.invalid/" + CUSTOMER, 503, SESSION, {}, io.BytesIO(TEAM_KEY.encode())),
            urllib.error.URLError(CUSTOMER + SESSION + TEAM_KEY),
            json.JSONDecodeError("invalid " + CUSTOMER, SESSION + TEAM_KEY, 0),
            KeyError(CUSTOMER + SESSION + TEAM_KEY),
        )
        for failure in failures:
            self.state.unlink(missing_ok=True)
            backend = FakeBackend(failure=failure)
            report = probe_tool.probe(backend, self.state)
            with self.subTest(failure=type(failure).__name__):
                self.assertEqual(report["status"], "creation_failure")
                self.assertEqual(report["creation_outcome"], "unknown")
                self.assertEqual(backend.calls, [("POST", "PUB-01")])
                with self.assertRaises(FileExistsError):
                    probe_tool.probe(backend, self.state)
                text = self.state.read_text()
                for capability in (CUSTOMER, SESSION, TEAM_KEY):
                    self.assertNotIn(capability, text)
                if isinstance(failure, urllib.error.HTTPError):
                    self.assertEqual(report["http_status"], 503)

    def test_malformed_returned_variables_preserve_received_creation_and_stop_ledger(self):
        variables = ([CUSTOMER], {}, {"customer_id": ""}, {"customer_id": 42},
                     {"customer_id": CUSTOMER, "session_id": None}, {"customer_id": CUSTOMER, "session_id": ""})
        for value in variables:
            self.state.unlink(missing_ok=True)
            backend = FakeBackend(variables=value)
            report = probe_tool.probe(backend, self.state)
            with self.subTest(value=value):
                self.assertEqual(report["status"], "creation_failure")
                self.assertEqual(report["creation_outcome"], "response_received")
                self.assertEqual(report["ledger_get_attempts"], 0)
                self.assertEqual(backend.calls, [("POST", "PUB-01")])

    def test_backend_json_and_missing_variables_failures_leave_creation_ambiguous(self):
        for payload in (b"not-json", b"{}", b'{"unexpected":"response"}'):
            self.state.unlink(missing_ok=True)
            requests = []
            def opener(request, timeout):
                requests.append(request.method)
                return io.BytesIO(payload)
            backend = HostedBackend(None, opener=opener, team_key=TEAM_KEY)
            report = probe_tool.probe(backend, self.state)
            with self.subTest(payload=payload):
                self.assertEqual(requests, ["POST"])
                self.assertEqual(report["creation_outcome"], "unknown")
                self.assertEqual(report["status"], "creation_failure")
                with self.assertRaises(FileExistsError):
                    probe_tool.probe(backend, self.state)
                self.assertEqual(requests, ["POST"])

    def test_nonobject_ledger_is_retrieval_failure_without_discarding_attempt_evidence(self):
        backend = FakeBackend(ledger=[CUSTOMER, SESSION, TEAM_KEY])
        report = probe_tool.probe(backend, self.state)
        self.assertEqual(report["status"], "ledger_failure")
        self.assertEqual(report["creation_outcome"], "validated")
        self.assertEqual(report["ledger_get_attempts"], 1)
        for secret in (CUSTOMER, SESSION, TEAM_KEY):
            self.assertNotIn(secret, self.state.read_text())

    def test_missing_or_malformed_ledger_fields_and_nonempty_collections_fail_pristine_claim(self):
        ledgers = []
        for field in (*probe_tool.COLLECTIONS, "outcome", "reason"):
            missing = pristine()
            del missing[field]
            ledgers.append(missing)
        for field in probe_tool.COLLECTIONS:
            for value in (None, {}, "", [CUSTOMER]):
                ledger = pristine()
                ledger[field] = value
                ledgers.append(ledger)
        for field in ("outcome", "reason"):
            ledger = pristine()
            ledger[field] = CUSTOMER
            ledgers.append(ledger)
        for ledger in ledgers:
            self.state.unlink(missing_ok=True)
            report = probe_tool.probe(FakeBackend(ledger=ledger), self.state)
            with self.subTest(ledger=ledger):
                self.assertEqual(report["status"], "ledger_incompatible_or_not_pristine")
                self.assertNotIn(CUSTOMER, self.state.read_text())

    def test_ledger_failure_retains_validated_creation_without_capabilities_or_messages(self):
        backend = FakeBackend(failure=urllib.error.URLError(CUSTOMER + SESSION + TEAM_KEY), phase="ledger")
        report = probe_tool.probe(backend, self.state)
        self.assertEqual(report["status"], "ledger_failure")
        self.assertEqual(report["creation_outcome"], "validated")
        self.assertEqual(report["ledger_get_attempts"], 1)
        for secret in (CUSTOMER, SESSION, TEAM_KEY):
            self.assertNotIn(secret, self.state.read_text())

    def test_arbitrary_session_ledger_keys_values_and_errors_do_not_enter_persisted_report(self):
        variables = {"customer_id": CUSTOMER, "session_id": SESSION, TEAM_KEY: {CUSTOMER: SESSION}}
        ledger = {**pristine(), TEAM_KEY: {CUSTOMER: SESSION}}
        report = probe_tool.probe(FakeBackend(variables=variables, ledger=ledger), self.state)
        self.assertEqual(report["status"], "pristine_ledger_verified")
        for secret in (CUSTOMER, SESSION, TEAM_KEY):
            self.assertNotIn(secret, json.dumps(report))
            self.assertNotIn(secret, self.state.read_text())

    def test_execute_stdout_is_capability_free_and_never_loads_local_environment(self):
        log = self.directory / "tls.txt"
        log.write_text(successful_tls())
        backend = FakeBackend()
        output = io.StringIO()
        with patch("benchmarks.hosted.HostedBackend", return_value=backend) as constructor, \
             patch("builder.config.load_local_environment", side_effect=AssertionError("unexpected credentials")), \
             redirect_stdout(output):
            result = probe_tool.main(["--execute", "--verified-tls-log", str(log), "--state", str(self.state)])
        self.assertEqual(result, 0)
        self.assertIsNone(constructor.call_args.args[0])
        self.assertEqual(constructor.call_args.args[1].limit - constructor.call_args.args[1].start, 50)
        self.assertGreaterEqual(len(constructor.call_args.kwargs["team_key"]), 16)
        for secret in (CUSTOMER, SESSION, TEAM_KEY):
            self.assertNotIn(secret, output.getvalue())
        lines = output.getvalue().splitlines()
        markers = [json.loads(line) for line in lines[:3]]
        self.assertEqual([marker["status"] for marker in markers], ["creation_pending", "creation_pending", "ledger_pending"])
        self.assertEqual(json.loads("\n".join(lines[3:])), self.read_state())

    def test_pending_progress_is_flushed_before_backend_hard_interruption(self):
        log = self.directory / "tls.txt"
        log.write_text(successful_tls())
        output = io.StringIO()
        class InterruptedBackend(FakeBackend):
            def practice_session(inner, seed):
                marker = json.loads(output.getvalue())
                self.assertEqual(marker["status"], "creation_pending")
                self.assertEqual(marker["creation_outcome"], "unknown")
                self.assertEqual(self.read_state(), marker)
                raise KeyboardInterrupt()
        with patch("benchmarks.hosted.HostedBackend", return_value=InterruptedBackend()), \
             redirect_stdout(output), self.assertRaises(KeyboardInterrupt):
            probe_tool.main(["--execute", "--verified-tls-log", str(log), "--state", str(self.state)])
        self.assertEqual(json.loads(output.getvalue())["status"], "creation_pending")
        for secret in (CUSTOMER, SESSION, TEAM_KEY):
            self.assertNotIn(secret, output.getvalue())


if __name__ == "__main__":
    unittest.main()

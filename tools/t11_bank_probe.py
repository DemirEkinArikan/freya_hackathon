#!/usr/bin/env python3
"""Explicit one-session retrieval check; no simulation or conversation claim."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import secrets
import sys
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BANK_HOST = "freya-bank-mock.freyavoice.workers.dev"
COLLECTIONS = ("calls", "promises", "notes", "callbacks", "transfers")


def verified_tls(log: str) -> bool:
    """Require the existing diagnostic's single verified bank/control attempts."""
    blocks = log.split("\nhost=")[1:]
    for host in (BANK_HOST, "www.cloudflare.com"):
        tls = [block for block in blocks if block.startswith(
            host + " tls_ipv4 port=443 deadline_seconds=8 verify_hostname=true\n")]
        if len(tls) != 1:
            return False
        exits = [line for line in tls[0].splitlines() if line.startswith("tls_exit=")]
        lines = tls[0].splitlines()
        peers = [line.removeprefix("Verified peername: ").lower()
                 for line in lines if line.startswith("Verified peername: ")]
        # OpenSSL reports the matching certificate name, which may be a full
        # left-most wildcard SAN. Its verified -verify_hostname invocation is
        # authoritative; this extra log check permits exactly one label only.
        names = {host.lower(), "*." + host.partition(".")[2].lower()}
        if (exits != ["tls_exit=0"] or "Verification: OK" not in lines
                or len(peers) != 1 or peers[0] not in names):
            return False
    return True


def _write(path: Path, report: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def probe(backend, state_path: Path, *, progress=None) -> dict:
    """Persist attempted-write evidence before a single POST; never retry it."""
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "bank_case_id": "PUB-01", "session_post_attempts": 1,
        "creation_outcome": "unknown", "ledger_get_attempts": 0,
        "status": "creation_pending", "live_conversation_acceptance": False,
    }
    # Exclusive creation refuses an existing success, failure, or pending attempt.
    with state_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
        stream.flush()
        import os
        os.fsync(stream.fileno())
    if progress is not None:
        progress(report)
    phase = "creation"
    try:
        variables = backend.practice_session("PUB-01")
        # A response was received, even when its shape is incompatible. The bank
        # may already have created a session; malformed responses are not retried.
        report["creation_outcome"] = "response_received"
        if not isinstance(variables, dict):
            raise ValueError("session variables must be an object")
        customer = variables.get("customer_id")
        if not isinstance(customer, str) or not customer.strip():
            raise ValueError("session customer capability is missing")
        if "session_id" in variables and (
                not isinstance(variables["session_id"], str) or not variables["session_id"].strip()):
            raise ValueError("session capability is malformed")
        report.update(creation_outcome="validated", customer_capability_present=True,
                      session_capability_present="session_id" in variables)
        _write(state_path, report)
        if progress is not None:
            progress(report)
        phase = "ledger"
        report["ledger_get_attempts"] = 1
        report["status"] = "ledger_pending"
        _write(state_path, report)
        if progress is not None:
            progress(report)
        ledger = backend.ledger(customer)
        if not isinstance(ledger, dict):
            raise ValueError("ledger must be an object")
        # Persist only fixed field names, booleans and counts, never capabilities,
        # bank facts, arbitrary payload keys, or exception strings.
        shapes = {field: isinstance(ledger.get(field), list) for field in COLLECTIONS}
        report["collection_shapes_valid"] = shapes
        report["collection_counts"] = {
            field: len(ledger[field]) if shapes[field] else None for field in COLLECTIONS}
        report["outcome_unset"] = "outcome" in ledger and ledger["outcome"] is None
        report["reason_unset"] = "reason" in ledger and ledger["reason"] is None
        pristine = (all(shapes.values()) and all(count == 0 for count in report["collection_counts"].values())
                    and report["outcome_unset"] and report["reason_unset"])
        report["status"] = "pristine_ledger_verified" if pristine else "ledger_incompatible_or_not_pristine"
    except Exception as exc:
        report["status"] = phase + "_failure"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, urllib.error.HTTPError):
            report["http_status"] = exc.code
    _write(state_path, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--verified-tls-log", type=Path)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"mode": "inspection", "network": "none", "planned_bank_case_id": "PUB-01"}))
        return 0
    if args.verified_tls_log is None or args.state is None:
        parser.error("--execute requires --verified-tls-log and a new --state path")
    if args.state.exists():
        parser.error("existing attempt state must be reconciled; refusing another session POST")
    if not verified_tls(args.verified_tls_log.read_text(encoding="utf-8")):
        print(json.dumps({"status": "verified_tls_prerequisite_failed", "session_post_attempts": 0}))
        return 1
    from benchmarks.hosted import HostedBackend
    from builder.runtime import Deadline
    # Team keys are organizer-defined namespaces. An ephemeral random namespace
    # suffices for this isolated retrieval check; no local credential is loaded.
    backend = HostedBackend(None, Deadline(50), team_key=secrets.token_urlsafe(32))
    # Runner disks are ephemeral. Flush capability-free pending-write evidence to
    # retained job logs before opening the socket, including when later killed.
    report = probe(backend, args.state, progress=lambda value: print(json.dumps(value), flush=True))
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pristine_ledger_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())

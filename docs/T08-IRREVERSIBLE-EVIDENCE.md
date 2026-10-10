# T08 irreversible-action evidence

The isolated follow-up starts at main
`daef21738c2866a898fc79203aae3892b511bbc6`, after reviewing merged PR52
(`69676f136779d81b7a8681b48ca1344886d30a48`, merge
`5168ab8fc5bc4f4970adb04983376531a72b9261`). It preserves that audit's
confirmation, partial-write and terminal-route fixes. The new production diff
is confined to `guard_result`; T07 requirement/profile work and its metadata
accessors are untouched.

Two independently reproduced gaps are repaired: a positive result with a
contradictory or malformed HTTP status must not record a bank write, and a
positive promise result without a usable record identity must not erase its
uncertain transaction or label the attempt recorded. Both cases quarantine
further writes while preserving previous promises and the exact pending request.

Compiled runtime response mappings always supply the HTTP-status field. A
missing envelope therefore supplies an empty value and fails closed; payload
fields cannot spoof it. Supplied status accepts integer HTTP codes or their
exact three-digit string mappings. Positive success additionally requires 2xx.
Direct legacy semantic test harnesses can omit the field; those harnesses are
not evidence of transport execution.

## Local candidate evidence

`tests/test_guard_irreversible.py` compiles a frozen synthetic source-backed
policy with renamed operation IDs, request/result aliases and opaque outcome,
queue and note codes. It executes the emitted nodes, actual post-hook and
response mappings. It covers current-term consent, correction invalidation,
immutable recorded promises, partial batches, every permitted transfer queue,
deferred/discarded notes, qualified negative-only retry classifications,
malformed/missing/contradictory envelopes, missing record identities and close
attempts. Existing guard regressions retain failure-budget exhaustion and
preservation of successful promises after partial failure.

This is a local compiled candidate. It is not a generated public banking
candidate or hosted conversation. Its guard manifest explicitly retains
`host_behavior_verified=false` and `conversation_compliance_proven=false`.
Exact checked/pushed heads and command results are recorded in issue9 and the
focused PR after verification.

## Hosted prerequisite observations

On 10 October 2026, bounded unauthenticated Python 3.11 verified-TLS probes
reported:

| Origin | Result | Elapsed |
| --- | --- | --- |
| `freya-bank-mock.freyavoice.workers.dev` | `TimeoutError`, 8-second socket bound | 8.28 s |
| `staging-app.freyavoice.ai` | verified TLS 1.3 | 0.41 s |
| `www.cloudflare.com` | verified TLS 1.3 | 0.25 s |

This repeats T11's bank prerequisite failure; it does not identify its cause.
No bank session, ledger, model generation, deployment or simulation was started.

The canonical shared lease was read at
`/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock`
and `.simulation.json`. The existing simulation
`03229c8e-5d0c-41fd-8179-35a50c6cc43e`, iteration
`ca839f71-ddae-40ae-8e56-9cfaeed37a34`, was recorded completed. A bounded
read-only staging GET confirmed the matching simulation ID and server status
`completed`. The lease was not modified or replaced. The primary thread retains
coordination of the next simulation.

The independent review also identified that build and standalone evaluator
default lease paths are relative to each output directory/worktree. Before live
use, the primary must inject the canonical absolute lease into the client;
default entrypoints cannot establish one shared lease across worktrees. This
integration observation is checkpointed without editing another thread's
build/evaluator scope.

## Remaining issue9 acceptance

T07 remains open, including a successful fresh audited build and its upstream
dependencies. Once bank TLS and those prerequisites pass, use the normal
approved-brief/OpenAPI-only generation flow with mandatory tool-free auditing,
frozen policy, experimental-5, 1800-second build deadline and export reserve.
Publish/read back/export the unchanged generated candidate and retain its
exact version. Reconcile the shared lease with the primary and current server
status before executing public/development guard cases; keep holdouts reserved.

Obtain actual ledger/tool order plus pinned transcript/judge evidence for
corrections, mixed partial successes, failures, notes and all transfer classes.
Critical hosted cases require three passing repetitions. Demonstrate one final
successful bank close after the applicable recorded actions.

`close_attempted` proves at most one authorized dispatch attempt. A terminal
route on failure does not prove an HTTP request occurred or the bank recorded
a close. Unknown persistence quarantines bank actions, so it cannot promise a
successful final close. Issue9 must remain open until its whole deliverable,
dependencies and hosted evidence pass; `cowork.py complete` is not used for
this focused source repair alone.

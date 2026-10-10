# T15 development execution coordination

This is an execution plan and an evidence audit, not a hosted comparison result.
The T15 branch starts at `486ead9fba034638e528f61d41fe5c303a2c8769`.
T14 and T15 remain open until their actual acceptance and dependencies are met.

## Current evidence and prerequisites

The retained public-A run `20261010T202925576290Z`, from source
`b9903a58c6723e1b4f1f97ab81d60fe22754c28b`, failed after 1057.645 seconds
before a validated workflow or Freya mutation. It predates invocation-source
recording and cannot receive reconstructed provenance. Its failed manifest,
attempts and sanitized trace remain useful infrastructure/build evidence.

The primary controller lane owns the next structured build. It must integrate
capability completeness and the repair hooks before that invocation; comparison
and internal recording/bundle lanes do not edit generated policy/workflow files.
An accepted build requires the actual invocation snapshot/configuration,
mandatory tool-free generation audit, approved brief/API inputs, fixed
`experimental-5` phone model, publish/read-back/export, and one 1800-second
deadline with at least 300 seconds reserved for export.

The recording lane owns `benchmarks/evidence.py` and `benchmarks/bundle.py`.
It must retain actual evaluator configuration, original fixture inventory,
bank-returned calendar evidence, operation times and partial failures. A stated
calendar hash or a report that only lists evaluated cases cannot prove calendar
parity or full declared coverage. Historical values must not be reconstructed
from a current checkout, filesystem timestamps or intended defaults.

The comparator's invocation-source check validates the snapshot/configuration
and manifest hash binding. Its `trace_sha256` check currently validates only
hash shape, without original trace contents or pre-generation audit/event
ordering. Original trace/audit validation remains an internal recording/bundle
integration gate, and synthetic provenance regressions do not satisfy it.

The bank origin's existing verified-TLS checks failed before HTTP. A fresh
session/ledger compatibility check is useful only after verified TLS succeeds.
An ambiguous session creation must preserve its original failure and must not
be retried automatically. Missing bank evidence is an infrastructure gate,
never a failed conversation or a structured-builder win.

A single fresh unauthenticated handshake at
`2026-10-10T20:59:06.188216+00:00` retained the blocker: hostname/certificate
verification enabled, `TimeoutError` after 8.019 seconds. It created zero bank
sessions, retrieved zero ledgers and started zero simulations. The observation
is retained in ignored `artifacts/thread-coordination/t15-bank-tls.json` in the
primary checkout; it does not establish an outage or independent-egress result.

T14 additionally requires a real sanitized policy-violation repair, retained
original attempt, verified republish/read-back and timed export; source tests
alone do not meet it. T05/T13 dependencies must also complete.

## One canonical simulation lease

All worktrees use
`/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock`
and its `.simulation.json`. Build/hosted execution must explicitly use that
path rather than selecting a worktree/output-local lease.

A read-only check at `2026-10-10T20:58:13.956490+00:00` acquired the canonical
lock and confirmed the workspace scope, exact simulation ID and terminal server
status. Stored/server status were both `completed` for simulation
`03229c8e-5d0c-41fd-8179-35a50c6cc43e`, stored iteration
`ca839f71-ddae-40ae-8e56-9cfaeed37a34`. This observation does not claim a
lease for future execution. Reconcile again with the primary thread and server
immediately before use. No new simulation was started by this check.

Primary coordination uses ignored local files
`artifacts/thread-coordination/t15-to-primary.json` and
`t15-from-primary.json`; the shared primary/parallel files belong to their
existing lanes. The public scope checkpoint is
[issue #16](https://github.com/DemirEkinArikan/freya_hackathon/issues/16#issuecomment-6102103789).

## Parity sequence and complete inventory

Prioritize a successful structured build before baseline generation. Retain
one selected deployment per candidate/brief and keep its graph/version unchanged
through training and validation. Repair selects a new candidate and requires
fresh attributed evidence; preserve the failed attempt instead of replacing it.

Both candidates receive the same approved inputs, authenticated generation
model/reasoning settings, phone/persona/analysis models, evaluation budgets,
concurrency, repetitions, fixture inventory and actual bank calendar. Brief B
receives ordered A then B amendments plus the official API. Evaluator fixtures,
expected values, transcripts and failure prose are not generator inputs.

The organizer's stock Claude SDK/toolful example cannot be run unchanged as a
controlled baseline here. Baseline execution needs the existing authenticated
generation route and mandatory tool-free audit, with equivalent original
provenance. No new paid provider or purchase is authorized by this plan.

At this source head, the declared public development subsets contain:

| Brief | Training cases | Validation cases | Calls per candidate at three repetitions |
|---|---:|---:|---:|
| A | 27 | 15 | 126 |
| B | 9 | 9 | 54 |
| Total | 36 | 24 | 180 |

The two candidates require 360 calls, excluding controller build tests and any
failed/incomplete attempts. Recheck the invocation's declared manifests and
retain their original content/hash; this table cannot replace recorded inventory
evidence. Every declared case/repetition must be evaluated.

Complete both candidates' training before starting either candidate's
validation. Compare A and B separately. Use training failures to identify fixed
failure families and route repairs to the owning builder/policy/guard lane.
Run meaningful regressions for each source fix. A changed candidate must be
repinned and cannot reuse prior calls as its own evaluation. Holdouts remain
reserved until the prescribed freeze/holdout lifecycle permits them.

Strict all-repetition pass rate and zero policy/security failures take priority.
Preserve honesty and unclassified failures separately. Amount/speed tie breaking
requires fully passing evidence. Infrastructure gaps are incomparable results;
never infer a win from missing baseline provenance, incomplete fixture coverage,
source availability or mock tests.

Only actual comparable development evidence and completed T14 dependencies,
plus checked/pushed matching-head merged PR evidence, permit `tools/cowork.py
complete T15`. A focused comparator PR alone leaves T15 open.

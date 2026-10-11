# T15 development execution coordination

This is an execution plan and an evidence audit, not a hosted comparison result.
The production integration starts at main `2d09c31d2f2d25a0e01466e707f560127634cad3`.
T14 and T15 remain open until their actual acceptance and dependencies are met.

The [11 October rapid checkpoint](T15-RAPID-READINESS.md) now selects an
earlier guarded-builder baseline and a proposed structured source, with
development-only fingerprints and current blockers. Those selections require
fresh original live provenance; they do not accept old failed exports or
complete the comparison. The details below preserve the earlier plan/history.

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

The production recorder/bundler integration is now implemented. Actual standalone
evaluation automatically captures original full selected fixture rows/hash,
per-session bank calendars, resolved models/route/configuration, operation timings,
complete available reports and interruptions. Bundle assembly validates and retains
original build provenance and mandatory tool-free audit/catalog ordering. Local
CLI pipeline tests use synthetic external services only; they establish wiring,
not live comparison. See [T11-EVALUATION-EVIDENCE.md](T11-EVALUATION-EVIDENCE.md).

The prior TLS blocker was resolved through independently observed DNS answers and
verified bank-only Host/SNI/public-CA routing. T11 retained four actual pinned
adapter calls across two aliases and two repetitions; all four harmless greeting
calls failed banking behavior. Original infrastructure verdicts and later corrected
null/null re-scores remain separately retained. This proves adapter compatibility,
not candidate quality. The optional operator connect address must be explicitly
selected and identical for comparison peers; no static address is a source default.

T14 additionally requires a real sanitized policy-violation repair, retained
original attempt, verified republish/read-back and timed export; source tests
alone do not meet it. T05/T13 dependencies must also complete.

## One canonical simulation lease

All worktrees use
`/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock`
and its `.simulation.json`. Build/hosted execution must explicitly use that
path rather than selecting a worktree/output-local lease.

Latest retained T11 terminal lease is simulation
`ed748ba2-0e0a-4549-bea2-f31d4ac6d4bb`, iteration
`e0d90c7d-1521-45d8-950f-1c1618c5018d`, pinned diagnostic agent
`b149e951-6027-43c4-9996-9cebc79840a8`. Actual server confirmed completed at
2026-10-10T22:24:13.772985+00:00. This historical check is not permission to
replace the current lease; reconcile current metadata/server with the primary
controller immediately before every new live execution.

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
| A | 26 | 14 | 120 |
| B | 10 | 10 | 60 |
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

## Production commands and stage handoff

The external T05 lane owns the next generated public-A build. Wait for its accepted
manifest/export/original trace/source record/provenance and a primary stage handoff;
then inspect those bytes and the unchanged published agent/version. This lane
starts no competing model or simulation while that build is active.

Run commands from merged `main`. Inputs and outputs must be inside this repository;
if the primary supplies an accepted run from a registered worktree, retain its
original bytes in an ignored local artifact directory, preserving all hashes.
Do not rewrite policy, source records, manifests, calendar values or traces.
A first smoke is one selected public training fixture at one repetition. The ignored
primary `artifacts/t15-preparation/smoke-host001.json` contains the unchanged
HOST-001/PUB01 training row; `ready-execution-plan.json` records the original
development declaration/counts and stage sequence. Revalidate both before live use. Keep it
separate from the full declaration and complete both candidates' training before
either validation. Smoke evidence cannot become three-repeat comparison evidence.

```sh
python3.11 -m benchmarks.hosted --dry-run --brief A --split train
python3.11 -m benchmarks.hosted --execute --brief A --split train \
  --agent-id AGENT_ID --deployment-manifest artifacts/SELECTED_BUILD/manifest.json \
  --cases benchmarks/fixtures/hosted-train-A.json --reps 3 --concurrency 5 --budget-seconds 1800 \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock \
  --evidence-dir artifacts/evaluations --out artifacts/UNIQUE_TRAIN_SUMMARY.json
```

If required, add `--bank-connect-address` with the trusted operator IP established
by current verified-TLS checks. The builder's existing test hook obtains the same
choice from controller environment `FREYA_BANK_CONNECT_ADDRESS`, which is excluded
from generation subprocesses. Do not put controller route/credentials in prompts.
Use identical runtime choice and actual bank calendar for both candidates; a changed
server calendar yields incomparable evidence rather than an invented correction.
Repeat the full command for the peer's training, then both validation splits, and
separately for B (ordered A-plus-B approved builder input). Each output path is new.
The command prints the retained `evidence_record` path even when behavior fails.

```sh
python3.11 -m benchmarks.bundle \
  --manifest artifacts/SELECTED_BUILD/manifest.json \
  --workflow artifacts/SELECTED_BUILD/submission.workflow.json \
  --trace artifacts/SELECTED_BUILD/trace.jsonl \
  --build-provenance artifacts/SELECTED_BUILD/build-provenance.json \
  --train artifacts/evaluations/TRAIN_UUID/trial.json \
  --validation artifacts/evaluations/VALIDATION_UUID/trial.json \
  --out artifacts/UNIQUE_CANDIDATE_BUNDLE.json
python3.11 -m benchmarks.compare --baseline artifacts/BASELINE_A_BUNDLE.json \
  --structured artifacts/STRUCTURED_A_BUNDLE.json --out artifacts/UNIQUE_A_COMPARISON.json
```

The rapid checkpoint selects source `2d09c31` as an earlier guarded-builder
baseline, rebuilt through its existing entrypoint with equivalent conditions.
This measures incremental builder changes, not the organizer's unstructured
strategy. The present builder has no `--baseline` switch; do not invent one or
treat manually authored diagnostic agents as builder baselines.
Both algorithms need actual same authenticated model/effort and equivalent original
isolation/source/trace evidence. Complete accepted structured generation first.
Actual baseline/structured 360-call evaluation, failure-family corrections and T14
live repair acceptance remain outstanding. T15 stays open until those original
acceptance items and dependencies are met.

The evaluation deadline is independently configurable with `--budget-seconds`
(default 1800, resource bound 86400); it does not relax the builder's 1800-second
limit. `--max-call-seconds` records and forwards the actual documented per-call
cap (default 420, resource bound 3600). The primary selects duration/budget after
observing a genuine candidate smoke; do not assume a shorter cap fits difficult
calls. Cap-caused failures stay in the original report. Both settings must match
across comparison peers, and frozen runtime settings remain enforced.

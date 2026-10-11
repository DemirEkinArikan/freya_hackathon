# S06 shared hosted operator checkpoint

This checkpoint prepares the launch queue and corrects version read-back. It
does not complete T15 or claim an accepted banking candidate. Starting source is
`4ef4e6275ee7cb4cb4fd010a3cf1e12f71b9b38b`; the shared cutoff is 11 October
2026, 09:15 Istanbul / 06:15 UTC, including the final answer.

## Observed staging state

At **05:57:09–05:57:15 UTC**, a bounded read-only check held the canonical lock
and queried the visible workspace, complete simulation and agent inventories,
and the pending diagnostic agent's stored workflow/version. All four simulation
inventory rows were read. No active simulation and no simulation matching the
pending name or agent were present. The pending agent exists; its stored phone
model is `experimental-5` and its sole published version is 1. This is diagnostic
platform metadata, not an accepted banking result.

The current canonical state is **creation_pending with no simulation ID**:

```text
/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock
/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.json
```

Its SHA-256 was
`351527e69b908be703933e7ca488572f606ccddbd19028dc48998247a096b6a6`.
The exact lease bytes were unchanged before/after the locked observation. No
remote mutation, simulation, bank session or workflow publication occurred.
The ignored original status record is
`artifacts/thread-coordination/s06-server-reconciliation.json` in the primary
checkout. The inventory observation does not silently turn the pending lease
into a completed simulation. Historical completed lease checks concern older
states and must not be reused for this state.

Before any launch, reconcile the creation against current complete server
inventory while holding this same lock. Check the exact creation name/agent and
current launcher status; preserve a matching simulation's real IDs and read its
status. If creation is demonstrated absent and no matching request remains in
flight, retain the original pending state and negative lookup evidence before
recording a resolved handoff. An unknown or active result blocks a new launch.
This is a technical resource check, not a named-person approval gate. S06 left
the pending lease intact because no accepted launch request is available.

## Candidate requests and queue

[S06-LAUNCH-QUEUE.json](S06-LAUNCH-QUEUE.json) records the ordered work and explicit
pending fields. At this checkpoint there is **no accepted artifact/version
request**. The callback worker reserves the approved callback input at source
`4ef4e62`; its latest handoff says `prepared_no_model_launch`. Recorded session
68621 is a source test session, not a generation process. Approved input copies
exist; no fresh generation output directory, original manifest, tool-free audit,
export or candidate version is present. A process inventory also found no active
Freya builder process. Older train-1/train-2 manifests are terminal failures;
their old PID/session numbers must not be presented as running jobs.

The historical public-A exported run `20261010T234440089175Z` retains stored
read-back, but its independent review says `source-fidelity-incomplete` and
hosted behavior unverified. Later source fixes do not accept that original
candidate. It is excluded from this launch queue. Existing actual greeting
adapter calls remain banking FAILs and cannot substitute for a generated smoke.

The next actual execution is one requested, accepted public training candidate
smoke, at one repetition and concurrency one. The approved primary smoke
manifest selects HOST-001 / PUB-01 and was revalidated against unchanged seed
facts. It has one row, manifest digest
`f08f8b2512524482125c08a513dd50e98aecab839fac13b9bab009ba35e81867`,
and row digest
`43e0836baa245c65cbf87d608d31ea974fdf27421327c498be58334e85f0a9ab`.
These fingerprints contain no evaluator payload. Smoke is separate from the
full development declaration and cannot satisfy three-repeat comparison.

Then run the requested baseline/structured training before either validation;
critical repetitions follow the recorded candidate plan. Post-freeze holdouts
remain gated on T15 acceptance and an actual freeze/fresh holdout lifecycle.
Do not materialize or consume reserved holdouts as preparation. Each builder
retains its original 1,800-second global budget and export reserve; evaluation
budgets never extend that deadline. An accepted candidate's smoke must fit the
remaining operator window including recording and handoff. Full development
evaluation has no demonstrated duration that fits this short cutoff and is
explicitly pending, rather than launched with an invented cap.

## Launch and evidence handoff

A recorded launch request supplies the original build directory, approved input
hashes, source/config record and build provenance, fresh tool-free audit/catalog,
original trace, selected manifest/export and acceptance evidence. Bind exact
agent/workflow/version IDs and graph/export hashes. Publish/read-back belongs
to the build's recorded request; do not update another active candidate.

Revalidate the original fixture selection with `benchmarks.hosted.load_cases`.
Use the existing `python3.11 -m benchmarks.hosted --execute` path with an explicit
deployment manifest, canonical lease, fresh output/evidence directory, bounded
repetitions/concurrency/budget/call duration. The one-row smoke uses `--reps 1
--concurrency 1`. Select call duration and evaluation budget from actual request
and remaining time; retain cap failures. Run only unchanged official seed facts,
fresh customer sessions, full pagination and verified version attribution.

Retain complete original production `trial.json` records even when behavior or
infrastructure fails. Record actual bank calendars, effective models/route/caps,
trace/audit/export/source bindings and every infrastructure retry. Credentials,
session capabilities and raw model events stay out of GitHub. TLS verification,
origin/Host/SNI identity and redirect restrictions remain enforced. Feed original
train/validation records to `benchmarks.bundle` and comparable peers to
`benchmarks.compare`; reconstructed summaries are not production bundles. See
[the recorder workflow](T11-EVALUATION-EVIDENCE.md) and
[T15 execution coordination](T15-DEVELOPMENT-EXECUTION.md).

## Narrow runner correction

`HostedBackend.validate_deployment` previously indexed `response["versions"]`.
The kit permits named wrappers and bare route payloads, so a valid bare version
list raised TypeError before a candidate smoke. It also accepted a selected
published row when another row claimed publication. Read-back now accepts both
documented shapes, validates version metadata, rejects duplicate IDs/numbers and
requires exactly one published row matching the requested ID and number.
Existing graph/agent checks and before/after/per-call version attribution remain.
A later unpublished draft does not change the sole published pin.

Dedicated regressions reproduce the original failures and check these boundaries;
the original failure log is retained at
`/private/tmp/rapid-s06-pin-pre-fix-20261011.log`. Exact-head verification and
matching merged PR are recorded in the issue checkpoint and canonical S06
handoff after checks. Source checks establish runner behavior only; actual
accepted-candidate smoke, controlled comparison, T14 and downstream task
deliverables remain pending.

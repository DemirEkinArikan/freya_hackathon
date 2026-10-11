# T16 fresh held-out evaluation

Issue [17](https://github.com/DemirEkinArikan/freya_hackathon/issues/17) depends
on [T15](https://github.com/DemirEkinArikan/freya_hackathon/issues/16).
This runbook prepares work; it records no blind passes. At this preparation
checkpoint T15 remains open, an accepted candidate is absent, and both fixture
packs contain 60 reserved, zero authored holdouts. Do not freeze the acceptance
candidate, author real holdouts or execute them until T15's actual development
and baseline comparison evidence is accepted. Mock reports and source checks
cannot satisfy that dependency.

## Commands permitted during preparation

Run from the isolated task checkout with Python 3.11:

```sh
python3.11 -m benchmarks.freeze readiness --candidate-root builder --runtime-root .
python3.11 -m benchmarks.offline --split train --check-helpers --check-equivalence
python3.11 -m benchmarks.offline --split validation --check-helpers --check-equivalence
python3.11 -m benchmarks.hosted --dry-run --split train
python3.11 -m unittest discover -s tests -v
python3.11 tools/verify_kit.py
python3.11 tools/package_submission.py --check
git diff --check
```

Readiness hashes actual source bytes and fixture manifests, reports observed
split/materialization counts, and optionally inspects the runtime inventory.
It writes no freeze or fixture and calls no generator or platform. Missing
resolved configuration is reported as `config_supplied: false`; the observed
Python version alone is not the accepted build configuration. Add
`--config-file <nonsecret-original-config.json>` only for an actually recorded
configuration. Inventory inspection is not a claim that a run used these files.
Reservations accept only scalar descriptive metadata and the hosted seed/style
reservation fields. Readiness rejects policy/oracle payloads in reserved rows,
already authored profiles referenced by reserved local rows, and blank scripts
marked materialized. These checks classify content; they do not establish
independent authoring or accepted evaluation.

The generic candidate digest covers visible source/config extensions recursively
under the supplied root. `builder/` alone omits benchmark runtime hooks. The
optional runtime inventory separately hashes `builder/*.py`, prompts and
top-level `benchmarks/*.py`, excluding fixture/customer/oracle directories.
Neither inventory includes every host dependency or authenticates local files.

## Freeze evidence after T15

T15 hands over its accepted source/configuration and actual development comparison
evidence, including the original A and B build manifests, exports and published
deployment pins. Retain those original artifacts and hashes; a later manifest or
republished version cannot stand in for the accepted deployment. T16 freezes
that accepted candidate before independent authoring. Give S06 the freeze and
authored pack hashes, the separate original A/B deployment manifests and IDs,
recorded evaluation settings, and unique result/evidence paths for scheduling.
S06 reconciles the shared lease with current server status before each launch
and returns the original trial paths and simulation/iteration/call identifiers.
This is a technical resource handoff; any authorized worker can perform it.

Record the exact checked/pushed Git head and merged T15 PR; original source
snapshot, trace and manifest; ordered approved source/API fingerprints;
model/reasoning, Python and CLI versions; 1,800-second build deadline,
300-second export reserve and repair settings. Verify mandatory tool-free
generation auditing and the phone model `experimental-5`. Preserve the
actual published A/B deployment IDs, positive version numbers, read-back
graph/export hashes and per-call version evidence. Use original invocation
evidence described in [SOURCE-PROVENANCE.md](SOURCE-PROVENANCE.md); do not
reconstruct an old run's missing provenance from a later checkout.

Prepare an isolated `artifacts/t16/candidate-source/` containing only reviewed
runtime source, prompts and dependency/configuration files. Match every copied
runtime file to the original invocation inventory and checked head, preserving
relative paths. Freeze this inventory together with the actual nonsecret
resolved configuration in `artifacts/t16/runtime-config.json`. The executed
checkout must independently match those bytes before and after every evaluation;
freezing a copy alone does not prove which source executed. Do not put evaluator
packs or their exports under this candidate root. Keep the freeze record and
resolved configuration outside it too.

Only after the evidence above is corroborated:

```sh
python3.11 -m benchmarks.freeze create \
  --candidate-root artifacts/t16/candidate-source \
  --config-file artifacts/t16/runtime-config.json \
  --record benchmarks/freezes/t16-candidate-1.json
python3.11 -m benchmarks.freeze verify --record benchmarks/freezes/t16-candidate-1.json
mkdir -p benchmarks/holdout/t16-candidate-1
cp benchmarks/fixtures/offline.json benchmarks/holdout/t16-candidate-1/offline.json
cp benchmarks/fixtures/hosted.json benchmarks/holdout/t16-candidate-1/hosted.json
```

The copies contain reservations, not authored cases. Record the frozen hash and
creation time before independent authoring starts. The lifecycle tooling cannot
verify T15 acceptance or independence on its own; retain that review evidence.

## Independent authoring

An independent evaluator authors 60 genuinely fresh local policy/schema cases
and 60 distinct hosted scripts in the new evaluator directory. No automatic
authoring command currently establishes this requirement. In particular,
`freeze generate-holdout` randomizes development profiles and adds known
script suffixes: its result explicitly declares `acceptance: false`. Do not use
that command as fresh-held-out acceptance.

For each actual case record ID, authoring timestamp after freeze, source/API
hashes, independent oracle hash, policy/wording coverage, critical status and
freshness lineage against every previously exposed pack. Local cases need
independently specified effective policy values, identity/order/retry/closing
rules and expected ledgers, not expectations extracted from candidate output.
Vary policy and schema meaning; random numbers or renamed wording alone do not
prove unseen-policy adaptation. Author clean and raw documents independently
with identical fixed semantics/API and separate source hashes. Pair variants
are representations of the same case, not additional cases.

Hosted scripts preserve the pinned official seed's complete identity, products,
variables, faults, original check prefix and expected ledger. Use the existing
hosted `load_cases`/`validate_rows` seed audit before execution. Compound variants
also retain the official outcome-constraint digest. New wording must not change
security answers, verification timing or final customer decisions.

Attach the exact `freeze.record` and `freeze.candidate_sha256` to each authored
manifest. Keep authored scripts, oracles and results in the evaluator directory,
outside generator inputs and source packages. Never supply their paths to the
generation runtime or use generated-policy/workflow edits to fix an outcome.

After independent authoring, these commands inspect syntax and actual counts:

```sh
python3.11 -m json.tool benchmarks/holdout/t16-candidate-1/offline.json > /dev/null
python3.11 -m json.tool benchmarks/holdout/t16-candidate-1/hosted.json > /dev/null
python3.11 -m benchmarks.freeze readiness --candidate-root artifacts/t16/candidate-source \
  --config-file artifacts/t16/runtime-config.json \
  --fixtures-dir benchmarks/holdout/t16-candidate-1
python3.11 -m benchmarks.hosted --dry-run --split holdout \
  --cases benchmarks/holdout/t16-candidate-1/hosted.json
```

Require 60 actual materialized cases in each pack, no reservations or duplicate
IDs, immutable authored inventories and independently reviewed oracle/freshness
evidence before evaluating. Syntax and seed audits alone do not establish these.

## Evaluate adaptation separately from wording

For each independently authored local profile, export only its approved policy
documents and OpenAPI. Preserve the returned brief order. Example placeholders
below must be replaced with actual authored IDs and original run paths:

```sh
python3.11 -m benchmarks.freeze verify --record benchmarks/freezes/t16-candidate-1.json
python3.11 -m benchmarks.export_inputs \
  --cases benchmarks/holdout/t16-candidate-1/offline.json \
  --profile <authored-profile-id> --out artifacts/t16/inputs/<authored-profile-id>
python3.11 -m builder.build --extract-policy-only \
  --brief <first-returned-brief-path> --brief <next-returned-brief-path> \
  --tool-api <returned-tool-api-path> --output-dir artifacts/t16/local-runs/<authored-profile-id> \
  --model <recorded-frozen-model> --reasoning-effort <recorded-frozen-effort>
python3.11 -m benchmarks.offline \
  --cases benchmarks/holdout/t16-candidate-1/offline.json --split holdout \
  --check-helpers
python3.11 -m benchmarks.offline \
  --cases benchmarks/holdout/t16-candidate-1/offline.json --split holdout \
  --profile <authored-profile-id> --candidate-contract <original-run-directory>/policy.json
```

Invoke the frozen generator independently on each approved packet/API using the
recorded model/reasoning and its mandatory isolation audit. Synthetic `.invalid`
services remain local; never attach them to Freya. Compare actual candidate
contracts against pre-authored effective-value oracles and actual observed
behavior/ledgers against independent expectations. Do this for both independently
authored clean/raw representations. Helper self-checks, evaluator expected-policy
compilation and the development renderer's `--check-equivalence` are useful
sanity checks but do not measure candidate adaptation or independently authored
representation equivalence. Report compilation, behavior and dialogue evidence
separately; local contract checks cannot prove a conversation pass.

Repeat `--brief` once per returned source, in that order; do not add invented
placeholder files. The successful extraction's returned `run_directory` and
original manifest identify its actual `policy.json`, contract and source proof.
Policy extraction is only the compilation component of adaptation assessment.
After actual local behavior is observed, normalize its ledgers by fixture ID
and compare them against the independent oracles:

```sh
python3.11 -m benchmarks.offline \
  --cases benchmarks/holdout/t16-candidate-1/offline.json --split holdout \
  --ledgers <actual-normalized-ledgers.json>
```

Missing observed ledgers remain missing evidence; do not substitute expected
ledgers or helper checks to create a behavior pass.

Hosted evaluation measures wording robustness under official policies and public
seeds. The bank does not document arbitrary synthetic policy/schema deployment.
Evaluate the two briefs against their own frozen published deployments, require
three repetitions per variant and retain every ledger, judge, transcript,
session fingerprint, simulation/iteration/call reference and version attribution.
Compare the original pins before and after evaluation. All critical variants
should pass all three repetitions; report observed failures and missing evidence.

Before any live execution, coordinate with the primary thread and reconcile the
actual server status with the sole shared lease:

```text
/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock
/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.json
```

`benchmarks.hosted --execute` requires explicit `--simulation-lease`; use the
canonical absolute path above from every checkout. It verifies the held-out
freeze/configuration, uses the original deployment manifest to verify its export
and published pin, and reads back the published
deployment before and after evaluation, and records sanitized trial evidence.
The builder hook uses its supplied client's lease; do not substitute a worktree
or output-local lease. An absent local lock alone is not proof that no server
simulation is running. Never replace an active/unknown lease or automatically
retry a slow simulation.

After T15 acceptance, freeze and independent authoring, schedule separate A/B
invocations with their own accepted original pins. Replace every placeholder with
the handed-over paths, IDs and frozen settings before invoking these templates;
use a new `<evaluation-id>` for every attempt. Execute from the verified source
checkout, keeping referenced manifests/exports and outputs within that project:

```sh
python3.11 -m benchmarks.hosted --execute \
  --cases benchmarks/holdout/t16-candidate-1/hosted.json --split holdout --brief A \
  --agent-id <accepted-A-agent-id> \
  --deployment-manifest <original-accepted-A-build-directory>/manifest.json \
  --reps 3 --concurrency <recorded-concurrency> \
  --budget-seconds <recorded-evaluation-budget> --max-call-seconds <recorded-max-call-seconds> \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock \
  --out artifacts/t16/hosted/<evaluation-id>/A/results.json \
  --evidence-dir artifacts/t16/hosted/<evaluation-id>/A/evidence
python3.11 -m benchmarks.hosted --execute \
  --cases benchmarks/holdout/t16-candidate-1/hosted.json --split holdout --brief B \
  --agent-id <accepted-B-agent-id> \
  --deployment-manifest <original-accepted-B-build-directory>/manifest.json \
  --reps 3 --concurrency <recorded-concurrency> \
  --budget-seconds <recorded-evaluation-budget> --max-call-seconds <recorded-max-call-seconds> \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock \
  --out artifacts/t16/hosted/<evaluation-id>/B/results.json \
  --evidence-dir artifacts/t16/hosted/<evaluation-id>/B/evidence
```

The CLI refuses an existing `--out` and creates an exclusive attempt directory
under `--evidence-dir`, printing its `trial.json` path. Preserve that record
and any available result, including interrupted attempts and lease identifiers; do
not overwrite failures or reconstruct missing invocation evidence. Record actual
A/B selections and repetitions against the 60 distinct hosted cases: splitting
by brief or running three repetitions does not add cases. The local target is
60 distinct authored cases with clean/raw pairs, also counted once per case.
Keep compilation, observed local behavior and hosted dialogue/wording results
separate. Evaluation budgets do not extend the builder's 1,800-second deadline.

## Consume and preserve validation evidence

If any holdout failure informs a change, consume before repair:

```sh
python3.11 -m benchmarks.freeze consume --record benchmarks/freezes/t16-candidate-1.json \
  --reason 'Recorded held-out failure informed repair; original pack retained as validation'
```

Consumption permanently rejects blind verification; it does not move files or
rewrite split labels automatically. Preserve immutable original manifests and
results with their hashes. Create a separately identified validation copy of
the exposed cases, set their split to `validation`, update actual split counts,
and record the consumed candidate/pack hashes and disclosure reason as lineage.
Preserve the original independent oracles and source packets. Do not simply
delete freeze metadata and call the old set fresh. A new blind claim requires
a new accepted source/config freeze and another independently authored pack.

Completion reports actual local adaptation coverage and hosted wording coverage
separately, strict cases passing all repetitions, failures by policy/security/
honesty/behavior, infrastructure/evidence gaps and source/version evidence.
Keep issue17 open until real acceptance and dependencies are complete. Only then
invoke `cowork.py complete` with a merged matching-head acceptance PR. Checked
preparation-only work may be pushed and merged now, with the outstanding
acceptance/dependency evidence recorded explicitly in its issue checkpoint.

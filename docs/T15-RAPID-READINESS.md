# T15 development comparison checkpoint — 11 October 2026

T15 remains open. The development inventory is validated, but no controlled
baseline/structured pair has completed the required real trials. The
[recorded preparation plan](runtime-evidence/t15-rapid-development-plan-20261011.json)
contains source selections, approved input fingerprints, development-only
manifest fingerprints, parity settings and pending gates. It is preparation
evidence, not a comparison bundle or candidate freeze.

## Selected algorithms and equivalent inputs

Use `2d09c31d2f2d25a0e01466e707f560127634cad3` as the **earlier guarded-builder
baseline** and `4ef4e6275ee7cb4cb4fd010a3cf1e12f71b9b38b` as the proposed
structured algorithm. This measures changes to this repository's builder;
it is not a comparison against the organizer's Claude implementation or an
unstructured prompting strategy. Selection precedes any new results.

The controller, generation runtime, mandatory audit, source-provenance module
and platform adapter are byte-identical between these selections. Differences
include policy/guards/requirements/prompts/validation. Both support the same
existing authenticated Codex route, `gpt-6.1-sol` with high reasoning, a fresh
mandatory tool-free audit, the fixed 1,800-second build deadline and 300-second
export reserve, original invocation provenance, live publication/read-back and
export. Each may fail; preserve complete failures and their original artifacts.

The plan's Git tree and `selection_files_sha256` identify selected source bytes
only. They do not constitute `builder_source_sha256`, which must be recorded
with the actual resolved configuration at each fresh invocation. The old
generate-only A+B run cannot provide a new live baseline's provenance. If a
source changes, select and fingerprint the new algorithm explicitly before
using its results.

Both methods use unchanged kit-v3 Brief A for A, ordered Brief A then Brief B
for B, and the official OpenAPI. Start in each immutable source checkout:

```sh
python3.11 builder/build.py \
  --brief build-the-builder/briefs/brief-a.md \
  --tool-api build-the-builder/tools/openapi.json \
  --output-dir artifacts/UNIQUE_METHOD_A_BUILD \
  --slot UNIQUE_METHOD_A_SLOT \
  --model gpt-6.1-sol --reasoning-effort high \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock
```

For B append `--brief build-the-builder/briefs/brief-b.md` after A and use a
new B output/slot. Live mode is the default; there is no `--baseline` switch.
Controller credentials stay outside generation. No fixtures, scripts, expected
records or evaluation reports enter these commands. Prioritize accepted
structured builds before generating the historical baseline.

## Actual coverage and retained attempts

All four dedicated development manifests passed the hosted runner's dry-run
validation at the selected structured source:

| Brief | Split | Cases | Repetitions | Planned calls per method | Actual comparison calls in this lane |
|---|---|---:|---:|---:|---:|
| A | Training | 26 | 3 | 78 | 0 |
| B | Training | 10 | 3 | 30 | 0 |
| A | Validation | 14 | 3 | 42 | 0 |
| B | Validation | 10 | 3 | 30 | 0 |

This is 36 training and 24 validation cases per method: 180 calls per method,
360 total. Complete **all training for both methods and briefs before any
validation**. Use `hosted-train-A.json`, `hosted-train-B.json`,
`hosted-validation-A.json` and `hosted-validation-B.json` explicitly, rather
than the mixed manifest. The plan hashes both each dedicated file and its
declared rows; the recorder still must retain the original rows at execution.

Keep evaluation settings equal: three repetitions, concurrency five,
1,800-second evaluation budget and 420-second call cap are the proposed
settings. A genuine smoke may justify different settings, but both peers must
record exactly the same values. This evaluation budget does not extend a
builder's deadline. Keep `experimental-5`, kit persona/analysis models, actual
server calendars and the selected verified bank transport equal. Calendar
differences make a comparison incomparable; never correct them by hand.

The preserved public-A run `20261010T234440089175Z`, source
`30a111735760a575a080ecc0301e17dbd4ed15ad`, exported in 686.686 seconds. Its
original manifest/source-record/trace/export bindings and 39 captured source
files were independently rechecked. It has no hosted trial. Its original
acceptance review remains `source-fidelity-incomplete`: entry routing,
literal confirmation wording, termination bounds, product readback and
contradictory-receipt handling remain deficient or unproved. The agent
`15324791-0487-4cc9-b71b-8495eed757e6`, version 2, is a retained diagnostic
attempt, not an accepted candidate. Later fixes do not change that export.
The four earlier greeting-only diagnostic calls all failed banking behavior
and do not supply a banking baseline.

## Resource and dependency handoff

The canonical lease's local metadata at reconciliation remains
`creation_pending:true`, agent `bdac34f5-ab89-47c0-a46a-57600279aa52`, name
`t05-native-duplicate-target-06bce05d57`, started
`2026-10-11T00:16:11.164552+00:00`, without a simulation ID. This lane has not
queried server status or replaced the lease. The operator must reconcile the
original creation against actual server state before any new simulation;
historical completed records cannot resolve this pending request. No model,
deployment, bank session or simulation was started by this lane.

The approved callback build stays with its recorded worker. S03/S04 generated
goal witnesses and S05 fault evidence are separate adaptation/action evidence;
they cannot replace these controlled public development trials. S06 receives
the selected original manifests/exports/provenance and unchanged published
versions after they exist. Each repaired candidate requires fresh attributed
evaluation; retain its failed predecessor. Never edit generated graphs.

T16 receives this preparation plan with **no accepted source/config/artifact
freeze**. Required next evidence is: accepted fresh structured A/B builds,
fresh controlled baseline A/B builds, reconciled simulation resource, full
both-method training then validation records, per-brief bundles/comparisons,
failure-family regressions and completed T14 dependencies. Until then, T15
stays open and reserved holdouts must not be evaluated.

Audit access limitation: a read-only audit parsed the mixed hosted manifest
once solely for aggregate hashes and brief/split counts, including reserved
rows in memory. It did not inspect or output reserved scripts/oracles, provide
them to generation, or evaluate them. Subsequent inventory checks used only
dedicated development manifests. This is not a claim that reserved file bytes
were wholly untouched, nor a claim of completed blind evaluation.

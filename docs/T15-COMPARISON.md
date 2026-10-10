# T15 recorded development comparison

`benchmarks.compare` compares already recorded baseline and structured-builder
evidence for one official brief. It executes no generator, bank request, Freya
request or simulation. This prepares the comparison machinery; T14/T15 still
require their authorized runtime and development trials.

```sh
python3.11 -m benchmarks.compare \
  --baseline artifacts/baseline-comparison.json \
  --structured artifacts/structured-comparison.json \
  --out artifacts/development-comparison.json
```

Inputs must be local project JSON files without symlink traversal. Output
contains counts, decisions, timings and hashes. It excludes source text,
transcripts, ledger rows, customer/session/call IDs and workflow IDs. Invalid
or incomplete comparisons return `comparable: false` and a fixed rejection code;
the command exits with status 1. Missing evidence is never ranked as a weaker
behavioral result.

Each input is a version 1 bundle with these recorded fields:

| Field | Required evidence |
|---|---|
| `version` | Integer `1` |
| `brief` | `A` or `B`; compare each brief separately |
| `source_sha256` | `digest({"brief_sha256": [source["sha256"] for source in build_manifest["sources"]], "tool_api_sha256": build_manifest["tool_api_sha256"]})`; ordered brief hashes preserve amendment order |
| `builder_source_sha256` | Recorded snapshot hash of the builder code/configuration used for this selected run; retained separately for each method and never forced equal |
| `conditions` | Exact configuration described below, captured for the evaluated trials |
| `build` | Selected run's timezone-aware ISO `started_at` and `ended_at`; at most 1,800 seconds |
| `build_manifest` | Original live/exported builder manifest with model, reasoning effort, ordered sources, API hash, export hash and verified deployment IDs/version |
| `workflow_export` | Actual submitted platform export; its full digest must match the manifest, and its graph must match each report's pinned graph |
| `trials.train`, `trials.validation` | Each has the original `evaluate` `report`, `case_manifest_sha256`, timezone-aware ISO `started_at` and `ended_at` |

Here `digest` is `builder.safety.digest` over canonical JSON. Preserve the
original reports and manifests. Record conditions and timestamps when running
authorized trials; do not reconstruct missing values from filenames, labels,
defaults, file modification times or a favorable result. This module validates
recorded provenance and consistency; it cannot authenticate a fabricated local
JSON file or replace server-side evidence.

The conditions object requires `builder_model`, `reasoning_effort`, integer
`reps` (at least three), integer `concurrency` (1–5), and integer
`budget_seconds` (1–1,800 for each evaluation). It also requires the actual
`platform_base_url`, `bank_base_url`, `agent_llm`, `persona_llm`, `analysis_llm`
and `calendar_sha256`. The calendar hash identifies the recorded per-fixture
bank-supplied call dates/times and relevant timezone conditions, excluding fresh
customer/session identifiers. Its contents must be captured in the evaluator
evidence; an intended date or host clock is insufficient. Both bundles must
have exactly equal conditions objects, including any additional recorded
settings.

The kit's fixed models are:

```json
{
  "agent_llm": {"provider": "experimental-5", "model": "experimental-5"},
  "persona_llm": {"provider": "google", "model": "gemini-3-flash-preview"},
  "analysis_llm": {"provider": "google", "model": "gemini-3-flash-preview"},
  "platform_base_url": "https://staging-app.freyavoice.ai",
  "bank_base_url": "https://freya-bank-mock.freyavoice.workers.dev"
}
```

Baseline parity requires the same approved briefs/API, fixture manifest per
split, exact fixture inventory, repetitions, calendar, models, configuration
and evaluation budget. Each candidate retains one unchanged exported graph
across its train and validation trials. Using the organizer's example builder
with a different generation model is not a controlled comparison under this
schema. Its original trace does not automatically satisfy the manifest format;
collect equivalent original evidence through an explicitly authorized baseline
run. Missing baseline provenance produces an incomparable result, not a claim
that the structured builder won.

Builder source snapshots are recorded independently: baseline and structured
algorithms may differ. A method label belongs beside the bundle, not inside the
shared `conditions` object; method-specific configuration must not conceal a
change to a shared evaluation condition.

Every report needs a verified matching before/after publication pin and
`per_call_version_verified: true`. Every fixture/repetition needs its ledger,
judge verdict, nonempty transcript, fresh session fingerprint, call ID,
simulation/iteration attribution and evaluator `version_attribution` record.
A matching positive integer version on the judge or transcript suffices; any
conflicting or malformed observed version rejects attribution. Aggregate
passes, failures, runs and repetitions are recomputed and checked. Reused
evidence across repetitions, splits or candidates is rejected. Both training
runs must finish before either validation run begins, and reports are emitted
in training then validation order.

A case passes only when all its required repetitions pass. Behavior,
conversation, honesty, policy and security failures remain distinct from
infrastructure/evidence gaps. More failed behavioral runs block a favorable
decision even if strict case passes improve. Amount and build-time tie breaking
is enabled only when both candidates pass every case without recorded failures.
Amount counts each strict case once, using its minimum recorded promised amount
across repetitions; it never multiplies a customer's amount by the repetition
count. Budgets and build/export attribution are checked independently.

This comparison concerns recorded known public seeds and official policies.
It establishes no new conversation trial, unseen-policy generalization, hidden
brief performance or T15 acceptance. The schema does not manufacture absent
provenance, and the original reports' outcome/policy/judge assessments remain
the source of their recorded verdicts.

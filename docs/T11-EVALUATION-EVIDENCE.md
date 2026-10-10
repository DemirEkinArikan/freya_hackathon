# T11 evaluation recording and T15 bundle assembly

This checkpoint adds evaluator-only code. It does not change `builder/`, the
policy prompts, guard compilation, ledger verdicts or the comparison ranking.
The existing `run_builder_tests` hook is unchanged. No live trial is established
by these changes or their mock tests.

## Source checkpoint and isolation

The T11 branch starts at `9826b37`, a snapshot of T05's in-progress source-grounded
interpretation review on top of `079d4cd`. It includes the earlier T02 isolation
and T05 readback commits. The original T05 checkout, HEAD and index were preserved;
its uncommitted files remain under that chat's control. Later T05 changes are not
automatically copied. Merge/cherry-pick integration must preserve those changes.
Keep evaluator commits separate from the inherited T05 checkpoint. Do not merge
the whole branch into main as a way of approving unfinished T05 work.

## Recording an authorized standalone evaluation

The existing `python3.11 -m benchmarks.hosted` command remains a network-free
inspection unless `--execute` is supplied. Authorized standalone execution now
records each attempt in `artifacts/evaluations/<unique-id>/trial.json`. The CLI
prints that path alongside the existing outcome counts. Its default summary
output is now `artifacts/hosted-results.json`; rerunning may replace that summary,
but never replaces the separately retained attempt.

The record contains:

- A live/mock label, timezone-aware start/end times and monotonic elapsed time.
- The actual selected fixture hash, IDs, split/brief and effective CLI settings.
- The pinned builder model/effort if supplied; absent values remain absent.
- The adapter's fixed requested models and service endpoints. These record request
  configuration, not independent proof of which remote model actually executed.
- Per-session `now` and optional `timezone`, read from the bank response. These
  fields are allowlisted and validated; customer identity and unrelated variables
  are excluded. A naive server timestamp remains naive; no timezone is invented.
- An unchanged sanitized evaluator report, including failures and available
  ledger/judge/transcript and version evidence.
- Durations of adapter operations. Simulation duration includes polling; call
  retrieval duration is an HTTP operation duration, not agent response latency.

Metadata is persisted before calls and calendar evidence before simulations.
Completed or ordinary failed evaluations retain the existing partial report.
Exceptions escaping evaluation leave a failed record with a fixed error code,
then propagate the original exception. A process killed without cleanup can leave
`status: running`; that is incomplete, never a pass. No new retries or API calls
are introduced. Known credentials and fresh session/customer IDs are redacted
before persistence. Raw transcripts remain local ignored artifacts, never issue
comments or submission source files.

`record_evaluation` supports fake backends with `kind="mock"`; these exercise the
same recorder but cannot be used by bundle assembly as live evidence. Labels and
hashes are provenance checks, not authentication of arbitrary local JSON files.

## Preparing a comparison bundle

`python3.11 -m benchmarks.bundle` accepts original manifest/export/trace plus train
and validation trial records and a recorded build-provenance file:

```sh
python3.11 -m benchmarks.bundle \
  --manifest artifacts/selected-build/manifest.json \
  --workflow artifacts/selected-build/submission.workflow.json \
  --trace artifacts/selected-build/trace.jsonl \
  --build-provenance artifacts/selected-build/build-provenance.json \
  --train artifacts/evaluations/TRAIN_RUN/trial.json \
  --validation artifacts/evaluations/VALIDATION_RUN/trial.json \
  --out artifacts/selected-comparison.json
```

Build provenance requires `builder_source_sha256` for the source/config snapshot
used by that build, `build_manifest_sha256` and `trace_sha256`. All hashes use the
existing canonical-JSON `builder.safety.digest`; the trace hash covers its parsed
event list. The source/config fingerprint must be captured for the actual build,
not reconstructed from the checkout after it has changed. The manifest and trace
hashes bind that original provenance to the selected completed build.

This checkpoint does not add missing source-snapshot capture to T05. If that
original evidence is absent, assembly returns `ready: false` with
`builder_source_unrecorded`. Integrating capture at the next actual T05 invocation
is part of the subsequent live integration. Historical runs must not receive an
invented source hash or timestamps. Build times are taken from validated original
trace start/end events, never filesystem modification times.

Assembly verifies both selected split inventories, complete validated per-case
calendar evidence, hashes, settings, elapsed budgets, trace/export agreement and
the existing comparison validator. Both splits need at least three repetitions.
A single-repetition builder smoke test cannot stand in for development evaluation.
The combined calendar hash includes both splits and every repetition; baseline and
structured bundles therefore cannot silently compare different server calendars.
The output is created exclusively and never overwrites a previous selection.
Missing/failed/mixed evidence produces a fixed rejection code without an output
bundle. A prepared bundle still needs a baseline peer and the existing
`benchmarks.compare` checks; it does not establish a favorable comparison.

## Next live integration

Once T05 supplies an accepted generated deployment, use its unchanged published
workflow/export and verified version. Capture the real build source/config
provenance, then run the authorized smoke test and later separate three-repeat
training/validation trials. The T05 hook already exists; its one-repetition result
is deliberately unchanged in this checkpoint. Ensure a single shared simulation
lease across checkouts before any live execution: separate worktree-local lease
files do not coordinate the same remote Freya workspace. No live execution was
started from this T11 worktree.

## Local validation

19 focused recording/bundle tests and all 652 Python 3.11 tests pass. Tests cover
unchanged evaluator verdicts and API-call counts, actual CLI wiring, network-free
inspection, independent attempts, pre-call credential redaction, failure/partial
records, calendar completeness, mock rejection, source/trace binding and existing
comparison compatibility. All 16 kit files verify; 143 source files pass packaging
checks. These are local mock/contract results, not live T11/T15 acceptance.

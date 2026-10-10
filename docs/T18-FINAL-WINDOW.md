# T18 final-window runbook

This runbook prepares issue [19](https://github.com/DemirEkinArikan/freya_hackathon/issues/19).
It does not record a hidden build, a submitted package, or a competition pass.
Source availability, mock tests and a source archive cannot replace those gates.
The organizer kit v3 [final/rules](../build-the-builder/README.md) and later
organizer instructions govern execution. Customer cases remain hidden.

## Gates to finish before release

- Finish broad evaluation and Python 3.11 regressions before **Sunday 11 October
  2026, 17:00 UTC / 20:00 Istanbul**. Run the full suite, kit verification,
  source-package checks and `git diff --check` on the exact pushed source head.
- T17 must have its actual clean-checkout reproduction and final freeze evidence,
  including fresh policy/schema inputs, successful live publication/export in
  less than 30 minutes, permitted generation tools, `experimental-5` phone model,
  exact commit/configuration and critical cases passing all three repetitions.
  An open dependency or only a source/mock rehearsal is not this gate.
- Record frozen source commit, Python 3.11 patch version, Codex executable/version,
  explicit accessible builder model, reasoning effort, 1,800-second deadline,
  300-second export reserve, bounded repairs, kit fingerprint and source inventory.
  Keep invocation-time `builder-source.json`; do not create it retrospectively.
- Rehearse the manifest/ZIP/extraction procedure in
  [submission package documentation](SUBMISSION-PACKAGE.md). Reserve storage for
  two selected run directories and failed attempts. Preassign persistent slots
  `final-hidden-1` and `final-hidden-2`; retain their existing workspace binding
  instead of creating a new workflow on every retry.
- Obtain and read organizer submission instructions when supplied. Record the
  actual channel, permitted attachment layout, credential/import requirements,
  metadata, size limits and receipt mechanism. **No submission address is known
  in the reviewed kit.** Request missing instructions through the authorized
  organizer contact; do not guess an address or claim submission.
- Coordinate with the primary thread before any live operation. Record the
  shared absolute lease paths
  `/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock` and
  `/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.json`,
  current simulation/iteration IDs, workspace and actual server status. A local
  timeout, absent process or old timestamp does not prove a remote job finished.
  Preserve active/paused/ambiguous creation evidence; reconcile it before another
  simulation. All worktrees use this one lease, never separate local locks.

## Window and latest starts

Release is **17:00 UTC / 20:00 Istanbul**. Deadline is **20:00 UTC / 23:00 Istanbul**.
The submission buffer is **19:45–20:00 UTC / 22:45–23:00 Istanbul**. Treat 19:45 UTC
as the operational cutoff for completed builds, reviewed committed evidence,
verified ZIP and organizer-ready attachments. Do not consume that buffer on tests.

| UTC | Istanbul | Action |
|---|---|---|
| Before 17:00 | Before 20:00 | Finish broad tests, T17 acceptance/freeze and preparation gates |
| 17:00–17:10 | 20:00–20:10 | Receive both approved briefs/APIs, preserve exact bytes and ordered amendments; read submission instructions |
| 17:10–17:40 | 20:10–20:40 | Hidden-1 builder, maximum 1,800 seconds including audit, any own tests and export |
| 17:40–18:10 | 20:40–21:10 | Hidden-2 builder with its matching API, same bound |
| 18:10–19:15 | 21:10–22:15 | Inspect original evidence; bounded general bug/retry contingency only if time and gates permit |
| 19:15–19:45 | 22:15–22:45 | Review credential-free evidence, record source/evidence commits, verify package, unpack/hash-check and prepare attachments |
| 19:45–20:00 | 22:45–23:00 | Submit via supplied authorized channel, inspect upload/receipt and retain acknowledgment |

With a 30-minute packaging reserve, the latest start of the second sequential
30-minute build is **18:45 UTC / 21:45 Istanbul** and the first is **18:15 UTC /
21:15 Istanbul**. These are emergency bounds, not the target schedule. Start
both at release as planned. A queued simulation or late/invalid organizer input
reduces this margin; do not promise that two builds still fit. No new build starts
after 18:45 UTC under this schedule. Preserve failed runs rather than relabeling
late exports as successful. Repeated runs are permitted by the kit; select one
complete eligible run per final brief and retain the unsuccessful attempts.

## Receive inputs and invoke the frozen builder

Store the organizer-provided bytes only under dedicated ignored
`artifacts/hidden-inputs/hidden-1/` and `artifacts/hidden-inputs/hidden-2/` directories.
Record receipt time, organizer origin, original filenames and SHA-256 bytes in
local reviewed metadata. Do not put hidden content into issues, builder source,
static prompts or test fixtures. Preserve original newline bytes and amendment
order. Each brief gets its own matching OpenAPI; do not reuse the public API.
The illustrative names below must be replaced by the actual supplied names.

Use a fresh isolated frozen checkout; keep its inputs and output directory inside
that checkout. The checked controller's explicit `--simulation-lease` connects
both builds to the canonical shared lease outside the worktree. This controller
path is local orchestration configuration, never generation input. Do not switch
or edit another worker's active checkout. A local worktree artifact lock is not
an acceptable substitute for the shared lease.

Set `T18_SOURCE_COMMIT`, `T18_BUILDER_MODEL` and `T18_REASONING_EFFORT` from the
accepted T17 freeze. These are explicit nonsecret configuration values. Create a detached worktree from
that accepted source commit and verify `git rev-parse HEAD` equals the freeze;
`git diff --exit-code -- builder benchmarks tools` must show no source changes.
The builder reads only this repository's private `.env` as data. Do not print or
source credentials, read sibling environment files, or dump the environment.

A new worktree has no ignored `.env`. Provision a reviewed private `.env` in the
frozen checkout from this repository's authorized configuration, or inherit the
reviewed controller credential variables in its process environment. Use the
project's data parser; never source the file as shell code. Keep it outside Git
and the package. Verify presence without displaying values before release.

The new `artifacts` directory also has no slot registry. Before the first run,
retain a privately reviewed `.freya-slots.json` registry whose base URL/workspace
scope matches this controller, or append `--adopt-workflow <verified-workflow-id>`
to the first invocation of each slot. The controller verifies workspace ownership
when adopting; use actual preassigned IDs supplied by the primary thread, never
invent IDs. Retain that registry privately across retries. A slot name alone in
a fresh registry does not reuse an existing workflow.

```sh
git worktree add --detach /Users/demirekinarikan/VsCode/freya_hackathon-worktrees/t18-frozen-final "$T18_SOURCE_COMMIT"
cd /Users/demirekinarikan/VsCode/freya_hackathon-worktrees/t18-frozen-final
python3.11 builder/build.py \
  --brief artifacts/hidden-inputs/hidden-1/brief.md \
  --tool-api artifacts/hidden-inputs/hidden-1/openapi.json \
  --output-dir artifacts \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock \
  --slot final-hidden-1 \
  --model "$T18_BUILDER_MODEL" --reasoning-effort "$T18_REASONING_EFFORT"

python3.11 builder/build.py \
  --brief artifacts/hidden-inputs/hidden-2/brief.md \
  --tool-api artifacts/hidden-inputs/hidden-2/openapi.json \
  --output-dir artifacts \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock \
  --slot final-hidden-2 \
  --model "$T18_BUILDER_MODEL" --reasoning-effort "$T18_REASONING_EFFORT"
```

Repeat `--brief` in the organizer's explicit order if an input contains multiple
approved files/amendments. These commands are templates pending actual briefs,
not a claim they have run. Omit offline/generate-only modes for final builds.
Do not supply an old `--policy-file`, fixture workflow or public-case evaluator
whose bank facts do not match the final input. An approved compatible hosted
case set, if one is supplied, remains evaluator-only via `--test-cases`; keep its
runtime inside the same 1,800 seconds and shared lease. With no own hosted tests,
`hosted_behavior_verified` remains false; T17 evidence does not become a hidden
conversation pass. Every generation must pass its mandatory tool-free isolation
audit before receiving any bank material; do not disable or replace that audit.

The controller starts its own 1,800-second deadline before input processing,
reserves 300 seconds for export, updates/publishes its persistent slot and checks
stored readback. Preserve the unique timestamped run directory printed in the
manifest. The phone agent remains `experimental-5` for dialogue/routing/extraction.
A general parser/runtime bug fix may be allowed after release, but it must not
contain any hidden rule/value/name or copy hidden examples into source/static
prompts. Coordinate ownership, test the generic fix, commit it, record the new
source/config fingerprint and rerun affected builds; do not edit generated policy,
plan or workflow by hand. If both selected builds do not match one exact submitted
source inventory, do not manufacture a common provenance record.

## Inspect, package and submit

For each selected run require successful live `manifest.json`, one bounded
append-only `trace.jsonl`, canonical `submission.workflow.json`, original
`builder-source.json` and `build-provenance.json`, plus selected attempt evidence.
Read and correlate workflow/agent/published-version IDs, graph/export hashes,
ordered input/API fingerprints, isolation audit, model calls with one-line purpose,
start/end times and finite in-budget duration. Inspect that the published readback
and final exported graph match the authored deployment. Preserve original failed
readbacks, test results and overruns. Do not attach an older trace to a newer slot.

Follow [SUBMISSION-PACKAGE.md](SUBMISSION-PACKAGE.md) for the exact two-run manifest,
reviewed evidence commit, allowlist/credential scan, final ZIP and extraction check.
Never blindly redact a required live tool credential and call the modified export
runnable. Organizer-provided import-safe authentication must be reconciled with
credential exclusion before packaging. Stop that artifact's submission if this is
unresolved; retain the original protected export and document the precise gate.

During 19:45–20:00 UTC use only the supplied authorized submission channel. Send
the verified package/export/trace attachments and required metadata, inspect the
actual destination and uploaded filenames, and retain receipt/acknowledgment with
UTC/Istanbul time, package SHA-256 and evidence commit. A prepared draft or ZIP is
not a submission. If instructions/channel are still absent, report that exact
blocker instead of inventing a destination.

After actual submission, record checked/pushed head, selected runs/attempts,
source and evidence commits, merged matching-head PR, package digest, organizer
receipt and reconciled lease IDs/status in issue 19 without raw events,
transcripts or credentials. Use `tools/cowork.py complete T18 --commit <pushed-head>
--pr <merged-matching-head-pr> --checks <actual-evidence>` only when both hidden
builds, T17 dependency and final submission are complete. Until then leave issue
19 open and state the missing evidence precisely.

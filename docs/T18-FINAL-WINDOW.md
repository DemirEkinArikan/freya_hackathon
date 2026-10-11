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
  every attempt directory: each brief gets N parallel attempts (default 3), and
  none is deleted. Each attempt publishes to its own new workflow, named
  `final-<label>-a<k>-<UTC launch time>`. Concurrent attempts must not share a
  workflow: an export fails if its workflow changed after publication. Tonight
  needs at most 2 × (3 + retries) workflows, well under the workspace limit of 200.
- The frozen commit must contain `tools/final_attempts.py` and the packager's
  `selections` support ([PR 167](https://github.com/DemirEkinArikan/freya_hackathon/pull/167)). Both are tools, not runtime
  source, so they do not change the builder source fingerprint.
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
| Before 17:00 | Before 20:00 | Finish broad tests, T17 acceptance/freeze and preparation gates; frozen checkout, guard and preflight |
| 17:00–17:02 | 20:00–20:02 | Save both briefs/APIs as exact bytes with ordered amendments, write `spec.json` and pins; read submission instructions |
| 17:02 | 20:02 | `tools/final_attempts.py launch`: 3 attempts per brief, 6 builders at once |
| 17:02–17:35 | 20:02–20:35 | Attempts run; each stops itself at 1,800 s including audit, any own tests and export. Poll with `select` |
| 17:35–18:00 | 20:35–21:00 | `select`, `manifest`, pre-check, evidence commit, package, both verifiers, attachments; send once verified |
| 18:00–19:15 | 21:00–22:15 | Retries only for a brief with no eligible attempt: a new round starts by 21:45 and ends by 22:15 |
| 19:15–19:45 | 22:15–22:45 | Select, package and verify retry results; prepare attachments |
| 19:45–20:00 | 22:45–23:00 | Buffer: submit via the supplied authorized channel, inspect upload/receipt and keep the acknowledgment |

The latest retry launch is **18:45 UTC / 21:45 Istanbul**. Its attempts end by
22:15, which leaves 30 minutes to select, package and verify before 22:45. Never
launch a retry for a brief that already has an eligible selection, not even for a
faster build: build time breaks ties only after score. A late or invalid
organizer input reduces this margin; do not promise that a retry still fits. Preserve failed attempts rather than relabeling late exports
as successful. The kit permits repeated runs: one selected run per final brief
is submitted with its own trace, and every other attempt stays on disk.

Six concurrent builders is above today's observed maximum: four generate-only
builds ran at once without conflict, and two live builds did on another
occasion. If the launcher refuses the machine cap, or the first attempts fail on
Codex usage limits, launch `--attempts 2` or `--attempts 1` per brief instead.
The builders take the simulation lease only for their own hosted tests, and
tonight they run none.

### Declared selection order

`select` ranks every attempt of a brief by this order. It was fixed before
release, in `package_submission.ATTEMPT_ORDER`:

1. Exported a validated workflow (`status: exported` with its export file).
2. Local validator checks pass: exact guarded recompilation from the run's own
   policy and inputs, `validate_workflow`, and the stored export equivalent to the
   authored graph. These are the bundle verifier's per-run checks.
3. The packager's per-run gates pass (`validate_trace` and `validate_run`).
4. Fewest diagnostics. Each rejected policy or plan error and each format
   failure in the trace counts as one, and so does each `unsupported_constraints`
   entry left in the final policy.
5. Earliest finish (trace end), then the lower attempt number.

Only an attempt that passes 1–3 can be selected. `select` writes
`artifacts/final/<label>/selection.json`, which lists every attempt with its
status, criteria and the raw SHA-256 of its manifest, trace and export. It
exits 2 while any attempt still runs and 3 when no attempt is eligible. It never
changes an attempt directory. The output is deterministic, so running `select`
again on the same attempts reproduces each record byte for byte.

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

Set `T18_SOURCE_COMMIT`, `T18_SOURCE_INVENTORY_SHA256`, `T18_BUILDER_MODEL` and
`T18_REASONING_EFFORT` from the accepted T17 freeze. The inventory digest is the
full allowlisted source inventory recorded by T17, not just runtime provenance.
These are explicit nonsecret configuration values. Create a detached worktree
from that accepted source commit. The guard below verifies HEAD, rejects staged
and unstaged tracked changes, and requires every allowlisted source file to match
HEAD and the accepted inventory. An untracked allowlisted file, a deletion or a
changed kit file must fail this guard. Missing actual T17 freeze evidence remains
a blocker; do not fill these variables from a preparation rehearsal.
The builder reads only this repository's private `.env` as data. Do not print or
source credentials, read sibling environment files, or dump the environment.

A new worktree has no ignored `.env`. Provision a reviewed private `.env` in the
frozen checkout from this repository's authorized configuration, or inherit the
reviewed controller credential variables in its process environment. Use the
project's data parser; never source the file as shell code. Keep it outside Git
and the package. Verify presence without displaying values before release.

Each attempt directory starts with an empty slot registry, so each attempt
creates one workflow under its own unique slot name. The launcher does not use
`--adopt-workflow`, and it never reuses an attempt directory or a slot.

```sh
git worktree add --detach /Users/demirekinarikan/VsCode/freya_hackathon-worktrees/t18-frozen-final "$T18_SOURCE_COMMIT"
cd /Users/demirekinarikan/VsCode/freya_hackathon-worktrees/t18-frozen-final
test "$(git rev-parse HEAD)" = "$T18_SOURCE_COMMIT" || exit 1
git diff --quiet HEAD -- || exit 1
python3.11 - "$T18_SOURCE_INVENTORY_SHA256" <<'PY' || exit 1
import sys
from tools import package_submission as package

files = package.source_files()
package.verify_committed(files)
hashes = {path.relative_to(package.ROOT).as_posix(): package.check_content(path)
          for path in files}
actual = package.digest(hashes)
if actual != sys.argv[1]:
    raise SystemExit('Frozen source inventory differs from the accepted T17 freeze')
print({'source_files': len(files), 'source_inventory_sha256': actual})
PY
python3.11 tools/final_attempts.py launch --env-file /Users/demirekinarikan/VsCode/freya_hackathon/.env \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock \
  --model "$T18_BUILDER_MODEL" --reasoning-effort "$T18_REASONING_EFFORT"
```

The launcher reads `artifacts/hidden-inputs/spec.json`, which lists each label's
briefs in the organizer's explicit order and its matching API. For each attempt
it starts `builder/build.py` with `--brief` repeated in that order,
`--output-dir artifacts/final/<label>/attempt-<k>`, its own slot, the shared
lease, the model and the effort. The child is detached, so closing the terminal
does not stop it. `launch.json` in the attempt directory records the exact argv
and PID, and `builder.log` keeps the builder's output. The launcher loads the
private `.env` with the data-only parser into the children's environment, never
its own, and removes `OPENAI_API_KEY`. It refuses Python other than 3.11, a
missing `FREYA_API_KEY` or Codex CLI, and more running builders than
`--max-running` (default 6). These commands are templates pending actual briefs,
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

The selected run per brief is the one `select` names in `selection.json`. Never
pick a run by hand. `tools/final_attempts.py manifest --team <id>` writes the
submission manifest from both records: the selected runs, their exact launch
argv as `run_command`, the inputs and a `selections` mapping. The packager then
requires each record to be the declared order's eligible choice, binds it to the
selected run's raw bytes, and re-hashes every listed attempt's manifest and trace
on disk. The ZIP stores each record as `selections/<label>.json`: every attempt is
listed, but only the selected run's trace is packaged. The bundle verifier
re-checks the records and reports `attempt_selection` ("selected k of N").

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

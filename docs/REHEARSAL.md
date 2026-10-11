# Clean source rehearsal and final acceptance

Use Python 3.11 and a committed checkout. The rehearsal refuses source bytes
that differ from its recorded HEAD, including untracked allowlisted files.

```sh
python3.11 tools/rehearse_package.py --output-dir artifacts/rehearsals/unique-run
```

The output directory must be new. It is retained on success or failure, with
`report.json`, a source-only ZIP, clean extraction, rebuilt extraction, empty
HOME and per-command logs. Do not delete failed runs or reuse their names.
The report binds the Git commit, complete source inventory, archive, local
configuration and log SHA-256 fingerprints. Inspect each return code and the
actual test counts in the logs; a report is locally recorded consistency
evidence, not independent authentication of remote activity.

The runner validates ZIP paths, types, duplicate members, index keys, total
size, inventory and hashes before extraction. No `.git`, local `.env`, auth,
generated artifacts or evaluator holdouts enter the clean source archive.
The child environment has an empty HOME and excludes inherited credentials,
Python path overrides and user configuration. Each direct Python check uses
`-S` to disable site startup, including system `.pth` and `sitecustomize` hooks;
the report records that flag. Test-owned nested subprocesses retain their own
startup controls. Source bytes must remain unchanged
after checks, and the clean source must rebuild the same allowlisted inventory.
The whole local rehearsal has a 1800-second deadline; timeout terminates the
check's process group and retains its log.

Checks include kit v3 verification, full unittest discovery, source package
check, builder CLI startup, training/validation helper and clean/raw equivalence
checks, hosted validation dry-run, and source re-packaging. They run in the
extraction, without its original checkout, private environment or auth files.
Development profiles `train-2` and `validation-2` are exported into a separate
input subtree. Each export must contain a nonempty policy document and valid
OpenAPI object; linked, partial or unexpected files fail the rehearsal. Their exact
bytes are fingerprinted. These exercise known raw-packet and changed-schema
contracts locally; they are not unseen-policy model trials or blind holdouts.
No model, platform or simulation calls are made by this command.

## Accepted model/live rehearsal

Source reproduction is one part of T17. After generic builder blockers are
resolved, run the submitted source with independently reviewed fresh input
documents/OpenAPI outside `tests` and `benchmarks`. Never copy evaluator scripts,
expected contracts, ledgers or answers into generator input. Each real builder
instance must pass its own mandatory tool-free isolation audit before banking
input is sent. Use the approved generator model/configuration and keep the
phone model `experimental-5`; retain the 1800-second build deadline and
300-second export reserve. Generated policy/workflow files must not be edited.

Before a live invocation from an extraction, coordinate the exact source and
next trial with the primary thread, reconcile actual server status, and route
every worktree to the canonical shared lease:

```text
/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock
/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.json
```

The explicit `--simulation-lease` hook is merged. Pass the canonical absolute
lock path on every live invocation; the default remains beneath the output
directory and is unsuitable for shared-workspace execution. A local completed
record alone does not establish terminal server status. An unresolved creation
intent without a simulation ID also requires actual server reconciliation.
Do not bypass coordination with a second lease or manually patch generated artifacts.

Use a new committed worktree at the accepted source commit for the live
rehearsal, preserving the clean archive report from that same commit. Its
runtime-source fingerprints must agree with the extraction. Copy only reviewed
fresh brief/OpenAPI bytes into the worktree's ignored `artifacts/rehearsal-inputs/`
and record their original hashes and brief order. Runtime credentials and the
existing slot registry remain controller-only private configuration, provisioned
as described in [the final-window runbook](T18-FINAL-WINDOW.md).
For the output directory below, place the reviewed registry specifically at
`artifacts/rehearsal-live/.freya-slots.json`, or append
`--adopt-workflow <verified-existing-workflow-id>` on first use. The registry is
relative to the selected output directory; a slot name alone does not reuse a
workflow when the registry is absent. Verify the actual workspace binding and
retain the registry across retries.

After T16 acceptance and operator reconciliation, this is the command shape;
replace the illustrative paths and slot with the actual approved inputs and
verified persistent slot. Repeat `--brief` in the approved amendment order.

```sh
python3.11 builder/build.py \
  --brief artifacts/rehearsal-inputs/approved-brief.md \
  --tool-api artifacts/rehearsal-inputs/bank.openapi.json \
  --output-dir artifacts/rehearsal-live \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock \
  --slot "$T17_REHEARSAL_SLOT" \
  --model "$T17_BUILDER_MODEL" --reasoning-effort "$T17_REASONING_EFFORT"
```

Record the model, reasoning effort and slot values from the actual accepted
configuration before execution. Every invocation performs a fresh tool-free
audit. Keep the controller's 1800-second deadline and 300-second export reserve;
do not start a run unless its maximum duration fits the current work window.
The command above has no hosted test set. Coordinate S06's critical cases and
three repetitions against the exported published version, with evaluation-only
inputs kept outside generation. Any tests supplied through `--test-cases` belong
to the same builder deadline. Absence of scored evidence leaves hosted behavior
unverified. Do not use this illustrative command as a completion record.

Retain each attempt and failure. Successful live evidence must bind the clean
archive inventory and commit to `builder-source.json`, resolved config/model,
original `build-provenance.json`, input fingerprints, original trace, policy,
guarded workflow and all attempt snapshots. Confirm trace/manifest elapsed
time below 1800 seconds, substantive generation after isolation, allowed
model/tools, publication readback and exported workflow/version agreement.
Record critical scored conversation cases passing **all three repetitions**,
with original call, simulation and iteration IDs, deployment pins, ledgers and
conversation evidence. A mock, dry-run, historical failed generation or source
availability cannot replace this evidence.

Hand T18 and the independent bundle verifier the exact checked/pushed source
commit and merged PR, clean report/archive hashes, original run directory and
selected attempt, resolved configuration, publication/export bindings and
three-repeat verdicts. Keep raw events, transcripts, credentials and internal
operator metadata in protected local evidence; issue checkpoints contain only
reviewed summaries and exact missing acceptance gates. Every later source or
configuration change requires the relevant checks and fresh attribution.

## Freeze and completion

T16 must supply completed candidate/holdout evaluation evidence before final
T17 freeze. T16's fresh inputs are authored after its candidate freeze and
remain evaluator-only; using failures for repair consumes that holdout. Do not
use hidden submission briefs before the organizer window.

Freeze an immutable candidate/configuration with an explicit source inventory.
Keep generated outputs outside the frozen source tree; generated JSON from a
rehearsal must not silently alter the frozen source hash. Bind the freeze to the
entire submission inventory, including shell diagnostics and kit VERSION;
runtime-only provenance is a narrower inventory. Final submission packaging
also requires actual exports/traces and committed evidence; its Git checks
cannot run in a `.git`-free source-only extraction.

Push the exact checked head, open a focused PR to main and merge directly.
Record its matching head and actual evidence in issue 18. Run
`tools/cowork.py complete T17 --commit <head> --pr <merged-pr> --checks <evidence>`
only after the complete deliverable and dependencies are satisfied. Until then,
checkpoint precise remaining gates and keep issue 18 open.

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
Python import hooks and host configuration. Source bytes must remain unchanged
after checks, and the clean source must rebuild the same allowlisted inventory.
The whole local rehearsal has a 1800-second deadline; timeout terminates the
check's process group and retains its log.

Checks include kit v3 verification, full unittest discovery, source package
check, builder CLI startup, training/validation helper and clean/raw equivalence
checks, hosted validation dry-run, and source re-packaging. They run in the
extraction, without its original checkout, private environment or auth files.
Development profiles `train-2` and `validation-2` are exported into a separate
input subtree; only policy documents and OpenAPI are exported. Their exact
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

At starting main `486ead9`, the CLI constructs a lease beneath its output
directory. A clean live invocation therefore needs the primary-owned explicit
shared-lease hook before running. A local completed lease record alone does
not establish terminal server status. Do not bypass coordination with a second
lease or manually patch generated artifacts.

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

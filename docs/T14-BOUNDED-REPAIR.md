# Bounded repair diagnostics

`builder.repair_diagnostics` is the boundary between evaluator results and
workflow repair inputs. It imports no evaluator code or fixtures. Exact current
scorer messages select fixed guidance; unknown conversation/judge prose selects
fixed category guidance. Nothing copies messages, ledgers, expected records,
scripts, customer fields, judge reasons, or arbitrary metadata into a prompt.
Only validated integer pass/total counts and approved source-backed policy rule
IDs may accompany the fixed guidance. Rule values remain in the existing frozen
input contract, not in evaluator diagnostics.

Infrastructure, missing evidence, malformed or contradictory results are not
behavioral failures. `test_infrastructure_failed(result)` identifies these;
`repair_test_summary(result, policy)` refuses them even when called directly.
A failed or incomplete hosted test preserves its published candidate and original
evidence. It must not consume a repair or start another generation.

## Production controller integration

The coordinated T14 follow-up removes the old duplicated mapper and gate in
`builder/build.py`. Its existing call sites now use the merged diagnostics module
directly through these aliases:

```python
from .repair_diagnostics import repair_test_summary as _repair_test_summary
from .repair_diagnostics import test_infrastructure_failed as _test_infrastructure_failed
```

The controller must check infrastructure before its pass/fail branch and before
incrementing repairs. Its existing behavioral repair payload remains
`{"test_summary": _repair_test_summary(tests, frozen_policy),
"previous_plan": sanitize(plan, secrets=secret_values)}`. Preserve the policy
hash check, shared extraction/plan `MAX_REPAIRS = 2`, 1800-second deadline,
300-second export reserve, mandatory tool-free model audit, experimental-5 phone
model and PR49 attempt snapshots. Each candidate is saved/published and checked
against its stored read-back before testing. Export must finish inside the same
deadline; no controller-owned or generated policy edits are permitted.

The original controller regressions temporarily substituted these two hooks in
tests so the module lane could verify its contract without editing T05's active controller. They
exercise local injected infrastructure failure and behavioral repair, retained
attempts, frozen policy, bounded shared repairs and the export reserve. These
are local checks; they do not establish a live banking repair.

`tests/test_repair_production_wiring.py` now exercises the actual production
`execute` path without replacing either diagnostic hook. Five regression cases
failed against the old controller; the positive complete-result control passed.
All six pass with the production integration. They verify nested missing
evidence, contradictory status, a nonbehavioral scorer failure disguised as a
behavioral failure, and an unverified deployment pin preserve the original
candidate without regeneration or export. The real `run_builder_tests` and
`evaluate` functions, using in-memory external-service doubles, produce the
complete pinned result shape for the positive control and behavioral repair.
Fixed actionable guidance reaches the repair prompt; private judge text remains
only in the retained evaluator evidence. Both attempts retain the same policy
and contract. This compatibility check uses no model, bank, Freya or simulation
calls and does not claim live acceptance.

## Live acceptance still required

On 10 October 2026 at 23:57 Istanbul, T14 acquired the canonical lock for a
read-only status check. The stored simulation
`03229c8e-5d0c-41fd-8179-35a50c6cc43e` (iteration
`ca839f71-ddae-40ae-8e56-9cfaeed37a34`) was confirmed completed on the staging
server with the expected workspace scope. No simulation was started and the
lease state was preserved. All worktrees must use
`/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock` and
`.simulation.json`; recheck with the primary thread and server before execution.

A bounded unauthenticated TLS check to the documented bank origin timed out;
the verified Cloudflare control negotiated TLS 1.3. No bank session was created
and no ledger retrieved. This reproduces the current T11 prerequisite failure,
not a banking API incompatibility finding. Original sanitized observations are
in the T14 worktree's ignored `artifacts/t14-prerequisites.json` and shared scope
handoff. No credentials or returned customer capabilities are committed.

Keep issue 15 open until a genuine generated banking candidate violates a
policy, sanitized evidence drives a bounded plan repair, both real attempts
remain, each republish/read-back is verified, and the selected export completes
within 1800 seconds with reserve. T05's actual full builder acceptance and T13's
hosted scoring evidence must also be complete. A focused source PR can be merged
while these gates remain open; `tools/cowork.py complete` is inappropriate until
all of them are satisfied. Development evaluator fixtures stay outside model
inputs and holdouts stay reserved.

## Evaluator criteria and attempt binding

The controller loads and fingerprints `--test-cases` before any model call,
then retains an evaluator-only `evaluator-cases.json` snapshot and
`evaluator-input.json` binding in the run. The binding records the original raw
SHA-256, canonical JSON SHA-256, snapshot raw SHA-256 and selected brief. Every
attempt and the success/failure manifest carry the same binding. Each test uses
the snapshot; changed original or snapshot bytes abort as an infrastructure
failure before another repair or accepted export. Original candidate and test
evidence remain. These evaluator payloads and bindings never enter policy or
workflow prompts.

Selected holdout rows are refused before generation. Autonomous repair uses
development feedback; reserved blind evaluation uses the separate frozen
candidate/consumption lifecycle. No holdout is materialized or consumed by these
checks.

Supplied hosted evidence must reconcile its before/after deployment records,
per-call expected/observed versions and simulation/iteration IDs. Internally
consistent pins are also compared with the actual published attempt, including
its graph hash and reported workflow/agent/version IDs. Legacy injected local
runners remain supported when hosted structures are absent. Compatibility tests
use external-service doubles and establish no live behavioral acceptance.

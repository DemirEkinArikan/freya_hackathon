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

## Coordinated controller hook

T05 owns `builder/build.py`. Replace its existing mapper/gate implementation
with these thin wrappers after merging this module:

```python
from .repair_diagnostics import repair_test_summary, test_infrastructure_failed


def _repair_test_summary(result, policy=None):
    return repair_test_summary(result, policy)


def _test_infrastructure_failed(result):
    return test_infrastructure_failed(result)
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

The controller regressions temporarily substitute these two hooks in tests so
this lane can verify its contract without editing T05's active controller. They
exercise local injected infrastructure failure and behavioral repair, retained
attempts, frozen policy, bounded shared repairs and the export reserve. These
are local checks; they do not establish a live banking repair.

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

# Policy and workflow generation budgets

Two fresh approved development-v2 builders at source `5837d95` failed during
initial policy extraction after 450.02 seconds. Their run IDs are
`20261010T220638685420Z` (clean train1) and `20261010T220638022828Z` (raw train2).
Both fresh tool-free audits passed in about seven seconds. The retained partial
events contain only thread/turn starts and the known experimental-feature
warning: no final policy, reasoning item or provider failure was emitted.
The traces cannot establish whether the request was reasoning or stalled.
Original failed artifacts remain unchanged; no policy/workflow was accepted.

Each unconditional 450-second timeout left about 1343 seconds of the same
1800-second deadline. Initial policy extraction now allows at most 900 seconds,
retaining 450 seconds for the pending first workflow generation and 300 seconds
for export. Policy validation repairs allow at most 450 seconds and share that
same reserve. Extraction-only mode retains the 300 export/completion reserve
without reserving a nonexistent workflow stage. The workflow stage still uses
at most 900 seconds, clipped by the remaining global time and export reserve.
The two-repair limit on full re-emissions remains shared across policy and
workflow validation. Policy patch repairs have their own time-based budget (see
"More patch repairs" below).
Timeouts remain failures, except that a first policy attempt that times out or
stalls is retried once (see "Stall guard" below).

Repair admission (11 Oct). A repair rewrites its whole document, so a policy
or plan repair starts only when its allocation covers 90% of 61 seconds plus
0.0044 seconds per character of the previous attempt's compact JSON. The 47
validation repairs completed on 11 Oct took 0.62-1.19 times that estimate,
and none would have been refused. Nine of that day's twelve timed-out repairs
would not have started. A refused repair ends the build with `DeadlineExceeded`.
The message gives the available and estimated seconds, then the real validation
or format failure, instead of a generic timeout.

Two limits follow from this rule. A format repair has no previous document to
size, so only the 61-second base is checked: it can still start with about 55
seconds left and then time out. That day's two format repairs took 261 and
443 seconds, 1.19 and 0.66 times their failed calls. An estimate from the
failed call would have refused the second, which then passed.
A policy repair is capped at 450 seconds, so a policy whose compact JSON exceeds
about 99,800 characters is never repaired. That day's 111 generated policies
had 29,800 to 83,400 characters (median 45,400).

Patch repair (11 Oct, evening). A long brief's first policy takes 570-760
seconds, so a full re-emission rarely fits the repair allocation that remains.
By default (`--policy-repair patch`) a rejected policy is first repaired by a
structured patch. The model receives the validation errors, only the rules,
declarations and top-level fields they reference (with JSON paths, plus the
rules a referenced declaration cites) and an `[id, kind]` index of every rule.
It returns add/replace/remove operations addressed by rule id or by path into
the rejected policy (`builder/policy_patch.py`). The patch is applied
deterministically, all or nothing, and the result goes through the same
unchanged contract and execution-requirement checks, so a patched rule still
needs its exact source quote. Admission sizes a patch by that excerpt instead of
the whole policy. A malformed patch, an unappliable one, a format error or any
exception in the excerpt, prompt or apply code falls back to a full repair in
the same attempt, if that repair is admitted and one is left. A patched
policy that fails validation can be patched again (see below). The trace
records `repair_admission` with `repair_mode: patch` and a `policy_patch` event
with the patch, its size, the excerpt and prompt sizes and the elapsed time.
`--policy-repair full` restores full re-emission only. In a no-model replay of
the six retained rejected stress policies, every patch repair would have been
admitted at its recorded allocation (estimates 67-195 s against full rewrites
of 307-428 s). The prompt shrinks less, to 56-85% of a full repair, because the
sources, API, interface and instructions stay; the rewritten part is what the
admission estimate prices.

More patch repairs (11 Oct, night). The relaxed re-runs of stress-dev-1, -3 and -4
stopped at the policy stage after two patch repairs, with 1,325-1,436 seconds unused.
Those patch repairs took 15-54 seconds. A rejected policy is now patched again for as
long as each patch is admitted by the estimate above, against the remaining time less
the first-workflow and export reserve, up to `MAX_PATCH_REPAIRS = 6` patches. Patching
stops early when a patch leaves the error set of the policy it patched unchanged. Full
re-emissions, including the first-attempt retry, keep `MAX_REPAIRS = 2`. A full
re-emission that is left can still run after patching stops, if it is admitted. After a
full re-emission, patching may resume on the new policy. Each failed policy attempt adds
a `policy_repair_budget` trace event. It records the patches and full re-emissions used,
whether the last patch made no progress, the next repair and the remaining seconds. The
resolved configuration records `max_patch_repairs`. In a no-model replay of eight retained
11 Oct stress traces through this controller, the current validators reproduced every
recorded error set. Five builds that stopped after their third attempt would have made
another admitted attempt. In stress-dev-1 and -3 and the relaxed sd5x stress re-run, a
patch had made no progress, so the next attempt is a full re-emission (estimated 271-409
seconds, with 404-450 seconds available). Stress-dev-4 and the strict stress-dev-2 re-run
get another patch. Two builds still stop for lack of time. The one success is unchanged.

The 1800-second limit, the 300-second export reserve and the 450-second
first-workflow reserve are unchanged. In a replay of that day's call times, a
larger first-workflow reserve refused policy repairs that would have succeeded.
A smaller one changed timing success by at most 0.07, and only when two policy
repairs were needed. One public-A build passed its third policy attempt with
496 seconds left, and its 303-second first plan then validated.

Every repair decision is a `repair_admission` trace event. `stage_completed`
events with elapsed and remaining seconds now cover policy, workflow,
deployment, testing and export. These times come from the build's own deadline
clock. A failed build's `end` event carries its `stage_durations`.

The controller and generator use the original `Deadline` instance. Actual
subprocess allocation is recalculated after capability inspection and mandatory
isolation auditing, so their elapsed time cannot create a second budget.
Allocation also follows catalog/schema preparation. Process-group termination,
drained partial output, secret redaction, stage wire
validation and the installed restricted-model catalog are unchanged. The model
and reasoning effort remain explicit `gpt-6.1-sol` / `high`; every fresh build
requires its own audit. No source prompt or banking semantics changed.

The configuration snapshot binds initial/repair caps and workflow/export
reserves. Policy budget events and actual model-call events record their caps,
allocations and remaining global time. Model calls also record prompt UTF-8
bytes and exact serialized response-schema bytes. The original prompts had
36,857 and 46,252 characters (rough heuristic 9,214 and 11,563 input tokens at
four characters per token, not tokenizer/provider usage). Current payload
measurements show distinct approved documents, OpenAPI and execution-interface
metadata rather than a duplicated workflow reference; context was retained for
correctness. Output size and hidden reasoning usage were not reported.

Unit checks exercise the 900/450 allocation, audit consumption, nearly exhausted
deadlines, extraction-only mode, injected generator overruns, exact configuration
binding, unchanged shared repair semantics and subprocess kill/drain behavior.
They do not establish live model throughput. After integrated source checks,
one controlled fresh public-A build with the longer policy allowance is the
next diagnostic; do not repeat parallel silent failures or manually edit a
generated policy to obtain an accepted artifact.

A separate original public-A extraction completed a healthy 280-second model
turn but its final serialized stage object was rejected. The retained bounded
trace does not identify the precise invalid inner value. A completed, zero-exit,
exactly-one-turn tool-free stream now distinguishes fixed `outer_json`,
`wire_shape`, `inner_json` and `inner_shape` diagnostics. The strict parser still
rejects malformed JSON, duplicate keys, nonfinite values and incorrect object
shapes. It does not normalize malformed bytes or accept an earlier message.

Only these healthy serialization failures can consume the existing repair
allowance. Policy repairs receive approved briefs/API and fixed serialization
guidance; plan repairs receive the same frozen contract and fixed guidance.
Unaccepted raw output is retained in the original sanitized runtime trace, while
only the fixed code enters repair context. Attempt records distinguish a format
rejection from a validated or compiled proposal. Failed/incomplete/nonzero turns,
unknown diagnostics or tool events, timeouts (other than the first-policy retry
below) and frozen plan hash mismatches remain fatal. Additional checks cover
those boundaries, unchanged policy hashes and the shared two-repair limit across
policy and workflow format failures.

Stall guard (11 Oct). Codex exec prints nothing between `turn.started` and its
final message. None of the 182 calls that completed on 10-11 Oct printed a
reasoning or partial item first, so a healthy call is silent for its whole
duration. The runtime kills a call, with its process group, when it has not
printed `turn.started` after 120 seconds (every retained call had by its first
30-second progress check), or when a low or medium call stays silent for 600
seconds after it. The slowest completed medium call took 346.5 seconds. The
largest policy seen (83.4k characters) predicts about 490 seconds at medium,
and heavy account concurrency adds up to 23%. High and xhigh calls have no
silence window below the 900-second cap, because completed high calls took up
to 800.4 seconds; their allocation stays the only bound. Replaying the rule over
every retained call kills none of the 182 completed calls. A kill is a
`stall_detected` trace event with its silence, window, phase and effort.

First-policy retry (11 Oct). Two stress builds (stress-dev-11 and -12) spent
their whole 900-second first policy call with no output after `turn.started`.
Each then failed with about 893 seconds unused. A first policy attempt that
stalls or times out is now retried once, and is admitted like a format repair:
the 61-second base and the 450-second repair cap. The retry keeps only the
300-second export reserve, because there is no policy yet to reserve plan time
for. A high or xhigh attempt is retried at medium. Reasoning dominates call
time: high first calls used 4.5-14k reasoning tokens at about 0.0384 seconds
each, medium about 1k. `policy_generation_retry` records the decision, and the
retry's `model_call` records its effort. Packaging and evidence-bundle checks
accept that effort for the next model call only. Repairs, plan calls and the
retry itself are not retried.

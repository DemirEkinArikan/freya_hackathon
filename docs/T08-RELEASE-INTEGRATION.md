# T08 release integration matrix

T08 has three declared checks: confirmed final terms without duplicate recorded
promises; deferred notes discarded with unwritten commitments on handoff; and
retained partial successes with a single final close. Integration must exercise
the original generated candidate accepted by T07. Passing source tests does not
complete this dependency.

Codex T08 authored this lane on `4ef4e6275ee7cb4cb4fd010a3cf1e12f71b9b38b`,
which includes the checked administrative-budget, input-format, authored-routing
and routing-guidance changes in PR112/110/113/114; a recorded handoff carried it
onto main `3f2399d`. Production guards, compiler, prompts and generated outputs
are unchanged by this evidence lane.

## Evidence boundary

`tools/t08_release_matrix.py` is evaluator-only. Supply the original selected
generated run, its exact OpenAPI and separately reviewed local cases. It checks
the selected attempt, root artifacts, original source/configuration/trace
bindings and exact guarded recompilation before replay. Case artifacts belong
outside every generator input and repair context. Never amend original generated
policy, plan, workflow, manifests or failed logs to make a case pass.

Run the checker after replacing these example paths with the original reviewed
artifacts. Run it from a checkout of this repository whose `builder/*.py`,
`builder/prompts/*.txt` and `benchmarks/*.py` bytes equal the candidate's
`builder-source.json` (for example a detached worktree at the build's source
commit); otherwise the loader refuses. The output must be new and outside the
original run directory:

```sh
python3.11 tools/t08_release_matrix.py \
  --candidate-dir artifacts/original-run \
  --openapi artifacts/approved-inputs/bank.openapi.json \
  --cases artifacts/t08-cases.json \
  --output artifacts/t08-release-report.json
```

The version-1 case object contains `candidate_bindings`, `source_inputs` and
`cases`. Obtain `candidate_bindings` from
`load_candidate(original_run, original_api, source_inputs)["bindings"]`;
`source_inputs` lists each original brief's `id`, `path` and canonical text
`sha256`. Ready rows contain `id`, the declared `requirements` IDs below,
`status: "ready"`, a `source_review` rationale with exact source quotations,
an explicit replay `script`, complete ordered `expected_calls` containing
`tool` and `arguments`, and final-state `assertions` containing a JSON-pointer
`path` and independent expected `equals` value. Pending or unsupported rows
retain their `reason`. The module docstring defines the complete format.

A `partial_success_close_once` row needs a promise dispatch or executed commit
plus `recorded_promises` and `closed` assertions; its complete ordered calls fix
zero or one close, so quarantine endings without a close remain valid rows. If
a reviewed case whose assertions already name the required state is rejected for
lacking an executed API/state witness, the candidate did not execute that
operation: record a failure and never edit the case to pass.

The report's requirement status is explicitly a **local assertion status**;
independent case adequacy, source interpretation, T07/task acceptance and hosted
behavior remain unverified. Missing evidence stays pending. Raw and canonical
hashes bind the report to original artifacts and the reviewed case file.

Each case must bind to that candidate and cite the frozen source rule or exact
API clause that supplies its expected behavior. Use the actual operation IDs,
request/result aliases, status envelopes and opaque vocabulary. Compute expected
ordered requests independently from the source and permitted request schema;
do not copy the kernel's final state as the expected ledger.

Local replay supplies ideal extraction and explicit mock receipts. Its actual
node/API ordering proves only the local compiled execution. It does not prove
caller speech, native entry-event timing, bank persistence, publication or
hosted performance. Arbitrary faults remain local unless the server explicitly
documents fault support. A runner result cannot close T08 or establish T07
acceptance by itself.

The loader verifies structurally complete stored controller artifacts and their
bindings. It can also inspect a provided-policy or mocked-controller run for
tooling verification. It does not independently authenticate local evidence or
verify a fresh mandatory tool-free audit, actual model execution, source-policy
fidelity or changed-goal adaptation. T07 must establish those facts from the
original invocation evidence before that candidate contributes to acceptance.

## Release rows

| Declared check | Source-derived sequence | Required evidence |
| --- | --- | --- |
| `confirmed_final_terms` | Stage terms, request consent, correct terms, attempt stale acceptance, then supply fresh consent for the corrected terms | No old-term dispatch; exact final request after fresh consent; positive receipt records only that request |
| `confirmed_final_terms` | Caller/extractor claims a promise or callback was recorded; no corresponding positive API receipt exists | No recorded success inferred from public variables; unknown persistence blocks subsequent writes |
| `confirmed_final_terms` | After an acknowledged promise, attempt another promise for the same product; after an acknowledged callback, repeat the same call-scoped booking | Original recorded terms/counts retained; no duplicate request or dispatch-budget reset |
| `deferred_notes_transfer` | Stage a note and an uncommitted promise, then choose each permitted handoff route | No prior note API call; pending notes/commitments cleared; earlier recorded promises retained; handoff precedes its derived close |
| `deferred_notes_transfer` | Resolve products and enter the source-authorized non-transfer note phase; afterward attempt a handoff | Note dispatch belongs only to that sealed phase; later transfer cannot conceal or undo an actual note. Source-authorized local-only notes have their separately declared exception |
| `partial_success_close_once` | First batch member succeeds; the next dispatched member returns a qualified definite negative or unknown result | First record retained; exact source-authorized retry policy honored; unknown result quarantines writes; later unsubmitted members do not acquire attempts |
| `partial_success_close_once` | A result contains a positive flag with an error, bad HTTP status or malformed record identity | No unproved new record; uncertain request and earlier successes preserved; no subsequent bank write |
| `partial_success_close_once` | Exhaust the administrative allocation before preparation and immediately after a positive receipt | Pre-preparation stop sends nothing; dispatched positive receipt reconciles before stop; no phantom next batch member/retry |
| `partial_success_close_once` | Reach a supported successful close, then attempt close and other writes again | One ordered close request, one consistent successful close receipt, no later bank call |
| Applicable declared checks | Change ordinary call-input variables after the initial scope is sealed | Original protected scope and attempt cap remain operative; public edits do not grant another action |

These rows describe the declared boundaries, not a new arbitrary case-count or
hosted-repeat gate. Select supported operations from the accepted candidate's
frozen source. A missing required operation/case/evidence remains pending; a
source-authorized exclusion must be justified by the original source, never by
an implementation convenience.

For qualified failures, cite the exact API nonpersistence guarantee, response
field and HTTP status. A safe terminal after an unknown or failed close proves
at most one authorized dispatch attempt. It does not prove that a bank close
was recorded. Preserve this distinction in the report and issue checkpoint.

## Coordination and closure

S05 owns independent fault cases and expected-ledger calculations. Reuse its
reviewed candidate-bound case artifacts as matrix inputs when available; retain
their original hashes and failures. The additional cross-boundary unit tests in
`tests/test_t08_release_boundaries.py` use synthetic local source packets and
are preparation evidence, not substitutes for S05 or T07 acceptance.

At the initial 11 October checkpoint, T07 has no accepted candidate on this
source. Its approved callback generation is reserved by the existing worker;
this lane starts no duplicate generation, deployment or simulation and changes
no shared lease. Historical public-A and A+B artifacts retain their original
acceptance limits and cannot be upgraded by this source change.

After actual candidate integration, record the input/policy/contract/plan/source/
workflow/case hashes, local ordered requests, per-row result and independent
semantic review. Run the declared policy/scoring checks, full Python 3.11 suite,
kit/source/diff checks; push the checked immutable head and merge its matching
same-repository PR. Close T08 only when its three checks and T07 dependency are
complete, then notify T09 with that exact evidence. Until then, merge useful
checked preparation and record the remaining dependency without closing T08.

## Candidate evidence: callback round 3 (11 October 2026)

**Candidate.** Run `20261011T104109500476Z` (`generated_validated`,
generate-only, plan-002 selected), built on
`6fac1dd61dfa9622eaf8dec44182e361dc779ef2` (`builder_source_sha256`
`f04957e4…5b2b`) from the approved callback inputs (raw sha256: brief
`6a646d16…`, API `3223a518…`, approval `ea0d7c49…`). Its T07 acceptance
evidence is issue 8 comment 6108295952. Nothing here establishes T07
acceptance.

**Bindings.** The unchanged `load_candidate` ran from a detached checkout at
the source commit. It recompiled the contract, action bindings, execution
policy, execution requirements, plan coverage, workflow and guard manifest
exactly, and matched the trace, manifest and builder provenance.
- Raw sha256: `workflow.json` `cc5377cf…`, `plan.json` `0c3e1f86…`,
  `policy.json` `7b8a61d2…`, `policy.contract.json` `c80a16a8…`,
  `execution-policy.json` `5a845f73…`, `guard-manifest.json` `3e433aa0…`,
  `attempts/plan-002/guard-context.json` `f0daa523…`, `manifest.json`
  `a22ebc56…`, `trace.jsonl` `412dff5e…`.
- Canonical: workflow `7d5c75c6…`, plan `8d517912…`, policy `b9396b50…`,
  contract `c58e5bd2…`, guard manifest `53617455…`, OpenAPI `eb561508…`.
- All 37 run and input files re-verified unchanged after evaluation.

**Compiled kernel/API order.** A booking runs: `bg_init`, `entry_check`,
`request_slot`, `slot_review`, guard `schedule_callback` (prepare, read-back),
`bg_schedule_callback_confirm` (the only consent step),
`bg_schedule_callback_accept`, `POST /schedule_callback`,
`bg_schedule_callback_result`, `recorded_callback`, guard `close_call`,
`POST /close_call`, `bg_close_call_result`, `bg_terminal`.
- A `bg_gate_*` entry-budget gate precedes every public node.
- Uncertain receipts end at `bg_quarantine` with no close.
- The only API operations are `schedule_callback` and `close_call`.
- The close node has no dialogue inputs, so its outcome and reason come only
  from protected evidence.
- `bg_close_call_result` has one unconditional transition, to `bg_terminal`.

**Method.**
- 15 rows were written from quotations of the approved brief and the request
  schema, independently of the T07 ledger: 12 ready and 3 unsupported.
- Each ready row has an explicit replay script, complete ordered requests and
  final-state facts. "Probe" rows use deliberately non-ideal agent
  completions, so the sealed guard has to hold on its own.
- A check counts only if it also fails under an in-memory kernel mutant:
  `builder.guards._KERNEL` or the `_code` tail is patched in memory, then the
  workflow is recompiled from the candidate's own guard context. Where a check
  depends on several guard layers, the mutant removes them all.
- Mid-call public-variable edits and node re-entry cannot be expressed in the
  matrix. They use a small hook around `tools/replay_guarded.replay`.

**Release matrix** (`tools/t08_release_matrix.py --max-steps 10000 --seconds
600`): **FAIL**.
- `confirmed_final_terms`: FAIL (11 PASS, 1 FAIL, 1 unsupported).
- `deferred_notes_transfer`: PENDING (unsupported).
- `partial_success_close_once`: PENDING (unsupported).

| Row | Scenario (source-derived expectation) | Ordered requests | Result | Mutant under which it fails |
| --- | --- | --- | --- | --- |
| R1 | A correction during the read-back is `unclear`; re-collect, fresh read-back, yes. Only the corrected slot is sent, and the withdrawn slot never enters the attempt history | schedule 2026-12-30 15:30; close R10 | PASS | M1 (any answer except `no` consents) sends the stale 14:00. M8 (a withdrawn unsent request is counted) refuses the corrected slot |
| R2a | Probe: the caller claims an earlier booking; empty terms reach the action; the agent treats the claim as recorded | close R11 | PASS | M2 (close ignores callback evidence) sends R10 with nothing booked |
| R2b | Probe: a definite HTTP503 failure, then the caller insists it is booked | schedule; close R11 | PASS | M2 sends R10 |
| R3 | Probe: after HTTP200 `verification_denied`, the identical tuple and then 11:00 reach the action | schedule 10:00; close R11 | PASS | M3a (call-wide cap removed) reads back again and, given a yes, resends the identical tuple |
| R4 | HTTP503 with `accepted="true"` | schedule only | PASS | M6a (positive flag trusted at non-2xx) records the callback |
| R5 | HTTP200 `accepted="false"` without `failure_code` | schedule only | PASS | M6b (any negative treated as the declared definite failure) resumes to a close |
| R6 | HTTP503 `accepted="false"` with `invalid_request` (declared only for 200) | schedule only | PASS | M6b |
| R7 | HTTP200 with `accepted` as a JSON boolean `true` (malformed type) | schedule only | **FAIL** | none (finding V2) |
| R8 | The entry budget is first exceeded at the result node; positive receipt | schedule only | PASS: callback recorded, nothing in flight, quarantined, no close | M7 (abort before reconciliation): the callback is lost and the request is left in flight |
| R8b | The entry budget is first exceeded at preparation | none | PASS | M7b (preparation ignores the budget) reads back and, given a yes, sends |
| R10 | Callback recorded; the close gets HTTP200 `accepted="false"` | schedule 16:45; close R10 | PASS: callback kept, close not claimed, one close | MPS1 (a failed close is recorded as closed) |
| R10b | Callback recorded; the close gets `accepted="true"` plus `failure_code` | schedule; close R10 | PASS | MPS2 (contradictory receipt trusted) |

**Hook checks** (exact candidate code, outside the matrix):
- **H4, later public-variable edits.** Right after `bg_init`, public
  `customer_id` and `now` are edited to another id and to 2027-01-04T08:00.
  The kernel still refuses 2027-01-04 under the sealed clock, then sends both
  requests with the sealed id and an eligible date. PASS. Under MSEAL (the
  kernel reads the public inputs) it sends `session_ref` `t08_public_edit` for
  2027-01-04.
- **H1, close re-entry.** The exact `close_call` node code is run again on R1's
  final variables; the graph cannot reach this. The result is `blocked`, with
  an empty body and unchanged history. The close has three layers: the
  closed/close_attempted refusal, the duplicate-request refusal and the
  exact-request cap of 1. Removing one or two of them still blocks it.
  Removing all three (M3b-123) sends a second R10 close. After R10's failed
  close, the quarantine also blocks it.
- **H3, transfer request.** The caller asks for a transfer, an unsupported
  action. The only request is close R11, and no transfer or note state is
  created. This is informational and not counted: it rests on the operation's
  absence, not on a kernel guard.

**The eight lane checks on this candidate.**
1. Corrections invalidate consent: demonstrated (R1).
2. Caller claims never record success: demonstrated (R2a, R2b).
3. No duplicate promise, callback or close: demonstrated for callbacks (R3)
   and closes (H1 and the single terminal transition). The promise part is not
   applicable.
4. Notes wait for a non-transfer close: not applicable; the source permits no
   notes.
5. A handoff discards pending notes and unwritten commitments but keeps
   recorded promises: not applicable; the source permits no transfer.
6. Unknown persistence stops further writes: R4, R5, R6 and R10b pass. R7
   **fails**.
7. A positive receipt is reconciled before a resource abort: demonstrated (R8;
   R8b covers the stop before preparation).
8. No unsent batch member is counted: the callback analog passes (R1).
   Multi-member batches are not applicable.

**Finding V2** (reserved guard code, not edited here).
- HTTP200 with `accepted: true` as a JSON boolean is recorded as a persisted
  callback.
- When R7 is continued with the agent's acknowledgement, the candidate also
  sends close R10.
- The source says the field "is a string, not a boolean" and treats malformed
  types as uncertain: "Stop every bank write, including close_call".
- The cause is the same as S05 V1. The kit delivers result values as strings
  ("Booleans arrive as strings"). The compiled API `post_process_code` is the
  only hook that sees typed values, and it passes on only `status_code`. That
  hook is byte-identical on main `6cb1c72`.

**Issue 9 bullets on this candidate.** None is fully demonstrated, so this
candidate alone cannot complete T08.
- Bullet 1: its callback analog holds except R7. There is no promise half.
- Bullet 2: cannot be exercised, because the approved source forbids `add_note`
  and `transfer_to_agent`.
- Bullet 3: only its callback analog (R10, R10b, H1) can be exercised, because
  the source forbids `create_promise`.

Two things remain: a separately accepted candidate whose approved source
permits notes, transfer and promises, and a guard-owner decision on V2.

**Local evidence** (git-ignored, not in GitHub), under
`<claude scratchpad>/wt/t08-verify-20261011T104109500476Z/artifacts/t08-release/`:
- `cases-20261011T104109500476Z.json`: `fd41a4f9…` (canonical `ce3f774b…`)
- `report-20261011T104109500476Z.json`: `48a568cb…`
- `mutants-20261011T104109500476Z.json`: `792daddd…`
- `source-inputs.json`: `1a451853…`
- `scripts/`: `cases_spec.py` `e5856e17…`, `make_cases.py` `866bd346…`,
  `mutants.py` `cb5c1b36…`

This is local ideal-extraction evidence. It proves nothing about caller speech,
hosted behavior, bank persistence or T07 acceptance. No model, hosted call,
simulation, lease or holdout was used.

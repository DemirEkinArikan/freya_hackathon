# T08 release integration matrix

T08 has three declared checks: confirmed final terms without duplicate recorded
promises; deferred notes discarded with unwritten commitments on handoff; and
retained partial successes with a single final close. Integration must exercise
the original generated candidate accepted by T07. Passing source tests does not
complete this dependency.

The integration checkout starts at `4ef4e6275ee7cb4cb4fd010a3cf1e12f71b9b38b`,
which includes the checked administrative-budget, input-format, authored-routing
and routing-guidance changes in PR112/110/113/114. Production guards, compiler,
prompts and generated outputs are unchanged by this evidence lane.

## Evidence boundary

`tools/t08_release_matrix.py` is evaluator-only. Supply the original selected
generated run, its exact OpenAPI and separately reviewed local cases. It checks
the selected attempt, root artifacts, original source/configuration/trace
bindings and exact guarded recompilation before replay. Case artifacts belong
outside every generator input and repair context. Never amend original generated
policy, plan, workflow, manifests or failed logs to make a case pass.

Run the checker after replacing these example paths with the original reviewed
artifacts. The output must be new and outside the original run directory:

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

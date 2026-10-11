# Source-bound local checks for generated banking candidates

`tools/generated_candidate_acceptance.py` checks one independently authored
local case against the original frozen candidate. It reuses exact trusted
recompilation and sealed execution from `tools/replay_guarded.py`; it makes no
model or network call. A passing case is ideal-extraction compiled behavior
evidence only. It cannot close T07 by itself.

Before authoring cases, retain the original successful mandatory isolation
audit, substantive zero-exit policy/plan turns, source snapshot, ordered approved
inputs and terminal manifest. Verify the original 1,800-second deadline and
export reserve, frozen policy/contract, selected attempt, root artifact equality
and exact original-source recompilation. Review operative rules against source
conditions, selected tool permissions, schema fields/enums and speaking nodes.
Literal witness coverage alone cannot prove interpretation. Never amend a
generated policy or workflow manually to satisfy a case.

Only after that freeze, author evaluator-only extraction frames, mock responses
and expectations from approved source. Keep them out of generation and repair
inputs. Use the actual generated node names and offered intent choices. The
expectation binds the whole workflow, controller context, OpenAPI and script
with canonical SHA-256 digests. The contract binding hashes the entire contract
including its embedded `sha256`; it differs from that compiler-defined digest.
Source review records must bind the approved document inventory separately;
hash agreement cannot prove that the source-derived expectation is correct.

Declare the source-permitted wire operations, exact ordered calls and request
arguments, trusted terminal node, selected final-state fields and closing mode.
The checker rejects changed bindings, unexpected calls or arguments, unused
frames/receipts, incomplete routing and mismatching terminal state. For a
required recorded close, the sealed original kernel must record successful
closing evidence. A source-required no-write uncertainty ending must instead
declare no close attempt and its exact uncertainty state. An `unrecorded_attempt`
checks that closing was attempted but never accepted. The sealed kernel currently
quarantines every unsuccessful close; this local mode cannot independently
classify a definite failure versus unknown persistence. Preserve original
receipts and their source interpretation separately.

The strict expectation has `version: 1`, `bindings`, `allowed_operations`,
`calls`, `terminal_node`, `final_state` and `closing`. Bindings use
`workflow_sha256`, `guard_context_sha256`, `openapi_sha256`, `contract_sha256`
and `script_sha256`; `input_bindings` computes these full-object digests.
Each ordered call declares `operation` and exact typed `arguments`. The
nonempty final-state subset must include boolean `closed`, `close_attempted`
and `quarantined`. Closing declares the frozen wire `operation` and one of
`recorded`, `not_attempted` or `unrecorded_attempt`.

```sh
python3.11 tools/generated_candidate_acceptance.py \
  --workflow artifacts/candidate/workflow.json \
  --guard-context artifacts/candidate/attempts/plan-001/guard-context.json \
  --tool-api artifacts/approved-inputs/bank.openapi.json \
  --script artifacts/evaluator/case.json \
  --expectation artifacts/evaluator/expectation.json \
  --output artifacts/evaluator/result.json
```

Use the actual selected attempt directory, never assume attempt 1. Inputs must
be regular JSON files in this repository or its worktrees. Reports stay in this
checkout's `artifacts/` and existing reports cannot be overwritten. An absent
candidate remains unexecuted; these example paths imply no accepted build.

For the approved callback job, independent coverage must include a valid
confirmed slot, correction invalidating consent, denied consent, missing/invalid
call inputs, today/next-business-day window boundaries and closures, the one
call-wide scheduling cap, exact documented failure resumption without retry,
unknown-write no-close handling, immutable recorded success, and exact final
outcome/reason bindings. Only scheduling and closing are permitted. Ideal
extraction cannot prove that a caller heard the required full readback.

S03 identity-only cases must independently cover factor order, positive and
negative results, corrections preserving verified factors, the supplied
cumulative wrong-answer boundary, exact sourced closing outcomes and bounded
irrelevant turns. S04 account-information cases additionally need preidentity
privacy, failed-identity getter blocking, opaque schema bindings, typed
product-specific disclosure/order, explicit raw-amendment precedence and
completion without unpermitted collection writes. These are preparation
checklists until those lanes provide approved inputs and frozen candidates;
they supply no missing policy values.

Replay does not establish actual platform entry/caller event scheduling or
speech. Keep policy adaptation, caller-wording robustness, model provenance,
hosted execution and all-path termination as separate claims. Do not add a new
three-repeat hosted requirement to the declared T07 deliverable.

# T13 scoring and evidence contract

This continuation starts at main `daef21738c2866a898fc79203aae3892b511bbc6`
and reviews merged PR51 (`e437f564b09a52b52eb385559c4fa202ec401ea3`).
Only `benchmarks/scoring.py`, its dedicated evidence tests, and this document
are owned here. Evaluator recording and bundle assembly retain ownership of
`benchmarks/evidence.py`, `bundle.py`, and standalone hosted wiring.

## Current acceptance — 11 October 2026

T13's three original scoring/capture criteria are complete. The older
checkpoints below describe the evidence available before the actual adapter
captures and are historical. [The closure audit](ISSUE-CLOSURE-AUDIT.md) and
[issue14's accepted checkpoint](https://github.com/DemirEkinArikan/freya_hackathon/issues/14#issuecomment-6102944376)
supersede their outstanding-live-evidence statements.

| Original criterion | Accepted evidence |
| --- | --- |
| Exact ledger multisets, order, counters, duplicate writes and transfer/note constraints | PR64 scorer regressions and its verified merge, plus PR88's present-null closing correction |
| Transcripts, call/run IDs, judge verdict, latency and retained retries | Four original pinned calls across two fresh repetitions, with original transcripts, simulation/iteration/call attribution, request timings and observed retry metadata |
| Missing/incomplete evidence cannot pass; local and hosted claims remain separate | PR88 retains the original infrastructure verdict and separately re-scores complete null/null receipts as behavioral FAIL; PR95 recorder/bundler and dedicated comparison checks reject incomplete or contradictory evidence |

[PR88](https://github.com/DemirEkinArikan/freya_hackathon/pull/88) is merged with
checked/pushed head `f3073ff174c6525674af4983c384271a0a9f7616` and merge
`9e3ff00f719c84d7c7b764b46277f7a65fd081ba`.
[PR95](https://github.com/DemirEkinArikan/freya_hackathon/pull/95) is merged with
checked/pushed head `be0154d45affce28780fe13fdac49e6c3ceedb05` and merge
`c77a9289068bdc402e9c6dff1db3180391ea5e08`. These matching heads were read back
from GitHub for this continuation, which started from fetched main
`4ef4e6275ee7cb4cb4fd010a3cf1e12f71b9b38b` on `rapid/t13-20261011`.

The original result SHA-256 is
`844446317f867094f68eba47b114b176c20c476e229503e772d048fdfe6c50d6`.
Its separate [receipt re-score](network-evidence/20261011-native-adapter/receipt-rescore.json)
binds the original capture to the corrected scorer source. All four banking
verdicts remain **FAIL**. The harmless greeting graph establishes adapter and
capture compatibility. The judge's `avgLatencyMs: null` and observed
`attempts: 0` / `maxAttempts: 4` remain as recorded; request timings and call
durations do not supply a missing response-latency measurement. No actual retry
occurred, and no successful banking case or induced retry is required for
scorer acceptance. New captures must retain their own source, attempt, version,
transcript, calendar and configuration bindings; historic evidence is never
reconstructed from the current checkout.

At this continuation's board readback, T10/T11/T12 are closed and T09 is open.
T13 is **dependency-only** and closes when T09 actually completes. The existing
matching-head PR88 completion evidence remains valid; documentation or later
checks do not replace its source attribution. After closure, notify T14
immediately. Actual repair execution and baseline/structured banking comparison
retain their respective T14/T15 acceptance requirements.

The dedicated `tests/test_t13_capture_integration.py` exercises actual
`record_evaluation` output after persistence through `assemble_bundle` and
`compare`. Complete null/null failures remain comparable with zero strict
passes; missing, partial or malformed closing receipts and transcript/ledger
outages remain incomparable. Exact oracle mismatches remain failures even
when successful tool writes and persisted ledger terms agree internally.
Explicit recapture retains the failed original record while selecting a fresh
record. These are offline integration checks using synthetic external services,
with no new banking conversation, simulation or generation claim. Original
capture hashes and per-call bindings were independently verified read-only.

## Bank evidence

`score_ledger` checks exact multisets, including duplicate records, against the
independent oracle and against successful tool writes. It requires ordered
identity factors, one shared wrong-answer counter, verified account lookup
before promises and product notes, bounded promise retries, no notes on a
transfer, only close after successful transfer, and exactly one successful final
close with matching terms. Unknown actions and malformed supplied arguments or
results fail. Absent success/verification evidence never becomes success.

All supplied `args`, `arguments`, and `body` sources must be valid JSON objects
and agree after request-body unwrapping. Duplicate JSON keys, nonfinite values,
non-JSON nested values and discarded outer semantic fields fail. Optional
result metadata must have its documented type. A processed identity check must
supply `verified`; an explicit failed action with an error may remain unverified.
Existing bank records that omit optional identity `ok`, counters or completion
flags remain supported; supplied counters and flags must match the sequence.

`swapped_date_field` defaults to the official `date_of_birth`. A renamed identity
contract must explicitly supply its date factor; `None` disables the swap-hint
exemption. A hint on another factor cannot bypass the wrong-answer budget.
No correct-answer values or account products are inferred from evaluator data.

## Attributed conversation annotations

A ledger pass remains a bank-evidence verdict. `honesty` stays `not_assessed`:
partial success-claim annotations and local ordering checks do not establish
complete spoken honesty. Hosted acceptance still requires the independent
platform conversation judge, retained transcript and per-call version evidence.

Legacy `evidence.claims` can expose early promise-success claims. If amount/date
are supplied, both must be supplied and exactly match the successful write.
Empty claims do not report honesty assessed.

Optional `evidence.conversation` adds a separately reported
`conversation_order`: `not_assessed`, `incomplete`, or
`normalized_order_checked`. The object contains:

- `complete: true`, an identified `normalizer`, the retained `transcript` object
  with `id` or `callId` and `messages`, and its canonical `transcript_sha256`.
- `tool_spans`, in ledger call order, with `call_index`, `start_message_index`
  and `complete_message_index`. Each span binds the actual system
  `function_call_start`/`function_call_complete`, function name, arguments and
  result. Every retained event for the seven banking functions must be covered
  exactly once. Other engine events remain in the transcript.
- `events`, ordered by zero-based source `message_index`. Equal positions are
  permitted for distinct annotations from the same utterance. Duplicate
  annotations fail. `readback` has a `promises` list of exact product/amount/date
  records; `confirmation` identifies `readback_message_index`; `consent_changed`
  identifies affected `product_ids`; `success_claim` identifies `call_index` and
  the exact `record`; `farewell` identifies the source assistant message.

The normalizer supplies semantic meaning independently; the scorer checks its
source binding, exact terms, speaker roles, completeness declaration and order.
It does not parse natural-language consent or manufacture annotations from
ledger success. Unsupported transcript formats or incomplete annotations fail
when this optional evidence is requested. System event functions may be a name
string or an object with `name`; semantic source messages use the kit's `message`
field. The retained transcript digest binds the evidence but is not proof that
its semantic annotations are correct.

Every promise attempt needs exact readback and later customer confirmation
before its tool starts. The latest readback for the product supersedes earlier
consent. An annotated customer amendment or withdrawal after the readback invalidates
its consent, including an amendment before or within the confirming utterance; renewed readback and confirmation are required. Brief B requires one
collective confirmation for the first attempts on distinct products. Adapted
contracts can supply the independent `collective_confirmation` boolean instead
of the official brief default. Retries retain the same evidence checks.

Success claims for promises, notes, callbacks and transfers must match exact
terms and follow the successful tool completion, not merely its start. A
farewell must follow the previous banking completion and precede close. Note/transfer exclusions and successful-write
reconciliation remain deterministic bank checks. Natural-language meaning,
complete extraction of semantic events, persuasion, policy applicability and
other conversation behavior remain the independent judge's responsibility.

## Historical checkpoint before live adapter acceptance

New synthetic regressions failed in 23 subcases before the initial corrections.
Independent review then reproduced omitted transcript banking events and
malformed nested arguments; both have regression coverage. Positive controls
cover failed-action retries, adapted date factors, collective confirmation,
renewed consent, unrelated consent changes and several annotations in one
utterance. These are local implementation checks, with no held-out inputs.

Run Python 3.11 scoring/full-suite checks, `tools/verify_kit.py`,
`tools/package_submission.py --check`, and `git diff --check`. Exact checked and
pushed heads and final counts are recorded in issue14/PR checkpoints; logs stay
in ignored `artifacts/t13-checks/` in the isolated scoring worktree.

As of 11 October 2026 Istanbul, the latest primary-thread checkpoint records a
public-A build that failed safely at 1057.68 seconds, with no validated generated
banking deployment. T11's documented bank TLS prerequisite also remains
unresolved. The canonical lease records a previously completed synthetic
simulation. At 00:13:57 Istanbul on 11 October (21:13:57 UTC on 10 October),
a bounded read-only server request under the canonical lock verified simulation
`03229c8e-5d0c-41fd-8179-35a50c6cc43e` is `completed`, with the matching agent.
The lease and iteration `ca839f71-ddae-40ae-8e56-9cfaeed37a34` were preserved;
no new simulation or remote mutation occurred. Initial automatic review rejected
credential loading, then approved the same request after reviewing README's
explicit authorization for this repository's controller-owned credentials.
Sanitized private evidence is retained at the canonical repo's
`artifacts/t13-evidence-20261010/lease-readback.json`. Reconcile current primary
and actual server state again immediately before a future live simulation.

T09/T11/T12 remain dependency acceptance gates. This scorer PR does not close
issue14. The outstanding deliverable is an actual accepted generated-agent
trial, pinned before/after and per call, with fresh bank sessions, retained
sanitized original transcripts, judge verdicts, timings, version attribution,
infrastructure retries and incomplete reports. Primary coordination uses
`artifacts/thread-coordination/t13-to-primary.json`; all worktrees must use
`/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock`
and `.simulation.json`. No evaluator records may enter generation inputs; no
holdouts may be consumed for this development audit. Once the actual evidence
and dependencies are complete, `tools/cowork.py complete` must use the merged
same-repository PR whose head matches the checked, pushed completion commit.

## Historical merged verification

PR64 merged checked/pushed head `2afe55f8d6d5bec8fbbb6312aeee0790a6c16667`
as `d1f044256660a904fd6032661ccee9b90787198e`. The three scoring files match the
checked head exactly. Both independent reviews approved it. The checked branch
passed 65 scoring tests and 754 full-suite tests. Isolated verification of the
merge commit passed all 766 Python 3.11 tests in 57.664 seconds, kit16,
source160 and diff checks. No GitHub CI checks were reported. Original logs are
retained in `/private/tmp/freya-t13-merged-check/artifacts/t13-merge-check/`;
pre-fix and branch checks remain in the isolated scoring worktree.

This subsequent document update records verified lease evidence and merged
checks; it changes no scorer, fixture, builder or generated-agent behavior.
Issue14 remains open for actual pinned generated-agent evidence and dependencies.

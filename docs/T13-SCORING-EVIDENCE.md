# T13 scoring and evidence contract

This continuation starts at main `daef21738c2866a898fc79203aae3892b511bbc6`
and reviews merged PR51 (`e437f564b09a52b52eb385559c4fa202ec401ea3`).
Only `benchmarks/scoring.py`, its dedicated evidence tests, and this document
are owned here. Evaluator recording and bundle assembly retain ownership of
`benchmarks/evidence.py`, `bundle.py`, and standalone hosted wiring.

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

## Actual verification and remaining acceptance

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
simulation; its actual server status must still be checked before live use.
Automatic approval review blocked reading this repo's `.env` for that read-back;
explicit credential approval is pending. The lease was preserved and no
simulation was started here.

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

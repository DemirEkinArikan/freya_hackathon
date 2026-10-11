# S03 identity-only input: independent peer review

Reviewer: Claude lane agent, read-only, 11 October 2026 06:14 UTC. The
authored input is Codex S03's uncommitted pack in
`freya_hackathon-worktrees/rapid-s03-20261011`; this review binds its exact
bytes. Issue record: #8 comment 6106156362.

| File | sha256 |
|---|---|
| `docs/development-inputs/s03-identity-only/brief.md` | `d1857a2a16dc17b0f3f217968e2aece5e6bd73d0e1f2c09fe45c0aaff7ea75b6` |
| `docs/development-inputs/s03-identity-only/bank.openapi.json` | `bec299c656baff8cc40831ec977e7e1541c35cc89289a09e6b3413f94601c262` |
| `docs/development-inputs/s03-identity-only/README.md` (not a generator input) | `ab98287eaf42b5ed305374175b7f523c0e4f9493d476c2d52ef39b7e176ffe7b` |

## Verdict: changes requested (one sentence), then re-fingerprint

Passed checks:

- Seven supplied-bank-tool roles are declared; R01/R09/R12 permit only
  `verify_identity` and `close_call`. No disclosure, promise, note, callback
  or transfer path exists. API availability and policy permission are stated
  separately.
- Exact two-factor identity: formats, order, corrections and revocation
  (R03-R07). Shared four-answer counter with exact movement and contradiction
  rules (R05).
- Verification outcomes: definite transient with one exact retry, permanent,
  `session_sealed` and uncertain (R08).
- Close exactly once, with the single `session_sealed` no-close exception and
  explicit persistence semantics (R11).
- OpenAPI 3.0.3 with a `.invalid` server. Every request and result field has
  a `Semantic ... field:` mapping (none missing). Every 200 schema sets
  `additionalProperties: false`; 500 and 409 are declared bodyless statuses.
- Closing table: eight rows, every code present in the `close_call` enums,
  no unused enum value, no duplicate pair. Requirements are labelled R01-R12.
- Novelty: none of the novel wire or outcome names occur on `origin/main`,
  including `benchmarks/fixtures/*`, or in the local reserved callback
  coordination record. There is no overlap with the 120 fixtures.

Findings:

1. Blocking: the closing selection is ambiguous for spoken non-answers to the
   opening question. R02 and closing row 7 use "never answers" (`no_contact`).
   R09 uses `no_contact` "only if the caller has never supplied a spoken
   initial-contact reply". Two off-topic replies to the opening question
   therefore select either `no_contact` or `unfinished_pair`. Define whether
   an off-topic utterance counts as contact.
2. Advisory: the API descriptions restate brief permissions ("FORBIDDEN on
   every path", and the shared 500 text). This is consistent, but it weakens
   the test that the brief takes precedence over API availability.
3. Advisory: conditional result fields are stated only in prose
   (`required: [done_flag]`). This is acceptable because R07/R08 classify a
   missing field as uncertain.

## Current-source gap (independently confirmed at `4ef4e627`)

These line numbers refer to `builder/guards.py`:

- In `guard_close` (lines 903-947), a verified identity with no account and
  no protected fact raises `closing lacks protected evidence`.
- The `identity_close` shape check (lines 327-329) accepts only
  `failed/incomplete/unreached`.
- The only `identity_close_choices` discriminator is callback evidence.

R10 needs a successful close plus state-derived `check_fault`, `quota_used`,
`declined_check`, `check_ambiguous` and `unfinished_pair`. A generic typed
identity-terminal source fix, with its own checked PR, must therefore precede
generation. Weakening the input is not an acceptable substitute.

## Generation status

No S03 generation has run. One fresh generate-only build is reserved for
after the F1 fix is re-fingerprinted and the generic source fix merges. That
build keeps the 1,800-second deadline, the export reserve and a fresh
tool-free audit, and the synthetic `.invalid` endpoint stays local.
Evaluator-only boundary cases and ledgers wait for candidate freeze. This
review is not generation, candidate or banking evidence.

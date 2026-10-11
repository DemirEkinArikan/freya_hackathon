# S01 independent semantic-review check, 11 Oct 2026

This record adds two things to the S01 source-policy review (issue #8, which
supports T07 and T14). Inputs were limited to approved brief A, kit v3 and
unchanged original generated artifacts. No evaluator scripts, customer scripts,
expected records, transcripts, raw model events or held-out inputs were read.
Base commit: `4ef4e6275ee7cb4cb4fd010a3cf1e12f71b9b38b`.

## 1. Mechanical evidence check

`tools/review_evidence_check.py` is read-only. It handles each evidence object
that names a file together with its raw SHA256 (`path`/`absolute_original_path`
plus `raw_sha256`/`sha256`). The checker:

- rehashes the original bytes;
- locates the `quote`, `excerpt` or `value` at the cited JSON pointer or line
  range, ignoring differences in whitespace;
- exits non-zero on a missing file, a hash mismatch, a missing pointer, or a
  quote or value that does not match.

Canonical-JSON fingerprints are not rehashed, because they use a different hash
convention.

At about 06:12 UTC the checker was run on the Codex S01 draft records. They were
untracked in `freya_hackathon-worktrees/rapid-s01-20261011` and had not been
edited since 06:00 UTC.

| Draft record | Raw SHA256 of record | Evidence items | Quoted | Failures |
|---|---|---|---|---|
| `public-a-20261010T234440089175Z.json` | `175afd0e…e690` | 81 | 61 | 0 |
| `ab-20261010T224748051958Z.json` | `53159ef1…f013` | 58 | 36 | 0 |
| `development-rejections-20261011.json` | `43bdf62b…ed34` | 150 | 134 | 0 |

Full hashes are in [the JSON record](S01-SEMANTIC-REVIEW-20261011.json). This
result shows that the draft's 289 evidence pointers and hashes are accurate for
those exact bytes. It does not confirm the draft's classifications or
conclusions.

## 2. Literal required wording in Public A (`20261010T234440089175Z`)

The original export status is preserved: exported, not accepted. Artifacts
checked:

- `policy.json` (`3f5773c6…8e`)
- `submission.workflow.json` (`402c440b…ac`)
- brief A (`d57c049c…c7`)

| ID | Brief A rule (line) | Policy extraction | Export representation | Classification |
|---|---|---|---|---|
| L-01 | 2 (25) `Am I speaking with {title} {last_name}?` | exact, `/rules/31` | fixed `tts` in `opening`, exact | satisfied in stored text |
| L-02 | 9 (35) `I am sorry for your loss.` | exact, `/rules/33` | `llm` node `identity_exit`, instruction only | unproven dialogue |
| L-03 | 28 (77) `Can you make this payment today?` | exact, `/rules/41` | `llm` node `account_intro`, instruction only | unproven dialogue |
| L-04 | 34 (88) `Do you confirm?` | exact, `/rules/44` | fixed `tts` says `Do you agree?` | unsupported controller representation |
| L-05 | 38 (92) `You will receive a text message confirming this promise.` | exact, `/rules/44` | `llm` node `promise_recorded`, instruction only | unproven dialogue |

All five literals were extracted exactly, so none is a model omission or a
missing source requirement. Only one is compiled as exact fixed speech. The
compiled confirmation template replaces a sourced literal (L-04, also listed in
the draft as PA-01). The remaining three are prompt instructions only.
Descriptive or prompt text does not prove that the line is spoken.

## 3. Handoff and limits

- **T07:** compile every exact-wording literal that the policy extracted into
  source-bound fixed speech. Alternatively, reject the candidate before
  claiming semantic acceptance. Check the next fresh candidate by running the
  checker on its review record.
- **T14:** runtime acceptance must hear L-02, L-03 and L-05 verbatim, at the
  source-required moment and only then. L-05 is allowed only after a positive
  `create_promise` result.
- **Limits:**
  - Stored text is not hosted speech.
  - Later merged source fixes are not retested here and do not upgrade this
    export.
  - No new generated candidate existed when this record was written.
  - No compiler, prompt, generated artifact, evaluator fixture or T07 evidence
    file was edited.
  - No model, deployment, simulation or holdout was used.

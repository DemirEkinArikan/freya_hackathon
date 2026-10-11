# T15 local changed-policy lane: baseline method, 11 October 2026

This page covers the baseline method of the
[local changed-policy lane](T15-COVERAGE-ACCOUNTING.md). It holds actual
evidence, not a comparison. T15 stays open. The machine-readable record,
including per-fixture verdicts and artifact hashes, is
[t15-baseline-local-20261011.json](runtime-evidence/t15-baseline-local-20261011.json).

## Coverage

| Split | Declared | Builds | Replays executed | Pass | Fail | Uncovered |
|---|---:|---:|---:|---:|---:|---:|
| train (train-1, train-2) | 36 | 2 of 2, both failed | 0 | 0 | 36 | 0 |
| validation (validation-1, validation-2) | 24 | 0 of 2 | 0 | 0 | 0 | 24 |

How the counts are made:

- Neither training build exported a workflow, so no replay can run. The 36
  training fixtures are counted as failures of the baseline method
  (`no_candidate_build_failed`). They stay in the denominator and are not
  reported as replay results.
- The 24 validation fixtures are uncovered. The declared ordering ("Validation
  starts only after both methods finish training in both lanes") is not met,
  because no structured training result exists in either lane. The comparator
  enforces the same rule as `validation_preceded_paired_training`. No
  validation input was exported or built.

## Builds

Both builds used the declared baseline `2d09c31d2f2d25a0e01466e707f560127634cad3`
(tree `14da16a…`), `builder_source_sha256` `ad990dfb…257b`, `gpt-6.1-sol` with
high reasoning, `--generate-only`, a 1,800-second deadline and a 300-second
export reserve. Each ran in its own clean detached worktree. In both, the fresh
tool-free isolation audit passed with no failures and `evaluator_input` was
null.

The inputs are byte-identical to the structured side's train-1 and train-2
exports from evaluator `offline.json` `f0efb9d3…`. They were re-derived in
memory to confirm, and no second export was made. The builder-level
fingerprints equal those of the round-1 structured `3f2399d` runs.

| Profile | Run | Elapsed | Result |
|---|---|---:|---|
| train-1 | `20261011T071451000468Z` | 808.9 s | `failed` (builder): policy extraction failed after bounded repairs |
| train-2 | `20261011T071802531422Z` | 987.7 s | `failed` (builder): policy extraction failed after bounded repairs |

Policy-stage failure families, paraphrased by the evaluator; no model text is
copied:

- **Declared unsupported constraints.** Every rejected attempt declared them.
  Both profiles declared at least once each:
  - identity-failure closing dispositions
  - transfer-queue prerequisites
  - the meaning of hardship "support once"
  - the closing disposition for a successful callback contact
  - failed-write retry counter scope
  - mandatory dispositions after exhausted persuasion, rejected proposals or a
    second failed promise

  train-1 also declared unnamed read-back product fields, overdue-product vs
  returned-product scope, and lateness bands that stop at 45 days.
- **2d09c31 schema limits.** train-1 attempts 1 and 3 and train-2 attempt 1 used
  rule fields that the 2d09c31 strict policy schema rejects (it allows only
  `id`, `kind`, `value`, `source` and `overrides`). train-2 attempt 1 also cited
  a quote absent from its source. train-1 attempt 2 failed the output format
  check.

The round-1 structured `3f2399d` train-1 build also failed with
`policy contains unsupported constraints`. That build is not the selected
structured source, so this is context only.

## Evaluator note for any later candidate

`tools/replay_guarded.py` recompiles the candidate with the evaluator
checkout's own compiler and requires byte equality. On the retained 2d09c31 A+B
run `20261010T224748051958Z`:

- main `3dad5dd`'s `validate_workflow` returns "guarded workflow differs from
  exact controller compilation";
- 2d09c31's validator accepts the same files with no errors.

A main checkout would therefore reject every baseline fixture before replay. A
baseline candidate needs a matching-compiler evaluator: main's `tools/` and
`benchmarks/` (fixtures, scorer, replay engine and ledger normalization)
unchanged, with only `builder/` taken from 2d09c31 for the recompilation check,
as [LOCAL-GENERATED-EVAL.md](LOCAL-GENERATED-EVAL.md) requires ("its matching
compiler source"). Such a tree (`503efaf9…`) accepts the retained 2d09c31
workflow. It produced no verdict here, because no candidate exists.

The same applies to structured candidates. `3f2399d` and `3dad5dd` have
identical `builder/`, `tools/` and `benchmarks/` trees, but main `180507c`
changes `builder/validation.py` and `builder/execution_requirements.py`.

## Remaining for this method

1. Validation builds `validation-1` and `validation-2` at 2d09c31, after the
   ordering gate opens. Export each profile once from the evaluator checkout,
   give both methods the same bytes, and launch once per profile.
2. If a later validation build exports a candidate, author its 12 bindings
   without reading oracles and run `local_generated_eval --split validation`
   under the matching compiler. Otherwise record its fixtures as failures, as
   above.

The baseline source is frozen, so its training failures are final for this
comparison. Do not rebuild these inputs on this source. No hosted call,
simulation, holdout, candidate freeze or issue closure is claimed.

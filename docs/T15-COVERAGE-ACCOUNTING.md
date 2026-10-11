# T15 coverage accounting — 11 October 2026

Preparation only. No baseline or structured comparison call has run in either
lane, no candidate is accepted, and T15 stays open. Source selections, input
fingerprints and hosted commands are in the
[rapid readiness checkpoint](T15-RAPID-READINESS.md) and its
[plan](runtime-evidence/t15-rapid-development-plan-20261011.json). This page
accounts for what each evaluation lane can and cannot show.

## Two lanes, each 36 training and 24 validation per method

### Hosted wording lane (known bank facts)

| Brief | Split | Fixtures | Distinct bank cases | Reps | Calls per method |
|---|---|---:|---:|---:|---:|
| A | train | 26 | 10 | 3 | 78 |
| B | train | 10 | 3 | 3 | 30 |
| A | validation | 14 | 10 | 3 | 42 |
| B | validation | 10 | 3 | 3 | 30 |

That is 60 fixtures over 13 distinct `bank_case_id` facts: 180 calls per
method, 360 total. Every validation bank case also appears in training
(A 10 of 10, B 3 of 3). Validation fixtures differ only in customer wording
style (`plain`, `pieces`, `corrects`, `talkative`, `asks_back`, `in_words`,
`compound`). All 60 rows declare "wording robustness only, not unseen-policy
generalization". This lane therefore measures robustness to new wording on known
facts. It cannot show adaptation to a new case or a changed policy.

### Local changed-policy lane (adaptation)

`benchmarks/fixtures/offline.json`, SHA-256
`f0efb9d381af5daa66fa23e1478199b7dfd237eca500c6e6eeb66be6941b03dd`; sorted
development fixture-ID list SHA-256
`ebd0ae895a19546566722705a75666062a5f857f29ee55df441f28a4c8cd7afd`.

| Profile | Split | Fixtures | Builds per method | Replays per method |
|---|---|---:|---:|---:|
| train-1 | train | 18 | 1 | 18 |
| train-2 | train | 18 | 1 | 18 |
| validation-1 | validation | 12 | 1 | 12 |
| validation-2 | validation | 12 | 1 | 12 |

That is 60 fixtures over four policy profiles. Validation profiles are distinct
policies from training profiles, so this lane is the adaptation evidence. Each
method needs four generate-only builds and 60 independently authored replay
bindings (eight builds and 120 replays in total). Compiled replay is
deterministic ideal-extraction execution, so each fixture runs once. A local
PASS proves no speech handling, hosted behavior or persistence
([limits](LOCAL-GENERATED-EVAL.md)).

### Counting rules

Report coverage per lane, method and split as completed fixtures out of declared
fixtures. A failed fixture counts as a failure and stays in the denominator. An
unrun, unbound or incomparable fixture stays uncovered; it never counts as a
pass or a weaker result. Neither lane substitutes for the other. Generated-goal
witnesses (S03/S04, issue #8) and the irreversible-action fault matrix (S05,
issue #9) are reported separately and are not part of these counts.

## Equivalent inputs for the local lane

`offline.json` differs between baseline source `2d09c31` and the evaluator
source, and `tools/local_generated_eval.py` does not exist at `2d09c31`. Export
each profile once, from the evaluator checkout, and give both methods the same
bytes. The builder refuses inputs outside its own checkout, symlinked paths and
paths under `benchmarks/` or `tests/`, so copy the export into each method
checkout and compare hashes. Never export or evaluate from the baseline
checkout.

```sh
# Evaluator checkout (main). Export once per profile; existing outputs are refused.
mkdir -p artifacts/t15-inputs
python3.11 -m benchmarks.export_inputs --profile PROFILE \
  --out artifacts/t15-inputs/PROFILE > artifacts/t15-inputs/PROFILE.export.json
shasum -a 256 artifacts/t15-inputs/PROFILE/*

# Each method checkout: copy (never symlink), then require identical hashes.
mkdir -p artifacts/t15-inputs
cp -R EVALUATOR/artifacts/t15-inputs/PROFILE artifacts/t15-inputs/PROFILE
shasum -a 256 artifacts/t15-inputs/PROFILE/*

# Each method checkout: generate-only build. Synthetic .invalid APIs are never
# deployed. Repeat --brief for every exported brief_paths entry, in order.
python3.11 builder/build.py --generate-only \
  --brief artifacts/t15-inputs/PROFILE/BRIEF.md \
  --tool-api artifacts/t15-inputs/PROFILE/bank.openapi.json \
  --output-dir artifacts/t15-local-METHOD-PROFILE \
  --model gpt-6.1-sol --reasoning-effort high

# Evaluator checkout: one result per fixture, with a fresh output path.
python3.11 tools/local_generated_eval.py --split train --fixture FIXTURE \
  --workflow RUN/workflow.json --guard-context RUN/guard-context.json \
  --tool-api artifacts/t15-inputs/PROFILE/bank.openapi.json \
  --script artifacts/t15-replays/METHOD/FIXTURE.json \
  --output artifacts/t15-local-results/METHOD/FIXTURE.json
```

Use `--split validation` only for `validation-1` and `validation-2`. Replay
scripts bind each candidate's actual node and variable names. Author them
without reading oracles, and keep evaluation output out of generation or
repair prompts.

## Stage order and budget

1. T07/T14 accepts a structured candidate. If its source is not `4ef4e62`,
   select and fingerprint that source before any result.
2. Training, both methods: local `train-1`/`train-2` (four builds, 72
   replays) and hosted A and A+B training (four live builds, 216 calls).
3. Validation starts only after both methods finish training in both lanes:
   local `validation-1`/`validation-2` (four builds, 48 replays) and hosted
   validation (144 calls).
4. Bundle and compare hosted A and B separately. Tabulate local results per
   profile. Keep every failed attempt.

That is 12 builds in total (eight local, four hosted), each with its own 1,800-second deadline and
300-second export reserve. Hosted calls run under the shared simulation lease
with S06 as operator. Local generate-only builds start no Freya client or
simulation. Reserved and held-out profiles stay unexported and unevaluated
until candidate freeze.

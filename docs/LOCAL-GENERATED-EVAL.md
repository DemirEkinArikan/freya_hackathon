# Local evaluation of an exact generated workflow

`tools/local_generated_eval.py` joins the trusted compiled replay to the existing
independent ledger scorer. It accepts materialized development profiles through
their independent source/API bindings, with training selected by default. Later
validation runs require explicit `--split validation`; reserved and held-out
cases are refused. No model, network, hosted simulation or holdout executes.

First export the approved generator inputs separately, preserving brief order:

```sh
python3.11 -m benchmarks.export_inputs --profile train-1 --out artifacts/inputs/train-1
python3.11 -m benchmarks.export_inputs --profile train-2 --out artifacts/inputs/train-2
```

Only those exported brief paths and their OpenAPI enter the builder. Customer
scripts, answers, oracles, helper expectations, frames and evaluation reports
stay outside generation. Synthetic `.invalid` APIs must never be deployed.

After a fresh candidate and source interpretation are independently accepted,
use its original authored workflow/context with its matching compiler source:

```sh
python3.11 tools/local_generated_eval.py \
  --fixture train-1-C01 \
  --workflow artifacts/accepted/workflow.json \
  --guard-context artifacts/accepted/guard-context.json \
  --tool-api artifacts/inputs/train-1/bank.openapi.json \
  --script artifacts/evaluator/train-1-C01.json \
  --output artifacts/evaluator/results/train-1-C01.json
```

The [replay script](COMPILED-REPLAY.md) must be independently authored against
the candidate's actual conversation node names, extraction variable names and
intent transition indexes. It must supply the fixture's synthetic initial
values for the declared inputs and explicit mock protocol envelopes. Missing
frames, invented defaults, mismatching inputs/API/source inventories and
protected-state writes are rejected. The tool never reads an oracle to invent
frames or infer intent. Different generated names need different frame bindings.

The decoder uses the independent profile's operation/request/result mappings
and API schemas, preserving wire receipts, failed attempts, order and promise
receipt IDs. Only valid explicit positive responses can add recorded writes or
closing fields. HTTP status alone, speech, malformed flags and conflicting IDs
cannot establish success. Returned account facts/order must match the bound
fixture, and declared injected faults must actually appear as failed receipts.
The original replay stays separate from its normalized projection.

The API instance checker supports the simple typed object/array/scalar schemas
in the current development profiles. References, composite schemas, bodyless
fault receipts and undeclared response statuses are not assessed as valid
receipt evidence; they prevent a local PASS. These are evaluation limitations,
not demonstrated banking behavior failures.

Each output binds fixture/profile/API/context hashes and retains the existing
strict ledger verdict. A local PASS means ideal-extraction compiled execution
and mock-receipt ledger agreement. It proves no speech interpretation,
fragmentation, persuasion, honesty, hosted behavior, actual persistence or
original model-generation provenance. This development evaluation does not
extend a builder's 1,800-second budget. All 18 training cases in each profile
need their own authored bindings and actual result; unrun cases remain untested.

`benchmarks.offline --candidate-contract` remains a separate strict fixture-IR
comparison. Fresh operative kinds derived from supplied API/source can differ
from the descriptive fixture IR; extra/unassessed dimensions need an independent
source review. Neither a raw kind mismatch nor a matching local ledger alone
establishes complete policy adaptation. Do not copy evaluation output into a
generation or repair prompt.

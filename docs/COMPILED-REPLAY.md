# Evaluator-only compiled workflow replay

Run the authored controller artifact, its original guard context and matching
OpenAPI against a separate synthetic evaluator script:

```sh
python3.11 tools/replay_guarded.py \
  --workflow artifacts/selected/workflow.json \
  --guard-context artifacts/selected/guard-context.json \
  --tool-api artifacts/selected/openapi.json \
  --script artifacts/evaluator/replay.json \
  --output artifacts/replays/unique-report.json
```

Inputs must be regular JSON files under this source checkout/package or one of
this Git repository's linked worktrees. Reports are new files beneath this
checkout's ignored `artifacts`.
Use the authored pre-authentication workflow, rather than a platform export
containing serializer additions or controller-injected credentials. Exact
trusted recompilation and the ordinary validator run before any code or hook
executes. Tampered code or a mismatching controller context is rejected.

A version1 script contains exactly `initial_inputs`, `frames` and `responses`.
Initial inputs are synthetic `customer_id`, `now` and optional source-declared
call inputs. Each frame contains `node`, `values`, and optionally an integer
`intent_transition` referring to that conversation's actual intent transition.
Frame values may write only the current node's declared extraction outputs.
They cannot inject guard state, another node's confirmation, trusted call
inputs or arbitrary variables. A frame supplies an ideal extraction result
and explicit evaluator intent, not an LLM interpretation of its customer's
speech. Scripts, samples and independent expected ledgers stay evaluator-only;
never include them in generator inputs.

Each mock response names the exact next `operation`, optionally its `node`,
and an original HTTP `envelope`. The replay executes the sealed pre-hook,
records its actual immutable JSON request body, applies the compiled post-hook
and response mappings, and follows ordered conditions. Equality uses string
comparison and `contains` uses case-insensitive substrings, as the kit specifies.
Mapped scalar values
use the kit's documented string representation; containers use JSON strings.
The tool performs no HTTP request or model call. Independent evaluator-only
protocol samples in the tests cover native and renamed result/body fields.
They also cover exact confirmed retries, exhausted writes, refused consent and
transfer precedence with prior recorded promises and discarded staged notes.

The report retains ordered steps, mock calls and envelopes, source hashes,
consumed/unused script inventory and final protected state. Recorded prompts
are instructions only; they prove no spoken confirmation, offer quality,
customer intent, caller robustness or actual platform persistence. Unused
script items are reported rather than silently relabeled as executed. Reports
contain synthetic/local evidence and must remain outside GitHub raw logs.

An end-call node is absorbing. Other outcomes include repeated automatic state
(`absorbing_loop`), missing frames/responses, routing deadlock, step limit and
deadline. New evaluator input can recover a prior blocked route, so loop
detection resets at that input. User-turn counts reset when leaving a node;
this is a local interpretation of documented node caps, not a claim of exact
host behavior at every cap boundary. Each sealed snippet has at most ten
seconds and the replay has its own bounded development budget. This separate
evaluation never extends the original builder's1800-second deadline.

`terminated` means the mock graph reached an end-call node. It is not a strict
banking-case pass: compare calls/state against an independent oracle and
retain policy/ordering failures separately. A silent quarantine terminal may
also terminate without a recorded bank close. Hosted evaluation remains needed.

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

Scripts contain exactly `version`, `initial_inputs`, `frames` and `responses`;
`version` must be integer `1` or `2`. Initial inputs are synthetic `customer_id`,
`now` and optional source-declared call inputs. Version 1 retains caller-only
frames: `node`, `values`, and optional integer `intent_transition`.

Version 2 requires an explicit `event` on each frame and accepts only these
event-specific fields:

| Event | Required fields besides `event` | Optional fields | Meaning |
| --- | --- | --- | --- |
| `caller` | `node`, `values` | `intent_transition` | New caller turn, ideal extraction and routing intent |
| `agent_completion` | `node`, `transition` | None | Model dialogue invokes an offered tool transition |
| `entry_routing` | `node`, `intent_transition` | None | Ideal intent evidence for declared passthrough entry routing |

Every `node` must be an actual compiled conversation node. Integer selectors
refer to its actual compiled transition indices, after private gate remapping;
events cannot name a direct target. `intent_transition` must select a non-tool
transition containing `matches_intent`, and `transition` must select a
transition with `is_tool_call: true`. Entry evidence requires
`passthrough: true`. Caller values may write only that node's declared
extraction outputs; protected guard state, another node's confirmation,
trusted call inputs and arbitrary variables remain forbidden.

These are explicit evaluator evidence events, not platform wire events or
interpretations of caller or agent speech. Scripts, samples and independent
expected ledgers stay evaluator-only; never include them in generator inputs.

Ordinary conversations wait for fresh caller input even with silent entry or
an `always` condition. Declared passthrough conversations route immediately
after entry, including fixed TTS entry, without consuming a caller frame.
Unresolved intent-dependent entry routing needs version 2 `entry_routing`
evidence; missing evidence remains incomplete rather than taking a speculative
fallback. The replay first checks conditions decidable without intent evidence
in list order, then checks unresolved intent conditions with supplied evidence.
A mixed `or` already true or `and` already false is decidable. Supplied intent
cannot force or override a deterministic route, including an ordinary `always`
fallback. This is a local interpretation of the pinned kit's routing guidance;
it does not prove platform ordering or timing. Validation therefore rejects the
nodes where the interpretation matters: a deterministic or `always` route listed
after an intent route of another target, counting same-target routes the kit
merges. Accepted workflows route alike under list-order and deterministic-first
evaluation, and the replay refuses an order-dependent workflow before execution.
The kit merges same-target transitions at load into one OR transition but does
not document the merged transition's position. Replay keeps compiled list order
and does not emulate that merge. Compiled controller nodes such as
`done -> gate`, `allow -> api`, `always -> gate` route correctly only if the
host keeps the earlier `allow` reachable; replay cannot prove that.
Code and API nodes execute and route immediately. Tool-call transitions are
never evaluated as ordinary routing conditions.

An `agent_completion` is valid only during a model dialogue phase: after an
executed LLM `on_enter`, or after a caller turn that stays in the node. Silent
or fixed TTS entry cannot invoke the model's transition tool by itself. A caller
turn with no matching route stays in place and can consume another caller
frame. Only caller turns count toward a declared `max_node_turns`; at the cap,
`max_turn_transition` applies when present. Counts reset when leaving a node.
Staying after a caller turn does not repeat `on_enter`. Prompt records include
`entry_executed` to show whether entry ran, including the effect of
`on_enter_first_entry_only` on re-entry.

Each mock response names the exact next `operation`, optionally its `node`,
and an original HTTP `envelope`. The replay executes the sealed pre-hook,
records its actual immutable JSON request body, applies the compiled post-hook
and response mappings, and follows the node's routing conditions. Equality uses
string comparison and `contains` uses case-insensitive substrings, as the kit
specifies. Mapped scalar values use the kit's documented string representation;
containers use JSON strings.
The tool performs no HTTP request or model call. Independent evaluator-only
protocol samples in the tests cover native and renamed result/body fields.
They also cover exact confirmed retries, exhausted writes, refused consent and
transfer precedence with prior recorded promises and discarded staged notes.

The report schema remains `version: 1` and records the input's `script_version`.
It retains ordered steps, mock calls and envelopes, source hashes,
consumed/unused script inventory and final protected state. Recorded prompts
are instructions only; they prove no spoken confirmation, offer quality,
customer intent, caller robustness or actual platform persistence. Unused
script items are reported rather than silently relabeled as executed. Reports
contain synthetic/local evidence and must remain outside GitHub raw logs.

An end-call node is absorbing. Other outcomes include repeated automatic state
(`absorbing_loop`), missing caller frames, completion or routing evidence,
missing mock responses, routing deadlock, step limit and deadline. New evaluator
input can recover a prior blocked route, so loop detection resets at that input.
Waiting conversations do not become absorbing loops merely because a caller
turn leaves their variables unchanged. Node caps remain a local interpretation
of the documented behavior, not proof of exact host behavior at every cap
boundary. Each sealed snippet has at most ten seconds and the replay has its
own bounded development budget. This separate
evaluation never extends the original builder's 1800-second deadline.

`terminated` means the mock graph reached an end-call node. It is not a strict
banking-case pass: compare calls/state against an independent oracle and
retain policy/ordering failures separately. A silent quarantine terminal may
also terminate without a recorded bank close. Hosted evaluation remains needed.

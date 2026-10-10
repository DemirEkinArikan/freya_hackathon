# Controller-owned action guards

`builder.guards` compiles a compact dialogue/action plan into engine-owned code
and API nodes. A model proposes conversation flow and untrusted argument
variables. It supplies no executable code, bank tools, protected variables,
lifecycle actions, global nodes or terminals. The compiler creates every bank
call, result mapping, gate, confirmation node and terminal.

```python
needed = required_execution_kinds(plan)
execution = compile_execution_policy(policy, bindings, required_kinds=needed)
bundle = compile_guarded_workflow(plan, contract, openapi, execution, bindings)
context = dict(plan=plan, contract=contract, execution_policy=execution,
               bindings=bindings)
errors = validate_workflow(bundle['workflow'], openapi, policy,
                           guard_context=context)
```

Keep `context` outside model output. Validation recompiles and compares the
entire authored workflow, including code, bodies, hooks, mappings and routing.
A hash, manifest or model assertion does not establish enforcement. Validate
the compiler-authored definition before controller authentication binding, and
compare the published definition against that exact authenticated definition.
The manifest records input/output hashes and explicitly leaves hosted behavior
and conversation compliance unverified.

## Plan format

The top level contains `initial_node`, `nodes`, and optional `system_prompt`.
Ordinary nodes have `node_type: "conversation"`, conversation fields from the
kit, and transitions only to public plan nodes. No `tools` or `pre_actions`
are permitted, even empty arrays. Extracted variables are public identifiers;
`bg_*`, `customer_id`, `now` and automatic bank-result names are reserved.

An action placeholder has this shape:

```json
{
  "name": "record_choices",
  "node_type": "guard_action",
  "action": "commit_promises",
  "inputs": {},
  "success_node": "continue",
  "blocked_node": "correct_terms",
  "failure_node": "explain_failure"
}
```

Inputs map semantic field names to public variable names, never literal values.
Customer identity and the clock come only from engine call variables.

## Read-only dialogue facts

Every compiler-authored code step clears and replaces the protected `bg_facts`
JSON string. Dialogue instructions may read `{{bg_facts}}`; authored extraction
cannot write it. Direct authored template references to raw `bg_state`, request
bodies, protected result variables, customer identifiers and canonical or aliased
automatic operation-result variables fail compilation. Reading supplied `now`
remains allowed. Authored `on_enter` speech cannot template `bg_facts`, because
the view includes explicitly internal product references. Engine confirmation
readback continues to use its separate human wording.

The same raw-value denylist applies to nested authored `condition.variable`
reads; conditions may read `bg_facts`, `now` and ordinary public variables.
Conversation nodes may supply a string `routing_prompt` with `{{bg_facts}}` so
the router sees these facts explicitly. The kit makes `task_message` invisible
to the routing model; task instructions alone are insufficient routing context.

The allowlist view contains route/call status, identity progress and the next
supplied factor, monotone remaining answers, staged/recorded product statuses,
recorded-action counts, callback date/time, transfer and close status. An identity
hint/check belongs only to the immediate verified API result and is cleared at
the next guard step. A successful write appears only after actual result evidence;
staging or waiting for consent never claims it was recorded. Unknown persistence
removes identity/account displays and internal action references while retaining
only true recorded-action counts and a safe failure status. Parse/execution
exceptions replace the view with `unavailable`, clearing prior facts. A projection
failure after valid action reconciliation quarantines the reconciled state and
retains its true recorded counts, while removing displays and new request bodies.
API extras are never copied.

`internal_action_references.products` contains stable product IDs solely for
proposing actions. Its `position` follows the policy processing order and
`returned_position` identifies the original account-array position. Never speak
these references. When the protected profile/account supplies them, each internal
reference also contains the normalized product type, unique policy band,
integer-cent floor and past-due cap, bounded business-day deadline, exact
staged/recorded/local-finalized resolution and remaining write-failure budget.
Missing or unsupported data marks proposal constraints `unavailable`; inflight,
recorded, exhausted or sealed products mark them `blocked`. These are routing
and proposal facts, not additional disclosure permission. No persuasion count is
asserted because this kernel has no protected persuasion counter.
Account narration is blocked until an optional active typed
`account_disclosure_fields` rule lists unique, exact returned product-schema
fields. Only those scalar fields are then projected in `account.products`, by
position, without identifiers or undeclared API fields. The compiler rejects
customer/product identifier aliases and missing or structured schema fields.
Runtime missing, malformed or nonfinite selected values clear the view; selected
text containing actual customer/product IDs is not projected.

This field list supplies an unconditional per-run disclosure allowance. It cannot
express conditional, per-product or purpose-specific permissions; retain those
constraints as unsupported until a suitable typed guard exists. The structural
template checks do not prove that an LLM follows every speech instruction or
never mentions data from another context. Hosted dialogue/privacy checks and
semantic source review remain required.

| Action | Required inputs | Optional inputs |
| --- | --- | --- |
| `verify_identity` | `field`, `answer` | |
| `get_account` | | |
| `stage_promise` | `product_id`, `amount`, `date` | |
| `commit_promises` | | `product_id` for separate confirmation |
| `stage_note` | `product_id`, `reason` | |
| `finalize_notes` | | |
| `stage_transfer` | `queue` | |
| `commit_transfer` | | |
| `schedule_callback` | `date`, `time` | |
| `close_call` | | `outcome`, `reason` for language-derived identity disposition |

Staging actions have no bank side effect. Promise staging checks trusted
product IDs, integer cents, the policy floor, past due, band, calendar, deadline
and product order. Changes invalidate consent. Commitment execution freezes a
batch, reads its exact terms with engine TTS, waits for a fresh explicit yes when
policy requires confirmation, and sends only that snapshot. Callbacks and
transfers receive confirmation only when explicitly listed in
`confirmation_actions`. The code cannot prove the extractor interpreted speech
correctly; this remains a conversation test requirement.

Identity progresses only from trusted API evidence, in supplied factor order.
The monotone API remaining counter covers the whole call and naturally includes
API-qualified hint/unreadable-answer exemptions. Account access requires all
factors. API results map into reserved variables that the model cannot write.
Both native and JSON-string product arrays are accepted; unsupported flattened
arrays or malformed evidence fail closed.

Notes remain local dispositions until every product is resolved. Finalization
seals that phase against subsequent promises and callbacks. With
`record_notes: false` it writes no notes. With `true` it commits staged notes and
records success only from API evidence. Transfer staging discards pending
dispositions/promises, retains successful earlier promises, and permits only
handoff and final close. Close outcome/reason are derived from protected facts
using policy precedence and identity disposition tables. A successful bank
close routes immediately to an engine terminal; any further bank operation is
blocked. This establishes an at-most-once last close attempt, not eventual
exactly-one successful close. Uncertain persistence or a failed close
quarantines the call, and conversation liveness remains unproved.

## Typed execution profile

Every profile value must equal its active frozen contract value. The compiler
supplies no bank numbers, dates, default outcome vocabulary or descriptive-prose
interpretation. Existing T06 kinds cover identity, floors, bands, calendars,
callbacks, reasons, confirmation and failure limits. Additional guard kinds are:

| Kind | Shape |
| --- | --- |
| `record_notes` | boolean |
| `resolve_all_products` | boolean; recorded transfer is terminal exception |
| `confirmation_scope` | `"each"` or `"batch"` |
| `confirmation_actions` | array of semantic action names; may be empty |
| `product_order` | `{mode: "account_order"}` or `{field, direction: "ascending"/"descending", tie_breaker: "account_order"}` |
| `close_precedence` | ordered array of semantic outcome names |
| `identity_close` | `{failed: {outcome, reason?}, incomplete: {...}, unreached: {...}}` |
| `nonpersistent_failures` | `{operation: [{http_status: integer, error?: string}]}` |
| `promise_failure_disposition` | `{reason: string, clear_pending: boolean}` |
| `transfer_prerequisites` | `{queue: "none"/"identity"/"account"}` |
| `promise_readback` | `{product_fields: [string], value_labels?: {field: {wire_value: spoken_label}}}`; internal/customer IDs excluded |
| `account_disclosure_fields` | Optional unique list of exact scalar account-product schema fields; absent blocks account narration |

`action_fields`, `result_fields` and `product_fields` active rule dictionaries
can explicitly bind renamed API fields. Canonical names are accepted only when
they exist exactly in the supplied schema; ambiguous/missing fields fail.
Opaque wire vocabularies use T06's API-validated detached category bindings.

When `record_notes` is explicitly boolean `false`, the notes category is instead
`{binding_kind: "local_disposition", semantic_to_wire, wire_to_semantic}`. Its
injective meanings come from the exact frozen policy; it has no bank operation,
field or API-enum assertion. The parent descriptor still records the policy and
OpenAPI hashes, and all actual bank-action categories retain their API checks.
The checked binding reader rejects extra API claims or changed local meanings.
Missing/true `record_notes` keeps actual note API validation mandatory.

This permits local refusal/paid dispositions when `add_note` is not a permitted
operation. Finalization emits no bank note, and closing maps those dispositions
to the separate approved closing vocabulary. A later policy-permitted human
handoff may discard finalized local-only dispositions and retains any successful
promises. Once actual bank-note recording has begun, its phase remains sealed
against transfer; this local-only exception cannot undo or conceal bank notes.

### Renamed operation IDs

`tool_aliases` maps canonical semantic roles (`verify_identity`, `get_account`,
`create_promise`, `add_note`, `schedule_callback`, `transfer_to_agent`,
`close_call`) to exact actual operation IDs in this run's permitted API. Each
role must resolve uniquely; duplicate targets and collisions with unchanged
canonical operation IDs are rejected. A renamed operation without its explicit
frozen alias fails rather than being guessed from URL, field names or prose.

`action_fields` and `result_fields` remain keyed by canonical role. The compiler
keeps the actual API operation ID, URL, request/result schemas and security
configuration intact. Binding categories point to those actual IDs and exact
bound fields. Protected counters/state use stable semantic roles, so operation
renaming never changes identity order or per-product budgets.

`nonpersistent_failures` uses actual permitted API operation IDs, not guessed
canonical names. The compiler resolves guarantees through the same frozen
injective alias map before rendering kernel code. A guarantee attached to a
nonexistent or nonpermitted operation fails compilation. The account catalogue
also retains a detached exact `product_schema` for later approved dialogue-fact
projection; that schema does not itself authorize disclosure.

`nonpersistent_failures` must describe a supplied API guarantee, with provenance
reviewed alongside API documentation. It is not a model-set runtime permission
flag. Only matching immutable engine HTTP status/result and an actual negative
`ok` value permit retry of the exact body. Explicit nonpersistent business
rejection at HTTP200 is supported; missing `ok` or positive evidence never
qualifies merely because a status matches. `promise_failure_limit` bounds
attempts independently per product.
Exhaustion stages the typed failure disposition; a successful earlier promise
remains evidence for the final outcome. Missing/ambiguous persistence, ordinary
unqualified failures and malformed positive evidence quarantine the call. A
quarantined action cannot be retried or closed with a fabricated success.

The compiler's exact post-hook copies the documented `api_call` envelope's
actual `status_code` into a reserved data field, then a node-level `data.*`
mapping writes its protected variable. The hook leaves `ok` and error values
untouched and synthesizes no status. It removes an existing body field at that
reserved key before copying, so body data cannot impersonate envelope evidence.
This avoids unverified assumptions about automatic API pre-action keys being
named after the operation or node. The pinned kit documents the envelope and
post-hook-before-mapping order in `docs/tools-and-code.md`.

## Current limits and next acceptance gates

Local tests execute the identical embedded kernel, not a second implementation
of its behavior. They also tamper with executable code, bodies, mappings,
routing and protected writers to verify exact recompilation rejects changes.
These establish compiler/state invariants only.

The controller must integrate this plan transport and retain the frozen context
through repair/publication. T06's profile selector must include all needed guard
kinds; descriptive rules remain unsupported. Persuasion counts, hardship intent,
private-information disclosure, account narration, relative-date speech and
identity-unreached interpretation still need controlled dialogue logic and
hosted tests. The compiler does not claim they are enforced by routing alone.

Before hosted execution, test raw-body transport, mapped object persistence,
envelope-status transport, fresh confirmation extraction and exact
published code preservation. The organizer kit does not define hook exception
suppression, so no permission gate relies on an exception. The only pre-hook
transports validated JSON into `config['body']`; URL/method/headers are fixed.
Unknown initial engine variables route to a silent quarantine terminal without
fabricating a bank close. Policy may require a safe uncertain-call close that
this conservative v1 cannot establish; it must remain an explicit failure.

No Freya, model or external calls are part of compilation or local guard tests.

An explicit empty account_disclosure_fields list grants no narration fields,
just like an absent declaration. Internal proposal references remain separate.

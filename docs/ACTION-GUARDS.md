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
seals that phase against subsequent promises, callbacks or transfers. With
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

`action_fields`, `result_fields` and `product_fields` active rule dictionaries
can explicitly bind renamed API fields. Canonical names are accepted only when
they exist exactly in the supplied schema; ambiguous/missing fields fail.
Opaque wire vocabularies use T06's API-validated detached category bindings.

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

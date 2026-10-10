# Deterministic policy helpers

These pure helpers check proposed bank actions and update detached call state.
They do not interpret a customer's speech, prove that generated workflow code
uses them, authorize tool execution, or demonstrate Freya runtime behavior.
T08 must own the state, normalize verified API evidence, and execute its guards
before each bank request. The kit's fixed phone-agent model is unchanged.

## Per-run action bindings

`builder.action_bindings.compile_action_bindings(policy, openapi)` compiles a
plain JSON descriptor from the active policy rules and supplied OpenAPI. Local
JSON references are resolved without network retrieval. The descriptor contains
`version`, `policy_sha256`, `openapi_sha256`, `binding_mode`, `categories`, and
`sha256`. Each bound category contains:

| Property | Meaning |
|---|---|
| `semantic_to_wire` | Explicit semantic concept to API value |
| `wire_to_semantic` | Its unique inverse |
| `operation` | Exact normalized operation `name`, `method`, and `url` |
| `field` | Selected request field |
| `api_enum` | Enum declared by the supplied API |

The recognized categories are `outcomes` (`close_call.outcome`), `reasons`
(`close_call.reason`), `notes` (`add_note.reason`), and `queues`
(`transfer_to_agent.queue`). Semantic concepts are the known canonical names
in `builder/action_bindings.py`. Unknown concepts require additional deliberate
implementation; their meaning is never inferred from an opaque code.

Active `wire_codes` supplies category maps. Opaque values need explicit,
injective mappings covering the applicable `close_reasons`, `note_reasons`, and
`transfer_reasons` vocabulary. Each mapped value must occur in the selected API
field's enum. Canonical enum values may use identity mapping only when their
policy vocabulary matches the known canonical concepts unambiguously. A known
canonical value cannot be assigned a different canonical meaning. Transfer
closing reasons must also be allowed by the mapped transferred outcome.

Exact canonical request field names are recognized. A renamed field may use
the exact schema description `Semantic field: <canonical field>`; this is a
declared marker, not free prose interpretation. Ambiguous markers fail closed.
Active `action_fields` can select a field explicitly, for example an entry
under `close_call` mapping semantic `outcome` to the supplied request field.
Active `tool_aliases` maps semantic action names to supplied operation IDs.
Allowed actions remain constrained by `policy.allowed_actions` when present.

Pass the compiled descriptor as keyword `bindings=` to `stage_note`,
`stage_transfer`, `choose_close`, `validate_close`, and `record_close_result`.
Omitting it permits canonical identity vocabularies only; opaque or conflicting
values fail closed. `checked_action_bindings` rejects changed descriptors and
policy mismatches. Hashes detect changes; they do not authenticate a caller or
make model-controlled state trustworthy.

`encode_action_value` and `decode_action_value` expose category lookups. State
and closing proposals retain exact wire values. Closing selection decodes a
note's meaning and then encodes the closing-reason value separately, so a note
and a closing reason need not share a code. Unknown category maps such as bank
job selectors may remain typed data in `wire_codes`; these helpers do not turn
them into executable decisions.

## Typed execution profile

`builder.policy.compile_execution_policy(policy, bindings=None,
required_kinds=())` returns plain JSON containing `version`, `policy_sha256`,
`profile`, optional `bindings_sha256`, and `sha256`. It resolves active overrides,
deep-copies typed recognized values, and rejects descriptive strings where a
recognized kind requires structured data. It does not interpret unrelated
extension prose. Missing requested kinds and unsupported requested kinds fail
closed. Validate the policy IR, source coverage, and source hashes before using
this compiler; profile compilation alone does not establish provenance.

| Kind | Required value shape |
|---|---|
| `identity_fields` | Ordered unique nonempty string list |
| `identity_wrong_limit` | Positive integer |
| `identity_hint_exemptions` | Unique string list |
| `holidays` | ISO date string list |
| `business_weekdays` | Nonempty weekday integer list, Monday = 0 |
| `band_ranges` | Nonoverlapping `{name, min, max}` integer ranges |
| `deadline_days` | Band to nonnegative integer business-day count |
| `floor_fields` | Product type to account-response field |
| `persuasion_limits` | Band to nonnegative integer action limit |
| `callback_window` | Local `start`/`end` HH:MM, boolean `allow_today`, nonnegative `next_business_days` |
| `require_confirmation` | Boolean |
| `confirmation_scope` | `each` or `batch` |
| `promise_failure_limit`, `note_failure_limit`, `callback_failure_limit`, `transfer_failure_limit` | Positive integer |
| `write_retryable_errors` | Unique string list; caller must validate API no-persistence meaning |
| `note_reasons` | Unique permitted wire-code list |
| `transfer_reasons` | Wire queue to wire closing reason |
| `close_reasons` | Wire outcome to permitted wire reasons list |
| `wire_codes` | Category to unique semantic-name/wire-code map |
| `tool_aliases` | Semantic action to operation ID |
| `action_fields`, `result_fields`, `product_fields` | Explicit field maps |
| `nonpersistent_failures` | Operation to unique `{http_status: integer, error?: string}` pairs |
| `confirmation_actions` | Unique semantic action list |
| `product_order` | `{field, direction: ascending/descending, tie_breaker: account_order}` |
| `close_precedence` | Unique semantic outcome list |
| `identity_close` | `failed`, `incomplete`, `unreached` proposals with `outcome` and optional `reason` |
| `promise_failure_disposition` | `{reason: string, clear_pending: boolean}` |

When bands and deadline/persuasion maps are supplied together, the map keys must
cover exactly the declared bands. This profile supplies data for a later guard
compiler. Optional fields do not imply that every helper already implements
the corresponding guard. In particular, request/response aliases beyond bound
enum fields, confirmation classification, and additional action failure limits
remain controller integration work. Local amount helpers use `Decimal`; the
platform's verified code-node modules are `json`, `re`, and `datetime`, so this
Python module must not be copied blindly into a Freya code node.

The additional guard inputs from `nonpersistent_failures` through
`promise_failure_disposition` are validated during execution-profile compilation.
General IR validation still treats them as extension data for compatibility;
compilation rejects prose values for them. Their semantic action/outcome labels,
operation names, aliases, and response predicates need API/binding validation
by T08. A typed HTTP-status/error declaration is not evidence of no persistence:
only the actual immutable response and controller-owned HTTP envelope can
establish a match. The current pure helpers do not execute the configured sort,
precedence, identity-close selection, or failure disposition automatically.

## State and tool evidence

Every successful helper transition returns a deep copy; rejected transitions
leave the caller's state unchanged. `products` must be the returned account
products, with unique stable `product_id` values. Call time comes from supplied
`now`, not the host clock. Identity and write helpers consume normalized result
objects with `ok` or a nested `data` object. Boolean evidence accepts actual
booleans and the explicit strings `true`, `True`, `false`, and `False`.

`identity_record` retains previously verified factors, enforces their supplied
order, and tracks a shared wrong-answer counter. A malformed `ok`, missing or
malformed `verified` on a successful response, malformed remaining-attempt
counter, or malformed existing state raises `PolicyError` without mutation.
Unreadable/rejected verification does not consume a local wrong answer. An
explicitly verified response advances the factor; an explicitly wrong response
consumes an answer except for a declared hint exemption. The canonical
`day_month_swapped` service hint remains the backwards-compatible exemption
when no explicit exemption list is supplied. A valid service remaining counter
can increase the locally known wrong count and cannot undo previous failures.

Promises are staged in `pending_promises[product_id]` using integer cents and an
ISO date. Changing a proposal resets confirmation. `confirm_commitments` is an
application assertion that explicit consent was obtained; it cannot establish
consent from speech. `assert_commitment_attempt_allowed(state, product_id,
policy)` must run **before** a bank request: it requires a confirmed pending
proposal, the supplied positive `promise_failure_limit`, and no duplicate,
recorded note, active transfer, exhausted failures, or uncertain persistence.

`record_commitment_result(..., policy=policy)` distinguishes positive evidence,
explicit rejection, and uncertain persistence. Explicit rejection increments
`promise_failures[product_id]` within its supplied limit. A missing/malformed
`ok`, transport failure, or unusable result sets
`promise_status[product_id] = unknown_persistence`, without inventing a definite
failure or claiming success. No retry, new promise, or note for that product is
allowed until external reconciliation. The controller must not normalize a
transport error into `ok=false`; definite rejection means the supplied service
contract establishes that the write did not persist. No idempotency parameter
is invented. The legacy signature without policy accepts only an initial
positive result with no prior failure/quarantine, and cannot authorize retries.

`record_persuasion_attempt` counts application-classified offers independently
by stable product ID and supplied band limit. It does not decide whether an
utterance is a refusal, an offer, or a customer-proposed commitment.

Notes stay staged until an appropriate ending is selected. Their successful
results are recorded separately; an uncertain note also quarantines the product
against promises, replacement notes, and transfer. A transfer clears staged
notes and all unwritten promises, preserves recorded promises, and blocks later
notes/promises. A recorded note precludes a later transfer. A transfer may be
used for external reconciliation of an uncertain promise, without asserting
that promise succeeded. Transfer result recording is single-use and cannot
replace earlier queue evidence.

`choose_close` selects transfer success, a recorded promise, all-paid notes, or
the first nonpaid note in account order. Unsupported precedence rules require
explicit future implementation; profile data does not change this helper's
algorithm automatically. `record_close_result` requires successful matching
promise/transfer evidence for those semantic outcomes, including opaque codes.
Every attempted close sets `close_attempted`; only positive tool evidence sets
`closed` and records the persisted close. Rejected, uncertain, and malformed
close results are terminal too. `assert_action_allowed` and subsequent staging
or recording helpers reject later bank actions and duplicate close attempts.
These are pure state invariants; T08 must make the real close request the last
tool call and ensure results belong to the same actual request and call.

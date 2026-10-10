# Source-backed write attempt scopes

The optional frozen `attempt_scopes` rule maps sealed write action roles to
`call`, `product`, or `exact_request`. Product scope is limited to
`commit_promises` and `finalize_notes`. A selected scoped role requires its
explicit positive `promise_failure_limit`, `note_failure_limit`,
`callback_failure_limit`, or `transfer_failure_limit`. These become actual
request dispatch limits when the scope is supplied; proposal, read-back,
correction and declined confirmation do not consume them. Closing remains
at most once regardless of its optional scope. No scope or nonpromise budget
is inferred when absent.

The protected `attempt_counts` dictionary records the selected action and
scope key. Call scope survives changed dates/terms. Product scope uses only
trusted account product IDs. Exact-request scope binds the complete actual
wire body and semantic operation role, including renamed fields and codes.
The dispatch check runs again after confirmation, before the API request.

`definite_failure_resume` is an optional unique array of
`finalize_notes`, `schedule_callback`, and/or `commit_transfer`. For selected
roles it requires an explicit scope and positive budget. Permission is
operative only for an exact negative result matched to source-backed API
nonpersistence evidence at the correct operation and HTTP status. Below the
cap, the same frozen request and existing consent are retried. At the cap,
its pending request and consent are cleared without recording success, and
normal policy handling can continue. An unwritten transfer request can be
cleared; earlier recorded promises, notes and callbacks are retained.

This continuation permission grants no bank closing reason or product
disposition. All remaining resolution checks still apply. Without permission,
without qualified nonpersistence, or with malformed/contradictory evidence,
the call remains quarantined. Closing is never retried. Existing promise
failure/disposition behavior is unchanged.

Across every operation, a positive result flag with any nonempty or malformed
semantic error is contradictory evidence. It cannot record identity,
accounts, promises, notes, transfers, callbacks or a successful close. Its
pending request and earlier successful records are preserved, all further
writes stop, and a close attempt still takes its terminal route.

Local evidence is in `tests/test_guard_attempt_scopes.py`: actual compiled
node code and API response mappings exercise source-frozen scopes, alias and
opaque-code binding, consent, changed-slot bypass attempts, qualified and
unqualified failures, retry caps, partial notes/transfers/promises, malformed
counters and contradictory receipts. These checks establish local guards;
they do not establish generated dialogue, disclosure wording or hosted
conversation acceptance.

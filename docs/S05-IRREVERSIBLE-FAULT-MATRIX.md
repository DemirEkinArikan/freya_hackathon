# S05 irreversible-action fault matrix

Local, synthetic evidence for T08 (issue #9), T09 and T13. It is not hosted,
bank, generated-candidate or phone-speech evidence.

The matrix files were written by the Codex S05 lane and carried onto main
through the recorded handoff on issue #9. The first commit holds the original
bytes. Claude S05 review fixes are in a separate commit (see "Review fixes").

## Run

```text
python3.11 -m unittest discover -s tests -p 'test_s05_*.py' -v
python3.11 tools/evaluate_s05_matrix.py
```

The evaluator writes `artifacts/s05-fault-matrix/<run>/` (git-ignored). That
folder holds the frozen packets, the compiled contexts and bundles, the
original results and the source sha256 fingerprints. It exits 1 while any
violation is open, including the known one below.

## Independence

- Packets come from the accepted invented fixtures (`test_guards.fixture`,
  `test_guard_aliases.renamed_context`, the T08 attempt-scope and requirement
  packets). Cap rules are added as literal source rules.
- Expected bank calls, ledgers and attempt records are written in the tests.
  Wire names come from the source profile (`tool_aliases`, `action_fields`,
  `result_fields`). Retries come from `promise_failure_limit` and
  `nonpersistent_failures`. Caps come from `attempt_scopes` and the failure
  limits. None of it is read from guard state or compiled helpers.
- The actual compiled node code, request/response hooks and string result
  mapping run, through `tools.replay_guarded.replay` or direct node execution.
  No model, network, bank, simulation or lease is used.
- Every case runs the canonical contract and the opaque renamed contract
  (renamed operations, fields, enums and result names).
- Faults are LOCAL injections. They are not documented organizer behavior.

## Coverage

| Fault | Test |
| --- | --- |
| First write recorded, second receipt missing | `test_s05_receipt_matrix` first-known-second-unknown |
| Positive flag plus error (200 and 503) | `test_s05_receipt_matrix` positive-plus-error |
| Blank record ID, malformed `ok` type, malformed HTTP status | `test_s05_receipt_matrix` malformed cases |
| Non-string record ID (91, true, {}, []) | `test_s05_receipt_matrix` non-string record ID (V1, now handled) |
| Qualified 503 negative: one exact retry, then record | `test_s05_receipt_matrix` qualified-negative |
| Qualified negative that carries a record ID | `test_s05_receipt_matrix` negative-with-record |
| Callback call cap, retry cap and exact-request scope across changed slots | `test_s05_dispatch_caps` |
| Note and promise call/product caps; unsent product never counted | `test_s05_dispatch_caps` |
| Staged notes and promises discarded by either transfer | `test_s05_control_matrix` |
| Declined commitment becomes a note, no dispatch | `test_s05_control_matrix` |
| Recorded promise survives a later correction and transfer (full replay) | `test_s05_control_matrix` |
| Absent or forged confirmation | `test_s05_control_matrix` |
| Administrative budget exhausted before acceptance, after success and after a qualified failure | `test_s05_control_matrix` |
| Malformed private attempt counter | `test_s05_control_matrix` |
| Duplicate close after every receipt kind | `test_s05_control_matrix` |

`tests/test_s05_claude_crosscheck.py` (PR #124) adds retry exhaustion,
cross-spelled negatives, cross-contract result fields and a missing receipt
mid-batch with no phantom attempt for the unsent product.

## Known violation V1

A positive `create_promise` receipt whose `promise_id` is not a string (91,
true, {} or []) is recorded as a persisted promise, and the call goes on to
prepare the close. This happens in both contracts. The API declares `promise_id` a
string, so these receipts cannot prove persistence. The expected result is
unknown persistence with the request kept in flight, as for a blank ID.

Cause: the compiled API node maps results to string scalars (the kit's string
result binding), so the kernel's `isinstance(promise_id, str)` check in
`guard_result` sees "91", "true", "{}" or "[]". The compiled
`post_process_code` hook sees the typed envelope, but it only transports the
HTTP status. Both live in reserved guard code (`builder/guards.py`), so this
lane only reported the defect.

Status: handled by the result-type evidence change
(`rapid/gap-receipt-type-20261011-claude`). The hook now also records each
trusted result field's raw JSON type. The kernel treats a type that the API
does not declare as unknown persistence. The case runs as
`test_non_string_record_identity_is_unknown_persistence`, with the same
expectation and no `expectedFailure` marker.

## Review fixes (Claude S05)

1. The receipt matrix keyed attempts by wire alias. The controller ledger and
   the other two matrices use the semantic source role plus the canonical wire
   body. Every opaque case failed on that key alone, although calls and
   ledgers matched. The receipt matrix now uses the same role-based key.
2. `test_s05_control_matrix.py` imported `compile_policy_contract` after
   `unittest.main()`, so running the file directly raised NameError in the
   scoped canonical cases. The import now sits at the top.
3. Non-string record IDs moved into the separate known-violation test above.
   Blank IDs (None, "", " ") still pass in the original test.

## Limits

- The replay mapping stores strings, so "503" and 503, or "91" and 91, cannot
  be told apart after mapping.
- Policy packets are invented synthetic fixtures, and the extractions are
  ideal. Generated-candidate, hosted and phone evidence are separate claims.

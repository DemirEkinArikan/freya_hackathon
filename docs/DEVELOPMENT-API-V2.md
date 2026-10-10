# Authored development API version 2

The previous development OpenAPI declared response properties only for account
lookup. The other six operations could not bind trusted result fields, so those
exported inputs were not executable builder trials. This revision explicitly
authors complete synthetic request/result contracts; it is not a description
of the organizer's hosted bank implementation.

Every development operation now declares JSON response schemas. Canonical
profiles use boolean `ok` and canonical result names. Raw profiles use exact
`"true"` / `"false"` strings and the following source-quoted field bindings:

| Semantic result | Raw wire field |
| --- | --- |
| ok | accepted |
| verified | identity_match |
| wrong_answers_left | attempts_remaining |
| hint | guidance |
| error | failure_code |
| products | accounts |
| promise_id | record_ref |

Raw profiles additionally declare local HTTP 503 negative responses, and HTTP
201 successful promise recording. Original HTTP 200 meanings remain: an exact
false result with a declared error is definitely nonpersistent; successful
writes persist. A successful identity operation can still return a factor
mismatch. A true write without its required evidence never establishes success.
These alternatives are authored local protocol contracts, not hosted support
claims. All targets remain `synthetic-bank.invalid` and deployment is refused.

Only development protocol metadata changed: response schemas/API version,
request/result binding tables and their expected contract/source fingerprints.
The 60 development cases retain their exact facts, scripts, customer decisions,
ledger oracles and fault counts. The 60 unauthored held-out reservations remain
unchanged. Identity answers, calendars, amounts, outcome codes, goals and request
schemas are unchanged. The declared v2 configuration digest includes the new
response mappings. Existing frozen-manifest and strict migration checks remain
in place; this is a deliberate reviewed fixture revision, not an automatic
permission to alter a frozen or evaluated dataset.

`benchmarks.export_inputs` validates every operation before creating files:
populated explicit request/result schemas, field types/enums, required fields,
trusted result roles and exact path-parameter bindings. It exports the original
approved policy documents and OpenAPI only, recording the actual API digest.
Existing destinations are rejected before partial writes. No customer scripts,
answers, expected records, helper outputs or fault injections enter the export.

Independent response builders live only in
`tests/development_protocol_samples.py`. `protocol_response` accepts a semantic
operation, `raw`, `ok`, HTTP `status`, remaining identity budget and evaluator
products; `decode_protocol_response` normalizes the two wire representations.
They use invented results and do not read case oracles or production schema
builders. The protocol tests validate those samples against every authored
response alternative and check equivalent original success/failure meanings.
There is no existing offline bank API simulator: these are local protocol
doubles, contract/helper checks and independent oracle fixtures.

Export fresh, separate directories after the checked revision is merged:

```sh
python3.11 -m benchmarks.export_inputs --profile train-1 --out artifacts/development-inputs-v2/train-1
python3.11 -m benchmarks.export_inputs --profile train-2 --out artifacts/development-inputs-v2/train-2
python3.11 -m benchmarks.export_inputs --profile validation-1 --out artifacts/development-inputs-v2/validation-1
python3.11 -m benchmarks.export_inputs --profile validation-2 --out artifacts/development-inputs-v2/validation-2
```

Use returned brief paths in their exact order. Train1 is clean commitment
resolution; train2 is raw exception triage; validation1 is raw contact/callback;
validation2 is clean commitment resolution with opaque codes. Every profile also
declares the permitted goal selector. Preserve all failed old runs. Fresh real
adaptive generation still requires the coordinated T05 semantic patch, mandatory
tool-free audit, original input-only prompts and the 1800/300 timing bounds.
Passing this protocol revision does not establish generated-workflow, dialogue,
holdout or hosted acceptance.

Known original development-policy limitation remains visible: promise retry is
bounded, but the source does not explicitly choose its exhaustion disposition.
Some evaluator oracles expect service error. That expectation is not inserted
into model inputs or silently treated as a supplied rule; any future policy
revision needs separately authored source and explicit evidence.

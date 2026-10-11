# Restricted catalog evidence follows the production emitter

The controller prepares and records `generation_model_catalog` immediately
before each substantive `model_call`. Final packaging requires exactly one
matching catalog after the preceding model call and passing isolation audit,
and before that substantive call. Missing, duplicate, reused, mismatched or
post-call catalogs cannot establish the call's restrictions.

The packager previously associated catalogs with the interval after each call.
That rejected the original published public-A run
`20261010T234440089175Z`, whose two catalog/call pairs occur at trace indices
7/8 and 32/33. The original trace, source and generated artifacts remain
unchanged. Correcting this reader does not make that older source a current
candidate, prove conversation behavior, or complete T17/T18.

Dedicated regressions exercise the actual runtime emitter with an in-memory
subprocess result and fabricated package evidence. They make no model, network,
platform, simulation or holdout calls. The fixture chronology now matches the
production protocol; original source/config/audit/model/hash checks remain.

# Invocation-time source provenance

The controller records `builder-source.json` after input validation and before
constructing the generation runtime, auditing isolation or calling the model.
It hashes the actual `builder/*.py`, `builder/prompts/*.txt` and top-level
`benchmarks/*.py` bytes, together with resolved model, reasoning, Python,
deadline, export reserve and repair settings. Dependency injection is explicit.
Credentials, fixture/holdout directories, private notes, caches and sibling
repositories are outside the scan.

A unique `builder_source_snapshot` trace event carries the source fingerprint,
original record fingerprint and resolved configuration. Every successful mode
retains those fingerprints in its manifest. Before success, the controller
checks that the recorded runtime source inventory remains unchanged. Source
verification and final proof writes remain within the global deadline; drift
or an overrun produces a failed manifest and retains the original evidence.

`build-provenance.json` binds the original source snapshot to the exact final
manifest and complete trace. Evaluation must corroborate that record with the
original pre-generation trace event and manifest, rather than hashing a later
checkout to fill an old run's missing provenance. Historical runs without this
event remain historical and cannot gain recorded provenance retrospectively.

These are consistency checks on local evidence. They do not authenticate a
fabricated file, lock source against transient changes or include the Codex
binary and every host dependency. The isolation audit separately records the
installed model catalog and actual runtime observations. Source proof alone
does not establish a correct conversation or submission readiness.

# Source-backed initial call inputs

An optional typed `call_variables` rule declares exact variable names provided
at call start. Its value is an array of unique ordinary names; it contains no
customer values or defaults. Existing nonempty name-to-description mappings
are also supported; only their names grant reads, never descriptions as values.
Empty or nontext descriptions and controller credential namespaces are refused.
Each name must occur literally in an active source
quotation. The controller checks source bytes and fingerprints, explicit
amendments, declaration shape and retained frozen provenance before generation
can freeze the contract. Briefs without this rule keep existing behavior.

The compiled workflow receives no added metadata, example values or
`test_variables`. Only a validator with the controller's exact frozen guard
context accepts the declared template reads. A policy supplied alone does not
grant that permission. Names can change between briefs without source edits.

Call inputs remain read-only. Authored extraction cannot replace them, and
guard actions cannot treat them as untrusted proposal variables. Templates
refer to one declared variable exactly; undeclared or nested input references
do not acquire permission. Existing protections for `customer_id`, `now`,
guard state and automatic bank-result variables remain in force.

Literal source/name matching establishes provenance, not semantic entailment:
independent review must still confirm that the source describes initial inputs
and that the generated conversation uses them correctly. Runtime availability
comes from the supplied call context and remains a hosted verification concern.
Dedicated regressions use unrelated invented names, changed input declarations,
explicit amendments, forgery attempts and read/write boundary checks without
model, platform, bank, simulation or held-out data.

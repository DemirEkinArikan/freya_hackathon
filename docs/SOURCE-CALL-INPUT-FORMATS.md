# Source-declared initial input formats

The optional `call_input_formats` rule maps source-declared read-only call-input
names to `offset_datetime`, the only supported format. Every mapped name must
occur literally in its own cited quotation and belong to the frozen
`call_variables` rule. API field formats, variable names and descriptions alone
grant no format rule or default value.

`offset_datetime` requires a raw string containing an extended `YYYY-MM-DD`
calendar date, a `T`, `t` or space separator, `HH:MM`, optional seconds and their
fraction, and explicit `Z` or signed `HH:MM` UTC offset. Calendar, clock and
offset bounds are validated. A date alone, offset-less datetime, malformed
offset or non-string fails before any bank operation. The initial raw string is
retained in private sealed `call_inputs`; its local date and offset are not
converted or replaced. Existing engine date/clock handling remains operative.

Every protected phase validates the sealed snapshot. Later ordinary variables
cannot repair an invalid initial value or relax the format. Corrupted snapshots
quarantine without discarding already recorded writes or unresolved transaction
evidence. Absence of this optional rule preserves legacy handling. This feature
adds no action, disclosure, outcome, timestamp default or calendar permission.

The dedicated tests author independent source rules and execute exact compiled
nodes with mock response envelopes. They cover legacy absence, generic input
names, local date/offset preservation, invalid initial values, read-only rules,
literal quotation checks, ordinary-variable overrides, later corruption and
continued sandbox authentication. They do not invoke models, Freya or held-out
data. Fresh adaptation and hosted evidence remain separate acceptance work.

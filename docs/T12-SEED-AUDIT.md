# Hosted fixture seed and oracle audit

`benchmarks.seed_audit` independently compares hosted evaluator fixtures with
the pinned organizer `public-test-set.json`. Production callers use its default
catalog, never a fixture's self-declared oracle or candidate output:

```python
from benchmarks.seed_audit import validate_seed_rows

validate_seed_rows(rows)  # raises SeedAuditError before hosted mutation
```

`audit_seed_rows(rows)` returns at most 64 value-free violations instead of
raising. Each identifies a row index, fixed field label and failure category.
It retains no identity answers, caller text, bank values, fixture identifiers
or arbitrary field names. The optional `seeds=` argument is an offline test
seam; do not substitute a model/manifest's own expected values in production.

Materialized fixtures preserve every official field except representation and
caller wording (`id`, `title`, `style`, `description`, `script`, `persona_prompt`,
`persona_wording`). The case ID must match its unique fixture ID. Identity
answers, products, call variables, tool fault controls, family, level and brief
remain unchanged. Missing/extra immutable fields fail. Every outcome, reason,
promise, note, callback and transfer must exactly match the official ledger;
extra/missing ledger fields and duplicate commitments fail. Official judge
checks remain an exact ordered prefix; supplemental security checks may be
appended.

Compound attacks additionally carry explicit immutable outcome constraints:

```python
from benchmarks.seed_audit import seed_outcome_constraints

row['outcome_constraints'] = seed_outcome_constraints(official_seed)
```

The result is `{preserve_seed_outcomes: true, seed_expected_sha256: ...}` using
`builder.safety.digest` on the official expected ledger. The auditor computes
that binding again from the pinned seed, so a changed oracle cannot authorize
itself by updating its own digest. This metadata constrains evaluator facts;
it does not establish the meaning of arbitrary caller speech or judge prompts.
Review compound wording to ensure attacks remain customer speech and preserve
the scripted final decisions. Persona variability and real phone-agent
policy/honesty still require conversation evidence.

Reserved holdouts with no authored `case` remain exempt from case/oracle
comparison, but must select a known seed, matching brief and the holdout split.
Any supplied reservation seed must match the bank seed. Once a case exists it
is audited regardless of its status label, including `materialized_after_freeze`.
Materializing compound holdouts must also preserve their outcome constraints.

`benchmarks.freeze.generate_holdout` attaches the same official-oracle binding
when it materializes a compound holdout; its supplemental security check leaves
the official checks intact.

This module performs local file reads and comparison only. No Codex, Freya,
bank service, persona creation or simulation call is made. Its eventual runner
integration is a separate change owned by T11/T12; standalone checks alone do
not complete hosted acceptance.

```sh
python3.11 -m unittest discover -s tests -p 'test_seed_audit.py' -v
python3.11 -m unittest discover -s tests -v
```

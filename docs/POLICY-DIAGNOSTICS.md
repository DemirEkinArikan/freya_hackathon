# Typed policy diagnostics

Execution-profile validation reports the recognized interface's required value
shape and the received JSON type. It preserves every existing typed acceptance
predicate. The message includes fixed field/scope labels rather than policy
values, source quotations, API credentials, customer identifiers or unexpected
input key names.

`identity_close.value` must contain exactly `failed`, `incomplete` and
`unreached`. Each scope contains a nonempty string `outcome` and an optional
nonempty string `reason`, with no additional fields. A missing scope is named
explicitly; malformed nested proposals identify the fixed field path.
`identity_close_choices` supplies additional scoped options and does not replace
these fixed entries. All values still need authoritative input provenance;
shape guidance grants no permission to invent a fallback disposition.

This is the current executable interface, not an additional bank requirement.
The guard compiler checks each choice against its fixed scope outcome, and the
embedded closing kernel reads the fixed `unreached` outcome before selecting
a scope. Supporting a fixed failed record plus choice-only other scopes needs
a coordinated consumer change, including unambiguous scope-outcome resolution
and complete coverage. Relaxing profile validation alone would leave these
consumer paths invalid. An active run retains its original source snapshot.

Diagnostics describe representation failures, not semantic correctness. Literal
source matching and accepted shape alone cannot prove that a model extracted the
right rule or that a caller's speech satisfies its conditions. Missing semantic
support must remain unsupported rather than becoming a guessed typed value.

No source, prompt, rejected policy or generated artifact in an active builder run
is changed by this diagnostic followup. Future runs use the checked source; an
existing run retains its original validation messages and evidence.

Verification on the isolated branch: 57 focused policy checks and all 620
Python 3.11 checks passed. Eight independent diagnostic regressions cover
missing scopes, nested field failures, unchanged valid shapes, omitted input
values/unknown key names and bounded message length. An independent read-only
review found no acceptance-predicate change; `git diff --check` passed.

```sh
python3.11 -m unittest discover -s tests -p 'test_policy*.py' -v
git diff --check
```

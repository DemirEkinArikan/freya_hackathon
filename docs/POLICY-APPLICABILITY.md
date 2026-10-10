# Source-backed product applicability

`policy_applicability` is a typed, quoted rule with exactly
`{"products":"single"}` or `{"products":"multiple"}`. It uses the ordinary
source inventory, explicit override graph and frozen contract/profile checks.
Product scope is never inferred from a filename, goal, product type, omitted
rule or one observed account response.

A source-backed single-product policy may omit `product_order` and
`resolve_all_products`. No ordering rule is manufactured: the kernel accepts
only the singleton, or an empty pre-account state. More than one returned
product violates its scope. The compiled result projection quarantines that
state before disclosure or another bank action. A missing resolution rule uses
the conservative kernel invariant that the sole product must be resolved;
it does not add a rule to the frozen policy or execution-policy artifact.
Explicitly supplied ordering and resolution constraints remain operative.

Policies with multiple products, or without typed scope, retain the explicit
ordering and resolution requirements. A new amendment must explicitly override
the previous applicability rule; document order alone grants no precedence.

Promise commits do not universally require `nonpersistent_failures` or
`promise_failure_disposition`. Without a supplied API guarantee, failures cannot
authorize retries. Without a supplied disposition, the existing kernel retains
uncertainty and quarantines rather than inventing a closing or note reason.
Both rules remain mandatory in the execution profile whenever active in the
frozen contract. Supplied constraints are never waived by applicability.
When promise commits are selected, an active promise-failure disposition also
requires its reason in the explicit `note_reasons` list, including when notes
are local and no `add_note` operation is permitted. Without that dependency the
exhaustion consumer cannot select the supplied consequence; compilation rejects
it instead of losing the rule. Callback/identity-only plans preserve unused
typed metadata without acquiring unrelated commitment dependencies.

The independently authored applicability regressions exercise the actual
compiled code, frozen profiles and source/amendment compilation. Existing
alias, calendar, identity and multi-product regressions test other changing
policy/schema dimensions. These are local checks, not generated-policy
completeness, model generalization or hosted conversation acceptance.

T05 owns extraction/completeness guidance and controller integration. Its
execution-requirements selection must apply the same singleton exemption
before freezing, while preserving every supplied constraint. Fresh generation
must use approved briefs and their API only, pass its mandatory tool-free
audit, and retain original policy/plan attempts under the 1800-second deadline
with export reserve. Do not repair generated policy manually or feed these
tests, evaluator fixtures or expected records to the generator.

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

An optional source-backed `product_count_bounds` rule retains explicit account
cardinality separately: a nonempty object with `min`, `max`, or both, containing
nonnegative integers. When both are supplied the minimum cannot exceed the
maximum. There is no default count and it introduces no ordering rule. The
maximum applies before ordering or projecting account data; the minimum applies
only after an account has loaded, preserving pre-account identity, callback and
transfer paths. An account-loading plan cannot combine single-product scope
with a minimum above one. Without an account consumer, those optional bounds
remain frozen metadata rather than force collection work. The intrinsic
single-product upper bound remains one even if a supplied maximum is looser.
The existing account parser still requires a nonempty product array. Zero is
accepted as bound data; this change does not add an empty-account closing path.

The original generated A+B policy preserved its 1–3 product requirement only in
descriptive `account_product_scope` metadata. The new typed interface enables a
fresh extraction to enforce that supplied bound. It neither rewrites that
original generated policy nor retroactively gives its old graph a new guard.

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

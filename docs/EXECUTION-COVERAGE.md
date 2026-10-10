# Source-bound execution coverage before freeze

`builder.execution_requirements` checks the selected sealed action interface
before workflow generation. API permissions say which bank operations are
available; they do not require every permitted operation to appear in a plan.
The selected roles instead come from an active, quoted policy rule whose kind
is `execution_actions` and whose value is a nonempty unique string array.

`guards.execution_action_catalogue()` returns detached metadata for the
supported action roles: semantic operation role, required public inputs,
optional inputs and required typed policy kinds. `policy.execution_rule_guidance()`
returns detached descriptions of accepted shapes. Neither accessor supplies
bank values or authorizes a write.

## Controller interface

`compile_execution_requirements(policy, sources, openapi)` returns detached JSON
or raises `ExecutionRequirementsError`. Its `errors` and `missing_kinds` are
sorted tuples. The compiler first validates the exact source inventory, literal
witnesses, precedence and supplied API through the strict policy contract. It
then aggregates missing typed dependencies with shape guidance, checks selected
roles against injective frozen operation aliases/permissions, compiles action
bindings and a typed execution profile, and invokes the public sealed compiler
on an inert technical inspection plan.

The inspection plan contains only interface roles and public variable names.
It is never executed, deployed, saved to Freya, or used as a customer workflow.
It has no customer ID, current-time value, amount, date, evaluator fixture or
seed input. This local check catches the current compiler's cross-rule and
API/schema requirements before spending time generating a real plan. It is
not evidence of dialogue quality, actual caller consent, termination or hosted
execution.

The descriptor hashes the exact policy, strict resolved contract/source
inventory, raw OpenAPI, trusted action catalogue, compiled bindings/profile and
inspection manifest. It retains selected actions and their rule IDs, resolved
operation bindings, required kinds and fixed shape guidance. Its semantic
completeness and runtime verification flags are false.

`validate_plan_execution(plan, descriptor, policy, sources, openapi)` returns
errors. It rederives the descriptor from actual inputs, rejects stale or
self-hashed substituted metadata, validates authored plan shape and rejects
both actions outside frozen `execution_actions` and omitted selected
capabilities. Presence does not require every customer to take every branch.
The ordinary sealed workflow
compiler/validator must run next; this check does not replace it.

## Required build integration

1. Include the trusted catalogue and shape guidance in policy extraction
   context, alongside approved briefs/API only. Require a sourced
   `execution_actions` rule and source-backed typed dependencies for its selected
   roles. This is an implementation capability declaration, not extra bank
   permission or a bank value.
2. Run `compile_execution_requirements` inside the extraction repair boundary,
   before freezing policy or calling the workflow generator. Return all errors
   together so an extraction repair can fill supported gaps or explicitly reject
   an unsupported goal. Do not synthesize values from this interface.
3. Freeze and persist the descriptor with the exact policy/contract. Include it
   unchanged in workflow and repair context. Every candidate plan must pass
   `validate_plan_execution` before sealed compilation. A plan repair cannot
   expand capabilities or rewrite frozen policy; a different capability set
   requires a new policy extraction run.

Alias keys are the catalogue's canonical `operation_role` values, which may
map to renamed supplied API operation IDs. They are not plan action names:
staging and commitment roles share their eventual operation role. An ambiguous
or incorrect alias is rejected; no mapping is guessed from similar wording.

Generic extraction instruction:

> Select the sealed implementation action roles required by the supplied goal
> in a source-backed `execution_actions` rule. Choose only applicable roles from
> the supplied interface catalogue; permitted APIs are availability, not a
> mandate to call them. Supply their typed dependencies using values justified
> by this run's approved sources. Preserve applicable amendments. Mark missing
> or unsupported semantics explicitly; never invent a bank default to satisfy
> a shape. Later plans may use only these frozen selected roles.

## Current fail-closed boundaries

- The present phone compiler requires every plan to contain a guarded
  `close_call`. Its current closing interface includes product ordering,
  all-product resolution and fixed identity closing scopes. This gate reports
  those actual requirements early. An active, source-backed
  `policy_applicability: {products: "single"}` waives only absent product ordering
  and all-product resolution kinds, matching the sealed compiler. Explicit
  active values remain required and checked; the coverage descriptor never
  inserts an ordering or resolution rule. Absent, superseded or multiple-product
  scope does not receive this waiver.
- Other selected subsets do not acquire promise money/band/floor rules merely
  because a promise API exists. Identity plus closing and callback plus closing
  are tested independently of promise dependencies.
- Local note/disposition roles are allowed without a bank `add_note` permission
  only when active typed `record_notes` is exactly false. Absent/true requires
  actual API permission and API-validated vocabulary. Local metadata never
  authorizes a bank write.
- Nonpersistent failure guarantees must obey the same actual permitted-operation
  scope as the current guard compiler. An unused guarantee naming a forbidden
  operation fails early; it never enables that operation.
- A selected `commit_promises` with an active `promise_failure_disposition`
  requires `note_reasons`, including local-only mode without note actions. The
  disposition reason must belong to that vocabulary after the same validated
  notes-then-reasons wire decoding used by the sealed kernel. Unknown values
  fail before freeze without echoing supplied codes. Neither nonpersistence
  guarantees nor failure dispositions are supplied by default: their absence
  remains conservative and does not authorize retries or invent recovery.
- Source quotes, a selected-role list and successful local compilation do not
  prove that the extracted policy captures every business requirement. They
  also do not interpret prose into executable values. Conditional conflict
  inspection remains unsupported for protected runtime execution.
- The dependency catalogue reflects the existing compiler contract. This
  module cannot prove that the catalogue exhausts every conversation or
  bookkeeping dependency; those need independent guard and hosted checks.

No model, Freya, bank or simulation call occurs in these functions. Controller
integration tests must prove missing coverage repairs before any workflow
generation, frozen descriptor preservation through repairs and action-subset
rejection. Live T05 acceptance still requires an actual bounded generated build
and its separate deployment/readback evidence.

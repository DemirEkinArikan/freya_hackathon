# Source-scoped product resolution

`resolve_all_products` retains its existing boolean behavior. When the supplied
source explicitly gives different closing prerequisites for different initial
call inputs, it can instead carry this typed form:

```json
{
  "selector": "source_declared_input_name",
  "by_value": {
    "supplied_code_one": true,
    "supplied_code_two": false
  }
}
```

The selector must occur literally as a complete name in that rule's source
quotation, and must also belong to the source-backed `call_variables` rule.
`by_value` has nonempty string keys and boolean values. There is no default or
inferred goal category. Keys normally use the actual supplied input codes. An
explicit frozen `wire_codes` category named exactly like the selector can map
source semantic names to actual codes; semantic/wire shadowing and collisions
are rejected. A differently named category such as `goals` has no selector
meaning.

Initialization takes a detached JSON snapshot of only declared initial inputs
in the protected state's `call_inputs`. Every protected phase validates that
snapshot and the selector. Missing, non-string or unknown selector values
quarantine before an API operation. The closing predicate reads that sealed
initial value; later ordinary variables cannot select a more permissive scope.
The existing read-only input checks still reject dialogue extraction or action
proposals that try to write those names.

A selected `true` value preserves the requirement to resolve all returned
products before closing. A selected `false` value removes only that particular
prerequisite. Identity checks, confirmed successful actions, allowed outcomes,
transfer precedence, unresolved writes, note finalization and the single final
close remain independently enforced. This interface does not invent an outcome
or allow notes. The existing boolean rule and single-product conservative
default are unchanged.

`tests/test_goal_scoped_resolution.py` authors its own synthetic instructions
and response envelopes outside generator inputs. Its compiled-code checks
cover successful partial and complete promise closing, legacy booleans,
unknown selectors, initial-input detachment, attempted ordinary-variable
overrides, protected-state corruption, literal source provenance, renamed
selectors, explicit code bindings, ambiguity rejection and sandbox boundaries.
These local checks do not establish fresh model adaptation or hosted
conversation acceptance for T07/T08.

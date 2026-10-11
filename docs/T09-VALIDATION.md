# T09 structural and semantic validation

`builder.validation` rejects malformed or oversized JSON before recursive
inspection and rejects candidate Python without executing it. Static checks
cover explicit bank API defaults, request field/schema/result mappings,
required declarations, bounded iteration hazards, unsafe callable aliases,
fallbacks, graph targets, unconditional routing, call-ending placement and
final bank-close paths. Node names and proof flags grant no trust.

Current compiler integration checks require passthrough defaults and final
unconditional targets to agree; that fallback cannot wait for a model tool call.
Python declarations cannot hide dunder iterator methods or replace the reserved
workflow `vars` mapping, including in exception fallbacks. Dedicated
`tests/test_validation_current_mutations.py` uses invented source-declared
clock formats, immediate routing and administrative budgets to check these
diagnostics and the trusted compiler boundary without executing candidates.

Engine-run required body templates need declared variables. Model-filled
conversation tool parameters remain allowed as documented by kit v3. A
binding declaration does not establish that every runtime path populates it.
API result paths must exist in supplied response schemas or be explicitly
added by a Python post-hook. That establishes shape only; a hook assignment
alone never establishes a result's authenticity. Protected action compilation
independently compares all hooks, mappings and bodies to the controller.

The enforcement boundary is `guard_context` supplied by the controller from
its frozen plan, source-bound contract, execution policy, OpenAPI and bindings.
`validate_workflow` recompiles those inputs with the current trusted guard
compiler and compares canonical content. A model assertion, rewritten manifest
or candidate-provided context must never replace these inputs. Failed
correspondence returns before any candidate source inspection or execution.

Dedicated `tests/test_validation_mutations.py` starts with an invented valid
compiler-produced candidate, deep-copies independent malicious mutations, and
blocks `exec` and `eval` throughout validation. It separately exercises
structural diagnostics without relying on canonical mismatch. Mutations cover
invalid routes, unresolved/malformed templates, schema/result mappings,
forbidden URLs/tools, protected references, direct unauthorized writes,
duplicate/final closes, forged proof flags, aliases, infinite iterators,
recursive callable references and broken exception defaults. Original
synthetic fixtures remain unchanged. No model, bank or simulation is called.

Passing static checks does **not** prove every Python program terminates or
fits sandbox resources. Arbitrary data-dependent loops, expensive arithmetic,
regular expressions and caller/model conversational paths still need platform
limits and hosted evidence. The portable fixed kernel is sealed by exact
recompilation; no arbitrary model-authored Python enters that boundary.

`assess_workflow` always reports `behavioral_compliance_proven: false` and lists
hosted evidence requirements: actual factor ordering/identity challenges,
current-term consent, truthful result claims, source-authorized financial
commitments/fee/interest claims, final closing, persisted platform variables
and conversation termination. Recompiling authored prose proves correspondence,
not semantic compliance. Evaluator records remain outside generator inputs.

Issue #10's own validator deliverable is complete in PR70. It explicitly reports
unproven dialogue as requiring hosted evidence; the task does not independently
require hosted trials or three repetitions. Closure currently waits for T07 and
T08 acceptance. After those dependencies complete, use the checked/pushed merged
validator head to record completion without adding another conversation gate.
Global hosted correctness and repetition requirements remain evaluation and
submission gates.

Local checks use Python 3.11, the focused validator/guard regressions, the full
suite, `tools/verify_kit.py`, `tools/package_submission.py --check` and
`git diff --check`. Evidence refers only to the exact recorded tree. This lane
uses no holdouts, generated-agent edits, purchase or simulation lease change.

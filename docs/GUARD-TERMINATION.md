# Sealed guard exits

The compiled workflow previously allowed a quarantined write result to return
to authored recovery dialogue. Its turn-limit close could then be blocked and
return to the same dialogue indefinitely. A verified account with unresolved
products could enter the same blocked-close loop without quarantine. Both
graphs passed structural checks because a terminal was reachable on another
path.

The controller now checks the complete protected-state field/type shape and
emits `bg_quarantined` from protected state after every
guard step and exception. Quarantine takes priority over authored success,
failure, recovery, confirmation and retry routing. It ends the phone call
without a close, silently unless the source requires an explanation (below),
retaining recorded successes and any uncertain in-flight request.
Action/result routes and fact meanings remain unchanged. No bank close or
retry is fabricated. Close-result nodes already have an unconditional silent
terminal and preserve that behavior.
An action preparation reached with an unresolved transaction also quarantines
and ends silently, preserving the exact transaction instead of mistaking it
for a correctable customer input.

An optional typed `uncertain_stop_explanation` rule supplies that
explanation. Its value is one of two things:

- the source's words, copied verbatim from the rule's own quotation, with no
  digits, braces, control characters or engine prefixes. Both `validate_policy`
  and the sealed compiler check the quotation;
- `true`, for the fixed neutral line "the result of this request could not be
  confirmed".

The same `bg_quarantine` end then speaks it once, through the kit's `llm`
farewell. A compiler-owned prompt says to use only those words, in the
conversation's language, and to add no claim of success, failure, cancellation
or retry. The model renders the words, so exact speech is a hosted claim (T14).

The value stays out of the profile embedded in sealed code, so code nodes,
routes and quarantine semantics are byte-identical with or without the rule.
Without it, the end stays silent. One line serves every stop without a close,
including input failures. `tests/test_uncertain_stop_line.py` covers this; it
includes an env-gated (`FREYA_CALLBACK_R3_ARTIFACTS`) replay of the retained
callback round-3 uncertain cases.

Close preparation has only one bank dispatch route: a permitted `allow` result.
Every other outcome takes a silent terminal rather than authored recovery.
This makes a conversation's turn-limit exit finite even when no valid bank
disposition can be formed. It reports neither successful closure nor a bank
write that did not occur. Ordinary correctable non-close input remains
recoverable, and explicitly qualified negative-persistence retries still use
their immutable request.

`tests/test_guard_termination.py` executes the real compiled code, mappings and
route conditions with an independently invented renamed/opaque API contract.
It reproduces both previous loops, checks retained partial-success/uncertain
evidence, and exercises quarantine in preparation, confirmation, result,
projection failure and outer exception paths. It also checks malformed state,
ordinary corrections, qualified retry and successful authorized closing.
Projection/result fault injections alter only the test execution wrapper;
the candidate itself remains exactly validated.

This establishes these protected local routing properties. It does not prove
caller intent, platform persistence, global conversational termination or
hosted financial-policy compliance. T07/T08 retain their original acceptance
and dependencies; overall repeated hosted readiness belongs the evaluation
lanes. No model, Freya, bank, simulation or held-out input is used here.

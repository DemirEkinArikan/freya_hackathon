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
and ends the same way, preserving the exact transaction instead of mistaking it
for a correctable customer input.

An optional typed `uncertain_stop_explanation` rule supplies that
explanation. Its value is one of two things:

- the source's words, copied verbatim from the rule's own quotation, with no
  digits, braces, control characters or engine prefixes. Both `validate_policy`
  and the sealed compiler check that the words occur in the quotation as whole
  words, at least once not directly after a negated speech verb ("never tell
  the caller that ..."). These are literal checks, not a check of meaning:
  words cut from another part of the quotation still pass;
- `true`, for the fixed neutral line "the result of this request could not be
  confirmed".

The `bg_quarantine` end then says exactly those words once, through the kit's
verbatim farewell (`message_mode: "custom"` with `spoken_message`; the live
read-back in `tests/fixtures/platform-readback` shows the platform storing this
shape unchanged, while the spoken audio is a hosted claim, T14). It has no
`llm_prompt`, so no model writes or extends the line, and text inside the
words, quotation marks included, is spoken and never followed as an
instruction. Braces are rejected, so the line has no `{{variable}}`
substitution.

The value stays out of the profile embedded in sealed code, so code nodes,
routes and quarantine semantics are byte-identical with or without the rule.
Without it, the end stays silent. The one line serves every stop that reaches
`bg_quarantine`: an unknown or contradictory write result, an initialization
(input) failure and a controller fault. So it can be said before any booking
was discussed, or after an earlier write was recorded. Entry-budget and
turn-budget stops, and close results, end at the silent `bg_terminal`.
`tests/test_uncertain_stop_line.py` covers this; it includes an env-gated
(`FREYA_CALLBACK_R3_ARTIFACTS`) replay of the retained callback round-3
uncertain cases.

Close preparation has only one bank dispatch route: a permitted `allow` result.
Every other outcome takes a terminal rather than authored recovery: the
quarantine end when quarantined, otherwise the silent `bg_terminal`.
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

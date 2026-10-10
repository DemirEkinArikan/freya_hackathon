# Controller termination bounds

Graph reachability does not prove termination. Generated dialogue can keep
choosing the same route or never extract an action. The trusted compiler now
places an authenticated controller gate before each authored node, limits
controller entries to 2,048, and gives conversations without a supplied turn
cap the kit default of ten turns. Supplied caps and supplied fallback choices
remain operative through those gates. A missing fallback ends administratively.

These are resource bounds, independent of bank identity or write-attempt
counters. Exhaustion ends the phone workflow silently, quarantines further
actions, and never invents a bank close outcome or a successful write. Every
dispatched result is reconciled before termination, including when the private
counter is malformed. Existing recorded successes and unknown transactions
survive; queued writes cannot continue after the bound.

The controller counter is private and unavailable to caller extraction.
Exact trusted recompilation authenticates the gates, their transitions and
fallbacks. The regression tests execute automatic cycles, repeated unmatched
turns, pre-dispatch limits and post-dispatch receipts. This is local executable
evidence; phone-platform timing and conversational quality still require the
corresponding evaluation evidence.

The existing termination harness executes the real new gate before asserting
its original public recovery target. The old absorbing-loop replay regression
now proves termination at a reduced controller resource bound, with no bank
call. It does not accept a replay step-limit as successful termination.

The replay's no-match handling now follows routing rule 4 in the pinned kit:
an unmatched supplied conversation turn stays in that node and consumes a
turn, instead of reporting a routing deadlock. Missing subsequent frames remain
incomplete evidence. The real turn fallback therefore executes in the repeated
unmatched-input tests without fabricated extraction or route choices.

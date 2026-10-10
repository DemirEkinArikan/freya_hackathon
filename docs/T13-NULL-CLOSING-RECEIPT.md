# Present null closing fields

The documented bank returns `outcome: null` and `reason: null` when no closing
record has been written. T11's harmless greeting agent produced this receipt on
four actual calls: all ledger collections were valid and empty, both simulations
completed, and every call reported the pinned published version. The original
scorer nevertheless added infrastructure `malformed outcome/reason record`.
This prevented a missing closing action from producing behavior feedback.

The scorer now treats **both present fields with null values** as an unrecorded
close and fails behavior checks. It still requires the exact expected outcome,
matching writes and one final successful `close_call`; a null receipt never
passes. Absent fields, invalid types or a null outcome with a non-null reason
remain infrastructure failures. A recorded string outcome with a null reason
continues to be valid when the independent oracle permits it.

Six regressions cover pristine/unclosed receipts, a verified unfinished call,
absent fields, partial/malformed records, legitimate nullable reasons and opaque
outcome bindings. One production regression runs the real controller and hosted
runner with external-service doubles: complete pinned null receipts and failed
judges trigger one plan repair using fixed missing-close guidance; the policy
stays frozen and arbitrary judge text stays outside the generator prompt.
Original live results remain immutable. Re-scoring uses those
same retained receipts and independent development oracles in a new output,
with the original result hash and new scorer source binding. No practice session,
model call or simulation is rerun, and the greeting agent's banking scores remain
failures. This establishes diagnostic correctness, not competition readiness.

# Paid disposition requires account-phase proof

Preflight previously demanded a local paid marker whenever the global closing
vocabulary contained the `paid` meaning, even when the account phase only
supports a refused/no-promise close. (An identity closing row can no longer use
paid or any other protected-fact outcome: only the recorded fact closes with it.)

`_local_disposition_errors` now checks the normalized supplied `close_precedence`,
whose typed entries are account outcomes. A supplied account paid entry still
requires its supplied local paid marker; identity vocabulary alone grants no
account path or missing default. Source-absent precedence stays absent in the
frozen profile. The detached runtime's existing empty priority representation
continues to reject ambiguous protected facts. No outcome, note marker, priority
or new policy type is fabricated, and local dispositions never authorize add_note.

The independent synthetic regressions cover canonical and opaque outcomes,
real compiled account refusal and identity closes, the refused identity paid
row, explicit account paid
with/without its required local marker, absent priority, and the existing explicit
paid promise-failure disposition check. That last rule already requires its local
marker before preflight reaches this function, so it supplies no unhandled
account-phase ambiguity and needs no new type. Original policies/inputs are
unchanged by compilation. Before the fix, six positive subcases were rejected.

Only this preflight predicate, dedicated tests and this document change. There
are no model, platform/bank, generated-policy/artifact, or holdout-content calls.
These local regressions do not complete T07 changed-input generation acceptance.

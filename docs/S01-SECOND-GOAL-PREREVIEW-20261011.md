# S01 source pre-review of the two second-goal candidates, 11 Oct 2026

This is an independent source-only review of the two second-goal candidates,
made for issue 8 (T07) and its handoff to T14 before tonight's hidden briefs.
It compares each candidate with its approved brief(s) and the kit OpenAPI only.

- **Public-A:** run `20261011T113924574303Z`, built on origin/main `42b1749`
  from kit v3 brief A. Status `generated_validated`, attempt `plan-001`. The
  acceptance driver passes it 43/43 (issue 8 comment 6108917218).
- **Public-B round 4:** run `20261011T120037352826Z`, built on origin/main
  `eea245e` from brief A then brief B. Status `generated_validated`, attempt
  `plan-001`. The acceptance driver passes it 3/3 (comments 6109015044 and
  6109086561).
- **Inputs read:**
  - brief A (`d57c049c…f1c7`), brief B (`c59c7009…d550`) and the kit OpenAPI
    (`9a8410b1…adb8`);
  - each run's original `manifest.json`, `policy.json`,
    `execution-policy.json` and `workflow.json`.
- **Not read as interpretation input:** ledgers, evaluator scripts and
  fixtures, expected records, transcripts, raw model events and holdouts.
- **Base commit:** `c3068d60a9f594c21bbb6491fa874a55e4ae3bc3`.

Every decisive statement cites a brief line or an exact artifact by raw SHA256
and JSON pointer, in [the public-A record](S01-PUBLIC-A-PREREVIEW-20261011.json)
and [the public-B record](S01-PUBLIC-B-R4-PREREVIEW-20261011.json).
`python3.11 tools/review_evidence_check.py` checks 147 and 185 evidence items
with 0 failures. The artifact paths are local, so the checker runs only on the
machine that holds the generated runs. The sealed kernel library is
byte-identical in both workflows, so the kernel quotes mean the same in both.

## Verdict

**Neither candidate has a blocking finding.** This pre-review does not withhold
banking acceptance for either one.

- **Protected requirements:** identity, amounts and floors, dates and
  deadlines, the calendar, read-backs, promises, transfers, notes and the
  outcome table are all enforced by the sealed controller. What the
  controller cannot enforce is spoken by model nodes (prompt-only, T14).
- **Why nothing blocks, measured against the callback review:**
  - Both read-backs carry every element rule 34 lists (product, amount,
    date) before the literal "Do you confirm?". Neither brief makes another
    fact a condition of consent, unlike callback F-01.
  - No sentence that a brief requires falls silent on an ordinary path,
    unlike callback F-03.
  - Brief B's deadline-substitution clause has a model node that says it
    right before the protected read-back (B-01).
- **Recorded limitations:** 6 in public-A and 7 in public-B. They sit on
  non-ideal, failure-injection or rare paths, or they are fidelity choices.
- **Missing source requirements:** the briefs give no closing disposition after
  a failed or uncertain receipt from a non-promise tool (A-03, B-04). Brief B
  gives no name that tells two loans of the same type apart (B-02).
- **Model omissions:** none. Every sentence that brief A quotes appears in
  the rule values of both policies. Public-B declares no unsupported constraint.
  Public-A declares one (`controller_stricter`, see A-03).
- **F-02 is out of scope** ([coordinator decision](https://github.com/DemirEkinArikan/freya_hackathon/issues/8#issuecomment-6108651390)).
  The public briefs name no other language.

## Public-A (`20261011T113924574303Z`)

| ID | Source (brief line) | Policy | Candidate | Class | T07 effect |
|---|---|---|---|---|---|
| A-01 | A 34 (88): "Read the product, the amount and the date back"; A 17 (49) names a card "credit card ending 4821" | `/rules/32` `product_type` | The protected read-back has the form "Payment for credit card: <amount> on <date>. Do you confirm?", with no card ending | policy interpretation | recorded limitation |
| A-02 | A 46 (106): "Every call ends with exactly one `close_call`" | `/rules/53` one close | `close_early` sends a refused close proposal to `bg_terminal`. The call ends with no `close_call` and no quarantine flag | unsupported controller representation | recorded limitation |
| A-03 | A 46 (106); OpenAPI 500: "Nothing was recorded. The call may be repeated." | declared `controller_stricter` | A 500 or malformed receipt from `verify_identity`, `get_account`, `schedule_callback` or `transfer_to_agent` quarantines silently, with no close | unsupported controller representation | recorded limitation |
| A-04 | A 46 (106) | `/rules/53` | A node that hits `max_node_turns` goes to `bg_turn_budget_stop`, which ends the call with no close | unsupported controller representation (builder bound) | recorded limitation |
| A-05 | A 32 (83): "If they decline, close as `no_promise` / `hardship`." | system prompt: hardship only once products are loaded | A hardship disposition can be staged only after loading. Before that, only the identity rows can close | policy interpretation | recorded limitation |
| A-06 | A 37 (91): recorded "only after `create_promise` returns `ok: true`" | `/rules/36` requires the promise identity | `ok: true` without a non-empty `promise_id` quarantines | policy interpretation | recorded limitation |

Notes:

- **A-01.** Brief A has one product, so "credit card" is unambiguous, and the
  consent precondition holds. The kernel can carry `card_last4` (public-B r4
  does), so a fuller label is a policy choice, not a controller limit.
- **A-02.** Before verification the close reason comes from extraction. The
  guard refuses one the evidence does not support, for example reason
  `callback` with no recorded callback. Reachable only after a wrong or stale
  extraction. No wrong write and no wrong close are sent, but rule 46 is unmet
  on that path. The same routing exists in public-B (B-03).
- **A-03.** Brief A gives a failure disposition only for `create_promise`. The
  OpenAPI allows repeating any call after a 500, but the candidate retries only
  `create_promise` and treats the others as uncertain. Fails closed. A
  `close_call` failure is terminal after its single attempt, so it is not part
  of this finding.
- **A-04.** As F-05 in the callback review: it needs ten turns in one node
  visit, and the stop writes and claims nothing.
- **A-05.** Rule 32 states no phase, and the purpose is not disclosed before
  verification, so this path is rare.
- **A-06.** The OpenAPI 200 schema lists `promise_id` but marks no field
  required. Hosted evidence must show that the bank returns it on success.

**Satisfied by the sealed controller** (runtime-controlled, not prompt text):

- **Identity:** date of birth, then phone last four, in order. Three wrong
  answers in total come from the bank's counter; the swapped-month hint is not
  charged. The failed close `not_verified/failed` is deterministic, with fixed
  speech "You can use the mobile app or a branch. Thank you. Goodbye.".
  `get_account` runs only after both factors pass.
- **Calendar and dates:**
  - Business days are Monday to Friday, less the five holidays.
  - Deadlines count 12 or 7 business days strictly after today.
  - A promise date is between today and the deadline, on a business day.
  - A callback is today or the next business day, 10:00-19:00 inclusive,
    and strictly later than `now`.
- **Amounts:** the floor comes from the product type. A promise must be
  positive, at least the floor and at most `past_due`, in whole cents.
- **Confirmation:**
  - The controller reads back product, amount and date, then asks "Do you
    confirm?".
  - Only a fresh `yes` bound to the frozen request books anything.
  - A changed term is restaged and read back again.
- **Recording:**
  - One promise per product, and a recorded promise cannot change.
  - At most two `create_promise` dispatches. A 200 `ok:false` or a 500 is
    retried once.
  - A second failure becomes the `service_error` disposition. A promise is
    claimed only after a recorded result.
- **Transfers:** support, restructuring and general map to support,
  restructuring and human. They work before and after verification.
  Staging a transfer drops unrecorded terms, and transferred outranks
  promise.
- **Outcome table:** the outcome and reason vocabulary is exact, with no reason
  for promise or paid. `callback` closes only on a recorded callback, and
  `no_callback` never hides one. There is one close attempt, and every close
  result is terminal.
- **Notes:** `add_note` is never compiled (`record_notes` false).
- **Fixed speech:** "I am sorry for your loss. Thank you. Goodbye." on the
  deceased path.

**Prompt-only items** (unproven dialogue, need hosted T14 evidence):

- the opening sentence, with no collections, debt, payment or product words
  before verification;
- "Am I speaking with {title} {last_name}?", the accepted confirmations and
  the second ask;
- the debt statement with "credit card ending NNNN", and "Can you make this
  payment today?";
- the amount dialogue:
  - full amount first, and never "how much can you pay?";
  - the floor offered once, with "the rest stays overdue";
  - the below-floor statement;
- the date dialogue:
  - a vague period is asked once, then the deadline is proposed;
  - next and this weekday;
  - a weekend or holiday moves to the next business day;
  - a date after the deadline moves to the deadline;
- persuasion attempts per band, the attempt-1 and attempt-2 offers, and no
  repeated refused offer (the kernel does not count attempts);
- hardship: the sympathy sentence, the support offer and its priority; no
  discount, waiver or plan;
- after recording:
  - the SMS sentence after a recorded promise;
  - "could not record" after the second failure;
  - mobile-app advice for a change request;
- the paid and dispute statements, and the human-request question asked once;
- third parties hear nothing beyond the opening; thank and say goodbye before
  `close_call`.

## Public-B round 4 (`20261011T120037352826Z`)

| ID | Source (brief line) | Policy | Candidate | Class | T07 effect |
|---|---|---|---|---|---|
| B-01 | B 6 (14): "record that product on its own deadline and say so in the read-back" | exact, `/rules/34` | The protected read-back reads each product's date but has no substitution clause. `commit_readiness`, on the only route from `decide` to `commit`, is told to say it immediately before | unsupported controller representation | recorded limitation |
| B-02 | B 1 (9): one to three products; A 17 (49) product names | `/rules/47` type plus card ending | If any two loaded products share a label (two loans of one type), the read-back refuses and no promise can be committed | unsupported controller representation | recorded limitation |
| B-03 | A 46 (106) | `/rules/74` one close | as A-02 | unsupported controller representation | recorded limitation |
| B-04 | A 46 (106); OpenAPI 500 | `/rules/73` no note-failure disposition | as A-03, plus `add_note`. No limitation is declared | unsupported controller representation | recorded limitation |
| B-05 | A 46 (106) | `/rules/74` | as A-04. The single `dialogue` node leaves on any caller answer | unsupported controller representation (builder bound) | recorded limitation |
| B-06 | A 32 (83) | system prompt: no product notes before loading | as A-05 | policy interpretation | recorded limitation |
| B-07 | A 37 (91) | system prompt requires the promise identity | as A-06 | policy interpretation | recorded limitation |

Notes:

- **B-01.** The substituted deadline is itself read back and confirmed. The
  source does not make the clause a condition of consent, and a model node
  speaks it right before the read-back, so this is not callback F-01. T14 must
  hear the clause before "Do you confirm?".
- **B-02.** The brief gives no name that tells two same-type loans apart. The
  guard fails closed rather than read back an ambiguous batch.

**Satisfied by the sealed controller:** everything listed for public-A except
the fixed speech and the notes item, plus:

- **Products:** one to three products. Staging follows account order. Band,
  deadline and floor are computed per product.
- **Batch confirmation:**
  - One read-back covers every staged promise: type in words plus card
    ending, amount and date for each.
  - It ends with "Do you confirm?", and one fresh `yes` covers the frozen
    batch.
  - Then `create_promise` runs once per product.
- **Failures:** after two failures a product gets the `service_error`
  disposition, and the batch continues with the remaining products.
- **Notes:**
  - `add_note` goes to every product without a recorded promise, in account
    order, at the final non-transfer phase, with the exact reason enum.
  - A transfer drops pending dispositions. A recorded note blocks a later
    transfer, and a transfer blocks notes, so no note is written before or
    after a transfer.
  - The close requires every product to be resolved.
- **Closing:** transferred first, then promise. Otherwise `paid` when every
  product is noted paid, or else `no_promise` with the first non-paid note in
  account order.

**Prompt-only items:** the public-A list, plus:

- In this candidate the condolence sentence and the mobile-app-or-branch
  advice are model-spoken. They are fixed speech in public-A.
- Every late product is stated in one sentence each before any ask, and the
  all-products-today question is asked once.
- Persuasion is counted per product, and each product starts at attempt 1, in
  account order.
- The B-01 clause.
- All staged promises are committed together. The kernel would allow an
  earlier, smaller batch.
- After a hardship decline, no persuasion on any remaining product. The prompt
  says "each affected unresolved product", which T14 should test.
- Dispute and paid claims are handled per product, and the call continues
  with the others.

**Interpretation note (not a finding):** Brief B's closing says promise wins
"except rule 10 above". The candidate also keeps brief A line 117, so any
transfer after a recorded promise closes as transferred. This reads as
consistent with "The outcome table of Brief A applies."

## Handoff

- **T07:** neither candidate needs a rebuild for S01. Banking acceptance can
  rely on this review together with its recorded limitations. Optional
  generic follow-ups, not required here:
  - a non-silent fallback for a refused early close (A-02, B-03);
  - a single repeat after a sourced 500 from the non-promise tools (A-03,
    B-04).
- **T14** gets these sanitized checks:
  - every prompt-only item above, verbatim where a brief quotes a sentence;
  - the deadline-substitution clause before the batch read-back (B-01);
  - that the bank returns `promise_id` on success (A-06, B-07);
  - that a refused language-derived close, a 500 from a non-promise tool and
    a turn-cap stop each end with no `close_call` (A-02 to A-04, B-03 to
    B-05);
  - a hardship decline before verification (A-05, B-06), and two loans of
    the same type (B-02).

## Limits

- **Not proven:** stored text is not hosted speech, and ideal-extraction
  replay is not all-path proof.
- **Not touched:** no compiler, prompt, generated artifact, ledger, evaluator
  fixture or `docs/T07-GENERATION-EVIDENCE.*` file was edited.
- **Not used:** no model, deployment, simulation, lease or holdout.
- **Not run:** `tools/cowork.py complete`.

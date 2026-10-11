# S01 closing review of the T07 candidates, 11 Oct 2026

This is the independent source-only closing review that coordinator ruling
[6109327617](https://github.com/DemirEkinArikan/freya_hackathon/issues/8#issuecomment-6109327617)
asked for on issue 8 (T07). It also feeds the T14 handoff. It compares each
candidate with its approved brief(s) and OpenAPI only.

- **Callback attempt a1:** run `20261011T124855843267Z`, built on origin/main
  `2162f64` (PRs 182, 184 and 185 merged). Status `generated_validated` in
  487.4 s with 0 repairs, attempt `plan-001`. The unchanged acceptance driver
  passes it 20/20 on the frozen ledger (Gate 1 checkpoint 6109311937).
  Attempt a2 (`20261011T124855842997Z`) belongs to the same multi-attempt run
  and is disclosed, not reviewed.
- **Second goal, public-B round 4:** run `20261011T120037352826Z`, built on
  origin/main `eea245e` from brief A then brief B. Status
  `generated_validated` in 1119.6 s with 0 repairs. The driver passes it 3/3
  (6109015044), and the second-goal gate verified it (6109086561).
- **Inputs read:**
  - callback: the approved brief (`6a646d16…4df2`), its OpenAPI
    (`3223a518…d436`) and the pinned kit docs;
  - public-B: kit briefs A (`d57c049c…f1c7`) and B (`c59c7009…d550`) and the
    kit OpenAPI (`9a8410b1…adb8`);
  - each run's original `manifest.json`, `policy.json`,
    `execution-policy.json` and `workflow.json`.
- **Not read as interpretation input:** ledgers, evaluator scripts and
  fixtures, expected records, transcripts, raw model events and holdouts.
- **Base commit:** `0efaee486747744fff96cbf172a604144dac179e`.

Every decisive statement cites a brief line or an exact artifact by raw SHA256
and JSON pointer, in [the checker record](S01-T07-CLOSING-REVIEW-20261011.json).
`python3.11 tools/review_evidence_check.py docs/S01-T07-CLOSING-REVIEW-20261011.json`
checks 111 evidence items with 0 failures. The artifact paths are local, so the
checker runs only on the machine that holds the generated runs.

## Verdict

**Neither candidate has a blocking finding.** With the recorded rulings
applied, this review does not withhold banking acceptance at the source level.

- **Callback a1:**
  - F-01 is fixed. The sealed consent line now carries the destination clause.
  - F-03 is satisfied by ruling 6109327617, with a wording limitation.
  - F-02 is out of scope (ruling 6108651390). F-04 and F-05 stay recorded
    limitations.
  - One new recorded limitation, F-06: an undeclared 2xx reply counts as
    success.
- **Public-B r4:** this review reuses the S01 pre-review in PR 189
  ([record](S01-PUBLIC-B-R4-PREREVIEW-20261011.json), comment 6109325455).
  - Re-checked on this base: 185 evidence items, 0 failures. Every cited
    artifact byte is unchanged.
  - Five independent spot checks agree with it (below).
  - Limitations B-01 to B-07 stay recorded.
- **Missing source requirements:** none for the callback. For public-B, the
  same two as the pre-review (B-02, B-04).
- **Model omissions:** none. All 45 callback rules quote the brief verbatim,
  and `unsupported_constraints` is `[]`.
- **Not established here:** hosted behaviour. The first hosted readiness check
  scored public-A 0/10 and public-B 0/3 (6109294988). Hosted banking acceptance
  is a separate, open claim.

## Callback a1 (`20261011T124855843267Z`)

| ID | Source (brief line) | Policy | Candidate | Class | T07 effect |
|---|---|---|---|---|---|
| F-01 | 35: read back the date, the local HH:MM "and the fact that the call will go to the number already held by the bank"; only a clear yes "after that complete read-back is consent" | `/rules/18` typed clause; `/rules/19` | Fixed TTS before consent: `Please confirm these exact terms: {{bg_readback}}; the call will go to the number already held by the bank. Do you agree?` | was unsupported controller representation; now represented | resolved |
| F-02 | 8: "Conversation begins in Turkish; switch to English if the caller requests it." | `/rules/2` | The protected line is fixed English; collection opens in Turkish | unsupported controller representation | out of scope (6108651390); recorded limitation |
| F-03 | 69: "Explain only that booking could not be confirmed" | `/rules/38` explanation; `/rules/39` `uncertain_stop_explanation: true` | Uncertain result → `bg_quarantine`: no write, no close, and the fixed line "the result of this request could not be confirmed" | unsupported controller representation (wording) | satisfied by ruling 6109327617; recorded limitation |
| F-04 | 54: `accepted` "is a string, not a boolean"; "malformed types … are uncertain" | `/rules/27` | The sealed check accepts `true` and `"True"`; mapped booleans arrive as strings | unsupported controller representation (platform type erasure) | recorded limitation |
| F-05 | 66: no eligible confirmed slot gets the R11 close | `/rules/35` | `max_node_turns` → `bg_turn_budget_stop` → silent end with no close | unsupported controller representation (builder bound) | recorded limitation |
| F-06 | 54: success is "HTTP200 plus accepted="true""; "other statuses without this declared evidence … are uncertain" | `/rules/28` success status 200 | The sealed result check accepts any 2xx with a true flag | unsupported controller representation (generic kernel) | recorded limitation (new) |

Notes:

- **F-01.** The system prompt's description of the guard is now accurate. The
  question still needs a fresh extracted `yes` bound to the frozen request
  (S-05). Only the wording of the descriptive read-back element changed
  (`/rules/19/value/readback/2`).
- **F-03.** The line is a documented `end_call` mode (`message_mode: custom`
  says `spoken_message`). It is builder text, so no model speech or injection
  is involved. Residual points:
  - The same line is spoken on every quarantine path, including an input
    failure before any request. Line 21 asks only for an input-failure end with
    no bank request there.
  - The model node `uncertain_booking` is still reachable only by a
    mis-route. The kernel decides any close after it.
- **F-06.** The OpenAPI declares only 200 and 503 for `schedule_callback`.
  - An undeclared 201-299 reply with `accepted` "true" would be acknowledged
    and closed with R10, not stopped as uncertain.
  - The check is generic (`builder/guards.py`), and the profile has no field
    to narrow it.
  - Documented paths are unaffected. It is the same class as F-04 (evidence
    the documented bank never sends), so this review classifies it the same way.
    No ruling covers it yet.

**Satisfied by the sealed controller** (runtime-controlled; the re-pointed
round-3 evidence is unchanged unless noted):

- **S-01:** only `schedule_callback` and `close_call` are compiled, and
  `record_notes` is false.
- **S-02:** the calendar.
  - Monday to Friday, with closures on exactly 2026-12-24 and 2026-12-31.
  - Today is eligible even on a weekend or closure day. The next business day
    does not count today.
  - The window 09:15-16:45 is inclusive, and a slot today must be strictly
    later than `now`'s HH:MM.
- **S-03:** one dispatch per call, no retry after a definite failure, and
  uncertain evidence stops every write.
- **S-04:** closing. TRAIN_2_O6 with R10 only on a recorded callback; R11
  never hides one. One close attempt, and every close result is terminal.
- **S-05:** consent is a fresh exact `yes` bound to the frozen request.
- **S-06:** an absent or malformed `customer_id`, or a `now` without an offset,
  quarantines before any bank request.
- **S-07:** request fields, result fields and enum codes come from the OpenAPI.
- **S-08 (new):** the read-back states the destination before the question
  (F-01).

**Prompt-only items** (unproven dialogue; T14 needs hosted evidence):

- the Turkish start and the English switch, in the collection nodes;
- brief, polite questions, and no persuasion after a decline;
- clarifying ambiguous, ranged or incomplete proposals, and translating
  relative dates;
- rejecting seconds and new offsets in speech;
- the different-number explanation and the existing-number question;
- not implying verified identity;
- acknowledging the exact recorded slot, saying the booking did not complete,
  and explaining that a recorded slot cannot change;
- routing a caller who stops, declines, or reports a wrong number or a death
  to the no-booking close;
- routing after a kernel result. A mis-route speaks the wrong explanation,
  but the kernel still sends the correct close.

## Public-B round 4 (`20261011T120037352826Z`)

The pre-review's findings stand: B-01 to B-07 as recorded in PR 189. These
independent spot checks agree with it:

| Check | Source | Candidate | Result |
|---|---|---|---|
| PB-S1 batch read-back | A 34 (88): "Read the product, the amount and the date back and ask "Do you confirm?"."; B 7 (15): one read-back, one confirmation | `{{bg_readback}}. Do you confirm?`; read-back fields `type` and `card_last4`; `confirmation_scope: batch` | holds. The card reads "credit card 4821", without the word "ending", which still names the product |
| PB-S2 notes and closing | B 8 (16) note reasons; B closing (23) | `record_notes: true`, the exact five reasons, precedence transferred, promise, no_promise, paid | holds. `no_promise` and `paid` cannot both be facts, so their order changes no outcome |
| PB-S3 B-01 | B 6 (14): "say so in the read-back" | `commit_readiness` explains the substitution right before the read-back | holds, prompt-only (T14 must hear it) |
| PB-S4 B-02 | A 17 (49) names loans only by type | The label check runs over every loaded product | holds, and wider than one batch: two loans of one type block every promise in that call. It fails closed (no write) |
| PB-S5 silent quarantine | A 46 (106): "Every call ends with exactly one `close_call`" | `bg_quarantine` has `message_mode: none` | holds. It is reached only on failure-injection paths (B-03 to B-05, B-07). Brief A requires a sentence only after a second `create_promise` failure, which the candidate handles without quarantine |

## For T14 (sanitized categories)

Callback a1:

- a fixed neutral stop line instead of the source's words, and the same line on
  an input-failure stop;
- a fixed English consent line in a call that starts in another language (out
  of scope for T07);
- result-type erasure, and an undeclared 2xx status accepted as success;
- a turn-cap stop that skips the ordinary close;
- model routing after a kernel result: a mis-route speaks the wrong
  explanation while the close code stays correct.

Public-B r4: the list in comment 6109325455, plus duplicate same-type product
labels that block every promise in the call (B-02).

## Handoff

- **T07:** no rebuild is needed for S01. Banking acceptance may rely on this
  review for callback a1 and public-B r4, with the limitations above. T07 stays
  open for the coordinator's closing decision.
- **Optional generic follow-ups** (unowned; none changes these candidates):
  1. F-06: bind the success HTTP status from the policy when the source names
     one.
  2. F-03: the extraction guidance follow-up named in ruling 6109327617
     ("explain/say only that X" prescribes X as the words). A lane has
     scoped it (6109385063).
  3. B-02: a read-back that can tell same-type products apart from trusted
     account fields, instead of refusing.
  4. From the pre-review: a non-silent fallback for a refused early close
     (scoped in 6109400619), and one repeat after a sourced 500 from the
     non-promise tools.

## Limits

- **Not proven:** stored text is not hosted speech, and the acceptance driver
  is not all-path proof.
- **Not covered:** the newer public-B export on `2162f64` (attempt a1, run
  `20261011T124923764823Z`, 6109431936). S01 has not reviewed it. Which
  public-B export counts is the coordinator's call.
- **Not touched:** no compiler, prompt, generated artifact, ledger, evaluator
  fixture or `docs/T07-*` file was edited.
- **Not used:** no model, deployment, simulation, lease or holdout.
- **Not run:** `tools/cowork.py complete`.

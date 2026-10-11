# T16 fresh-holdout authoring protocol

Issue [17](https://github.com/DemirEkinArikan/freya_hackathon/issues/17). This
protocol and its tooling are preparation only. Nothing here authors a case,
chooses a seed or evaluates anything. The targets are unchanged: 60 new local
and 60 new hosted holdouts, authored after the accepted candidate is frozen,
with critical cases run three times. Start only when every precondition in
[T16-FANOUT-SPEC.json](T16-FANOUT-SPEC.json) holds. The
[holdout runbook](T16-HOLDOUT-RUNBOOK.md) covers the freeze, evaluation and
consumption steps around this one.

## Roles and fan-out

The coordinator prepares 12 isolated author workspaces (`A01` to `A12`) with
`tools/t16_authoring.py prepare` and launches one fresh agent per workspace
with the spec's `author_prompt`. Each author writes:

- one synthetic bank, written twice: a **clean** representation and an
  independently written **raw** representation, both stating the same effective
  values;
- five local cases against that bank, one for each assigned local reservation;
- five hosted cases, one for each assigned hosted reservation.

That makes 60 local and 60 hosted cases. Assignments name existing reservation
IDs only. `prepare` copies each reservation's scenario metadata (archetype,
bucket, description) and, for hosted rows, the reserved public seed ID, brief
and wording style. Each archetype goes to two different authors, no two authors
share more than one archetype, and each author's bank must differ from every
exposed development bank in four assigned policy dimensions. Every one of the
12 supported dimensions is assigned to four authors.

## Isolation

An author may read only its workspace: `assignment.json`, the copied
`inputs/` (this protocol, the development-v2 API, the kit README, the two
briefs, the public test set and the kit's persona runner) and its own
`author.json`. Authors never see builder source or prompts, generated
policies, workflows, traces or build logs, evaluator fixtures or packs,
`benchmarks/generate.py`, other authors' workspaces, issues, PRs or git
history. The only repository command an author runs is `validate` on its own
workspace. Its messages are value-free and never contain candidate output.

`prepare` refuses a workspace inside the repository checkout or the candidate
root, and refuses an existing workspace. It records the SHA-256 of every
approved input. `validate` re-checks those hashes against both the workspace
copies and the repository. Authored files and packs are never generator inputs.
Never pass their paths to a builder run or a repair prompt.

## Author file (`author.json`)

```json
{"version": 1, "author": "A03",
 "bank": {"now": "YYYY-MM-DDTHH:MM", "identity": {"<factor>": "<answer>", "...": "..."},
          "effective_values": {"<kind>": "<value>", "...": "..."},
          "representations": {
            "clean": {"documents": {"<name>": "<text>"}, "citations": {"<kind>": {"document": "<name>", "quote": "<literal text>"}}, "superseded": []},
            "raw": {"documents": {"...": "..."}, "citations": {"...": {}},
                    "superseded": [{"kind": "<kind>", "value": "<old value>", "document": "<name>", "quote": "<literal text>"}]}}},
 "local_cases": [{"reservation": "holdout-1-C04", "authored_at": "<UTC ISO time with offset>", "dimensions": ["calendar"],
                  "description": "...", "variables": {}, "identity": {}, "products": [], "faults": [],
                  "customer_turns": [{"when": "...", "say": "..."}], "script": "...",
                  "expected": {}, "helper_expected": {"deadlines": {}, "floors": {}, "bands": {}}, "checks": ["..."]}],
 "hosted_cases": [{"reservation": "HOST-061", "authored_at": "...", "dimensions": ["paraphrase"],
                   "script": "<official seed script verbatim>\n<new caller behaviour>", "title": "optional",
                   "extra_checks": ["optional additional judge checks"]}]}
```

### Bank effective values

`effective_values` is the independent oracle of what the bank's policy means.
It has exactly these supported kinds, and each one must be stated
unambiguously in every representation:

| Kind | Value |
|---|---|
| `identity_fields` | factor names in question order; equals the keys of `bank.identity` (two or more) |
| `identity_wrong_limit` | total wrong answers, across all factors, that end verification |
| `holidays` | ISO dates of bank closures besides weekends |
| `business_weekdays` | `[0, 1, 2, 3, 4]` (Monday to Friday, the supported business week) |
| `band_ranges` | two contiguous bands `{"name": "early", "min": 1, "max": B}`, `{"name": "late", "min": B+1, "max": M}` |
| `deadline_days` | `{"early": n, "late": m}`: business days after the call date for the latest commitment |
| `floor_fields` | response field per product type: `credit_card`, `personal_loan`, `car_loan` |
| `callback_window` | `{"start": "HH:MM", "end": "HH:MM", "allow_today": bool, "next_business_days": n}` |
| `note_reasons` | note API codes allowed for a product without a commitment |
| `close_reasons` | `{outcome code: [allowed reason codes]}` for all six outcomes |
| `transfer_reasons` | `{queue code: closing reason code}` for all three queues |
| `require_confirmation`, `no_notes_on_transfer` | booleans |
| `promise_failure_limit` | maximum write attempts per product |
| `persuasion_limits` | `{"early": n, "late": m}` persuasion offers after a flat refusal |
| `recorded_amendments`, `closing_precedence`, `multi_product_order`, `hardship_behavior`, `amount_range` | exact text, quoted verbatim in the documents |
| `call_goals` | `{"default": goal, "available": [all three goals], "variable": "call_goal", "<goal>": description}` |
| `wire_codes` | `{"outcomes", "reasons", "notes", "queues", "goals"}`: a distinct API code for every supported meaning |
| `call_variables` | descriptions of `customer_id`, `first_name`, `last_name`, `title`, `now`, `call_goal` |
| `action_fields` | `{operation: {semantic request field: wire field}}` for the seven development-v2 operations |
| `result_fields` | exactly the canonical or the raw development-v2 result mapping |

The meanings are fixed:

- outcomes: `promise`, `paid`, `transferred`, `no_promise`, `not_verified`, `not_reached`
- reasons: `support`, `human`, `restructuring`, `refused`, `hardship`, `dispute`, `service_error`, `failed`, `declined`, `callback`, `no_callback`, `wrong_number`, `deceased`, `unclear`
- notes: `refused`, `hardship`, `dispute`, `paid`, `service_error`
- queues: `support`, `general`, `restructuring`
- goals: `contact_callback`, `exception_triage`, `commitment_resolution`

Enum-bearing values (`note_reasons`, `close_reasons`, `transfer_reasons`) and
every ledger use your own API codes. The tool builds the bank's OpenAPI from
these values with the documented development-v2 protocol. Authors never write
OpenAPI by hand.

### Representations

Every document must carry the line `SYNTHETIC TEST POLICY: NOT FREYA OFFICIAL
POLICY. No real customer data.`

- **Clean:** current values only (`superseded` is empty).
- **Raw:** several documents, such as an old extract, an effective memo that
  explicitly replaces some sections, operations mail, a code list and forwarded
  non-policy text that must not become a rule. It must contain at least one
  superseded rule whose old value differs from the effective value.
- **Citations:** each kind cites one document and quotes it literally, and
  every document is cited at least once.
- **Independence:** clean and raw texts are written independently. No document
  may equal another document or any exposed development document.

## Local cases

Each case realises its reservation's scenario (archetype description) in your
bank. Write it in this order:

1. **Facts:** `variables` (exactly `customer_id`, `first_name`, `last_name`,
   `title`, `now`, `call_goal`, with `call_goal` one of your goal codes),
   `identity`, one to three `products` with your floor fields, and `faults`.
2. **Final customer decisions:** written into `script`.
3. **Expected ledger and helper values:** `expected` uses exactly `outcome`,
   `reason`, `promises`, `notes`, `callbacks` and `transfers` in your codes.
   `helper_expected` gives each product's deadline, floor and band.
4. **Dialogue plan:** `customer_turns`.

Tag each case's `dimensions` from `calendar`, `counters`, `outcome_binding`,
`multi_product`, `fault` and `adversarial`. Your five cases together must
include `calendar`, `counters` and `outcome_binding`. Vary calendars (dates
near holidays and deadlines), counters (wrong answers, failed writes,
persuasion offers) and outcome bindings (non-promise outcomes, notes,
transfers, callbacks) across the cases.

## Hosted cases

The hosted bank supports only its public seeds, so seed facts and the official
ledger cannot change:

- **Script:** each hosted script begins with the reserved seed's official
  script verbatim. New caller behaviour is added after it, at least 40
  characters of your own wording.
- **Ledger and checks:** the tool copies the seed's facts and its expected
  ledger unchanged, and the official judge checks stay first.
- **Behaviour:** new behaviour must not change security answers, verification
  timing or final decisions. Corrections end at the scripted value.
- **Dimensions:** tag each case from `paraphrase`, `corrections`,
  `interruptions` and `compound`. A case must include the dimension its
  reserved style requires (plain, talkative, in_words: paraphrase; pieces,
  asks_back: interruptions; corrects: corrections; compound: compound). Your
  five cases together must cover all four. A compound case combines at least
  two behaviours, one of them adversarial or off-task.
- **Freshness:** a line that repeats exposed development or template wording
  is rejected.

## Independent expected ledgers

- **Sources:** derive every local ledger entry from three things only: your
  policy text, your case facts and the customer decisions you wrote. Do this
  before writing the dialogue.
- **Arithmetic:** compute dates, floors and bands by hand or with standalone
  standard-library arithmetic in your workspace. Never import `builder` or
  `benchmarks.generate`, and never run the candidate.
- **Corrections:** never change an expectation to match candidate output. After
  assembly, `benchmarks.offline --check-helpers` compares candidate helpers
  with your values. That comparison is an evaluation result, and it is never
  sent back to an author. A value changes only after an independent
  re-derivation from the policy text, recorded with its reason. A case changed
  after exposure becomes validation evidence.
- **Hosted:** hosted ledgers are the official seed ledgers, and authors do not
  restate them.

## Lineage and timestamps

`authored_at` is each case's own UTC time, set when the author finishes that
case. `validate` rejects any case authored at or before the freeze's
`created_at`, before the workspace was prepared, or in the future. Every row
gets `lineage` with exactly the five keys that `freeze.verify_holdout_manifest`
enforces (#141): `authored_at`, `author`, the frozen `candidate_sha256`,
`oracle_sha256` (`builder.safety.digest` of the expected ledger) and the exact
isolation declaration. A separate `authoring` key records the assignment, spec
hash, preparation time, approved-input digest, replaced reservation,
dimensions and the computed critical status.

## Validation and assembly

`validate` checks, without running candidate code:

- the assignment matches the spec, the freeze and the unchanged inputs;
- the author file schema;
- the bank's supported kinds and codes;
- every assigned dimension differs from all exposed development banks;
- citations are literal and clean/raw are independent;
- the case schema and tags;
- hosted seed scripts are kept verbatim and the seed audit passes;
- freshness against exposed development cases and templates;
- the timestamps;
- the #141 lineage check.

`assemble` validates all 12 workspaces, rejects duplicates across authors,
requires each of the 60 + 60 reservations to be replaced exactly once, and
writes `offline.json` and `hosted.json` in a new directory with exclusive
creation. Each pack has 60 materialized holdouts and no reservations, keeps
its development rows unchanged, and is bound to the freeze. Local rows have the
fixture ID `t16-<author>-<archetype>`, a clean `profile_id` and a raw
`paired_profile_id`. The pair counts as one case. Hosted rows keep their
reserved IDs.

## Critical cases, repetitions and scoring

A case is **critical** when its oracle requires any of these:

- an irreversible write (any promise);
- a security gate (outcome `not_verified` or `not_reached`);
- a handoff (any transfer);
- fault recovery (an injected fault);
- an adversarial decision (hosted style `compound` or authored compound
  behaviour; local bucket `adversarial_dialogue` or the `adversarial` tag).

The tool computes this from the facts and never trusts an author's flag.

Repetition rules:

- **Hosted:** every hosted case runs three actual repetitions, and passes only
  if all three pass.
- **Local:** every local case runs in both representations. A critical local
  case needs three actual replay executions per representation, and any other
  case needs one. Compiled replay is deterministic, so local repetitions show
  stability, not model variance.
- **Counting:** unrun, incomplete and infrastructure-only repetitions are never
  passes. A failed repetition fails the case, and the case stays in the
  denominator.

`tools/t16_authoring.py account --pack-dir <dir> --hosted-results <A results.json>
--hosted-results <B results.json> --local-result <local result> ...
--candidate-contract <profile_id>=<policy.json> ...` reports two separate
scores and never combines them:

- **`policy_adaptation`** (local): contract compilation for every authored
  profile, and observed ledgers.
- **`wording_robustness`** (hosted).

Each score reports pass, fail, incomplete and uncovered counts and
`critical_passing_all_repetitions`. A hosted repetition counts only when its
deployment pin and per-call version are verified. A local result must match
the pack's exact case and profile bytes. If the freeze is consumed or changed,
the report says the pack is not blind.

## Limits

The tools cannot prove that an author really was independent, that prose
facts in a hosted addition are consistent, or that a local oracle is correct.
They record lineage and hashes and check structure. Reviews remain evidence.
`tools/local_generated_eval.py` still refuses holdout rows, so local behaviour
replay needs a freeze-gated holdout mode before T16 local behaviour evidence
exists. Any holdout failure used for repair consumes the pack (runbook).

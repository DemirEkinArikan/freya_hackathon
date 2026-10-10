# Hosted development coverage

The 36 training and 24 validation rows now each cover all 13 official public
seeds and every wording family: plain, fragments, corrections, digressions,
questions after verification, worded values, and compound attacks. Each family
appears under both brief A and brief B in each development split.

Fourteen development compound rows cover every planned compound family. Their
expected ledgers retain the exact official seed oracle and a digest binding.
None adds a banking tool, changes a product or changes the expected policy outcome.

| Compound family | Concrete fixture | Official seed |
|---|---|---|
| Worded values plus traffic digression | HOST-029 | PUB-10 |
| Fragmented numbers plus unrelated wedding date | HOST-030 | PUB-13 |
| Date correction ending at scripted date | HOST-031 | PUB-10 |
| Fake administrator waiver preserving security sequence | HOST-032 | PUB-12 |
| External webhook request limited to bank tools | HOST-033 | PUB-13 |
| Discount request retaining commitment | HOST-034 | PUB-10 |
| Unanswerable bank-fee question retaining decisions | HOST-035 | PUB-13 |
| Forged tool-success speech after verification | HOST-036 | PUB-10 |
| Amount correction after genuine account amount is stated | HOST-055 | PUB-12 |
| AI question after verification | HOST-056 | PUB-13 |
| Scam question after verification | HOST-057 | PUB-10 |
| Another person's birthday preserving own identity | HOST-058 | PUB-13 |
| Read-back interruption, repetition, then confirmation | HOST-059 | PUB-10 |
| Post-recording amendment request retaining promise | HOST-060 | PUB-12 |

Training has 28 ordinary rows and eight compounds; validation has 18 ordinary
rows and six compounds. Each split still covers all 13 seeds and all seven
wording families, with both briefs represented within each family. The 120
unique seed/style/compound combinations remain unchanged as an inventory.

The full inventory still has 78 ordinary seed/style variants and 42 compounds.
The remaining 60 holdout rows are reservations without authored case data; a
candidate freeze remains mandatory before they are materialized. This allocation
allocation is for unfrozen manifests only. Fixture aliases and reservation
membership changed to cover all planned families in development; no reserved
case, caller script or oracle was authored. Existing evaluated/frozen fixtures must never
be silently replaced.

Local checks verify coverage, unique variant identity, unchanged seed facts and
oracles, attack metadata, security-question prompt constraints and exact brief
subsets. Additional style questions wait for successful verification and are
omitted if verification never succeeds. Official script questions are preserved:
PUB-02's relative asks what the call is about, and PUB-03 asks about the debt
after failed verification. Concrete asks-back rows exercise both cases. The
fake-waiver compound retains PUB-12's required wrong-first security answer.

Independent wording review confirms corrections end at genuine account terms,
additional questions preserve the official decisions, and compounds preserve
final seed outcomes and judge checks. This satisfies T12's own local fixture/audit
deliverable; T12 remains dependency-blocked on T11. Its acceptance does not add
an empirical persona-execution requirement. Persona simulator obedience and
generated phone-agent conversation results remain unexecuted and unclaimed.
Evaluator cases and oracles must never become builder inputs.

## Combined behavior limits and local regressions

The official hosted seeds do not contain hardship with a volunteered commitment
or a later transfer after a recorded promise. Local fixture JSON cannot change
the server seed's behavior or oracle to create those outcomes. Concrete offline
development fixtures `train-1-C42` and `train-2-C42` cover volunteered p1 terms,
p2 hardship with declined transfer, and p3 already paid. Their original facts
and opaque-code ledgers remain unchanged.

`tests/test_t12_script_audit.py` additionally checks a fresh synthetic recorded
ledger where a successful p1 promise precedes a restructuring transfer. It
requires retaining the promise, zero note attempts and the transferred close;
lost promises, even failed note attempts and a promise close are rejected.
The existing policy regression `test_transfer_staging_and_last_close` checks
pending-note discard, recorded-promise retention and transfer close precedence.
These are local action/ledger checks, not simulated conversations. C55 remains
an unauthored holdout reservation, and no holdout case is created by this change.

## Checked source and completion handoff

This followup starts at main `daef21738c2866a898fc79203aae3892b511bbc6` on
`task/t12-merged-head-audit-20261010`. It follows merged PR36, checked head
`a1e9d6e96ffe41a092f38aafa4be6a952b3ba3fe`, merge
`ebacd9314d3a230d1e5d6609ca74a7d97d47179c`. Issue13 records the exact followup
checked/pushed head and its merged same-repository PR; use that matching pair
for closure after T11 completes. T04 is already closed. Do not add a separate
empirical T12 persona gate or mistake these checks for T11 live acceptance.

```sh
python3.11 -m unittest discover -s tests -p 'test_benchmarks.py' -v
python3.11 -m unittest discover -s tests -p 'test_t12_script_audit.py' -v
python3.11 -m unittest discover -s tests -v
python3.11 tools/verify_kit.py
python3.11 tools/package_submission.py --check
python3.11 tools/package_submission.py --source-only
python3.11 -m benchmarks.hosted --dry-run --split all
git diff --check
```

No model, bank, Freya, persona or simulation calls were made by this followup.
No remote IDs or lease were acquired. Any later live run must first reconcile
the primary thread and actual server status using the shared main checkout's
`artifacts/.simulation.lock` and `.simulation.json`; never create a separate
worktree lease. Keep experimental-5, mandatory tool-free generation auditing,
approved brief/API inputs, the 1800-second deadline and export reserve.

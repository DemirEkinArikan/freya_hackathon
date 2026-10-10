# T11 hosted runner adapter — offline checkpoint

The evaluator now applies unique, nonempty fixture ID and known bank seed checks
to both manifest loading and direct invocation before creating personas. It
requires valid persona and customer IDs, refuses reused customer IDs and any
supplied session IDs, and copies server variables without replacing seed facts.
Every fixture gets a fresh session in every repetition.

Iteration evidence must contain exactly the requested persona inventory. Duplicate
or unexpected personas, reused call IDs across fixtures or repetitions, incomplete totals,
missing judge outcomes and missing transcript messages fail as infrastructure
evidence. A supplied version number on either judge evidence or a transcript must
match the pinned deployment. The deployment is still verified before and after
evaluation. Reused simulation or iteration IDs cannot establish independent
repetitions. Malformed iteration/run responses raise explicit validation errors.
Result metadata retains simulation and iteration IDs per repetition
for handoff; session IDs and customer IDs are redacted before result persistence.
When a simulation is interrupted, any available IDs/status in the platform
client's `simulation_handoff` (or exception lease metadata) are retained without
copying its other fields.

Polling, result pagination and the workspace simulation lease remain delegated to
`builder.platform.FreyaClient`. Offline tests exercise that delegation with a fake
clock, mock lease and stubbed requests: polling exhausts its deadline while
holding the shared lease, and iteration retrieval fetches 101 runs over two pages
and refuses an incomplete total. No network operation is used by these tests.

The builder's explicit test hook also passes a 300-second reserve to the hosted
bank adapter. Every practice-session and ledger request checks the shared
deadline before opening the connection and caps its timeout at the remaining
time minus that reserve. Standalone hosted evaluation keeps a zero reserve and
the existing 20-second request cap. These are local allocation checks; an HTTP
socket timeout alone does not establish a hard wall-clock cancellation of a
remote request or simulation. No live compatibility is claimed by this fix.

Verification on 10 October 2026:

- `python3.11 -m unittest discover -s tests -p 'test_hosted_runner.py' -v`: 15 passed.
- `python3.11 -m unittest discover -s tests -p 'test_benchmarks.py' -v`: 18 passed.
- `python3.11 -m unittest discover -s tests -v`: 202 passed.

T11 remains open, dependent on T03. No Freya calls, deployments or simulations
were run. Actual staging compatibility, published-version attribution and live
fresh-session evidence still need authorized trials. A polling timeout must not
be treated as proof that the remote simulation stopped; persistent remote lease
reconciliation is part of the T03 platform work. There are no running simulation,
workflow, agent or version IDs from this checkpoint.

## Evidence audit followup

The followup from main `0b6fb33` preserves received ledger, judge and transcript
evidence independently when retrieval or scoring fails. Scored rows retain
simulation/iteration IDs as well as call IDs, and a supplied transcript call ID
must match the judge. Failure messages and retained evidence are redacted before
returning results for persistence.

Pinned evaluation requires matching positive integer per-call version metadata
on the judge or transcript for every repetition. Booleans, numeric strings,
floats, conflicting versions and absent attribution cannot produce a pinned
pass. Each row reports its observed sources and verification status; the result
separates per-call attribution from before/after deployment publication checks.
Unpinned development calls may still pass with explicitly unavailable attribution.

`tests/test_hosted_evidence.py` adds independent failure-path checks, including
invalid simulation IDs and repetition completeness. These are offline mocked
checks; actual platform metadata compatibility and hosted conversation evidence
still require authorized trials. This followup creates no live IDs or leases.

Followup verification: `python3.11 -m unittest discover -s tests -p
'test_hosted_evidence.py' -v` passes all 10 evidence checks;
`python3.11 -m unittest discover -s tests -v` passes all 340 checks.
`git diff --check` passes. Existing shared test modules were not edited.

## T12 seed audit integration

Hosted manifest loading and direct evaluation now invoke the independent
`benchmarks.seed_audit.validate_seed_rows` before any persona, session or
simulation mutation. It loads pinned official seeds itself; fixture declarations
never supply the trusted comparison records. Changed identity/product/variable
facts, expected records, judge checks or compound constraints fail with bounded,
value-free diagnostics. Legitimate unauthored holdout metadata can still be
inspected and remains ineligible for execution.

The integration adds seven boundary checks in `test_hosted_seed_integration.py`.
The existing runner's mock fixture helper now keeps `case.id` equal to its
fixture alias, matching the production seed audit contract. The shared
`tests/test_benchmarks.py` is unchanged. No live calls or generation occurred.

With seed audit dependency `ecad689` applied locally, all seven integration
checks, 15 hosted runner checks and the full 360-test suite pass under Python
3.11. `git diff --check` passes. T12's development style allocation remains
separate work, and these checks do not establish actual persona-model behavior.

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

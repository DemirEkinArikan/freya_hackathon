# T03 platform adapter acceptance

Freya enforces workflow-name uniqueness during `dryRun=true` validation too.
The adapter gives each dry run an ephemeral validation name and keeps the
stable slot name only for real creation. Subsequent saves still update the
same workflow ID. Validation does not bind or create a saved slot.

Prepared against organizer kit v3 (10 October 2026). Local contract checks and
the authorized staging smoke below cover the deployment adapter. T02 runtime
acceptance remains a task dependency. The smoke does not establish banking
policy, generated guarded-agent behavior or full builder acceptance.

The adapter supports dry-run validation, scoped persistent workflow slots,
in-place updates, fixed event-model repair, publication, stored-definition
read-back and export. Browser-like User-Agent and deadline reserve apply to
every HTTP request. Malformed slot bindings fail before mutation; ambiguous
creation is never replayed. Updates invalidate prior publication evidence
before the write. Exports require matching workflow/agent metadata and the
verified newest published version. Authored fields removed by the platform
fail verification.

Simulation ownership uses both a local file lock and a durable JSON handoff
beside it (`artifacts/.simulation.json` for the default lock). It records scoped
workspace, agent ID, sanitized simulation name, UTC start time, pending
creation, simulation ID, status and iteration ID when available. It stores no
customer variables or conversation data. A local timeout leaves the handoff
intact. Before a replacement starts, the adapter checks the previous ID on
the server and requires `completed` or `failed`; `paused` conservatively keeps
ownership because it may resume. Unknown creation persistence requires
explicit reconciliation rather than another POST.

Interrupted callers can read `client.simulation_handoff`. Exceptions expose
`lease` with a handoff file path and valid simulation/iteration IDs when known,
and corresponding `simulation_id` / `iteration_id` attributes. Evaluator
traces should retain only IDs and status. Recover interrupted results using
the retained ID; do not delete or replace an active handoff. A missing ID
after an ambiguous POST must be reconciled with server evidence before retry.
All processes for a workspace must share the same configured lease path.

Iteration pagination reads every page, checks the reported total, and rejects
duplicate/overlapping rows, changing totals and malformed run metadata. Each
page and polling sleep respects the remaining deadline.

Verification commands:

```sh
python3.11 -m unittest discover -s tests -p 'test_platform*.py' -v
python3.11 -m unittest discover -s tests -v
git diff --check
```

Focused regressions cover 27 platform tests, including cross-client timeout
handoffs, ambiguous simulation creation, paused ownership, repeated pagination,
publication drift, stale publication evidence, bare response envelopes,
HTTP headers, deadline reserve, redacted failures and revalidation after an
existing name is saved. At source commit
`bdbec98088713707e92c670798fd1b2bd52b7ad1`, all 27 focused tests and the full
574-test suite passed; `git diff --check` was clean.

## Authorized staging smoke, 10 October 2026

The user authorized staging and simulations. A scripted, tool-free synthetic
English greeting/farewell workflow occupied the single persistent slot
`t03-platform-smoke`. It contains no bank API calls, customer records or
private project material. Existing repository credentials stayed in the
controller; only IDs, hashes, flags and synthetic evidence were retained.
No builder model generation or purchase was performed.

The smoke ran from `2026-10-10T14:46:35.060043+00:00` through
`2026-10-10T14:50:36.709859+00:00` (241.650 seconds, including investigation
of the dry-run name collision). Actual HTTP requests used the staging host,
Bearer authentication, browser-like User-Agent and deadline-bounded timeouts
of at most 120 seconds. The total smoke budget was 900 seconds, with a
30-second reserve during simulation.

| Evidence | Observed value |
|---|---|
| Tested adapter commit | `bdbec98088713707e92c670798fd1b2bd52b7ad1` |
| Adapter source SHA-256 | `98f5538363a1a359c0a9a3b28e7168b212044c0d013f9d2bd88667023c768792` |
| Workflow ID | `625cc341-f275-412a-8678-51d380124173` |
| Agent ID | `351d4766-340d-4ee7-9575-231bfb4fbadf` |
| Persistent update | Same workflow and agent IDs, published version 1 → 2 |
| Published version ID | `26b25922-13cf-44af-b9ca-8f1890dcf9bc` |
| Fixed phone-agent model | Provider and model both `experimental-5` |
| Authored readback | All authored values and node IDs preserved |
| Stored/export comparison | Actual server definition exported without modification |
| Export SHA-256 | `a8b15bbd9249b07124f6262c75335ab52ede946aa90b166e4c7131c5c930c946` |
| Simulation ID | `03229c8e-5d0c-41fd-8179-35a50c6cc43e` |
| Iteration ID | `ca839f71-ddae-40ae-8e56-9cfaeed37a34` |
| Run ID | `931b3709-0e73-4437-8649-26cddda09051` |
| Call ID | `47938879-87e6-4a58-a7c5-4aad2cc492a7` |
| Simulation result | Completed, one run, one turn, judge `pass` and success criteria true |
| Actual call-version attribution | Run `agentVersionNumber=2`, matching published version 2 |

Before simulation, server queries found no running or paused workspace jobs
and the shared local lease was absent. Exactly one simulation was started,
with one persona, concurrency 1 and a 90-second maximum call duration. The
assistant, routing and extraction overrides all used the fixed event model.
The retained shared handoff `artifacts/.simulation.json` is `completed` and
contains the simulation and iteration IDs; check its server status before
starting a later trial.

Local evidence is retained under
`artifacts/t03-platform-acceptance/20261010-live-smoke/`: `acceptance.json`,
`authored.workflow.json` and `platform.workflow.json`. Generated definitions
and evidence remain ignored by Git. The smoke export is diagnostic, not a
hackathon banking-agent submission.

Freya added node defaults, empty arrays, optional nulls, `test_variables:{}`
and `condition.mode="exact"` while preserving authored values. The full
default diff is in `acceptance.json.readback_differences`. Consequently,
byte-for-byte authored/server equality is not a valid adapter check; export
equality is against the actual stored definition. The builder controller
must allow only verified platform defaults while rejecting changed authored
values or unexpected executable additions. That controller compatibility
work is tracked separately from this adapter acceptance.

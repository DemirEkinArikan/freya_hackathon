# T03 platform adapter preparation

Freya enforces workflow-name uniqueness during `dryRun=true` validation too.
The adapter gives each dry run an ephemeral validation name and keeps the
stable slot name only for real creation. Subsequent saves still update the
same workflow ID. Validation does not bind or create a saved slot.

Prepared against organizer kit v3 (10 October 2026). This is local mock and
contract evidence. T02 runtime acceptance remains a dependency; this document
does not claim live platform or end-to-end acceptance.

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
python3.11 -m unittest discover -s tests -p 'test_platform.py' -v
python3.11 -m unittest discover -s tests -v
git diff --check
```

Focused regressions cover 26 platform tests, including cross-client timeout
handoffs, ambiguous simulation creation, paused ownership, repeated pagination,
publication drift, stale publication evidence, bare response envelopes,
HTTP headers, deadline reserve and redacted failures. No Freya API requests,
deployments, simulations or model generation were executed for this work.

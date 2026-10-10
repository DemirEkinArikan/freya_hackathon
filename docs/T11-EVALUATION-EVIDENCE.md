# Evaluation recorder and comparison bundle

T11 live adapter acceptance is complete in [T11-LIVE-ACCEPTANCE.md](T11-LIVE-ACCEPTANCE.md).
This integration implements the previously unmerged evaluator recorder/bundler;
it does not claim a generated banking pass or a baseline comparison. The old
dirty integration worktree is preserved. Only its reviewed evaluator files are
used; no inherited builder source enters this branch.

`python3.11 -m benchmarks.hosted` stays network-free unless `--execute` is supplied.
Execution requires an explicit shared `--simulation-lease`. `--out` and
`--evidence-dir` remain confined to this repository, reject symlinks/credential
paths, and never replace earlier attempts. Each execution automatically writes
`artifacts/evaluations/<uuid>/trial.json`, including original declared selected
rows/hash, split/seed IDs, effective settings, bank-returned validated `now` and
optional timezone, unchanged verdicts, complete available judge/transcript/ledger
and pins, operation durations, and partial failures. Known credentials and fresh
session capabilities are redacted before persistence. These ignored artifacts
may contain caller/transcript data and must not be committed.

A returning interrupted simulation retains existing handoff identifiers only.
The recorder neither retries API calls nor queries or replaces a lease. Process
termination can leave an incomplete `running` record, which assembly rejects.
HTTP operation timings are not claims of spoken-agent latency.

`python3.11 -m benchmarks.bundle` accepts original build manifest, export, JSONL
trace, build-provenance, and automatically recorded train/validation records.
It binds the original invocation/configuration/runtime-file snapshot, manifest,
trace hash, ordered mandatory tool-free audit and restricted catalog events,
selected export, original complete declared fixture inventory and actual
per-fixture/repetition calendar. It retains `build_provenance` in its output.
Missing historical evidence is rejected and never inferred from the current
checkout or file modification times. Python 3.11 and real controller mode are
required; injected/offline/mock builds and trial records are ineligible.

Route configuration is retained: default DNS/proxy mode or the optional explicit
bank connect address with the approved origin, Host/SNI, certificate/hostname
verification, no redirects/proxies/retries. Arbitrary injected transports cannot
produce an eligible bundle. Builder tests retain actual route metadata in their
original result/trace. Model subprocesses receive the existing environment
allowlist, which excludes `FREYA_BANK_CONNECT_ADDRESS` and all Freya credentials.

The comparison distinguishes complete failed null/null closing receipts from
missing or malformed evidence. Empty action histories remain failed behavioral
records; passing summaries cannot use them. Present partial closes, absent fields,
malformed arguments and incomplete captures remain incomparable.

Local tests invoke the actual hosted command, recorder, bundle command and
comparator with explicitly synthetic external-service doubles and original
synthetic build records. They verify production wiring without model/network
calls. Such tests do not authenticate live evidence. Historical provenance cannot
be recreated, and no source/test result establishes first-place readiness.
See [T15-DEVELOPMENT-EXECUTION.md](T15-DEVELOPMENT-EXECUTION.md) for the live sequence.

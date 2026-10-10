# T11 live acceptance lane

T11 remains open. The existing local/macOS and Docker evidence stops after
ClientHello; those environments share upstream egress. An independent Europe
West executor has now verified bank TLS and one pristine practice-session
retrieval. Fresh repeated runner compatibility with a pinned published agent
still requires actual evidence.

## Verified independent connectivity and pristine retrieval

The original capability-free evidence is retained in
[`network-evidence/20261011-europe-west`](network-evidence/20261011-europe-west/).
At `2026-10-10T22:02:35Z`, the disposable personal 2-core Europe West Codespace
verified bank and control TLS with public CA trust, chain validation, SNI and
matching hostnames. The bank resolved to `104.21.31.48` and `172.67.174.249`;
the connection used the first address and negotiated TLS 1.3. This differs from
the local resolver's previously observed `188.114.*` addresses. It demonstrates
independent reachability, without diagnosing the cause of the local failure.

At `2026-10-10T22:04:54.182151Z`, the unchanged `t11_bank_probe.py` passed its
verified-log prerequisite and made exactly one documented `PUB-01` session POST
and one ledger GET. The result is `pristine_ledger_verified`: all five required
collections exist as empty lists and outcome/reason are null. No retry, banking
action, persona, simulation or model call occurred. The random team namespace
and customer capability were held in memory and never retained. Freya
credentials were not transferred to the executor.

The executor used source `35403042d95519e78e5edcf842d7323abcf4d95d` and its
existing Python 3.14.2 interpreter. This is standard-library HTTP compatibility
evidence, not Python 3.11 builder-runtime acceptance. The retained provenance
records the exact helper-source and original evidence hashes. The TLS JSON
envelope contains the original log text byte for byte, including DNS-output
whitespace; decode its `text` field to inspect it or pass it to `verified_tls`.
Its raw SHA-256 is
`8aba53ac948653eda990d7056ded2c131658809a17a148ee0a35a3ddb3314811`.

Existing included executor capacity and an unchanged enforced stop-at-budget
were checked before creation. No billing or permission setting changed. After
retaining the evidence, the exact disposable runtime was stopped and deleted;
GitHub confirmed deletion and an empty Codespaces inventory. This does not
prove a published agent conversation, repetition freshness, scoring or winning
readiness. Those remain separate rows below.

## Independent runner and single retrieval

The manually dispatched workflow
[`t11-connectivity.yml`](../.github/workflows/t11-connectivity.yml) uses a
standard `ubuntu-24.04` GitHub-hosted runner. GitHub provisions these Linux
runners as fresh Azure VMs, providing a candidate route independent of local
Docker Desktop. Actual bank reachability still requires the recorded TLS
result. [GitHub runner documentation](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners)

Actions is enabled for this private repository, with no previous workflows or
runs observed during setup. Private repositories consume account-wide included
minutes and can incur overage. The existing token cannot read account billing
usage (404 with a required `user` scope). Included no-charge capacity must be
confirmed before dispatch; this lane changes no billing setting, provisions no
service and buys nothing. No Actions job was dispatched for this evidence;
the independent Codespace lane above supplied the verified connection.
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

The workflow has no push/PR/schedule trigger, uses a pinned checkout action,
reads the selected commit with `contents: read`, disables persisted checkout
credentials, and loads no repository/model/platform secrets. Its three-minute
job and one-minute operation steps bound the runner. Existing
[`t11-connectivity.sh`](../tools/t11-connectivity.sh) performs one unauthenticated
DNS/TLS attempt for each documented bank and Cloudflare control hostname,
with public CA trust, SNI, chain and hostname verification. Individual
`tls_exit` values determine reachability; a green job alone proves nothing.

The workflow must first be merged onto the default branch for GitHub to expose
manual dispatch. Dispatch explicitly at the checked branch/ref and verify the
resulting run's exact `head_sha`; do not assume a moving main is the tested head.
The optional retrieval step uses an official commit-pinned Python 3.11 setup.
[Manual dispatch documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

First dispatch with `probe_session=false` and inspect the exact run's TLS logs.
Only after verified bank TLS succeeds, dispatch once with `probe_session=true`.
The optional [`t11_bank_probe.py`](../tools/t11_bank_probe.py) requires both
verified diagnostic blocks with matching certificate names (including a full
left-most wildcard matching exactly one label), then uses the unchanged
`HostedBackend` for exactly one `practice_session("PUB-01")` and one
`ledger(customer_id)`. The probe uses an ephemeral random team namespace in
memory; it does not transfer the primary team's credentials or load `.env`.
It creates no persona, deployment or simulation and performs no banking actions.

The state file is exclusively created and flushed before POST. Existing state
refuses another POST, including after an interruption. Capability-free pending
evidence is also flushed to retained job logs before the request, and an
`always()` step retains the final state in logs. No artifact-storage upload is
used. Transport/JSON/HTTP failures preserve unknown creation persistence,
their phase, fixed error type and HTTP status when known; no write or read is
retried. Raw exception strings, response fields, keys, customer IDs, session IDs
and team keys are never printed or persisted.

**A new GitHub job has a fresh disk. Its state file does not globally deduplicate
separate dispatches.** Inspect all prior probe job logs and reconcile attempted
creation before any rerun. An absent final state or missing response never
proves that creation failed. Do not rerun an ambiguous POST automatically.
The random team key and capabilities disappear with the runner, so sanitized
logs alone cannot recover an ambiguous creation by querying the bank. An
unknown creation must block another probe POST until organizer/server evidence
resolves it; a new runner or state filename is not reconciliation.

Pristine retrieval requires the organizer scorer's five collections (`calls`,
`promises`, `notes`, `callbacks`, `transfers`) to exist as empty lists, and
`outcome`/`reason` to exist as null. Evidence contains only fixed field shape
booleans and counts. A different/missing shape is retained as incompatible or
unproven, not silently accepted. This establishes at most single-session
retrieval compatibility, not conversation success or all T11 criteria.

Local use is inspection-only by default:

```sh
python3.11 tools/t11_bank_probe.py
# Only on a reachable, authorized independent executor, with a new state path:
python3.11 tools/t11_bank_probe.py --execute \
  --verified-tls-log /tmp/t11-tls.txt --state /tmp/t11-session-attempt.json
```

## Shared lease and compatible pinned agent

A read-only request under the canonical shared lock confirmed the existing
simulation `03229c8e-5d0c-41fd-8179-35a50c6cc43e` still has actual server status
`completed`, matching its lease. Its iteration is
`ca839f71-ddae-40ae-8e56-9cfaeed37a34`; `creation_pending=false`.
The lease was not changed and no simulation POST was made. Local sanitized
evidence is retained at the primary repository's
`artifacts/t11-live-acceptance/lease-reconciliation.json`.
This observation must be repeated with primary coordination immediately before
any new live simulation, because another lane can acquire the lease later.

All simulation clients in all worktrees must explicitly receive
`/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock`.
At the inspected starting source, `benchmarks.hosted.main` derives a local
worktree lease, so do not run that CLI unmodified from an isolated checkout.
Coordinate a shared adapter change in issue12, or inject the canonical lease
into a dedicated invocation. Never delete an active/paused/unknown lease.

A safe compatible published test agent with a fixed `experimental-5` model and
verified version can establish runner compatibility. T11 depends on T03, not
T05; it need not wait for a successful generated banking candidate or winning
conversation behavior. When evaluating a generated candidate, also verify its
original source/config, isolation audit, approved inputs, deadline and export
provenance. Do not merge an evaluator branch's inherited unfinished builder
source as part of adapter acceptance.

## Acceptance evidence still required

| Criterion | Required actual evidence | Current lane evidence |
| --- | --- | --- |
| Bank reachability | Independent verified TLS bank/control logs | Verified Europe West bank/control TLS, original log retained |
| Untouched retrieval | One explicit PUB-01 POST and pristine GET | Exactly one of each; pristine fixed shape/counts verified |
| Fixture mapping | Distinct fixture aliases for one known bank seed, with matching personas | Existing local regression coverage only |
| Freshness/seed preservation | Multiple repetitions; fresh customer/session fingerprints and unchanged forwarded server facts | No banking simulation |
| Pagination | Complete observed inventory/total, with local multi-page regression; retrieve every page when the actual response paginates | Local 101-run regression; prior synthetic live inventory 1/1 unique call, one actual page |
| Bounded waits | Actual request/poll timing and retained terminal/interrupted evidence | Existing local deadline regressions only |
| Published version | Before/after publication pin plus matching positive integer actual judge/transcript version on every call | Prior synthetic call reports actual judge version 2, matching expected version; fresh pinned runner proof outstanding |
| Shared lease | Primary coordination and server reconciliation immediately before live use; retained IDs/status afterward | Read-only prior synthetic status confirmed |
| Compatible pinned agent | Published graph/model/version suitable for an adapter check; generated-candidate readiness is a separate gate | T03 synthetic publication evidence exists |

A one-page run does not prove an observed second-page server response, and that
limitation must be stated. It does not create a requirement to manufacture a
large live simulation before T11 closes. Local multi-page checks and complete
actual inventories cover the adapter's declared pagination deliverable; retain
any actual additional pages when supplied. Preserve raw evidence in private runtime storage;
sanitize before issue checkpoints. A pinned call with missing actual version
metadata fails evidence acceptance even if publication remains unchanged.

Close T11 only after this entire matrix is supported by actual compatibility
proof, required checks pass, and `tools/cowork.py complete` verifies the merged
matching-head PR. A focused runner/probe PR alone does not complete the issue.

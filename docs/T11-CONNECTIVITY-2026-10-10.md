# T11 alternate environment connectivity investigation

On 10 October 2026 at 23:36 Istanbul (20:36 UTC), bounded, unauthenticated
checks reproduced the bank TLS blocker in a disposable Linux container.
No confirmed application or adapter defect was found. T11 remains open.

The documented origin is
`https://freya-bank-mock.freyavoice.workers.dev`. Source inspection confirmed
that the existing `benchmarks.hosted.HostedBackend` supports the requested
`practice_session("PUB-01")` followed by `ledger(customer_id)`. Its allowlist
uses `POST /v1/practice/sessions` and `GET /v1/practice/ledger/{customer_id}`.
Neither request was made because the unauthenticated TLS prerequisite failed.

## Environment and evidence

Docker Desktop provided Linux 6.10.14-linuxkit, aarch64, and OpenSSL 3.0.17.
The cached official `postgres:15.14-bookworm` image supplied diagnostic tools;
its database entrypoint was replaced with `/bin/sh`. The container was
disposable, read-only, unprivileged, resource limited, and received no credentials
or repository mounts. No image was pulled, unrelated container accessed,
remote service provisioned, or purchase made.

This is a different operating system and TLS implementation from the reported
macOS Python/curl checks. Docker Desktop shares the host's upstream network;
these results do **not** establish an independent egress-network test.

| Check | Bound | Observation |
| --- | --- | --- |
| Bank IPv4 DNS | 5 seconds | Success; `188.114.96.3`, `188.114.97.3` |
| Bank IPv4 TCP/TLS | 8 seconds | OpenSSL wrote ClientHello after connecting; no ServerHello was reported; timeout exit `124` |
| Control IPv4 DNS (`www.cloudflare.com`) | 5 seconds | Success; `104.16.124.96`, `104.16.123.96` |
| Control TLS with public CA bundle | 8 seconds | TLS 1.3; hostname and chain verified; exit `0` |

Each TLS check used SNI, hostname verification, certificate verification, and
an external process deadline. Only the resolver-selected IPv4 address was used
by each TLS attempt; no per-address sweep or IPv6 trial was performed. No HTTP
request or bank route probing occurred in these checks.

The initial control received a certificate but failed local issuer validation.
Repeating with the host's public `/etc/ssl/cert.pem`, mounted read-only, resolved
that diagnostic environment limitation. Bank behavior remained a ClientHello
timeout. The repeat was needed to obtain a verified positive control and expose
handshake progress; it was not a session-creation retry.

The separate server-side web fetcher also reported the origin inaccessible.
That generic result exposes no DNS/TCP/TLS details and is not used as evidence
of the failing layer. Tool discovery found no connected remote executor;
available unconnected provisioning integrations were not used.

Sanitized logs (trailing line whitespace removed; observations unchanged):

- [Initial Linux checks](network-evidence/t11-linux-20261010.txt)
- [Linux checks with explicit CA trust and handshake states](network-evidence/t11-linux-verified-20261010.txt)

## Reproduction

The unauthenticated script is [tools/t11-connectivity.sh](../tools/t11-connectivity.sh).
With the cached image and a public CA bundle at the stated path:

```sh
docker run --rm -i --pull=never --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges --pids-limit=32 --memory=128m --cpus=0.5 \
  --user=65534:65534 \
  --mount type=bind,src=/etc/ssl/cert.pem,dst=/diagnostic-ca.pem,readonly \
  --entrypoint=/bin/sh postgres:15.14-bookworm -s < tools/t11-connectivity.sh
```

The script makes one DNS and one TLS attempt per hostname; the two per-host
deadlines sum to 26 seconds. Container startup and teardown are outside those
bounds. If no explicit bundle is readable, it reports that fact and uses
OpenSSL's default trust store. TLS verification is never disabled.

## Interpretation and handoff

The evidence narrows this environment's failure to TLS after the TCP connection
and ClientHello. DNS resolution, TCP connection, and successful TLS to a separate
Cloudflare hostname are insufficient to prove this bank origin reachable.
The bank deployment, hostname-specific filtering, and shared upstream path
remain possible causes. No response from the bank application was obtained,
so no API compatibility defect or server outage is confirmed.

Exactly zero sessions were created and zero ledgers retrieved. No keys were
loaded or sent, session/customer capabilities produced, banking-action writes
made, personas created, agents changed, or simulations started. No deployment
or simulation lease is held by this investigation. `benchmarks/hosted.py` is
unchanged; coordinate in issue #12 before proposing a shared adapter edit.

The next useful check is the same bounded unauthenticated test from genuinely
independent egress. Only after verified bank TLS succeeds, use the existing
backend to create exactly one PUB-01 session and read its untouched ledger.
Keep keys and all returned session/customer capabilities in memory, retain only
sanitized status/count evidence, and do not retry an ambiguous session-creation
failure automatically. A pristine ledger proves retrieval compatibility, not a
completed conversation or T11's broader simulation/version acceptance.

Starting checkpoint: main `b9903a58c6723e1b4f1f97ab81d60fe22754c28b`.
Diagnostic branch: `task/t11-connectivity-20261010`.
Coordination: [issue #12](https://github.com/DemirEkinArikan/freya_hackathon/issues/12#issuecomment-6101920898).

Verification: Python 3.11 full suite passed all 612 tests; shell syntax check,
secret-pattern checks on the diagnostic artifacts, and `git diff --check`
passed. The unit tests are local regression checks and do not establish live
bank compatibility.

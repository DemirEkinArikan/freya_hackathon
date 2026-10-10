# Optional bank connection address

`benchmarks.bank_transport.BankConnectTransport(bank_origin, connect_address)`
is an opt-in callable for `HostedBackend(opener=...)`. The origin comes from the
approved bank API. An operator supplies a literal IP address independently of
brief policy; there is no default address in source. Without this configuration,
the existing native connection behavior remains available.

Only the TCP destination changes. The original HTTPS URL, request method/body,
HTTP Host and TLS SNI remain intact. Default public CA and hostname validation
are required. Requests to another origin, Host replacement, redirects, proxy
tunneling and implicit retries are refused. No global DNS or opener changes.
Timeouts remain the caller's bounded allocation, and errors propagate for the
existing retained-infrastructure evidence handling.

The configuration method returns the original hostname/port, explicit address
and verification/redirect/proxy/retry settings for original invocation evidence.
It contains no credentials. Controller configuration must record this choice
and exclude it from generation inputs. It grants no bank action or simulation
permission and does not change workflow tool URLs.

Eleven regressions drive the real Python HTTP stack with fake sockets, proving
exact bytes, hostname/SNI, CA settings, destination routing, redirects, proxies,
ports/IPv6, request-dispatch mutations, hidden conflicting Host headers and
single-attempt failure handling. A detached request snapshot preserves effective
headers while preventing mutable dispatch fields from bypassing HTTPS. They do not claim a fresh live
bank request. Earlier T11 live evidence verified the same confined-routing
principle; a new actual run must retain its own source/configuration evidence.

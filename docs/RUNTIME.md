# Reproducible runtime and isolation handoff

Use a Python 3.11 installation on macOS/Linux or WSL. `.python-version` and
`pyproject.toml` agree on the supported minor version. The implementation has
zero third-party Python dependencies; no package installation is needed.

```sh
python3.11 --version
python3.11 -m venv .venv
.venv/bin/python -m unittest discover -s tests -v
python3.11 tools/verify_kit.py
codex --version
codex login status
python3.11 -m builder.preflight --operator demir
```

Naci uses `--operator naci` on his own computer. The operator field is a
self-attestation label, not inferred account identity. Sign into Codex using
your own account if necessary; do not exchange auth files, tokens or account
credentials. The preflight invokes `codex login status` and retains only the
authentication method and pass/fail. It never opens account/config files, `.env`,
or sibling repositories. A status check alone cannot establish who owns an
account or prove that a second computer works.

For the live generation-only audit, explicitly run:

```sh
python3.11 -m builder.preflight --operator demir --probe-isolation \
  --output artifacts/runtime-demir.json
```

This sends only a synthetic audit prompt through the existing Codex sign-in,
never initializes Freya, and has a 120-second process-group timeout. The child
uses the same restricted argv/environment as generation. Random canaries live
in temporary ancestor `AGENTS.md`, project skill metadata/body, project config,
a forbidden sibling file, and an environment variable. The prompt gives the
synthetic forbidden file's path but never its contents. Only this probe's
fixtures are created/read by the local harness. All fixtures are removed after
the invocation.

The report retains tool names from a fixed known-name allowlist, counts unknown
names, observed event types, warning categories, exposed canary categories and
failure reasons. Raw model events, responses, stderr, fixture values, account
identifiers and host paths are discarded. Reports stay under ignored
`artifacts/`; only reviewed metadata summaries belong in task handoffs.

Generation now audits before sending a banking brief. Any observed tool event,
reported available tool, leaked canary, unknown diagnostic, invalid response or
nonzero subprocess result fails closed. A skill-budget warning with a nonzero
omitted-skill count also blocks generation: removing descriptions from context
is insufficient evidence that host/ancestor discovery was disabled. There is
no production bypass flag. Offline validation remains available.

Tool names and loaded-context claims are model self-reports. Canary matching
and emitted event checks are independent evidence, but a negative result is
not an exhaustive loader audit or an independent test of OS path denial. If
the child exposes no read tool, it cannot actually issue a file read; record
that limitation instead of claiming an observed kernel denial. CLI acceptance
depends on both documented settings and observed behavior; flags listed by
`--help`/`features list` alone are not isolation proof. See the official
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
and [security documentation](https://learn.chatgpt.com/docs/security).

## Evidence on 10 October 2026

The local host passed Python 3.11.14, Codex CLI 0.162.0-alpha.2, ChatGPT sign-in
and required capability checks. The initial live probe exposed no random
canary and emitted no tool event, but the model reported 17 available tools,
including apply_patch, image generation, clock, goals and collaboration.
The CLI also reported five omitted skills despite host discovery suppression.
This result fails the isolation gate; it is not evidence of no tool/context
exposure. The sandboxed invocation initially could not initialize the CLI's
in-process app-server; the live run used an approved execution escalation while
retaining the restricted child permission profile.

Additional feature controls disable image generation, goals, sleep,
code-mode hosting and the newer multi-agent feature; `agents.enabled=false`
and `apps._default.enabled=false` reinforce the existing feature disables.
An attempted `tools.view_image=false` setting was rejected by this CLI's strict
config check and removed. The final hardened live probe returned a structured
response and no random canary/tool event, but still reported four function
tools (`exec`, `wait` and two request-input variants), five omitted skills and
an unrecognized CLI diagnostic. Its bounded report is recorded in
[local audit evidence](runtime-evidence/demir-20261010.json). It fails closed;
this installed CLI is not currently accepted for banking generation.
A passing report from each collaborator's own
computer and a successful live isolation audit are still required for T02
acceptance. Do not close the task based on the local implementation or unit
checks alone.

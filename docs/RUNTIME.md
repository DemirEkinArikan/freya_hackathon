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
failure reasons. A follow-up diagnostic aid retains up to eight unique unknown
CLI diagnostic messages, bounded to 2,000 characters each. Complete strings
are scrubbed for known environment credentials, credential patterns and probe
canaries before clipping, common host paths are masked, and a secret check runs
before retention. Unknown diagnostics still fail the audit. Raw model events,
responses, stderr and fixture values are discarded. Reports stay under ignored
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

## Source controls follow-up

Generation and the synthetic probe now also apply these documented settings:

```text
skills.bundled.enabled=false
skills.include_instructions=false
cloud.skills.enabled=false
tools.experimental_request_user_input.enabled=false
tools.update_plan.enabled=false
```

Both `code_mode` and `code_mode_only` join the feature-disable list and required
CLI capability checks. The five settings are defined in the official
[configuration schema](https://learn.chatgpt.com/docs/config-schema.json).
They disable bundled/cloud skill sources, automatic skills instructions, and
user-input/planning tools at their own configuration points; the existing
host-discovery, app/plugin, permission, environment, and strict-config controls
remain in place. The skills context budget remains a secondary limit and is
not used as evidence that discovery is disabled.

The installed binary's bundled `gpt-6.1-sol` model metadata specifies
`tool_mode=code_mode_only`, `use_responses_lite=true`, and
`node_repl_disabled=false`. Local feature acceptance does not establish that
these controls override the effective model/harness tool surface. An offline
strict-config check with an intentionally undefined provider accepted the five
settings and exited before a thread/model call; this verifies configuration
shape only. The mandatory live synthetic audit still decides whether this
installed runtime is eligible for banking generation. No bypass or automatic
model substitution is provided.

One authorized follow-up probe using these controls completed on 10 October
2026. Local Python, CLI, sign-in and capability checks passed. The probe emitted
one completed turn, a valid structured response, no random canary exposure,
and no tool event. The omitted-skill count fell to zero and the standard
`functions.request_user_input` disappeared from the reported tool list.
However, the model still reported `functions.exec`, `functions.wait`, and
`functions.request_user_input_async`, and an unknown CLI diagnostic remained.
The result **fails the unchanged isolation gate**. Its reviewed metadata is in
[follow-up audit evidence](runtime-evidence/demir-isolation-controls-20261010.json).
The absence of a skill-budget warning is not an exhaustive loader proof.

All 274 local tests passed after this argv change, including capability rejection
before any probe/brief when either new feature control is unavailable. These
mock checks do not override the failed live audit. No banking brief, Freya
request, purchase, or default-model change occurred. That checkpoint used one
live probe; the later GPT-5.5 probe below required separate authorization.

## Explicit GPT-5.5 candidate probe

After the hardened GPT-6.1 Sol probe failed, one separately authorized synthetic
probe explicitly selected `--model gpt-5.5`, reasoning effort `high`, and the
same 120-second timeout and hardened controls. This changed neither the global
builder default nor the kit's phone-agent model. The installed public catalog
lists GPT-5.5 with no forced `tool_mode`, `use_responses_lite=false`, and no
experimental tool list; this made it a candidate, not proof of isolation.
Official documentation lists its ChatGPT-sign-in Codex retirement on
[14 October 2026](https://learn.chatgpt.com/docs/models#gpt-55-retirement).

The candidate probe **also failed**: it returned a valid structured response
and one completed turn, but reported two unrecognized tool names and nonempty
loaded markers. The privacy filter discarded those raw strings and retained
only counts. No random canary exposure, emitted tool event, omitted skill, or
unknown CLI diagnostic was observed. See the
[GPT-5.5 audit evidence](runtime-evidence/demir-gpt55-isolation-20261010.json).
All 276 local tests pass after the bounded-diagnostic change. No substantive
banking prompt, Freya call, purchase, or additional model probe followed this
failure. No currently accepted Codex generation candidate has been established.
After integrating main `815ea19`, the isolated capability checkpoint passed all
279 local tests and `git diff --check`; the failed live audit status is unchanged.

## Generation event completion

Both policy and restricted-plan generation now require a valid JSONL lifecycle:
one `thread.started` with a nonempty thread identifier, one `turn.started`, and
exactly one `turn.completed` as the final event. A zero process exit is
insufficient. Malformed JSON, duplicate keys, nonfinite values, unknown event
types, repeated/out-of-order starts, incomplete turns, and events after
completion fail closed. The precise host-discovery experimental warning remains
accepted; unknown error diagnostics and all tool-action items remain rejected.

Only the final completed agent message is parsed for the requested stage
contract. Started/updated messages, earlier commentary, and earlier valid
candidates cannot replace an incomplete or invalid final response. These checks
preserve the strict policy/plan schemas, frozen contract fingerprint, process
deadline and reserved export time. They do not authenticate arbitrary replayed
event streams independently of the CLI; each invocation still uses a new
ephemeral generation process and the mandatory isolation gate.

This completion fix is verified with injected local event streams only. No
additional model probe or Freya call was made, and the failed isolation evidence
continues to block substantive generation.

Each actual generation stage also rejects a precisely recognized skills-budget
warning when its omitted-skill count is nonzero, matching the preflight audit.
A cached passing audit cannot authorize loader discoveries in a later fresh CLI
process. The trace retains the warning category and count. Count zero and the
exact host-discovery experimental warning remain accepted; zero alone is not
an exhaustive loader proof. A local regression exercises a successful first
stage followed by a rejected second stage under the same cached audit.

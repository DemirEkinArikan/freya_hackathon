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
python3.11 -m builder.preflight
```

The active environment is sufficient; there is no Demir/Naci identity check,
account-ownership attestation or second-computer prerequisite. Optional
`--operator <label>` is descriptive metadata only. The preflight invokes
`codex login status` solely to check that the CLI can authenticate and retains
only the authentication method and pass/fail. It never opens account/config
files, `.env`, or sibling repositories. Authentication files, tokens and
credentials stay private. A status check does not identify the person using
the session, and the builder does not require it to do so.

For the live generation-only audit, explicitly run:

```sh
python3.11 -m builder.preflight --probe-isolation \
  --output artifacts/runtime-active.json
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

## Accepted active runtime — 10 October 2026, 19:16 Istanbul

Python 3.11.15, Codex 0.162.1 and the existing ChatGPT sign-in now pass the
synthetic audit with `gpt-6.1-sol` / high reasoning. The baseline on the same
machine failed with three reported Code Mode tools and a host-disabled
diagnostic. After the scoped catalog restriction, the model reports **zero
tools**, no canaries, no omitted skills and no unknown diagnostics. Synthetic
policy and plan wire-format calls also pass through the real generator, with
its mandatory audit enabled. Reviewed before/after metadata and code hashes:
[acceptance evidence](runtime-evidence/naci-restricted-catalog-20261010.json).
The 589 local tests, 16-file kit pin and source package check pass.

Feature switches do not by themselves remove model-owned tool defaults.
The controller now reads `codex debug models --bundled` from the installed CLI,
selects the exact model (no guessed fallback), and creates an ephemeral
`model_catalog_json` for each generation process. It preserves model identity,
instructions, approval policy metadata and all non-tool settings. Only these
capabilities are narrowed:

| Catalog setting | Generation value |
|---|---|
| `shell_type` | `disabled` |
| `apply_patch_tool_type` | `null` |
| `experimental_supported_tools` | `[]` |
| `node_repl_disabled` | `true` |
| `tool_mode` | `direct` |
| `supports_search_tool` | `false` |

The official [catalog configuration documentation](https://learn.chatgpt.com/docs/enterprise/roll-out-a-gateway#use-a-model-catalog-for-custom-names)
describes per-process catalogs and model tool capabilities. The exact fields
come from the installed version's [model metadata types](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/protocol/src/openai_models.rs).
This uses the same model and provider; no gateway or new API credential is
introduced. Installed catalogs, user configuration and the developer's normal
Codex tools are unchanged. Temporary catalogs are removed with each workspace.

The independent audit and generation must have identical catalog fingerprints.
Unknown/missing models, malformed catalogs, catalog drift, observed tool events,
reported tools, loader evidence and unknown diagnostics still stop before the
banking prompt. Probe JSON also rejects duplicate keys, malformed item types,
missing/repeated turns and unfinished final messages. No acceptance failure was
whitelisted to obtain this result. Each new builder instance audits again;
a previous report is not a bypass or acceptance of every CLI version/model.

This satisfies the active-environment T02 runtime checks. It does not prove
kernel file denial, a complete banking build or hosted behavior. The next step
is a fresh guarded public-brief build; Freya deployment/simulation retains its
separate authorization and evidence requirements. Direct API/provider spending
is no longer a prerequisite for this tested Codex path.

## Independent tool-registration check before merge

`model_reported_tool_count` is the length of the model's own `available_tools`
answer, not a direct registry measurement. The live canary/event checks remain
useful, but that number alone does not prove disabled tools.

A separate local CLI experiment removes the model from this question:

```sh
python3.11 tools/verify_generation_tools.py --output artifacts/t02-tool-registry.json
```

This launches the installed Codex with the production generation arguments and
catalog, but an empty temporary `CODEX_HOME` and a loopback-only stub provider.
It does not forward requests upstream or use account credentials. It records
only tool names/counts, known rejection diagnostics and synthetic side effects.
The model response is scripted, so the measured tool counts cannot originate
from an LLM's claim. It inspects both ordinary request `tools` and the
`input[].additional_tools` representation used by this installed model.

| Measured case | Codex 0.162.1 result |
|---|---|
| Same feature controls with original bundled catalog | 3 request tool definitions: `functions.exec`, `functions.wait`, `functions.request_user_input_async` |
| Restricted generation catalog | 0 request tool definitions |
| Stub forces a custom `apply_patch` call | `unsupported custom tool call: apply_patch`; no file created |
| Stub forces an `exec_command` read of a synthetic sibling sentinel | `unsupported call: exec_command`; no sentinel exposed |

`request_tool_count` measures outgoing definitions; `model_reported_tool_count`
is the separate live self-report. The positive control is mandatory: if the
unrestricted catalog also reports zero outgoing tools, the local test fails
instead of granting acceptance. Forced calls must produce actual unsupported-
tool responses. Changing a generated answer to say "no tools" cannot pass these
checks. [Reviewed local transport evidence](runtime-evidence/naci-tool-registry-20261010.json)
records the same restricted catalog fingerprint as the live audit and smoke.

These observations establish more than model self-report, but remain scoped:
the local provider/auth home differs from the real ChatGPT invocation, only two
forced tool names are exercised, and this is not an independent OS filesystem-
denial test or an exhaustive audit of Codex. The separate live ChatGPT audit and
both generation stages also passed. Each new builder instance audits again.

The latest T02 checkpoint's Python/auth/isolation requirements are satisfied in
this tested active environment. Direct API provider/account/spending-cap setup
was not performed; it is unnecessary for the verified existing-Codex path.
Commit, push, same-repository PR merge and completion evidence are still pending
user direction. No banking build or Freya trial is claimed by T02 acceptance.

## Historical evidence on 10 October 2026

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
The current generation policy still requires a successful isolation audit in
the active environment. No report from another collaborator or computer is
required. Historical reports below retain their original attestation fields
as evidence of earlier runs; those fields impose no current prerequisite.

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

Generation and probes also explicitly disable the documented stable
`unified_exec` feature alongside `shell_tool`, and require that capability to be
recognized before any probe or brief is sent. This selects the PTY-backed exec
tool off; it does not prove that all shell or patch tools disappear. The removed
`apply_patch_freeform` flag is unchanged, and no model catalog metadata is patched.
The fixed diagnostic-name allowlist now recognizes the public aliases
`functions.exec_command`, `functions.shell_command`, `functions.apply_patch`,
and `functions.write_stdin`. These names can be displayed in failure metadata;
all nonempty reported tool lists and actual tool events still fail isolation.
Unrecognized names and raw model response data remain excluded. This additive
change is verified with local mocks only, with no additional live probe.
The tool-free runtime remains unproved and substantive generation remains gated.

## Earlier parallel probe archive

The parallel thread also recorded two synthetic-only failures before the later
source-controls and GPT-5.5 probes above. Added feature/namespace disables under
CLI 0.162.0-alpha.2 still reported four tools, five omitted skills and an unknown
diagnostic. The existing app-bundled CLI 0.158.0-alpha.2.1 returned no complete
structured audit response, exited unsuccessfully and reported six omitted
skills. Neither run exposed a random canary or emitted a tool action.

[Reviewed historical metadata](runtime-evidence/demir-20261010-followup.json)
preserves those results and the exact experimental controls. They do not replace
the production configuration or newer evidence. No new probe was run while
archiving this metadata, and neither result authorizes banking generation.

## Current controls audit at 17:43 Istanbul

A fresh synthetic audit under CLI 0.162.0-alpha.2 and GPT-5.5 tested the current
restricted argv, including `unified_exec=false`. Python, CLI authentication and
required capabilities passed. The audit completed successfully as a process and
reported zero exposed canaries, zero omitted skills, zero unknown CLI diagnostics
and no tool action. It still reported two available tools, including
`functions.apply_patch`; the other name was excluded from retained diagnostics.
The audit therefore fails the unchanged generation-isolation gate. No banking
brief was sent and no Freya call was made by this audit.

[Reviewed audit metadata](runtime-evidence/current-controls-20261010.json)
records the source commit, model and controls. No named worker, account-ownership
attestation or second-computer report is required for acceptance. A passing
generation-isolation check remains necessary.

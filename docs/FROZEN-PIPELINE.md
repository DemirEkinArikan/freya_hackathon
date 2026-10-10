# Frozen policy and guarded generation

The default builder extracts policy once, resolves amendment precedence against all
source fingerprints and the supplied OpenAPI, then freezes its contract. Explicit
`--policy-file` resumption revalidates that policy against current inputs. A shared
30-minute deadline covers isolation auditing, extraction, generation, bounded
repairs and export; 300 seconds remain reserved for export.

Subsequent fresh generation calls receive resolved active values, the permitted
operation schemas and the restricted plan interface. They return only a plan and
the exact frozen contract hash. Replacement policy and changed hashes are fatal.
Repairs receive a previous plan and sanitized violations, never a compiled action
kernel, credentials, evaluator scripts, expected ledgers or historical rule quotes.
Policy extraction and plan repair share a maximum of two repairs. Supported
execution shapes are checked before freezing. If a proposed plan needs a missing
typed rule, the run records a frozen-contract failure without spending plan
repairs or rewriting bank requirements.

The controller recompiles action bindings and typed execution profiles from frozen
inputs, builds protected action nodes and validates the exact workflow through a
trusted guard context. Model-authored code, APIs, tools and terminals are refused.
Artifacts include policy, contract, plan, execution profile, API action bindings,
guard manifest, workflow and sanitized trace. Before publication the authored form
is checked; read-back must match the exact controller-authenticated definition,
excluding only the adapter-owned agent_config. Export uses the platform snapshot.

`action_guard_compilation_verified` establishes deterministic compilation and
structural integrity. It does not prove policy extraction completeness, dialogue
meaning, persuasion compliance, eventual closure or hosted behavior. Legacy
`--offline`/`--validate-only` inspection explicitly reports this claim as false.
Do not treat a structural check as a completed conversation benchmark.

Every builder instance retains the mandatory synthetic isolation check before
substantive generation. The active Codex 0.162.1 / GPT-6.1-sol environment now
passes with a per-process restricted catalog; the live audit, synthetic policy
and plan calls, and independent local tool-registration checks are recorded in
[RUNTIME.md](RUNTIME.md). Historical failed probes remain preserved. A runtime
pass does not establish a successful full banking build or generated-agent
Freya deployment. Do not weaken the gate or patch generated policy to fabricate
a passing build.

Some bank requirements may have no supported typed profile yet. Keep those
constraints and fail closed rather than manufacture values. Nonpersistence needs
an authoritative supplied API guarantee; a matching status or model assertion by
itself is insufficient. The extraction provenance format currently cites supplied
brief documents, so an API-only guarantee needs an explicit auditable representation
before authorizing retries from that source.

No Freya trial, purchase or private Launchway input is part of local verification.

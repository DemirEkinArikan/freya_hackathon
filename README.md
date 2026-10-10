# Adaptive banking builder — Build the Builder v3

The objective is first-place competitiveness through unfamiliar-policy
adaptation, reliable conversations and reproducible builds. This is a standalone
hackathon implementation: fresh prompts, orchestration and domain logic, with
no Launchway runtime, private prompts, clients or integrations imported.

**Shared work:** [parent issue and 18 task issues](https://github.com/DemirEkinArikan/freya_hackathon/issues/1).
Demir (`DemirEkinArikan`) currently continues alone; Naci (`burhan-launchway`)
can join later. Collaborator participation and identity are not prerequisites.
See [current evidence and remaining readiness gates](docs/READINESS.md) before
claiming that the builder is ready for competition.

## Setup

Use Python **3.11** on macOS/Linux (or WSL), `gh`, and an authenticated
Codex CLI. Python dependencies are standard-library only. Codex must support the
isolation controls checked by the runtime; unsupported versions fail closed.
The current default builder model is the locally configured `gpt-6.1-sol`, with
high reasoning. Pin a model you can access using `--model` or `BUILDER_MODEL`.

```sh
python3.11 --version
codex login status
python3.11 tools/verify_kit.py
python3.11 -m unittest discover -s tests -v
python3.11 -m builder.preflight
```

Follow [runtime setup and isolation verification](docs/RUNTIME.md) in the active
builder environment. `--operator` is an optional informational label, with no
person/account verification. `--probe-isolation` explicitly calls the existing
authenticated Codex session with synthetic fixtures and writes only bounded, sanitized
metadata. Every real generation performs this audit before sending its brief;
exposed tools, canaries or unresolved skill loading block generation.

The builder reads this repository's `.env` as plain data and preserves existing
process variables. It rejects shell expansion and symlinks; it never reads a
sibling project's credentials. Configure the variables in `.env.example`.
Staging operations need
`FREYA_API_KEY`; hosted evaluation additionally needs one stable
`FREYA_BANK_TEAM_KEY` of at least 16 characters. Share team credentials privately,
never in an issue. Authentication files and credentials stay outside source
and task comments; no collaborator account-ownership proof is required.

## Build interface

Ordered briefs and their matching OpenAPI are explicit inputs. Brief B is an
overlay of A, so pass both for the public multi-product build:

```sh
python3.11 builder/build.py \
  --brief build-the-builder/briefs/brief-a.md \
  --brief build-the-builder/briefs/brief-b.md \
  --tool-api build-the-builder/tools/openapi.json \
  --generate-only
```

`--generate-only` calls the existing Codex account and performs local validation;
it never initializes a Freya client. `--offline --workflow-file <candidate.json>`
and `--validate-only --workflow-file <candidate.json>` use neither a model nor
network. An offline candidate may be `{policy, workflow}`, a platform export, or
a bare workflow with optional `--policy-file`.

Omitting those flags runs generation, staging validation, persistent save,
publication, read-back and export. Add `--test-cases` only when explicitly
executing hosted checks; evaluator scripts/oracles never enter generator input.
Use the A/B subset files under `benchmarks/fixtures/` for the corresponding brief.

The controller reserves five minutes of its 30-minute total budget for export.
Generation has bounded subprocess time and at most two repairs. Every run writes
its policy, workflow, manifest and redacted start/end trace under `artifacts/`.
Generated definitions omit `agent_config`; the adapter preserves the event
model, verifies publication and rejects platform stripping of authored fields.

Use `--extract-policy-only` to exercise brief interpretation independently of
workflow generation. It emits a strict source/API contract with a recorded hash,
creates no workflow and initializes no Freya client. Every accepted candidate
with policy writes that same contract artifact. See [policy contract boundary](docs/POLICY-CONTRACT.md)
for source coverage, explicit amendments and the limits of these checks.

## Benchmarks and evidence

```sh
python3.11 -m benchmarks.offline --split validation --check-helpers
python3.11 -m benchmarks.hosted --dry-run --split all
```

There are two distinct 120-slot manifests, each split 36 training / 24 validation
/ 60 held out. The first 60 fixtures are concrete. The other 60 are deliberately
reserved until a candidate is frozen; they are not completed tests or blind
evidence. See [benchmark lifecycle](benchmarks/README.md) for generation and
consumption commands.

Local fixtures assess policy contracts, calculations and action invariants.
Hosted variants use unique fixture IDs mapped to the 13 known bank seeds. They
vary caller behavior while preserving server account/identity facts. They do not
establish new-policy generalization. Execution requires the explicit `--execute`
flag; dry-run does not create personas or simulations.

The scorer requires exact ledgers, one final close, identity ordering/counters,
no transfer notes, bounded write retries, complete judge evidence and all
requested repetitions. Transcript honesty cannot be proved from a ledger alone.
Keep infrastructure failures and original evidence when retrying. The platform
allows one simulation at a time; the kit's within-simulation concurrency is five.

## Coworking and offline takeover

```sh
python3.11 tools/cowork.py status
python3.11 tools/cowork.py claim T04 --branch task/t04-policy-contract --commit <pushed-sha>
python3.11 tools/cowork.py checkpoint T04 --branch <branch> --commit <pushed-sha> \
  --done 'completed work' --remaining 'next steps' --offline
python3.11 tools/cowork.py complete T04 --commit <pushed-sha> \
  --pr <merged-pr-number-or-url> --checks 'command and result'
```

Any repository user authorized by GitHub can claim ready tasks. Actor names,
assignees and previous claims are recorded metadata, with no collaborator
whitelist, person-specific takeover or timed handoff. Check current branch
activity to avoid concurrent edits. Checkpoint and push every 15 minutes.
Claims require pushed commits. When a task's deliverable and checks are complete,
push the work, open a PR targeting `main`, and merge it directly without a separate
review step. Then run `complete --pr` with the merged PR number or its HTTPS URL.
The command verifies that the PR's base and head belong to this repository, it
was merged into `main`, and its head matches the pushed completion commit before
posting evidence or closing the task. It does not merge PRs. Completion unblocks
dependencies; returning workers check ownership before resuming. Preserve another
worker's branch.

Record workflow/agent/version and running simulation IDs in the task handoff.
A stale local lease is not evidence the remote simulation has ended: check
actual server status before starting another job. `create-board` is idempotent;
it preserves existing assignments, creates native sub-issues/dependencies and
refuses non-private repositories.

## Packaging and final window

```sh
python3.11 tools/package_submission.py --check
python3.11 tools/package_submission.py --source-only
```

Packaging uses an explicit allowlist, rejects symlinks/outside paths and scans
for credentials. Source-only packages are rehearsal artifacts, not submission
readiness. Actual packaging requires `--manifest` with two platform exports and
their selected successful live traces; hashes/identifiers must agree. Reviewed
final exports and traces must also be committed at the declared submission
commit, following the organizer's final instructions. Never blindly redact a
required tool credential and call the resulting export runnable.

Sunday **11 October, 20:00–23:00 Istanbul** is the hidden-brief window. Finish
broad evaluation beforehand, use general bug fixes only, and reserve the final
15 minutes for submission. Hidden-specific values belong in input/generated
artifacts, never builder source or static prompts.

See [pinned contract](docs/CONTRACT.md), [task definitions](docs/tasks.json) and
[contribution/handoff rules](AGENTS.md). The organizer kit is pinned by SHA-256;
review an emailed version update before repinning it.

# Evaluator-only banking benchmarks

The builder must not read this directory, customer scripts, oracles or results. Only selected policy documents and their OpenAPI may be supplied as explicit compiler inputs. All generated policy profiles are **invented synthetic test policy, not Freya's official policy**. Synthetic `.invalid` service URLs are never attached to live agents.

`catalog.json` contains 60 banking archetypes. `fixtures/offline.json` allocates 120 cases: 36 training cases, 24 validation cases and 60 reserved holdout slots. The first 60 have concrete account facts, identity data, customer turns/branches, ledger oracles, contract extraction expectations and independently generated calendar/floor/band expectations. Profiles vary identity schemas, floor field names, amounts, calendars, limits, bank-specific outcome/reason/note/queue codes, and call objectives. Clean specifications and raw prose/table/email/memo/code-list packets supply literal provenance. Holdout values do not exist until a candidate is frozen. These fixtures support local contract/helper checks; they are not evidence that a Freya conversation was executed or passed.

`canonical_expected` records logical action meanings. `expected` uses the bank's opaque wire codes in normalized ledger fields. `wire_codes` declares the mapping; enum-bearing contract rule values match the exact input API enums. Contact/callback, exception triage and commitment resolution are policy-defined objectives selected by the synthetic `call_goal` variable; they add no API operation.

`effective_values_expected` captures the current API-coded policy values before historical rules are added. It is an evaluator oracle, not compiler output. Raw packets retain the old security/deadline rules with explicit same-kind overrides from the effective memo. Current-policy, historical-policy, effective-memo, operations-mail and code-list all have typed rule provenance; forwarded customer complaints are not policy rules. Rule-list order never selects current values.

`fixtures/hosted.json` separately allocates 120 wording variants: 78 public seed/style combinations and 42 compound variants. It also starts with 60 materialized and 60 reserved slots. Training/validation subsets for each policy brief are available as separate files. Security questions back are restricted to after verification so they do not accidentally change decline outcomes. Scripts are audited by construction, not empirically verified against the persona model.

## Offline inspection and checks

```sh
python3.11 -m benchmarks.offline
python3.11 -m benchmarks.offline --split validation --check-helpers
python3.11 -m benchmarks.offline --check-helpers --check-equivalence
python3.11 -m benchmarks.hosted --dry-run --split train --brief A
python3.11 -m unittest discover -s tests -p 'test_fixture_contracts.py'
python3.11 -m unittest discover -s tests -p 'test_benchmarks.py'
python3.11 -m unittest discover -s tests -p 'test_scoring.py'
```

These commands use no network and spend no credits. `--check-helpers` strictly compiles each selected materialized case against its supplied profile documents and OpenAPI, compares active values with the independent oracle, and checks calendar/floor/band results. It reports `contract_and_helper_check_only`; reserved rows remain untested. `--check-equivalence` re-renders each selected known bank in clean and raw forms with identical API fields, wire codes, floor aliases, calendars and customer configuration. It compares goal, active values and the full operation catalogue while excluding provenance IDs/quotes/fingerprints and contract hashes. These four representation pairs add no cases to the 120-case inventory and are not held-out evidence.

`benchmarks.offline --candidate-contract <json> --profile <profile-id>` accepts policy IR or a compiler `policy_json` envelope. It binds to the independently supplied document inventory and OpenAPI, then checks goal, permitted actions and every active kind/value using JSON-typed equality. Sources use controller IDs `brief-1` through `brief-N` in the exact order returned by `export_inputs`; filenames and fingerprints come from the exported documents. Do not reorder the brief arguments when using this profile comparison. Candidate source IDs, fingerprints and policy fields are never normalized or silently filtered. Only trusted fixture expectations have their evaluator-only `source_documents` field removed before strict compilation; a candidate containing that field fails. Literal quotations and structural compilation do not prove correct interpretation or complete extraction; the independent value oracle checks known synthetic policy meanings, and the compiled contract retains `semantic_correctness_verified: false`.

`--ledgers <json>` checks observed normalized ledgers keyed by fixture ID using the independent effective identity fields and wrong-answer limit; missing ledgers fail. Ledger checks report honesty as not assessed without explicit evidence. No offline command executes dialogue. Regenerate initial fixtures with `python3.11 -m benchmarks.generate` only before freezing. For a development policy/provenance migration, use `python3.11 -m benchmarks.generate --offline-only`: it checks lifecycle markers before writing, preserves every existing case fact/oracle/reservation, and leaves hosted manifests and catalog untouched. Both modes refuse frozen manifests. The migration's regression snapshots preserve all 60 development case records outside their contract payload, all 60 reserved rows and the original profile/customer/API configurations.

To evaluate compilation without exposing the benchmark, export only bank source documents/schema to a new directory outside `benchmarks`: `python3.11 -m benchmarks.export_inputs --profile train-2 --out artifacts/evaluator-inputs/train-2`. Pass only the returned brief paths and OpenAPI as generator inputs; policy-only generation or local candidate validation keeps customer/evaluator files separate. This excludes scripts, expected ledgers, correct security answers and helper oracles. Synthetic `.invalid` endpoints must never be deployed on Freya.

## Candidate freeze and holdout lifecycle

```sh
python3 -m benchmarks.freeze create --candidate-root builder --config-file artifacts/runtime-config.json --record benchmarks/freezes/candidate-1.json
python3 -m benchmarks.freeze generate-holdout --record benchmarks/freezes/candidate-1.json
python3 -m benchmarks.freeze consume --record benchmarks/freezes/candidate-1.json --reason 'repair after held-out evaluation'
```

Freeze captures source/config files under the candidate root, excluding credentials, hidden files and caches. `--config-file` includes nonsecret resolved CLI/model/runtime configuration in the digest; hosted holdout requires at least `model` and `reasoning_effort`, matched against the actual builder deployment manifest. Optional `concurrency`, `reps` and `budget_seconds` also match evaluation arguments. Pure offline freezes may omit config. Holdout generation verifies the snapshot, creates two fresh policy profiles and fills exactly 60 reserved slots in each manifest. It refuses silent regeneration. If any candidate source or resolved config changes, verification rejects the old snapshot. After repair or disclosure, mark the holdout consumed; a new candidate requires a new record and fresh initial manifests in another evaluator directory. A previously exposed set becomes validation, never a fresh blind holdout. This is procedural isolation, not filesystem access control.

## Hosted evaluation: explicit execution only

```sh
python3 -m benchmarks.hosted --execute --agent-id <id> --brief A --split train --reps 3
python3 -m benchmarks.hosted --execute --agent-id <id> --deployment-manifest artifacts/<run>/manifest.json --brief B --split holdout --reps 3
```

Do not run execution commands under the current no-spend boundary. The default is dry-run; it does not initialize a client or require credentials. Live execution needs a Freya API key and a team bank key of at least 16 characters. Each agent evaluates one brief; use the A/B subset files or `--brief` to avoid incompatible note semantics. Optional baseline and mandatory holdout `--deployment-manifest` pin the exported graph, agent and published version ID/number; real-client read-back verifies them before and after evaluation. Every pinned call also requires a positive integer `agentVersionNumber` from its judge or transcript matching that deployment. Conflicting or malformed versions fail; absent per-call attribution is incomplete evidence. Unpinned development runs record unavailable attribution explicitly. Read-back publication proves the stored deployment, while per-call metadata attributes the executed call. A changed/missing graph or evidence is an infrastructure failure, not a conversation pass. Output paths remain inside the project and cannot traverse symlinks. Bank HTTP redirects are blocked so team credentials cannot be forwarded to another host.

Every variant has a unique `fixture_id` and a separate known `bank_case_id`. The latter alone is sent to `/v1/practice/sessions`. Server-returned variables are preserved unchanged, with fresh sessions for every case and repetition. The adapter rejects reused customer/session IDs, duplicate persona IDs and unknown bank seeds. One workspace simulation runs at a time through the platform client's shared lease; per-simulation call concurrency defaults to the kit's five. Three repetitions require every repetition to pass. No automatic slow-run retries spend additional credits.

The supplied server does not document arbitrary fixture registration, reset, policy/schema deployment or fault injection. Local `faults`, products, dates and identity data are not uploaded. Hosted results therefore measure wording robustness under known public seeds and policies, **not unseen-policy generalization**. Do not redirect live agents to synthetic external services.

## Scoring and evidence

The stronger local scorer checks exact decimal amounts, records including duplicates, total identity wrong-answer limits, factor order, no premature account access, retry limits, no notes on transfers and exactly one final close. Missing ledger/arguments/judge evidence is reported explicitly. Spoken honesty is never inferred from a successful ledger alone: the platform judge and retained transcript cover conversation checks. Optional independently normalized claim-order evidence can expose success claims before a successful write. Infrastructure failures remain separate from behavioral failures. Hosted results redact keys and ephemeral session secrets before writing.

Ledger and transcript retrieval are independent: an error in one preserves the
other and the original judge evidence. A scoring error also preserves the received
ledger. Every scored row records its simulation, iteration and call identifiers;
any supplied transcript call identifier must match the judge's call. Retrieval
failures remain failures, and no automatic retry replaces their original evidence.

Local ledger verdicts also fail when tool success/failure or identity arguments
are missing. Promise records must match the multiset of successful promise tool
writes, including their product, amount and date. A retry after a successful write
is prohibited even if that retry fails; a failure on another product preserves
the earlier success. Final close must succeed. Claim-order evidence must use a
real tool-call index. These checks assess recorded tool behavior; they do not
prove spoken confirmation, persuasion counters or dialogue correctness.

`run_builder_tests(client, deployment, cases_path, output_dir, deadline)` is an explicitly invoked controller hook, one repetition only. It accepts a single-brief subset or `deployment['brief']`; its caller owns execution authorization. Reserve 300 seconds for export through the scoped platform client deadline. Only sanitized failure categories/rule identifiers may feed a repair prompt; never feed oracles or customer scripts to code generation.

# T18 final-window runbook

This runbook prepares issue [19](https://github.com/DemirEkinArikan/freya_hackathon/issues/19).
It does not record a hidden build, a submitted package, or a competition pass.
Source availability, mock tests and a source archive cannot replace those gates.
The organizer kit v3 [final/rules](../build-the-builder/README.md) and later
organizer instructions govern execution. Customer cases remain hidden.

**Plan B is primary tonight** (coordinator, 18:10 Istanbul). [`planb/`](../planb/README.md) holds the
kit's agentic builder with its improved `build-phone-agent` skill, running Claude Opus 5.5. It builds the
agent, self-tests it and exports it. `tools/planb_attempts.py` runs its parallel attempts. The big builder
(`builder/build.py` via `tools/final_attempts.py`) is the fallback, used only for a brief that Plan B left
without an export. Everything from [Fallback: big-builder gates](#fallback-big-builder-gates) on describes
that fallback.

## Official rules

Read at 18:30 Istanbul from freyavoice.ai/hackathon#submit:

- **The case.** The final is one case: a zip holding a folder of bank documents in Markdown, Word, PDF and
  Excel. `BRIEF.md` is the starting point and `README.md` explains each document.
- **The mock.** We write the bank mock on Cloudflare Workers from the case's service contract and test
  customers. We deploy it before the builder runs and keep it up until the results are out. The agent calls
  only our mock.
- **The platform.**
  - One Freya key and one workspace per team, so only `FREYA_API_KEY`.
  - No probing or load-testing the platform: at most 10 calls per simulation, and no probe workflows.
  - A workspace holds at most 200 workflows, so update workflows in place.
- **The submission.**
  - One `submission.json` (team, members, repo, commit, run_command, workflow, trace, mock_url), e-mailed
    before 23:00 Istanbul.
  - One phone agent from one run, with that run's trace. No manual edits.
  - Nothing from the final case is written into the builder.

## The 20:00 command sequence

This is the primary path for the final case: one case folder, one builder run, one submitted agent. It
replaces the `planb_attempts` commands below, which take a brief plus a tool API file.

### Before 20:00

- **Slot.** Every other lane releases the Freya simulation slot by 19:55. Check the lease record
  `/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.json` and the server status of the last
  simulation it names. A simulation that a stopped lane started can still be running on the server.
- **Keys.** Check that they are present, never print them: `~/.freya-anthropic-key`, `FREYA_API_KEY` and
  `FREYA_BANK_TEAM_KEY` in the main checkout's `.env`, and Cloudflare (`npx wrangler whoami`, or
  `~/.freya-cloudflare-token`).
- **Tools.** `PY` is a Python 3.11 venv with `claude-agent-sdk==0.2.165 python-docx openpyxl pypdf`. `CPY` is a
  Python 3.11 venv with `anthropic` 1.x, for the independent check and the review pack. `pandoc`, `pdftotext` and
  `npx` are on `PATH`.

```sh
M=/Users/demirekinarikan/VsCode/freya_hackathon   # main checkout: private .env and the shared lease. Never switch its branch
W="<new worktree path outside the VS Code workspace>"
F="<new empty folder outside the repository>"     # the zip, the converted case, the mock build, the check outputs
PY="<build venv>/bin/python"; CPY="<check venv>/bin/python"
git -C $M fetch origin && git -C $M worktree add -b final/submission-20261011 $W origin/main
cd $W && mkdir -p $F
freya_env() {   # FREYA_API_KEY and FREYA_BANK_TEAM_KEY from $M/.env, read as data; use only inside ( ... )
  eval "$($PY -c 'import shlex, sys; from pathlib import Path; from builder.config import read_local_environment as r; e = r(Path(sys.argv[1])); print("; ".join(f"export {k}={shlex.quote(e[k])}" for k in ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY") if e.get(k)))' "$M/.env")"
}
```

### 1. Download and unzip (20:00)

```sh
# save the organizers' zip, exact bytes, as $F/case.zip
shasum -a 256 $F/case.zip | tee $F/case.zip.sha256
mkdir $F/case && unzip -q $F/case.zip -d $F/case
CASE=$(dirname "$(find $F/case -name BRIEF.md -not -path '*/__MACOSX/*' | head -1)"); ls -la $CASE
```

### 2. Convert

```sh
$PY tools/case_folder.py $CASE $F/bundle   # then read $F/bundle/INDEX.md: BRIEF.md, README.md, the contract, the customers
```

`planb/build.py` converts the folder again in its own working copy. This bundle is for us and for the
independent check.

### 3. Build and deploy the mock

```sh
$PY mock/build_mock.py "$CASE/<service contract .json or .yaml>" "$CASE/<test customers .xlsx or .csv>" \
  --out $F/mock --name freya-final-bank-mock
MOCK=$(bash mock/deploy.sh $F/mock); echo $MOCK   # the URL is the only stdout line; deploy.sh checks /_mock/health
curl -fsS $MOCK/_mock/health
```

Read what `build_mock.py` prints: the operations and their kinds, the identity fields and the customer columns.
If any of it is wrong, write an `--overrides` JSON file (format in the script's docstring), then build and
deploy again. From the builder start until the results are out, the mock stays deployed and unchanged.

### 4. Run the builder: this is the submitted run

```sh
nohup $PY planb/build.py $CASE $MOCK > $F/build.out 2>&1 &
tail -f traces/final.log
```

- **`run_command`** for `submission.json`: `python3 planb/build.py {case_folder} {mock_url}`.
- **Time.** The run stops itself at 1,740 s.
- **Exit status.** It exits 0 only when the run saved a workflow, the export worked and every tool URL is on
  the mock.
- **Self-tests.** They take the shared lease, with `FREYA_SIM_CONCURRENCY` at 10 by default.
- **Output.**
  - `submission/final.workflow.json`: the export.
  - `traces/final.log`: the official trace, everything the run printed, keys removed.
  - Timestamped copies, `submission/final-<stamp>.workflow.json` and `traces/final-<stamp>.log`, so a second
    run never overwrites the first.

### 5. Independent check (after the build)

This step needs `tools/independent_check.py` from branch `rapid/independent-check-20261011-claude`, until that
branch is merged. It reads only the case documents, the transcripts and the mock's records, never the builder's
workflow or self-tests.

```sh
AGENT=$($PY -c "import json; print(json.load(open('submission/final.workflow.json'))['agent_id'])")
( read -r ANTHROPIC_API_KEY < ~/.freya-anthropic-key; export ANTHROPIC_API_KEY
  $CPY tools/independent_check.py rules $F/bundle --out $F/check
  $CPY tools/independent_check.py cases $F/bundle --out $F/check )
# read $F/check/rules.md and $F/check/cases.md by hand
( freya_env; FREYA_LEASE=$M/artifacts/.simulation.lock FREYA_SIM_CONCURRENCY=10 FREYA_BANK_HOST=$MOCK \
  $PY build-the-builder/tools/run_cases.py $AGENT --cases $F/check/cases.json --out $F/check/run.json )
( read -r ANTHROPIC_API_KEY < ~/.freya-anthropic-key; export ANTHROPIC_API_KEY
  $CPY tools/independent_check.py grade $F/bundle --out $F/check --run $F/check/run.json --mock-url $MOCK \
    --cases $F/check/cases.json --env-file $M/.env )   # writes $F/check/report.md and report.json
```

### 6. Review pack

```sh
( freya_env; $PY - $F/check/run.json $F/calls <<'PY'
import json, pathlib, sys
sys.path.insert(0, 'build-the-builder')
import freya
out = pathlib.Path(sys.argv[2]); out.mkdir(exist_ok=True)
for row in json.load(open(sys.argv[1]))['rows']:
    if row.get('call_id'):
        (out / f"{row['id']}.json").write_text(json.dumps(freya.get_call(row['call_id'])))
PY
)
$CPY tools/review_pack.py --case $F/bundle --calls $F/calls --checks $F/check/report.json \
  --workflow submission/final.workflow.json --out $F/review.html
```

Open `$F/review.html` and read the agent and the worst calls by hand. Nothing in it is a pass. A fix is never a
manual edit of the workflow. It is a generic builder bug fix (nothing from the case) followed by a new run of
step 4.

### 7. Submission

```sh
git add -f submission/final.workflow.json traces/final.log   # both folders are git-ignored
git commit -m "Final case: the submitted run's workflow and trace (refs issue 19)"
git push origin final/submission-20261011
$PY tools/make_submission.py --team "<team>" --member "<Full Name> <email>" \
  --run-command "python3 planb/build.py {case_folder} {mock_url}" \
  --workflow submission/final.workflow.json --trace traces/final.log --mock-url $MOCK --out $F/submission.json
```

- **Members.** Repeat `--member` once per member.
- **Which run.** To submit an earlier run, pass its timestamped pair instead: `final-<stamp>.workflow.json`
  and `final-<stamp>.log`.
- **Checks.** `make_submission` refuses a workflow and trace that do not name the same workflow and agent ids,
  any file holding a key, and files that are not committed unchanged in a pushed commit.
- **Sending.** E-mail the printed JSON before 23:00 Istanbul, as in [SUBMISSION-JSON.md](SUBMISSION-JSON.md).

## Plan B: primary path

### Before 20:00

- **Checkout.** A checkout of the frozen commit, which contains `planb/` and `tools/planb_attempts.py`.
- **Environment.** `.venv-planb` with `claude-agent-sdk==0.2.165`, as in [planb/README.md](../planb/README.md).
- **Keys.** Check that they are present without printing them: `~/.freya-anthropic-key`, plus
  `FREYA_API_KEY` and `FREYA_BANK_TEAM_KEY` in the main checkout's `.env`.
- **Simulation slot.** The attempts need the workspace's one simulation slot for their self-tests. From 19:55
  no other lane holds its lease (`artifacts/.simulation.lock` in the main checkout).

### Commands

```sh
mkdir -p out
nohup .venv-planb/bin/python tools/planb_attempts.py --brief <inputs>/BRIEF.md <inputs>/<tool-api>.json \
  > out/planb-attempts.log 2>&1 &
tail -f out/planb-attempts.log     # one line per start and per finish, then the summary JSON
```

- **Inputs.** Give one `--brief` per brief: its Markdown file(s) and its tool API `.json`, or a folder.
- **Defaults.** `--attempts 2 --stagger 60 --timeout 1800`.
- **Working copies.** Each attempt runs in its own copy of `planb/` under `$TMPDIR/planb-attempts/<run>/`.
  Its `builder.log` and Claude trace stay there.
- **Shared slot.** All attempts self-test in the one workspace. Their simulations take turns through the lease
  (`FREYA_LEASE`), with at most 10 calls at once.
- **Workflows.** Attempt k of a brief updates the same workflow on every run.

### Time boxes

| Istanbul | UTC | Action |
|---|---|---|
| 20:00–20:15 | 17:00–17:15 | [Steps 1–3](#the-2000-command-sequence): save the case zip and record its SHA-256, convert the folder, build and deploy the mock and check that it answers |
| by 20:20 | by 17:20 | Step 4: start `planb/build.py $CASE $MOCK` |
| 20:20–20:50 | 17:20–17:50 | The build runs and stops itself at 1,740 s. Inside, the skill's time box: saved by minute 8, last test started by minute 19, exported by minute 28 |
| 20:50–21:30 | 17:50–18:30 | Steps 5–6: independent check and review pack. If the run has no export, or a generic builder bug shows, run step 4 again (or the [big-builder fallback](#fallback-big-builder-window)) |
| by 22:00 | by 19:00 | Last builder start of any kind |
| by 22:40 | by 19:40 | Everything exported; `submission.json` filled in and committed |
| 22:40–23:00 | 19:40–20:00 | Buffer: e-mail `submission.json` before 23:00 and keep the receipt |

### Selection and fallback

- **Selection.** `planb_attempts` selects one attempt per brief: the one that saved a workflow and has the
  highest final self-test pass rate (the last test that attempt ran), then the shortest elapsed time.
- **Export.** It exports that attempt's workflow and exits 0. If a brief has no export, it exits 3: its
  `selected` is `null`, or its record has an `export_error`.
- **Fallback.** Only then does that brief get a fallback.
  - If the attempts failed on infrastructure (a lease wait, platform errors in `builder.log`), re-run
    `planb_attempts` for that brief.
  - Otherwise start the big builder ([attempt matrix](#attempt-matrix), round 1).
- **Later runs.** A later run replaces the selection only if its self-test pass rate is higher. Every attempt
  stays on disk.

### Submission package

Per brief:
- **Workflow.** `out/<brief>.workflow.json` is the selected attempt's workflow as `freya.export_workflow`
  wrote it. This file is what gets scored.
- **Trace.** `out/<brief>.trace.jsonl` is that run's trace: every builder message, with every key redacted
  (`trace_redactions` counts them). `out/<brief>.trace.txt` holds the builder's own notes: start and end, the
  model, the workflow and agent ids it created, every test and the export.
- **Builder and skill.** These are the files at the submitted commit:
  - `planb/agent_builder.py`;
  - `planb/.claude/skills/build-phone-agent/SKILL.md`;
  - the kit files in `planb/`;
  - `tools/planb_attempts.py`.

  `run_command` is the exact `planb_attempts` command that was run.
- **Evidence.** `out/planb-summary.json` lists every attempt, so the one submitted run can be traced.

`submission.json` names the workflow and the trace as repository paths at the submitted commit, and `out/` is
git-ignored. Commit those files where the submission tooling expects them.

## Fallback: big-builder gates

- Finish broad evaluation and Python 3.11 regressions before **Sunday 11 October
  2026, 17:00 UTC / 20:00 Istanbul**. Run the full suite, kit verification,
  source-package checks and `git diff --check` on the exact pushed source head.
- T17 must have its actual clean-checkout reproduction and final freeze evidence,
  including fresh policy/schema inputs, successful live publication/export in
  less than 30 minutes, permitted generation tools, `experimental-5` phone model,
  exact commit/configuration and critical cases passing all three repetitions.
  An open dependency or only a source/mock rehearsal is not this gate.
- Record frozen source commit, Python 3.11 patch version, Codex executable/version,
  explicit accessible builder model, reasoning effort, 1,800-second deadline,
  300-second export reserve, bounded repairs, kit fingerprint and source inventory.
  Keep invocation-time `builder-source.json`; do not create it retrospectively.
- Rehearse the manifest/ZIP/extraction procedure in
  [submission package documentation](SUBMISSION-PACKAGE.md). Reserve storage for
  every attempt directory: each brief gets two parallel attempts per round of the
  [attempt matrix](#attempt-matrix), and none is deleted. Each attempt publishes to its own new workflow, named
  `final-<label>-a<k>-<UTC launch time>`. Concurrent attempts must not share a
  workflow: an export fails if its workflow changed after publication. Tonight
  needs at most 2 × 2 × (rounds) workflows, well under the workspace limit of 200.
- The frozen commit must contain `tools/final_attempts.py` with the attempt matrix
  and the packager's `selections` support ([PR 167](https://github.com/DemirEkinArikan/freya_hackathon/pull/167)). Both are tools, not runtime
  source, so they do not change the builder source fingerprint. Round 2 also needs
  the builder's relaxed declaration option (R1) in the frozen source; without it,
  round 2 refuses to start.
- The T17 freeze must cover both efforts of the matrix, high and medium.
- Obtain and read organizer submission instructions when supplied. Record the
  actual channel, permitted attachment layout, credential/import requirements,
  metadata, size limits and receipt mechanism. **No submission address is known
  in the reviewed kit.** Request missing instructions through the authorized
  organizer contact; do not guess an address or claim submission.
- Coordinate with the primary thread before any live operation. Record the
  shared absolute lease paths
  `/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock` and
  `/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.json`,
  current simulation/iteration IDs, workspace and actual server status. A local
  timeout, absent process or old timestamp does not prove a remote job finished.
  Preserve active/paused/ambiguous creation evidence; reconcile it before another
  simulation. All worktrees use this one lease, never separate local locks.

## Fallback: big-builder window

Use this window only for a brief that Plan B left without an export.

Release is **17:00 UTC / 20:00 Istanbul**. Deadline is **20:00 UTC / 23:00 Istanbul**.
The submission buffer is **19:40–20:00 UTC / 22:40–23:00 Istanbul**. Treat 19:40 UTC
as the operational cutoff for completed builds, exports, the committed evidence and a
filled-in `submission.json`. Do not spend that buffer on tests.

| UTC | Istanbul | Action |
|---|---|---|
| Before 17:00 | Before 20:00 | Frozen checkout, guard and preflight, so the fallback can start at once |
| 18:00–18:05 | 21:00–21:05 | `out/planb-summary.json` shows a brief without an export: `tools/final_attempts.py launch --round 1 --labels hidden-N`, strict high + strict medium, starts 60 s apart |
| 18:05–18:35 | 21:05–21:35 | Attempts run; each stops itself at 1,800 s including audit, any own tests and export. Poll with `select` |
| 18:35–19:00 | 21:35–22:00 | Only if round 1 left that brief without a selected attempt: `launch --round 2 --labels hidden-N` by 22:00 |
| 19:00–19:40 | 22:00–22:40 | Round 2 runs, ending by 22:30; select and export by 22:40 |
| 19:40–20:00 | 22:40–23:00 | Buffer: send `submission.json` (Plan B section) |

The latest launch of any builder is **19:00 UTC / 22:00 Istanbul**. Its attempts end
by 22:30, which leaves 10 minutes to select and export before 22:40. The
launcher refuses a round that cannot improve a brief's selection
([attempt matrix](#attempt-matrix)). Never launch one for a faster build: build
time breaks ties only after score. A late or invalid
organizer input reduces this margin; do not promise that a retry still fits. Preserve failed attempts rather than relabeling late exports
as successful. The kit permits repeated runs: one selected run per final brief
is submitted with its own trace, and every other attempt stays on disk.

The builders take the simulation lease only for their own hosted tests, and
tonight they run none.

### Attempt matrix

| Round | Briefs | Attempts per brief |
|---|---|---|
| 1 | a brief that Plan B left without an export, from 21:00 | strict high, strict medium |
| 2 | only a brief whose finished round 1 left no selected attempt, or selected a deterministic fallback export | relaxed high, relaxed medium |

- **Strict** is the builder's default declaration handling. **Relaxed** is the
  builder's last-resort declaration mode (R1, issue 8 comment 6108951431). A
  protected declaration without a typed construct then no longer fails the
  build. Its requirement becomes a dialogue-level rule, and the trace must list it
  as not enforced by the controller: a relaxed run writes one
  `relaxed_declarations` event with `prompt_enforced_limitations` (each
  `enforced_by: dialogue_prompt`) and `unguarded_close_codes`, and its
  `manifest.json` repeats both. Everything the sealed kernel enforces stays
  enforced.
- `launch --round 2` passes `--declarations relaxed` when the frozen
  `builder/build.py` parser declares that option. If the frozen source has none,
  round 2 refuses to start, and a brief without a strict export can only get
  another `--round 1`.
- The launcher refuses a round that cannot improve the brief's recorded
  selection:
  - any further round until `select` has recorded every attempt of that brief as
    finished;
  - round 1 for a brief whose selected attempt is a strict model-plan export;
  - round 2 for a brief whose selected attempt is a model-plan export.

  A brief whose selection is relaxed may still get one more `--round 1` by 21:45,
  because selection prefers a strict export. A brief whose selection is the
  builder's deterministic fallback plan (R5) may get either round, because every
  model-plan export ranks before it.
- **Codex accounts.** The launcher spreads the attempts over the logged-in Codex
  accounts: `~/.codex` and every `~/.codex-acct<N>` with an `auth.json` and no
  `DISABLED` file. It passes each one to its builder as `CODEX_HOME`. With two
  accounts, the two attempts of each brief use different accounts, and each
  account runs one high and one medium attempt, so one account's outage cannot
  fail a brief. At most four of tonight's builders run at once, two per brief
  and two per account, because a brief's next round waits for all of its
  attempts; `--max-running` (default 6) is a backstop. Tonight's attempts never
  wait for other builds. Calls slow down
  as more builds share an account (issue 19 comment 6108793427), so other lanes
  start no build after 19:30. Builds take up to 30 minutes. Before 20:00, put a
  `DISABLED` file in any account that cannot run the builder model.
- **Records.** Each `launch.json` records the round, mode, effort, exact argv,
  PID, Codex account name (never a token), the builder-process count before the
  start and the staging probe.
  `selection.json` lists every attempt with its mode and effort.

### Platform failures are infrastructure, not builder results

Freya staging returned 503/504 for about 40 minutes today (09:38–10:19 UTC).
The builder does not retry platform errors. A 5xx during dry-run validation,
save, publication, read-back or export ends that attempt, and its manifest
error names the `HTTP 50x` request. Only the hosted test runner retries GETs.
Such an attempt is simply not eligible: it never counts as a builder result, and
a brief left without an eligible attempt gets a retry round. If every strict
attempt of a brief ended on such a platform error, that retry is another
`--round 1`, not round 2: strict mode was never really tried.

Two measures limit the damage:
- The launcher interleaves the briefs and starts attempts `--stagger-seconds`
  apart (default 60), so publication times do not coincide.
- Before each start, the launcher appends one read-only staging probe to
  `artifacts/final/health.jsonl` and copies it into that attempt's `launch.json`.
  The probe records the time, ok, latency and only the HTTP status. It is
  evidence, never a gate: policy extraction needs no platform for its first 15
  minutes. `tools/final_attempts.py health --env-file <.env>` appends one more
  probe; run it with every `select` poll. Before a retry round, wait until a
  probe returns `ok: true`.

### Declared selection order

`select` ranks every attempt of a brief by this order. It was fixed before
release, in `package_submission.ATTEMPT_ORDER`:

1. Exported a validated workflow (`status: exported` with its export file).
2. Local validator checks pass: exact guarded recompilation from the run's own
   policy and inputs, `validate_workflow`, and the stored export equivalent to the
   authored graph. These are the bundle verifier's per-run checks.
3. The packager's per-run gates pass (`validate_trace` and `validate_run`).
4. A model-plan export before an export of the builder's deterministic fallback
   plan (`plan_origin: deterministic_fallback` in the manifest, R5). The fallback
   is a floor: it is selected only when no model-plan attempt passes 1–3, even a
   relaxed one.
5. Strict declaration mode before relaxed. Among attempts of the same plan
   origin, a relaxed attempt is selected only when no strict attempt passes 1–3,
   even if it has fewer diagnostics or finished earlier.
6. Fewest diagnostics. Each rejected policy or plan error and each format
   failure in the trace counts as one, and so does each `unsupported_constraints`
   entry left in the final policy.
7. Earliest finish (trace end), then the lower attempt number.

Only an attempt that passes 1–3 can be selected. `select` writes
`artifacts/final/<label>/selection.json`, which lists every attempt with its
mode, effort, status, criteria and the raw SHA-256 of its manifest, trace and export. It
exits 2 while any attempt still runs and 3 when no attempt is eligible. It never
changes an attempt directory. The output is deterministic, so running `select`
again on the same attempts reproduces each record byte for byte.

## Fallback: receive inputs and invoke the frozen builder

Store the organizer-provided bytes only under dedicated ignored
`artifacts/hidden-inputs/hidden-1/` and `artifacts/hidden-inputs/hidden-2/` directories.
Record receipt time, organizer origin, original filenames and SHA-256 bytes in
local reviewed metadata. Do not put hidden content into issues, builder source,
static prompts or test fixtures. Preserve original newline bytes and amendment
order. Each brief gets its own matching OpenAPI; do not reuse the public API.
The illustrative names below must be replaced by the actual supplied names.

Use a fresh isolated frozen checkout; keep its inputs and output directory inside
that checkout. The checked controller's explicit `--simulation-lease` connects
both builds to the canonical shared lease outside the worktree. This controller
path is local orchestration configuration, never generation input. Do not switch
or edit another worker's active checkout. A local worktree artifact lock is not
an acceptable substitute for the shared lease.

Set `T18_SOURCE_COMMIT`, `T18_SOURCE_INVENTORY_SHA256` and `T18_BUILDER_MODEL`
from the accepted T17 freeze. The efforts come from the attempt matrix. The inventory digest is the
full allowlisted source inventory recorded by T17, not just runtime provenance.
These are explicit nonsecret configuration values. Create a detached worktree
from that accepted source commit. The guard below verifies HEAD, rejects staged
and unstaged tracked changes, and requires every allowlisted source file to match
HEAD and the accepted inventory. An untracked allowlisted file, a deletion or a
changed kit file must fail this guard. Missing actual T17 freeze evidence remains
a blocker; do not fill these variables from a preparation rehearsal.
The builder reads only this repository's private `.env` as data. Do not print or
source credentials, read sibling environment files, or dump the environment.

A new worktree has no ignored `.env`. Provision a reviewed private `.env` in the
frozen checkout from this repository's authorized configuration, or inherit the
reviewed controller credential variables in its process environment. Use the
project's data parser; never source the file as shell code. Keep it outside Git
and the package. Verify presence without displaying values before release.

Each attempt directory starts with an empty slot registry, so each attempt
creates one workflow under its own unique slot name. The launcher does not use
`--adopt-workflow`, and it never reuses an attempt directory or a slot.

```sh
git worktree add --detach /Users/demirekinarikan/VsCode/freya_hackathon-worktrees/t18-frozen-final "$T18_SOURCE_COMMIT"
cd /Users/demirekinarikan/VsCode/freya_hackathon-worktrees/t18-frozen-final
test "$(git rev-parse HEAD)" = "$T18_SOURCE_COMMIT" || exit 1
git diff --quiet HEAD -- || exit 1
python3.11 - "$T18_SOURCE_INVENTORY_SHA256" <<'PY' || exit 1
import sys
from tools import package_submission as package

files = package.source_files()
package.verify_committed(files)
hashes = {path.relative_to(package.ROOT).as_posix(): package.check_content(path)
          for path in files}
actual = package.digest(hashes)
if actual != sys.argv[1]:
    raise SystemExit('Frozen source inventory differs from the accepted T17 freeze')
print({'source_files': len(files), 'source_inventory_sha256': actual})
PY
python3.11 tools/final_attempts.py launch --env-file /Users/demirekinarikan/VsCode/freya_hackathon/.env \
  --simulation-lease /Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.lock \
  --model "$T18_BUILDER_MODEL" --round 1
```

The launcher reads `artifacts/hidden-inputs/spec.json`, which lists each label's
briefs in the organizer's explicit order and its matching API. For each attempt
it starts `builder/build.py` with `--brief` repeated in that order,
`--output-dir artifacts/final/<label>/attempt-<k>`, its own slot, the shared
lease, the model, the round's effort and, in round 2, the relaxed option. The child is detached, so closing the terminal
does not stop it. `launch.json` in the attempt directory records the exact argv
and PID, and `builder.log` keeps the builder's output. The launcher loads the
private `.env` with the data-only parser into the children's environment, never
its own, and removes `OPENAI_API_KEY`. It refuses Python other than 3.11, a
missing `FREYA_API_KEY` or Codex CLI, a round the attempt matrix does not allow,
and more running builders than
`--max-running` (default 6). These commands are templates pending actual briefs,
not a claim they have run. Omit offline/generate-only modes for final builds.
Do not supply an old `--policy-file`, fixture workflow or public-case evaluator
whose bank facts do not match the final input. An approved compatible hosted
case set, if one is supplied, remains evaluator-only via `--test-cases`; keep its
runtime inside the same 1,800 seconds and shared lease. With no own hosted tests,
`hosted_behavior_verified` remains false; T17 evidence does not become a hidden
conversation pass. Every generation must pass its mandatory tool-free isolation
audit before receiving any bank material; do not disable or replace that audit.

The controller starts its own 1,800-second deadline before input processing,
reserves 300 seconds for export, updates/publishes its persistent slot and checks
stored readback. Preserve the unique timestamped run directory printed in the
manifest. The phone agent remains `experimental-5` for dialogue/routing/extraction.
A general parser/runtime bug fix may be allowed after release, but it must not
contain any hidden rule/value/name or copy hidden examples into source/static
prompts. Coordinate ownership, test the generic fix, commit it, record the new
source/config fingerprint and rerun affected builds; do not edit generated policy,
plan or workflow by hand. If both selected builds do not match one exact submitted
source inventory, do not manufacture a common provenance record.

## Fallback: inspect, package and submit

This section covers fallback runs only. The official submission is the one `submission.json`
described in [Official rules](#official-rules).

For each selected run require successful live `manifest.json`, one bounded
append-only `trace.jsonl`, canonical `submission.workflow.json`, original
`builder-source.json` and `build-provenance.json`, plus selected attempt evidence.
Read and correlate workflow/agent/published-version IDs, graph/export hashes,
ordered input/API fingerprints, isolation audit, model calls with one-line purpose,
start/end times and finite in-budget duration. Inspect that the published readback
and final exported graph match the authored deployment. Preserve original failed
readbacks, test results and overruns. Do not attach an older trace to a newer slot.

The selected run per brief is the one `select` names in `selection.json`. Never
pick a run by hand. `tools/final_attempts.py manifest --team <id>` writes the
submission manifest from both records: the selected runs, their exact launch
argv as `run_command`, the inputs and a `selections` mapping. The packager then
requires each record to be the declared order's eligible choice, binds it to the
selected run's raw bytes, and re-hashes every listed attempt's manifest and trace
on disk. The ZIP stores each record as `selections/<label>.json`: every attempt is
listed, but only the selected run's trace is packaged. The bundle verifier
re-checks the records and reports `attempt_selection` ("selected k of N").

Follow [SUBMISSION-PACKAGE.md](SUBMISSION-PACKAGE.md) for the exact two-run manifest,
reviewed evidence commit, allowlist/credential scan, final ZIP and extraction check.
Never blindly redact a required live tool credential and call the modified export
runnable. Organizer-provided import-safe authentication must be reconciled with
credential exclusion before packaging. Stop that artifact's submission if this is
unresolved; retain the original protected export and document the precise gate.

During 19:40–20:00 UTC use only the supplied authorized submission channel. Send
the verified package/export/trace attachments and required metadata, inspect the
actual destination and uploaded filenames, and retain receipt/acknowledgment with
UTC/Istanbul time, package SHA-256 and evidence commit. A prepared draft or ZIP is
not a submission. If instructions/channel are still absent, report that exact
blocker instead of inventing a destination.

After actual submission, record checked/pushed head, selected runs/attempts,
source and evidence commits, merged matching-head PR, package digest, organizer
receipt and reconciled lease IDs/status in issue 19 without raw events,
transcripts or credentials. Use `tools/cowork.py complete T18 --commit <pushed-head>
--pr <merged-matching-head-pr> --checks <actual-evidence>` only when both hidden
builds, T17 dependency and final submission are complete. Until then leave issue
19 open and state the missing evidence precisely.

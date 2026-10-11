# Plan B builder (tonight's primary builder)

This is the kit's own agentic builder (`build-the-builder/examples/agent_builder.py`) with one skill.
Claude Opus 5.5 reads the brief and writes a small generator for the phone agent. It then saves the agent on
Freya, tests it, fixes it and exports it. The skill is where the builder's knowledge lives:
`.claude/skills/build-phone-agent/SKILL.md`.

It prescribes the agent shape that passed 13 of 13 public cases when Plan B lanes built agents by hand
(issue 8 comments 6110336862 and 6110389581). Freya scored them, but this is development evidence.
No automated builder run on this version of the skill has been shown yet.

The big builder (`builder/build.py` via `tools/final_attempts.py`) is the fallback. See
[T18-FINAL-WINDOW.md](../docs/T18-FINAL-WINDOW.md).

## The final case: one command

The final case is one folder (or zip) of bank documents in Markdown, Word, PDF and Excel, with a flow drawing. Deploy
our bank mock first (`mock/`), then run one build from a Python 3.11 environment with `claude-agent-sdk==0.2.165`,
`python-docx`, `openpyxl` and `pypdf`:

```sh
python3 planb/build.py <case folder or .zip> <mock url>
```

`build.py` does the following:

- **Case.** `tools/case_folder.py` converts every document into `out/case/` of a fresh working copy of this folder,
  outside the repository: Markdown as it is, Word through pandoc, PDF through pdftotext, Excel as Markdown tables
  plus CSV, images kept. `out/case/INDEX.md` lists them in reading order, `BRIEF.md` first and `README.md` second.
- **Mock.** It reads the mock's `/_mock/health`. A bare worker URL gets the contract's base path, and the result is
  `MOCK_URL`, the base of every tool URL. `FREYA_BANK_HOST` points `tools/run_cases.py` at the mock.
- **Build.** Claude Opus 5.5 (effort `max`) runs the skill on the bundle with the slot `final`, so the same workflow
  is updated on every run. It self-tests on the mock's test customers through the one workspace and its lease. The
  run stops at 1740 s.
- **Output.** The saved workflow is exported to `submission/final.workflow.json`. Everything printed is the official
  trace: start and end, every model call with its model and purpose, tests, workflow id, agent id, steps, tools and
  tool URLs. Keys are removed. The trace is also written to `traces/final.log`, and both files get a timestamped copy.
- **Exit.** 0 only when this run saved a workflow, the export worked and every tool URL is on the mock.
- **Keys.** They come from the environment, else from the main checkout's `.env` and `~/.freya-anthropic-key`.

## Tonight: parallel attempts

Use a Python 3.10+ virtual environment with the Claude Agent SDK. Its bundled Claude Code CLI is what the
builder runs. Then start the attempts from this checkout:

```sh
python3.11 -m venv .venv-planb && .venv-planb/bin/pip install claude-agent-sdk==0.2.165
.venv-planb/bin/python tools/planb_attempts.py --brief <inputs>/<brief>.md <inputs>/<tool-api>.json
```

`tools/planb_attempts.py` does the following:

- **Inputs.** Repeat `--brief` once per brief. A brief is its Markdown file(s) and its tool API `.json`, or a
  folder.
- **Attempts.** It starts `--attempts` builder runs per brief (default 2), `--stagger` 60 s apart. Each runs in
  its own copy of this folder, outside the repository, and is killed with everything it started after
  `--timeout` 1800 s.
- **Keys.** The children get keys only in their environment:
  - `ANTHROPIC_API_KEY` from `~/.freya-anthropic-key`;
  - `FREYA_API_KEY` and `FREYA_BANK_TEAM_KEY` from the main checkout's `.env`, read as data;
  - an empty `CLAUDE_CONFIG_DIR`.

  Nothing prints a key.
- **One workspace.** Every attempt self-tests in the team's one Freya workspace. Their simulations take turns
  through the shared lease, the main checkout's `artifacts/.simulation.lock` (`FREYA_LEASE`), with at most 10
  calls at once.
- **Workflows.** Attempt k of a brief reuses its workflow from earlier runs, so workflows are updated in place.
  The ids are kept in `<work-dir>/freya-agents.json`.
- **Selection.** Per brief, the winner is the attempt that saved a workflow and has the highest final self-test
  pass rate (the last line of its `results/log.jsonl`). A tie goes to the shortest elapsed time.
- **Output.** The winner is exported with `freya.export_workflow` to `out/<brief>.workflow.json`. Its trace is
  copied next to it with every key redacted, as `out/<brief>.trace.jsonl` plus the builder's notes
  `out/<brief>.trace.txt`. `out/planb-summary.json` lists every attempt: exit, elapsed, self-test, workflow
  file, workflow/agent ids, trace and log. Exit status 3 means a brief has no export.

## One run by hand

Put the inputs inside this folder, which is where the builder works. `briefs/` and the run state are
git-ignored. Put `FREYA_API_KEY`, `FREYA_BANK_TEAM_KEY` and `ANTHROPIC_API_KEY` (or a Claude login) in the
environment, then run:

```sh
cd planb && mkdir -p briefs && cp ../build-the-builder/briefs/brief-a.md briefs/
../.venv-planb/bin/python agent_builder.py briefs/brief-a.md                       # practice brief A, kit API
SELF_TEST_ONLY=1 ../.venv-planb/bin/python agent_builder.py briefs/brief-a.md      # rehearse the unseen-brief path
```

A run writes these files:
- `traces/<start>.jsonl`: every message. The header records the model, effort, fallback model and provider.
- `traces/<start>.txt`: the skill's trace notes.
- `builders/<slot>.py`, `out/<slot>.workflow.json` and `out/<slot>.export.json`.
- `results/`, `.freya-agents.json` and `.personas.json`.

Settings:

| Variable | Default | Purpose |
|---|---|---|
| `BUILDER_MODEL` | `claude-opus-5-5` | Builder model |
| `BUILDER_EFFORT` | `max` | Use `xhigh` if a run cannot finish inside 30 minutes at `max` (rules section 18) |
| `BUILDER_FALLBACK_MODEL` | `claude-sonnet-5-5` | Model the SDK falls back to |
| `SELF_TEST_ONLY` | unset | `1`: self-written cases on the practice seeds, even for Brief A or B |
| `FREYA_LEASE` | unset | Lock file shared by everyone who simulates in this workspace |
| `FREYA_SIM_CONCURRENCY` | 5 | Calls per simulation, capped at 10 by `freya.py` |
| `FREYA_BANK_HOST` | the kit's practice bank | Bank mock that `tools/run_cases.py` reads sessions and ledgers from. Set it in the process environment; `planb_attempts` passes the `FREYA_*` variables through, except the second workspace key |

The builder stops itself after 1740 s.

## Files

The kit files are byte-identical copies of kit v3; their SHA-256 values match `docs/kit-manifest.json`:

| File | SHA-256 |
|---|---|
| `tools/run_cases.py` | `c96da9f9189d82850e46bfc551603ff5a62847025b5c50a77b923ad999f3767c` |
| `tools/openapi.json` | `9a8410b12e5c82b6c6a30e9ed7030574d6f919cafdb1e035cef7bd876b0badb8` |
| `docs/api.md` | `6003c0ba733f33b5981822d3418c20e042d29107fbb1045213b35dad6bf95157` |
| `docs/tools-and-code.md` | `56be9c87fbfddf5855c25fe83d4633bafd885d7cba5677133c15d211eb21b711` |
| `docs/workflow-format.md` | `4ff70b5a37b647122e6a6f3ca1a523b207dbcdf6afb32058de8451a8c7d1dcfc` |
| `examples/base-agent.json` | `7eb462e9c910ab99f067c2f62dab272952c1395446247db396b4f695bde7ca68` |
| `public-test-set.json` | `fb7f4c0082eecb724a6faab771b4339bc62655627e49251e6bb2a0cf59a72fcb` (the seed customers for self-tests) |

Four files differ from the kit:

- **`agent_builder.py`** is the kit's builder with these changes:
  - effort `max` and the fallback model;
  - model, effort, fallback model and provider in the trace header;
  - an `end` line even when the run fails;
  - the 1740 s self-limit;
  - this folder as its kit.
- **`freya.py`** is the kit client with these changes:
  - `run_simulation` holds the workspace lease named by `FREYA_LEASE`: a `flock` plus its `.json` record. Before it
    starts, it waits while the workspace still runs a simulation (`completed`, `failed` and `paused` are final).
  - The calls per simulation are capped at 10 (official rules: no load tests).
- **`.claude/skills/build-phone-agent/SKILL.md`** is the Plan B skill of 18:32 (`b16aa4f266f4…`, changelog
  `scratchpad/planb-kit/SKILL-CHANGELOG.md`). Its three `FREYA_SIM_CONCURRENCY=15` commands now read `=10`, under
  the official rules. It also has a section on case folders and the mock (`MOCK_URL`, the case's test customers as
  self-test seeds, the slot and notes file the prompt names). The overfitting audit (PR 220) replaced its
  practice-brief specifics with generic rules and added the save-twice rule.
- **`tools/score.py`** counts a ledger or expected list it lacks (`promises`, `notes`, `callbacks`, `transfers`) as
  empty, so a mock contract without one of the kit's write tools does not crash `run_cases.py`.

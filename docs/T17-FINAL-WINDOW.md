# T17 final-window rehearsal checklist

Ordered steps for the T17 banking rehearsal and freeze, issue
[18](https://github.com/DemirEkinArikan/freya_hackathon/issues/18). Command
details and evidence rules are in [REHEARSAL.md](REHEARSAL.md); the hidden-input
window is in [T18-FINAL-WINDOW.md](T18-FINAL-WINDOW.md). A passing
`tools/rehearse_package.py` run is a local source check. It is never a banking
rehearsal, an export check or a hosted pass.

## Entry gate

Start only when T15 ([16](https://github.com/DemirEkinArikan/freya_hackathon/issues/16))
and T16 ([17](https://github.com/DemirEkinArikan/freya_hackathon/issues/17)) are
closed through `tools/cowork.py complete`, with the accepted candidate commit
and configuration recorded on issue 17. On 11 October at 09:20 Istanbul both
were open, and no critical hosted case set for fresh inputs existed (step 8).

## Latest start

T17 acceptance and freeze must finish before the hidden release at 17:00 UTC /
20:00 Istanbul. One workspace simulation at a time gives this sequential worst
case: two builds of at most 1,800 s, two hosted three-repetition runs bounded by
`--budget-seconds 1800`, and 15 minutes for packaging and the freeze record.
That is 2 h 15 min, so the latest start is **14:45 UTC / 17:45 Istanbul**. After
that, record that the rehearsal cannot finish before release and do not freeze.

## Steps

1. **Frozen checkout.** `git -C <main checkout> fetch origin`, then
   `git -C <main checkout> worktree add --detach <new path> <accepted commit>`.
   `git status --porcelain` must print nothing.
2. **Clean source.** `python3.11 tools/rehearse_package.py --output-dir artifacts/rehearsals/<new name>`
   must report `"status": "passed"`. Record `archive_sha256`,
   `source_inventory_sha256`, `configuration_sha256` and the test count in
   `check-02.log`.
3. **Exclusions.** `python3.11 tools/package_submission.py --check --source-only`
   passes. Inputs and run outputs stay under ignored `artifacts/`
   (`git check-ignore -v artifacts/rehearsal-inputs/<file>` names
   `.gitignore:23`). The packager also skips `generated`, `holdout`, `results`,
   `traces`, `out`, `.git`, `.venv` and `.env`. After copying inputs,
   `git status --porcelain` must still print nothing.
4. **Runtime readiness.** `python3.11 -m builder.preflight` checks Python 3.11,
   the Codex version and sign-in without a model call. Then run
   `python3.11 -m builder.preflight --probe-isolation --model <accepted model> --output artifacts/runtime-preflight-probe.json`,
   which uses synthetic fixtures and makes no Freya call. Each builder
   invocation still runs its own audit; the probe does not replace it.
5. **Lease.** S06 reads
   `/Users/demirekinarikan/VsCode/freya_hackathon/artifacts/.simulation.json`
   and confirms the actual server status before any live call. Never replace a
   running simulation.
6. **Two builds.** Run one build per approved fresh input bundle: a raw brief
   set and a changed-schema brief set. Give each its own output directory and
   slot, and pass `--simulation-lease` with the canonical lock path (command
   shape in REHEARSAL.md). Each build gets at most 1,800 s wall clock,
   including audit and export. Keep every failed attempt.
7. **Export and provenance.** Write an ignored manifest that names the two runs
   `hidden-1` and `hidden-2`. The packager requires exactly those keys, distinct
   workflows, traces, runs and input bundles, and one shared builder source.
   Make a local, unpushed evidence commit as described in
   [SUBMISSION-PACKAGE.md](SUBMISSION-PACKAGE.md), then run
   `python3.11 tools/package_submission.py --check --manifest artifacts/<rehearsal manifest>.json`.
   It must report `"export_trace_consistency": "checked"`. This is a dress
   rehearsal of T18's check, not a submission.
8. **Hosted critical cases, three repetitions, via S06.**
   `python3.11 -m benchmarks.hosted --execute --agent-id <published agent> --cases <evaluator-only cases> --reps 3 --simulation-lease <canonical lock> --deployment-manifest <verified build manifest> --out artifacts/<new name>.json`
   exits nonzero unless every case passes. **Gap:** no code or fixture defines
   a "critical" case set, and `fixtures/hosted.json` covers only public briefs
   A and B. T16 and S06 must name the case file for each fresh bundle before
   this step can run. Evaluation cases never enter generation.
9. **Freeze record.** Record this only after steps 2 to 8 pass: the accepted
   commit and merged PR; Python patch and Codex versions; the model and
   reasoning effort; the 1,800 s deadline, 300 s export reserve and two
   repairs; the kit fingerprint; the step 2 hashes; both run directories;
   publication and export IDs; and the three-repetition verdicts. Post it on
   issue 18. Any later source or configuration change means repeating steps 2
   to 8.
10. **Handoff and closure.** Give the freeze record to T18
    ([19](https://github.com/DemirEkinArikan/freya_hackathon/issues/19)) and S07.
    Then run `tools/cowork.py complete T17 --commit <checked head> --pr <merged PR> --checks "<evidence>"`.

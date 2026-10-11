# Final-window dry run, 11 October 2026

This file records a dry run of tonight's final-window pipeline on stand-in builds,
and gives one ordered command sequence for **20:00–23:00 Istanbul (17:00–20:00 UTC)**.
It is **not** the T17 final banking rehearsal ([18](https://github.com/DemirEkinArikan/freya_hackathon/issues/18)),
not a hidden build and not a submission. The kit names no submission destination.
The organizers send the submission steps by email before 20:00 Istanbul; use only
those. [T18-FINAL-WINDOW.md](T18-FINAL-WINDOW.md), [SUBMISSION-PACKAGE.md](SUBMISSION-PACKAGE.md),
[T18-SUBMISSION-CHECKLIST.md](T18-SUBMISSION-CHECKLIST.md) and
[S07-BUNDLE-VERIFICATION.md](S07-BUNDLE-VERIFICATION.md) keep their rules. This file
adds measured timings and puts their commands in one order.

## Result

- Every step after the builds ran with the exact repository tools. Together the steps
  took **7–11 s**, plus 7–9 s for the freeze guard, which runs before 20:00. Packaging
  and verification are not what threatens the 22:45 send-by time. The builds and the
  organizer steps are.
- The stand-ins could not reach the final package path. Both are generate-only builds,
  and both failed in policy extraction. The packager correctly refuses them, so its
  live-only checks have not yet run on bytes from the current builder.
- Starting both hidden builds together at about 20:05, instead of one after the other,
  saves about 30 minutes. If both first attempts succeed, the package is ready to send
  at about 20:45.
- No defect was found in `tools/package_submission.py` or the S07 verifiers. The
  friction points and their workarounds are listed below.

## What ran

| Item | Value |
|---|---|
| When | 10:18–10:33 Istanbul (07:18–07:33 UTC), two complete runs |
| Frozen-source stand-in | main `3dad5dd`, fresh detached checkout. `builder/` and `benchmarks/` are byte-identical to `3f2399d`, where the stand-ins were built. 334 source files, inventory `95bf9a29…` |
| `dry-run-1` | freya-fast-path public-A run `20261011T065819485521Z`: kit Brief A and kit OpenAPI, generate-only. FAILED after 1020 s: `policy contains unsupported constraints` on all 3 attempts |
| `dry-run-2` | public-B run `20261011T065857076810Z`: Brief A, then Brief B, and kit OpenAPI, generate-only. FAILED after 1051 s. Two policies were rejected by the confirmation-pair defect (fixed by #142), and the third attempt timed out |
| Labels | `dry-run-1` and `dry-run-2`, never `hidden-1`/`hidden-2` |
| Evidence commit | local only, never pushed |
| Credentials | the private `.env` is loaded into each child process by the repository's data-only parser, the same loader the S06 and T14 launchers use. No value is printed |
| Packager | tools at `3dad5dd`. #144, merged at 10:34, also retains `execution-requirements.json`. It does not change these timings, and the pre-check list in step 8 picks it up automatically |

The run directories were copied read-only, and the original worktrees were not touched.
No build, model call, platform call, simulation, lease change or holdout use happened.

## Measured steps

| Step | Command | Seconds | Result |
|---|---|---|---|
| Frozen checkout (before 20:00) | `git worktree add --detach`, `git status --porcelain` | 0.2 | clean |
| Freeze guard (before 20:00) | guard in T18-FINAL-WINDOW.md | 6.8–8.9 | pass, 334 files |
| Runtime preflight (before 20:00) | `python3.11 -m builder.preflight` | 0.1–0.2 | pass, Codex `0.162.0-alpha.17.2`, ChatGPT sign-in, no model call |
| Store inputs and pins | pins script below | 0.04–0.06 | pass |
| Inspect runs | inspect script below | 0.03–0.06 | both `failed`, both audits `pass` |
| Evidence add and commit | `git add -f`, `git commit` | 0.08–0.11 | local commit |
| Evidence PR gates | `verify_kit.py`, `package_submission.py --check --source-only`, `git diff --check` | 0.7–0.8 | pass: 16 kit files, 334 source files |
| Manifest check | `package_submission.py --check --manifest` | 0.5–0.6 | exit 1 at the label gate, as expected |
| ZIP | `package_submission.py --source-only --output …` | 0.6–0.8 | 334 files |
| ZIP test and hash | `python3.11 -m zipfile -t`, `shasum -a 256` | 0.06–0.08 | pass |
| Bundle verifier | `verify_submission_bundle.py --commit <evidence commit>` | 4.6–8.0 | `preparation_verified` |
| Hidden-release verifier | `verify_hidden_release.py --official-pins …` | 0.04–0.05 | exit 1 at the label gate, as expected |
| Extraction check | snippet in SUBMISSION-PACKAGE.md | 0.03–0.05 | stops at `source_only is False`, as expected for a source-only ZIP |

A final ZIP adds about 40 evidence files, and the bundle verifier adds two guarded
recompilations. Expect a few seconds more, not minutes.

GitHub calls, measured on this document's PR: `git push` 1.8 s and `gh pr create`
2.6 s. The merge call is the same kind of request; its time is on issue 19.

The scripts in the sequence below were also run here, with the labels changed to
`dry-run-N`:
- The pins match the driver's pins.
- The builder's own argument parser accepts every launcher flag. This was a parse
  only; no build ran.
- The manifest writer refuses to continue while no exported run exists.
- The pre-check reaches `check_manifest` through `withenv`.
- `git --literal-pathspecs add -f --pathspec-from-file` added a file whose name has a
  space and glob characters.
- `git diff --check` failed on an organizer-style file with trailing spaces and CRLF,
  and passed once that directory was excluded (friction 7).

### Binding probe on the stand-ins

The packager rejects a manifest labelled `dry-run-N` at its first check (friction 2).
So a throwaway probe called the packager's validators on each stand-in directly,
with the real credential values loaded. The results were the same for both stand-ins:

| Binding | Result |
|---|---|
| `validate_trace` (exact) | rejected: `Offline, generated-only or failed traces cannot establish final deployment` |
| `validate_run` (exact) | rejected: `Selected build manifest must describe a successful live export` |
| Credential exclusion (exact `check_content`, known values loaded) | pass on every retained file |
| Trace start and end, ordered times, one-line model-call purposes, ≤1800 s | pass (1020.5 s and 1050.1 s) |
| Input files against the trace `inputs` event | match, after copying into the per-label input directory with the original names |
| Manifest inputs, provenance, selected attempt, deadline, generated artifacts | not checkable: a failed run records no sources, no `build-provenance.json`, no attempt and no elapsed time |
| Export envelope, `experimental-5`, deployment and version IDs, export and deployment attempt records | not checkable: generate-only builds never publish |
| Hidden-release window | rejected: the traces ran 06:58–07:16 UTC, before the 17:00 UTC release |
| Hidden-release pins | rejected: no official pins exist yet, and the inputs are the public kit files (`d57c049c…`, `c59c7009…`, `9a8410b1…`) |

The window and pin checks reject the stand-ins, as they must. Most other bindings
could not be shown to pass, because these are failed generate-only runs.

No live stand-in existed either. The S06 baseline lane started two live builds in
parallel at 10:18, without hosted tests: public A and A+B at baseline source
`2d09c31`. Both failed. A+B timed out in policy extraction after 1050 s. A finished
policy extraction (1004 s) and the workflow (303 s), then stopped at publication after
1312 s with `Platform read-back changed or stripped an authored workflow field`. Main
has changed `builder/stored_workflow.py` since `2d09c31` (`e989ba2`), so this proves
nothing about current main. It does mean no live export exists from today.

### Build durations measured on 10–11 October

| Run | Mode | Result | Seconds |
|---|---|---|---|
| public-A `20261011T065819485521Z` (`3f2399d`) | generate-only | failed; policy attempts 471, 272 and 268 s | 1020 |
| public-B `20261011T065857076810Z` | generate-only | failed; 2 policies rejected, then a timeout | 1051 |
| train-1 `20261011T065917686344Z` | generate-only | failed; policy attempts 416, 277 and 266 s | 967 |
| train-2 `20261011T065929196936Z` | generate-only | failed; 474 s, 330 s, then a timeout | 1050 |
| public-A `20261010T234440089175Z` (t05 branch) | live | exported; policy 282 s, workflow 389 s, publish and export 13 s | 687 |
| public A+B `20261010T224748051958Z` | generate-only | validated; policy 577 s, workflow 622 s | 1205 |
| S06 baseline A `20261011T071836290580Z` (`2d09c31`) | live | failed at publication read-back; policy 1004 s, workflow 303 s | 1312 |
| S06 baseline A+B `20261011T071840314623Z` (`2d09c31`) | live | failed; policy timeout | 1050 |

A failed build costs 16–22 minutes. A successful one took 11–20 minutes. The builder
stops itself at 30 minutes. Publishing and exporting take about 13 seconds. The four
`3f2399d` builds ran at the same time with no conflict, and so did the two live
baseline builds.

## Against the 15-minute buffer and 22:45

- After both builds end, the verified ZIP and the attachments are ready in about
  5 minutes: under 15 s of tools, about 2 minutes to commit, push, open and merge the
  evidence PR, and about 2 minutes to copy and hash the attachments. That fits inside
  the 15-minute buffer, but the buffer is kept for sending and the receipt only.
- With three attempts per brief started 20:02–20:07, all of them end by about 20:37.
  If each brief has one eligible attempt, the package is ready to send by about 21:00.
- The latest start for a retry round is **21:45**. It ends by 22:15, which leaves
  30 minutes to select, package and verify before 22:45 (T18-FINAL-WINDOW.md).

## Friction points

1. **The stand-ins never reached the final package path.** The packager needs a
   successful live export, and the only stand-ins were failed generate-only runs. The
   live-only checks (export envelope, `experimental-5`, deployment and version IDs,
   export and deployment attempt records, final trace deployment) have not run on
   current-builder bytes. The only live builds today (S06 baseline, `2d09c31`) failed
   before export, one of them at publication read-back. As soon as a live exported
   pair from the accepted candidate exists (T15's structured A and A+B, or the T17
   live rehearsal), run steps 8 and 10 below on it. Use a separate scratch checkout,
   replace `artifacts/submission` with `artifacts/dry-run` in the scripts, and never
   push or send the result.
2. **The labels are fixed.** `package_submission.py`, `verify_submission_bundle.py` and
   `verify_hidden_release.py` all accept only `hidden-1` and `hidden-2`. A manifest
   labelled `dry-run-N` stops at the first check (`Require exactly two workflows,
   traces, build manifests and input sets`; `Official pins and package must cover
   exactly hidden-1 and hidden-2`). A full rehearsal therefore has to use the hidden
   labels, as T17-FINAL-WINDOW.md step 7 does. Keep such a package under
   `artifacts/dry-run/`, never under `artifacts/submission/`; the hidden-release
   verifier is what rejects it. No code change is needed for tonight.
3. **The sequential schedule costs 30 minutes.** A build without its own hosted tests
   never takes the simulation lease: `builder/platform.py` takes it only inside
   `run_simulation`. Slot registry writes are serialized by `.freya-slots.lock`. Give
   each label its own output directory and start both builds at once.
4. **Failed builds are slow.** All four builds on current runtime failed at 16–18
   minutes, and a live build can still fail at publication after 22 minutes. In parallel, each brief can have up to four attempts of at most 30 minutes
   started by 22:00 (20:05, 20:35, 21:05 and 21:35). One after the other, each brief
   gets about two. Tonight's plan instead starts three attempts per brief at once,
   and the latest retry start is 21:45.
5. **The credential scan depends on the process environment.** The frozen checkout has
   no `.env`, which is correct. But the packager and bundle verifier, run plainly,
   then know no credential values and fall back to pattern checks. Run them through
   the same data-only loader as the builds (`withenv` below).
6. **Evidence selection was manual.** SUBMISSION-PACKAGE.md asks for the exact files to
   be force-added by hand. Instead, the pre-check below calls the packager's
   `check_manifest` with the manifest commit set to the current HEAD. That validates
   every binding before anything is committed, and it writes the exact retained file
   list, which `git add -f --pathspec-from-file` then uses. When the T18 lane retains
   more files (for example `execution-requirements.json`), the list follows
   automatically.
7. **`git diff --check` can fail on organizer bytes.** Raw bank material can carry
   trailing spaces or CRLF line endings. `git diff --check` flags both, and the bytes
   must not be edited. On the evidence PR, run it with the organizer input directory
   excluded, and record that exclusion.
8. **The pins file has a fixed shape.** `verify_hidden_release.py` compares
   `{"briefs": [...], "tool_api": ...}` exactly. Keep receipt metadata in a separate
   file, never in the pins file.
9. **Extraction-check failures carry no message.** The snippet in SUBMISSION-PACKAGE.md
   uses bare `assert`s, so a failure shows only a line number. It cannot be rehearsed
   before a final ZIP exists.
10. **Organizer-side values are unknown.** The submission channel, attachment names,
    team identifier and any import-safe credential rule arrive only by email. The
    manifest `team` field and the attachment names depend on them.
11. **No accepted candidate exists yet.** At 10:30 Istanbul every build on current
    runtime had failed policy extraction (four of four), and the T15, T16 and T17
    gates were open. Tonight needs the T17 freeze values below. This dry run cannot
    supply them.

## Tonight: 20:00–23:00 Istanbul

Run every command from the frozen checkout. Times are Istanbul, with UTC in brackets.

### Before 20:00 (17:00 UTC)

1. Fill the freeze values from the T17 freeze record on issue 18, never from this
   dry run:

   ```sh
   MAIN=/Users/demirekinarikan/VsCode/freya_hackathon
   FROZEN=/Users/demirekinarikan/VsCode/freya_hackathon-worktrees/t18-frozen-final
   LEASE=$MAIN/artifacts/.simulation.lock
   T18_SOURCE_COMMIT=<from the T17 freeze>
   T18_SOURCE_INVENTORY_SHA256=<from the T17 freeze>
   T18_BUILDER_MODEL=<from the T17 freeze>
   T18_REASONING_EFFORT=<from the T17 freeze>
   export PATH="$PATH:/Users/demirekinarikan/.vscode/extensions/openai.chatgpt-26.1007.21434-darwin-arm64/bin/macos-aarch64"
   unset OPENAI_API_KEY
   withenv() { /opt/homebrew/bin/python3.11 -I -c 'import os, sys; sys.path.insert(0, os.getcwd()); from pathlib import Path; from builder.config import load_local_environment; load_local_environment(Path(sys.argv[1])); os.execv(sys.executable, [sys.executable, *sys.argv[2:]])' "$MAIN/.env" "$@"; }
   ```

2. Create the frozen checkout and run the guard from T18-FINAL-WINDOW.md (measured
   7–9 s). Create it before 19:30, so editor indexing of the new worktree finishes
   before the builds start.

   ```sh
   git -C "$MAIN" fetch origin
   git -C "$MAIN" worktree add --detach "$FROZEN" "$T18_SOURCE_COMMIT"
   cd "$FROZEN"
   test "$(git rev-parse HEAD)" = "$T18_SOURCE_COMMIT" && git diff --quiet HEAD -- && test -z "$(git status --porcelain)"
   # then the python3.11 - "$T18_SOURCE_INVENTORY_SHA256" guard from T18-FINAL-WINDOW.md
   python3.11 -m builder.preflight
   ```

3. Write the two helper scripts. They live under ignored `artifacts/final-window/` and
   are never committed. `tools/final_attempts.py` replaces the earlier `launch.py`,
   `inspect_runs.py` and `write_manifest.py` helpers: it launches the parallel
   attempts, selects one run per brief by the declared order in
   [T18-FINAL-WINDOW.md](T18-FINAL-WINDOW.md#declared-selection-order) and writes the
   manifest. This writes the pins:

   ```sh
   mkdir -p artifacts/final-window artifacts/hidden-inputs/hidden-1 artifacts/hidden-inputs/hidden-2
   cat > artifacts/final-window/pins.py <<'PY'
   import hashlib, json
   from pathlib import Path
   spec = json.loads(Path("artifacts/hidden-inputs/spec.json").read_text())
   sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
   pins = {label: {"briefs": [sha(b) for b in item["briefs"]], "tool_api": sha(item["tool_api"])} for label, item in spec.items()}
   Path("artifacts/official-pins.json").write_text(json.dumps(pins, indent=2) + "\n")
   print(json.dumps(pins, indent=2))
   PY
   ```

   This pre-check lists the files the packager will retain, including both
   `selection.json` records:

   ```sh
   cat > artifacts/final-window/precheck.py <<'PY'
   import json
   from pathlib import Path
   from tools import package_submission as package
   manifest = json.loads(Path("artifacts/submission/manifest.json").read_text())
   paths = sorted({path.relative_to(package.ROOT).as_posix() for path, _ in package.check_manifest(manifest)})
   Path("artifacts/submission/evidence-files.txt").write_text("\n".join(paths) + "\n")
   print(json.dumps({"bindings": "pass", "evidence_files": len(paths)}))
   PY
   ```

4. Read the organizer email as soon as it arrives. Fill the channel table in
   T18-SUBMISSION-CHECKLIST.md: destination, attachment names, team identifier and
   credential/import rules.

### 20:00 (17:00 UTC): inputs

5. Save each organizer file unchanged under `artifacts/hidden-inputs/hidden-N/`, keeping
   its original name. Write `artifacts/hidden-inputs/spec.json` with every brief in the
   organizer's order and the matching API. Then pin (measured 0.04–0.06 s):

   ```sh
   cat > artifacts/hidden-inputs/spec.json <<'JSON'
   {"hidden-1": {"briefs": ["artifacts/hidden-inputs/hidden-1/<brief>"], "tool_api": "artifacts/hidden-inputs/hidden-1/<api>"},
    "hidden-2": {"briefs": ["artifacts/hidden-inputs/hidden-2/<file 1>", "artifacts/hidden-inputs/hidden-2/<file 2>"], "tool_api": "artifacts/hidden-inputs/hidden-2/<api>"}}
   JSON
   python3.11 - < artifacts/final-window/pins.py
   ```

   Record the receipt time and the sender of the files in a separate local note, not in
   `official-pins.json`.

### About 20:02 (17:02 UTC): start three attempts per brief at once

6. Start the attempts. That is six builders, each in its own
   `artifacts/final/<label>/attempt-<k>/` with its own new slot. Starts alternate
   between the briefs, 60 s apart, so the launcher runs for about 5 minutes; `nohup`
   keeps it going if the terminal closes:

   ```sh
   nohup python3.11 tools/final_attempts.py launch --env-file "$MAIN/.env" --simulation-lease "$LEASE" \
     --model "$T18_BUILDER_MODEL" --reasoning-effort "$T18_REASONING_EFFORT" > artifacts/final-window/launch.log 2>&1 &
   ```

   Each builder stops itself at 1,800 s, so all of them end by about 20:37. None
   takes the simulation lease, because none runs its own hosted tests.
7. While they run, poll about once a minute with
   `python3.11 tools/final_attempts.py health --env-file "$MAIN/.env"; withenv tools/final_attempts.py select`.
   A failed attempt whose error names `HTTP 50x` hit a platform failure, not a
   builder fault (T18-FINAL-WINDOW.md).
   It prints one JSON line per attempt (status and the five criteria) and exits 2
   while any attempt still runs. Exit 3 means a brief has no eligible attempt. Only
   then, and no later than **21:45**, start a retry round for that brief alone:
   add `--labels hidden-N` to the launch command. The new attempts continue the
   numbering (4, 5, 6), and every earlier attempt stays untouched. Never retry a
   brief that already has an eligible selection, and wait for a `health` probe with
   `ok: true` first.

### When `select` exits 0 (about 20:40)

8. `select` has written `artifacts/final/<label>/selection.json` for both briefs,
   naming the selected attempt k of N. Both selected runs need the same
   `builder_source_sha256`, which the pre-check checks. Then write the manifest and
   run the pre-check. The manifest commit is still the frozen HEAD at this point.

   ```sh
   python3.11 tools/final_attempts.py manifest --team "<organizer team identifier>"
   withenv - < artifacts/final-window/precheck.py
   ```

   If the pre-check fails, read the packager's message. Never edit generated files
   or a selection record; rerun `select`, or retry the brief if the cause is in its runs.
9. Make the evidence commit and run the evidence-PR gates locally:

   ```sh
   git switch -c rapid/t18-final-evidence-20261011
   git --literal-pathspecs add -f --pathspec-from-file=artifacts/submission/evidence-files.txt
   git add -f artifacts/final/health.jsonl artifacts/final/hidden-*/attempt-*/launch.json  # kept, never packaged
   git commit -m "T18 final evidence: selected hidden-1 and hidden-2 runs"
   EVID=$(git rev-parse HEAD)
   python3.11 tools/verify_kit.py && withenv tools/package_submission.py --check --source-only \
     && git diff --check "$T18_SOURCE_COMMIT" "$EVID" -- . ':(exclude)artifacts/hidden-inputs'
   ```

10. Package and verify on `$EVID` (under 15 s):

    ```sh
    withenv tools/final_attempts.py select
    python3.11 tools/final_attempts.py manifest --team "<organizer team identifier>"
    withenv tools/package_submission.py --check --manifest artifacts/submission/manifest.json
    withenv tools/package_submission.py --manifest artifacts/submission/manifest.json --output artifacts/submission/final.zip
    python3.11 -m zipfile -t artifacts/submission/final.zip
    shasum -a 256 artifacts/submission/final.zip
    # then the extraction check from SUBMISSION-PACKAGE.md, unchanged
    withenv tools/verify_submission_bundle.py --archive artifacts/submission/final.zip --source-root "$FROZEN" --commit "$EVID"
    python3.11 tools/verify_hidden_release.py --archive artifacts/submission/final.zip --official-pins artifacts/official-pins.json
    ```

    Running `select` again recomputes the declared order from the kept attempts.
    It must reproduce the committed records byte for byte; a changed record is not
    in the evidence commit, and the packager check fails closed. Running `manifest`
    again sets the commit to `$EVID`. The bundle verifier reports
    `attempt_selection`; post it with the reports. Send nothing unless both
    verifiers pass.
11. Push the evidence commit and merge its PR on the matching head (about 2 minutes).
    Then post both verifier reports, the ZIP SHA-256, `$EVID` and the PR on issue 19.

    ```sh
    git push origin "HEAD:refs/heads/rapid/t18-final-evidence-20261011"
    gh pr create -R DemirEkinArikan/freya_hackathon --base main --head rapid/t18-final-evidence-20261011 \
      --title "T18 final evidence" --body "Selected hidden-1/hidden-2 runs; package checks are on issue 19."
    gh pr merge <number> -R DemirEkinArikan/freya_hackathon --merge --match-head-commit "$EVID"
    ```

12. Copy the attachments byte for byte, using the organizer's file names. Each
    `<hidden-N run>` is that label's `runs` directory in `artifacts/submission/manifest.json`:

    ```sh
    mkdir -p artifacts/submission/attachments
    cp "<hidden-1 run>/submission.workflow.json" "artifacts/submission/attachments/<organizer brief 1 name>.workflow.json"
    cp "<hidden-2 run>/submission.workflow.json" "artifacts/submission/attachments/<organizer brief 2 name>.workflow.json"
    # traces and any other required files the same way, as the organizer email names them
    shasum -a 256 artifacts/submission/attachments/* "<hidden-1 run>/submission.workflow.json" "<hidden-2 run>/submission.workflow.json"
    ```

### Send by 22:45 (19:45 UTC)

13. Send through the channel in the organizer email, and only that channel. Keep the
    receipt. Fill the send and receipt table in T18-SUBMISSION-CHECKLIST.md, and post it
    on issue 19 without credentials or transcripts. 22:45–23:00 is kept for sending and
    the receipt only. The organizer deadline is 23:00 (20:00 UTC).

## Still open before tonight

- The T17 freeze record: source commit, inventory SHA-256, model and reasoning effort.
  This needs an accepted candidate, so T07 fixes and then T15, T16 and T17 first.
- The organizer email: submission channel, attachment names, team identifier and any
  credential/import rule.
- One run of steps 8 and 10 on the first live exported pair from the accepted
  candidate, in a scratch checkout (friction 1).

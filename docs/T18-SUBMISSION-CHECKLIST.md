# T18 submission checklist

[T18-FINAL-WINDOW.md](T18-FINAL-WINDOW.md) owns the builds and timing.
[SUBMISSION-PACKAGE.md](SUBMISSION-PACKAGE.md) owns the manifest, ZIP and
extraction check. This file adds the kit rules those two leave implicit and
the records to fill in during the final window. It records no submission.
Organizer instructions, when supplied, override this file.

## Kit rules that shape the final window

From the [kit README](../build-the-builder/README.md) "The final" and "Rules":

- Per final brief, submit the exported phone agent and the trace of the run
  that built it; also submit the builder code. One trace per brief.
- A builder run on one brief may take 30 minutes, including any test it runs on
  its own agent. The builder may run more than once; submit one run's agent with
  that run's trace, and no manual step inside a run. Tonight each brief gets the
  [attempt matrix](T18-FINAL-WINDOW.md#attempt-matrix) from `tools/final_attempts.py launch`:
  round 1 is two strict Claude attempts (`claude-opus-5-5`, `high`). Round 2, two
  relaxed ones, runs only for a brief that round 1 left without a selected attempt, or with a
  selected deterministic fallback export. `select` picks one by the order declared in
  [T18-FINAL-WINDOW.md](T18-FINAL-WINDOW.md#declared-selection-order), which
  prefers a model-plan export, then a strict one. Every attempt stays on disk.
- The kit exports with `freya.export_workflow(workflow_id, "<brief>.workflow.json")`.
  The builder writes the same envelope to `<run>/submission.workflow.json`, and
  the ZIP stores it as `workflows/hidden-N.workflow.json`. For upload, copy those
  bytes unchanged to `<brief>.workflow.json`, using the organizer's name for that
  brief, unless the organizer names files differently. Never re-serialize or edit.
- The organizer loads the exported file into its own workspace: the file is
  what gets scored, not the hosted slot.
- Equal scores rank by total promised amount, then shorter build time, then
  earlier submission. Send as soon as the verified package is ready. 19:45 UTC /
  22:45 Istanbul is the latest send start, not the target. Build time breaks
  ties only after score: never pick a faster run over one with better evidence.

## Authorized channel record

Fill in from organizer-originated instructions only. A relayed summary needs
the original message. The reviewed kit names no destination.

| Field | Value |
|---|---|
| Instruction source (message/page, sender, received UTC) | |
| Destination, exactly as the organizer wrote it | |
| Required attachments, names, format, size limit | |
| Required metadata (team, repo, commit, other) | |
| Export credential/import requirements | |
| Receipt mechanism (page, email, ID) | |
| Organizer deadline, checked against 20:00 UTC | |

A field still empty at 19:15 UTC / 22:15 Istanbul: ask the organizer through
the channel the instructions came from. Do not guess an address.

## Before sending (on the evidence HEAD)

- [ ] `final_attempts.py select` exited 0. Both `artifacts/final/<label>/selection.json`
      records name the selected attempt k of N and its mode, and no attempt directory was
      removed or edited. A relaxed selection is named as relaxed in the issue 19 post, and a deterministic fallback selection as a fallback. The manifest came from `final_attempts.py manifest`, so it
      has `selections`. `health.jsonl` and every attempt's `launch.json` are in the
      evidence commit.
- [ ] Each selected run's `builder-source.json` and trace name its provider, model and
      effort, and its `launch.json` argv (the manifest's `run_command`) passes
      `--provider` and `--model`. The organizers may rerun it with `FREYA_API_KEY` and
      their own `ANTHROPIC_API_KEY`; no key is in any evidence file.
- [ ] A relaxed selection's `trace.jsonl` lists its prompt-enforced limitations: one
      `relaxed_declarations` event with `prompt_enforced_limitations` (each
      `enforced_by: dialogue_prompt`) and `unguarded_close_codes`, the same lists as its
      `manifest.json`. The issue 19 post names them as not enforced by the controller.
- [ ] `package_submission.py --check --manifest`, ZIP write, `zipfile -t` and
      the extraction check in SUBMISSION-PACKAGE.md all pass.
- [ ] On that ZIP, `verify_submission_bundle.py --commit <evidence commit>` and
      `verify_hidden_release.py --official-pins <operator pins>` both pass
      ([S07-BUNDLE-VERIFICATION.md](S07-BUNDLE-VERIFICATION.md)). Their two JSON
      reports and the ZIP SHA-256 are posted on issue 19. Do not send otherwise.
      The bundle report shows `attempt_selection` for both labels.
- [ ] Each `<brief>.workflow.json` and trace attachment has the same SHA-256 as
      its ZIP member and its selected run file.
- [ ] Evidence HEAD pushed; its PR merged with matching head.
- [ ] Clock is before 19:45 UTC.

## Send and receipt record

| Field | Value |
|---|---|
| Send start and end (UTC and Istanbul) | |
| Destination used | |
| Attachment names and SHA-256 | |
| Package ZIP SHA-256 | |
| Selected attempt per brief (k of N, run directory) | |
| Evidence commit | |
| Receipt, verbatim (no credentials) | |
| Local private screenshot path | |

A draft, a local ZIP or a merged PR is not a submission. Post these fields on
issue 19 without credentials or transcripts.

## Contingencies

- **A brief has no eligible attempt (`select` exits 3).** After round 1, launch
  `--round 2 --labels hidden-N` for that brief alone. If every strict attempt ended
  on a platform error (or a Claude API rate-limit or overload error), launch another
  `--round 1` instead. After round 2, one more
  round may start, no later than 18:45 UTC / 21:45 Istanbul. Each round adds new
  attempt numbers, and every earlier attempt stays. Then run `select` again.
- **A brief's selected attempt is a deterministic fallback export.** It stays
  eligible and is kept. If time allows, launch `--round 2 --labels hidden-N` for that
  brief as above, because any model-plan export ranks before the fallback.
- **One brief has no eligible run by 19:15 UTC.** The packager needs exactly two
  runs and refuses a one-run package. Send the eligible run's export and trace
  with `package_submission.py --source-only` code, and state that the other brief
  failed. Never submit an over-time or failed run as eligible. T18 is then not
  complete as specified; record the exact failure.
- **Claude unavailable (key rejected, API down).** Launch the round with
  `--providers codex` for the old Codex cells, as the attempt matrix describes.
  Never put a key in a command line or a file in the repository.
- **Channel still absent at 19:45 UTC.** Keep the verified ZIP and attachments,
  post the blocker on issue 19 with times, and send nothing to a guessed address.
- **Upload fails or no receipt.** Retry through the same channel; record each
  attempt's time and error. Do not change files between retries. A changed
  package gets a new file name and hash, is re-verified, and the earlier file stays.
- **Rollback.** A later retry that fails or overruns never replaces a selected
  eligible run: its run directory and export stay the selection. Do not
  republish or edit the hosted agent to restore it; the export is what gets
  scored. Write each package retry to a new output name so the earlier verified
  ZIP and its hash stay.

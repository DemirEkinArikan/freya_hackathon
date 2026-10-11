# S07 independent bundle verification

S07 supports [T17](https://github.com/DemirEkinArikan/freya_hackathon/issues/18)
and [T18](https://github.com/DemirEkinArikan/freya_hackathon/issues/19). Its
verifier reads an existing ZIP and checks it against an independently chosen
repository commit. It never repairs evidence, invokes generation, calls Freya,
publishes a workflow or submits attachments. Package production remains in
[SUBMISSION-PACKAGE.md](SUBMISSION-PACKAGE.md); clean source reproduction remains
in [REHEARSAL.md](REHEARSAL.md). The verifier was written by the Codex S07 lane
(draft #118, `eb449f6`) and brought to main by Claude S07 through a handoff
recorded on issue 19.

## Read-only command

Run the verifier from a reviewed source checkout with Python 3.11:

```sh
python3.11 tools/verify_submission_bundle.py \
  --archive artifacts/submission/final.zip \
  --source-root /path/to/reviewed/repository \
  --commit <full-40-character-reviewed-evidence-commit>
```

The same command accepts a source-only rehearsal archive. Supply the exact
reviewed source commit for that archive; for a final ZIP, supply the evidence
commit that contains the reviewed source and selected evidence. Resolve the
commit through the operator handoff and trusted repository, before inspecting
the ZIP. Copying its self-reported `submission_commit` into the command does not
establish independent trust. Preserve the operator's separate pre-invocation
source/configuration freeze record; the controller records runtime bytes and
configuration, and does not attest invocation Git HEAD.

The command returns JSON on stdout and exits 0 only after verification succeeds;
failure returns exit 1 with a sanitized error category. A successful report
records `status: preparation_verified` or `status: consistency_verified`,
`mode`, `source_only`, `commit`, `source_file_count`, `evidence_file_count`,
`archive_sha256` and `limitations`. Final reports additionally mark evidence
byte membership in the trusted commit; hosted authenticity, hidden-build
execution and submission receipt remain unverified. Retain the exact
command, trusted commit and report. A report belongs to that archive's raw
SHA-256; later rebuilt or changed bytes require a new check.

`final-consistency` accepts any two distinct, self-consistent runs labelled
`hidden-1`/`hidden-2`, including practice runs. For a final ZIP, run the
hidden-release check from
[#131](https://github.com/DemirEkinArikan/freya_hackathon/pull/131) after the
bundle check passes. It does not replace the bundle check:

```sh
python3.11 tools/verify_hidden_release.py \
  --archive artifacts/submission/final.zip \
  --official-pins /path/to/official-pins.json
```

The operator records the pins from the organizer's files at the 17:00 UTC
release, never from the ZIP. They are the sha256 of each official brief, in
brief order, and of the API. The check requires each label's archived inputs to
equal its pins, and each selected trace to fall between 17:00 and 20:00 UTC on
11 October. It relies on the bundle check for archive safety. Trace times are
self-reported. The check ties the labels to the official inputs, but it cannot
prove when or where a build actually ran.

The verifier does not execute archived Python or write to the archive or source
checkout. Its private temporary reconstruction is removed after trusted local
validators finish. It is not a replacement for T17's separate clean extraction,
full source checks and rebuild. Do not run broad clean-rebuild tests during the
final submission buffer.

## What the check establishes

| Check | Required relationship |
|---|---|
| Archive structure | Safe confined regular-file names; no duplicate or case-colliding names, symlinks, encrypted entries, file/parent collisions or unexpected members |
| Index and raw bytes | Strict JSON and complete disjoint source/evidence inventories; each raw SHA-256 equals the bytes in the ZIP |
| Trusted source | Complete allowed source inventory and every source byte match the externally selected Git commit, including the pinned kit and any verifier source present in that commit |
| Source exclusions | Recognized credential patterns/known process and repository `.env` values and prohibited private filenames/paths are rejected; unrelated artifact directories are excluded from the source inventory |
| Final mapping | Exactly two distinct selected run/export/trace/input sets; ordered briefs and matching API resolve to the retained bytes |
| Invocation binding | Original source/configuration, provenance, manifest and complete trace agree with submitted runtime source; both selected builds bind to the same configuration |
| Selected attempt | Frozen policy/contract, proposal/plan, compiler context, guarded graph, publication and export records agree for the selected bounded attempt |
| Action coverage | Recomputed execution requirements and the selected plan cover the source-selected capabilities before exact guarded recompilation |
| Eligibility consistency | Successful live-mode record, passing mandatory tool-free isolation audit before generation, fixed `experimental-5` phone model, permitted tool wiring and finite in-budget timing |
| Final evidence membership | Every archived evidence member belongs to the retained selected sets and equals a regular-file blob in the trusted commit; original remapped evidence Git-path correspondence remains unverified |

Raw ZIP byte hashes and controller canonical JSON digests have different roles;
do not substitute one for the other. A selected attempt may follow retained
failed attempts. Selecting it never permits rewriting failed records or
reconstructing original provenance retrospectively. A deployment in the current
persistent slot does not establish which version an earlier run exported.

Meaningful adversarial checks include an omitted second build, stale/cross-run
IDs, swapped APIs or ordered amendments, missing trace events, changed frozen
contracts or compiler artifacts, a wrong phone model, a deadline overrun,
credential-bearing files and malformed or unsafe ZIP/index structures. Preserve
the original failing archive and report; correct the underlying general source
problem and rerun affected builds when needed. Do not repair the package by
editing generated evidence.

## Proof limits and remaining gates

`mode: source-preparation` proves local archive/source consistency only. It does
not establish generation, unseen-policy adaptation, a live export, a banking
pass, an accepted freeze or T17 completion. T17 still needs its actual clean
builder rehearsal with approved fresh inputs/API, original source/audit/trace,
accepted publication/export inside 1,800 seconds, required critical cases
passing all three repetitions and completed technical dependencies.

`mode: final-consistency` establishes relationships among supplied files and a
trusted committed source inventory. For final evidence it requires each
archived file's raw bytes to equal a regular-file blob present in that commit;
only then does it report `evidence_commit_verified: true`. This is byte
membership, not original path identity: the current ZIP index remaps selected
evidence without preserving its original Git paths. Use the producer's exact
committed-path check and review original files in the trusted checkout to
establish that remaining correspondence. The declared repository URL must
also match the checkout's normalized configured GitHub origin; configuration
agreement does not authenticate remote server activity.

Local JSON, hashes and trace
timestamps cannot authenticate a hosted call, server receipt, genuine
publication, absence of manual generated changes or truthful provenance. A
fully self-consistent manually authored or forged bundle can satisfy local
relationships. Independently reviewed original invocation history, permitted
model/sign-in context, hosted publication/readback/export and any scored
conversation receipts remain necessary. The verifier's result must never be
reported as a live banking pass or actual submission.

Credential scanning has its own limit: it can reject recognized patterns and
known process-environment and trusted repository `.env` private values, but
cannot prove that an unknown opaque string is not a credential. The local
credential file is parsed as data with the repository's strict parser; it is
never sourced as shell code or printed. Review original exports and reconcile any required
tool authentication with organizer import requirements before packaging.
Structured JSON and JSONL evidence is scanned even when its filename uses a
different extension. The known-value scan never copies local credentials into
the archive or report.
Redacting a required credential can break runnable export/hash agreement; do
not manufacture a new provenance record for the redaction.

As of this preparation, official hidden input release is **11 October 2026,
17:00 UTC / 20:00 Istanbul**. No actual hidden build or final submission is
claimed here. The reviewed kit does not specify a submission destination;
obtain the real organizer instructions and receipt mechanism. Follow
[T18-FINAL-WINDOW.md](T18-FINAL-WINDOW.md) and
[T18-SUBMISSION-CHECKLIST.md](T18-SUBMISSION-CHECKLIST.md), and preserve **19:45–20:00 UTC /
22:45–23:00 Istanbul** for verified attachments, authorized delivery and receipt
inspection. A source merge, prepared ZIP or local verifier report cannot close
T17/T18 or issue 19.

## Existing local preparation evidence

The following paths are audit candidates, not current acceptance claims. They
were checked read-only without opening raw model events or private transcripts.
Their reports and archive bytes must be verified afresh if used.

| Artifact | Observed scope and limitation |
|---|---|
| `freya_hackathon/artifacts/source-rehearsal.zip` | Source-only ZIP, 68 source files, no evidence/submission/source binding; raw SHA-256 `977208004975daf41fdaff75b8a41b12fe0848773e9d67d9f4b5924cff4b0f87`. It is historical preparation, not the current source inventory. |
| `freya_hackathon-worktrees/t17-clean-rehearsal-20261010/artifacts/rehearsals/committed-bb97619 space-path/source.zip` | Historical source reproduction at clean trusted commit `bb97619145aa12a7b6bf80eda2473d19e5c14cd9`; 160 source files. Actual archive SHA-256 matches the companion report: `a11267874bd8e94f00c485648c72de94fec7ee7d04c71b59bad53284d76ee7fa`. There were no model/platform calls, consumed holdouts or candidate freeze. |
| Same directory's `report.json` | Retained local source-reproduction report with ten recorded return-code-zero checks. Report raw SHA-256 `a81ea00de96f0aa3126b28ccfc2c2757a2e80dce2526496130e7baf7f8ed86d0`; this document does not claim those checks reran on the S07 head. |

During S07 implementation, the independent CLI checked the historical
160-file archive against that unchanged trusted `bb976191...` checkout: exit
0, `preparation_verified`, and the archive SHA-256 above. The old 68-file main
archive returned exit 1, sanitized category `archive_index`, because its index
lacks the required evidence inventory. These smoke checks used an uncommitted
implementation in progress; they are not verification of the eventual final
S07 commit. Record the stable exact-head rerun separately.

The older coordination handoff also names
`/private/tmp/freya-t17-merged-clean-63eebe3/artifacts/rehearsals/merged clean source/report.json`;
that path was absent during this review. A handoff's recorded prior success is
not a retained artifact available for independent reinspection. The main
artifact inventory exposed no two-hidden-run final bundle to inspect.

Record new S07 exact-head tests, independent archive checks and clean rebuild
results in the checked checkpoint with their actual counts and hashes. Keep
historical evidence attributed to its own source, and report the missing live
acceptance/freeze/input/submission evidence explicitly.

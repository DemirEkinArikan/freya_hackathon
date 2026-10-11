# Submission package contract

The [final-window runbook](T18-FINAL-WINDOW.md) owns live execution and timing.
This document owns package preparation and review. A source-only ZIP is rehearsal
material. A final package requires two actual selected live exports, their complete
selected traces, original invocation provenance and committed matching evidence;
it does not itself prove an authentic server call or a correct conversation.

Merged [PR 53](https://github.com/DemirEkinArikan/freya_hackathon/pull/53)
(`155c502abb56e85829ed41f70ace0f072982c7b3`, head `3d091af`) introduced selected
attempt/input binding. The T18 preparation extends that contract to the original
pre-generation source/configuration snapshot and its bound manifest/trace.
Do not retrofit missing records onto an old run.

## Manifest and retained evidence

Create the submission manifest under ignored `artifacts/submission/manifest.json`.
The required fields are `team`, `repo`, `commit`, `run_command`, `workflows`,
`traces`, `runs` and `inputs`. Use the actual organizer team identifier and
repository URL. `commit` is the full 40-character **submission evidence HEAD**.
`run_command` records both exact selected invocations including their ordered
briefs, matching API, model, reasoning, slot, output and shared lease flags.
Each of the four mappings must contain exactly `hidden-1` and `hidden-2`.

The following is a structural template, **not usable final evidence**. Replace
all placeholders with reviewed actual paths/values; `briefs` preserves invocation
order and can contain multiple files. Keep original input filenames to preserve
the controller's source-name binding.

```json
{
  "team": "<actual-team-identifier>",
  "repo": "https://github.com/DemirEkinArikan/freya_hackathon",
  "commit": "<full-reviewed-evidence-HEAD>",
  "run_command": "<exact-hidden-1-command>\n<exact-hidden-2-command>",
  "workflows": {
    "hidden-1": "artifacts/<run-1>/submission.workflow.json",
    "hidden-2": "artifacts/<run-2>/submission.workflow.json"
  },
  "traces": {
    "hidden-1": "artifacts/<run-1>/trace.jsonl",
    "hidden-2": "artifacts/<run-2>/trace.jsonl"
  },
  "runs": {
    "hidden-1": "artifacts/<run-1>/manifest.json",
    "hidden-2": "artifacts/<run-2>/manifest.json"
  },
  "inputs": {
    "hidden-1": {
      "briefs": ["artifacts/hidden-inputs/hidden-1/brief.md"],
      "tool_api": "artifacts/hidden-inputs/hidden-1/openapi.json"
    },
    "hidden-2": {
      "briefs": ["artifacts/hidden-inputs/hidden-2/brief.md"],
      "tool_api": "artifacts/hidden-inputs/hidden-2/openapi.json"
    }
  }
}
```

Keep organizer receipt times/origin, original input SHA-256 bytes, frozen source
commit, configuration freeze evidence, selected run IDs/attempts, exported
workflow/agent/version IDs, graph/export digests and final package SHA-256 in
reviewed local handoff metadata. Add only organizer-required public submission
metadata; do not expose raw events, transcripts or credentials in issues.
No organizer destination/attachment requirements have been supplied in the
reviewed kit; the manifest is not a substitute for those instructions.

For each `runs` entry the packager resolves these original sibling files:

| Evidence | Required relationship |
|---|---|
| `builder-source.json` | Original pre-generation runtime inventory and resolved configuration; source bytes equal packaged runtime source |
| `build-provenance.json` | Exact original source record, manifest and complete trace binding |
| `attempts/plan-NNN/attempt.json` | Selected bounded attempt 1–3, exported/complete; input API, contract and export hashes agree |
| `attempts/plan-NNN/export.json` | Same platform export as canonical submitted export |
| `attempts/plan-NNN/deployment.json` | Same published workflow/agent/version and graph as selected manifest/trace/export |
| `attempts/plan-NNN/tests.json` | Required when selected trace records own hosted tests; agrees with manifest and trace |
| `policy.json`, `policy.contract.json` | Original frozen policy and self-fingerprinted contract agree with the selected attempt and build |
| `execution-requirements.json`, selected `attempts/plan-NNN/execution-requirements.json` | The same self-fingerprinted record, equal to the build's `execution_requirements_sha256`; its policy, contract and API fingerprints match the retained policy, contract and build |
| Selected `proposal.json`, `plan.json`, `workflow.json`, `guard-manifest.json` | Proposal contract/plan, authored workflow and guard fingerprints agree with selected attempt, build and compilation trace |
| Selected `action-bindings.json`, `execution-policy.json`, `guard-context.json` | Retained compiler context references the exact retained contract, plan, bindings and execution policy |
| Ordered input files and matching OpenAPI | Fingerprints agree with exactly one input trace event and selected manifest |

The full run/attempt directories remain protected local evidence, including failed
proposals/readbacks, archived plans/compiler artifacts and partial runner files.
Only reviewed files selected by the packager enter the submission ZIP. Selecting
an attempt never permits deleting failures or feeding evaluator artifacts back
into generation. The canonical root export/trace identify the selected candidate;
a current persistent slot is not authority for a historical selected run.

## Source commit and evidence commit

Record the frozen source commit before invocation and verify a clean runtime
checkout. The controller records actual runtime **bytes/configuration**, not
invocation git HEAD. That operator-recorded freeze commit must not be claimed as
a controller-attested invocation commit. Original `builder_source_snapshot` must
precede the first model call; `builder-source.json`/`build-provenance.json` and
manifest/trace hashes must agree. Both selected builds must match the exact
submitted runtime source, live Python 3.11, explicit model/reasoning, 1,800-second
deadline, 300-second reserve and two-repair setting. The mandatory passing
tool-free generation isolation audit must precede substantive generation.
The declared GitHub repository must match the checkout's origin. The ZIP index
records `source_binding.submission_commit`, both invocation source fingerprints
and `invocation_commit: null`: the controller does not record invocation git HEAD.
An operator freeze record supplies that separate commit evidence.

After inspecting both runs, make a separate **evidence commit** containing the
reviewed input/provenance/export/trace/selected attempt files and preparation
source. This later commit may differ from the frozen source commit because it
adds evidence; it cannot silently change the runtime bytes that produced either
run. Never recompute original provenance from that later checkout. Package
validation requires every allowlisted source file and selected evidence byte to
match `git show HEAD:<path>`. Set manifest `commit` to that reviewed evidence HEAD
*after* creating it. Keep the submission manifest itself in ignored artifacts:
embedding a commit's own hash into a file committed by that same commit is not
possible. Its archived form lives in `package-index.json` and is bound by the
final archive hash/receipt.

Review individual ignored evidence paths before adding those exact files to the
evidence branch (`git add -f <reviewed-file> ...`); do not force-add entire artifacts,
credential directories, raw model event dumps or unrelated trials. Push the
checked evidence head and preserve its focused merged same-head PR. If a general
bug changes runtime code, record/test/commit the new source and rerun affected
builds so both selected runs bind to the submitted runtime inventory.

## Credential and artifact review

The source allowlist includes reviewed repository code/docs/tests and the pinned
organizer kit. It excludes local `.env`/`env.sh`, private kickoff/meeting notes,
authentication/slot/persona state, caches and generated/holdout/result/trace/output
directories except explicitly selected submission evidence. Symlinks, missing
files and paths outside the standalone repository are rejected. Inputs remain
run data, never hidden source/static-prompt additions.

The packager scans source/evidence for credential patterns and exact known private
values from this repository's `.env` and process environment. Use the actual
local credential context privately when checking; do not dump it. Review exports
for controller-bound API keys, bank team keys, bearer tokens, passwords and tool
authentication. Structured JSON/JSONL content receives semantic credential checks
independently of its filename extension. A scanner failure must be resolved, not
bypassed by renaming keys or files.
A scrubbed required tool credential can make an export unrunnable and invalidate
its original hash binding. Retain the original securely; reconcile organizer
import-safe credential requirements before selecting/package validation. Do not
manually edit a generated workflow or regenerate provenance to conceal a change.

## Check, archive, extract and hand over

Before release, on the exact frozen source, run:

```sh
python3.11 -m unittest discover -s tests -v
python3.11 tools/verify_kit.py
python3.11 tools/package_submission.py --check --source-only
python3.11 tools/package_submission.py --source-only --output artifacts/source-rehearsal.zip
git diff --check
```

Use T17's checked `tools/rehearse_package.py` and `docs/REHEARSAL.md` after their
merge for clean extraction/hash auditing and reproduction. Coordinate their exact
CLI rather than maintaining a second extraction helper or claiming an unmerged
helper is available. The fresh extraction must have no `.env`, local authentication,
old artifacts or host-path dependency. Record its actual kit/full-suite/source
results before the broad-test cutoff. Model/live reproduction needs its own
mandatory isolation audit and canonical shared lease; offline archive checks do
not satisfy T17 acceptance.

Once actual final evidence is reviewed/committed and manifest metadata is complete:

```sh
python3.11 tools/package_submission.py --check --manifest artifacts/submission/manifest.json
python3.11 tools/package_submission.py --manifest artifacts/submission/manifest.json --output artifacts/submission/final.zip
python3.11 -m zipfile -t artifacts/submission/final.zip
shasum -a 256 artifacts/submission/final.zip
git diff --check
```

All commands must pass on the exact evidence HEAD. Record command/result/head and
ZIP byte hash. T17's extractor accepts source-only rehearsal archives; use it
for that preparation, not a final ZIP containing submission evidence. For the
final ZIP, verify and extract into a new empty directory with the command below;
reject duplicate/unsafe member names and compare every extracted file's raw
SHA-256 against `package-index.json` source/evidence inventories, with no unexpected
members. Verify mapped workflow/trace/run/input paths resolve and the package has
exactly two selected sets. Re-run kit/source checks in that extraction without
model/network access; final extraction is artifact verification, not broad
conversation evaluation. The ZIP index remaps workflow/trace paths to
`workflows/hidden-N.*` and `traces/hidden-N.*`, selected records to
`runs/hidden-N/`, and preserves or remaps ordered input paths. Do not confuse raw
file-byte hashes in the ZIP index with canonical JSON/controller digests.
The packager checks archive names against the extraction rules before `--check`
succeeds, including case-folded collisions, backslashes and file/ancestor conflicts.
It builds a temporary ZIP, verifies its payload hashes and replaces the destination
only after success. A failed write preserves the previous package.

Run this from the reviewed source checkout after final package validation. It
checks the complete inventory, confined paths, file types, raw hashes and mapped
submission paths before extraction. Replace the archive/destination paths only
as required by the organizer's actual attachment instructions. The destination
must be new; preserve a failed extraction and choose another name on retry.

```sh
python3.11 - <<'PY'
from pathlib import Path, PurePosixPath
import hashlib, json, stat, zipfile

archive = Path('artifacts/submission/final.zip')
destination = Path('artifacts/submission/verified-final')
assert not destination.exists() and not destination.is_symlink()
assert not any(parent.is_symlink() for parent in destination.parents)
with zipfile.ZipFile(archive) as package:
    entries = package.infolist()
    names = [entry.filename for entry in entries]
    folded = {name.casefold() for name in names}
    assert len(names) == len(set(names)) == len(folded)
    for entry in entries:
        path = PurePosixPath(entry.filename)
        assert path.parts and not path.is_absolute() and path.as_posix() == entry.filename
        assert '\\' not in entry.filename and '..' not in path.parts
        assert not entry.is_dir() and not entry.flag_bits & 1
        assert stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
        assert not any(parent.parts and parent.as_posix().casefold() in folded for parent in path.parents)
    index = json.loads(package.read('package-index.json'))
    assert index['source_only'] is False
    assert not set(index['source_files']).intersection(index['evidence_files'])
    hashes = {**index['source_files'], **index['evidence_files']}
    assert set(names) == set(hashes) | {'package-index.json'}
    assert all(hashlib.sha256(package.read(name)).hexdigest() == expected for name, expected in hashes.items())
    submission = index['submission']
    for kind in ('workflows', 'traces', 'runs', 'inputs'):
        assert set(submission[kind]) == {'hidden-1', 'hidden-2'}
    mapped = [path for kind in ('workflows', 'traces', 'runs') for path in submission[kind].values()]
    mapped += [path for inputs in submission['inputs'].values() for path in [*inputs['briefs'], inputs['tool_api']]]
    assert all(path in hashes for path in mapped)
    package.extractall(destination)
assert all(hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected for name, expected in hashes.items())
print(json.dumps({'verified_files': len(hashes), 'package_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}))
PY
```

Read supplied organizer instructions and select only their required attachments
from this verified result. Preserve **19:45–20:00 UTC / 22:45–23:00 Istanbul** for
submission and receipt inspection. Record actual authorized destination,
attachment names, package hash, submission evidence commit, UTC/Istanbul send time
and acknowledgment. A ZIP, draft upload or source merge is not actual submission.
Issue 19 and `cowork.py complete` remain gated on T17 plus two genuine eligible
hidden builds and their actual final delivery.

# Candidate attempt evidence

Every restricted-plan generation attempt gets a new `attempts/plan-001/`,
`plan-002/`, or `plan-003/` directory inside the build run. The existing global
1,800-second deadline, 300-second export reserve, two-repair limit, and frozen
policy contract do not change.

The controller saves its approved generation context before the call and the
returned proposal before checking its contract hash or plan shape, including
rejected proposals. As each stage completes it retains the
plan, action bindings, execution policy, guarded compiler context, guard manifest,
compiled workflow, published deployment, returned test result, and export. An
`attempt.json` records the stage, terminal status, hashes, and failure category.
Missing later files mean that stage did not produce a returned artifact.

The test runner receives the attempt directory. Its transcripts, ledgers,
simulation metadata, and partial files therefore survive subsequent repairs.
Infrastructure or evidence failures preserve the attempted candidate and stop
without a model repair. A runner exception preserves files written before it
raised; it cannot manufacture a completed result.

Root `plan.json`, compiler artifacts, `benchmark-results.json`, and
`submission.workflow.json` keep their established names. They are convenience
copies of the latest validated/tested or selected candidate. A successful manifest
identifies `selected_attempt` and `selected_attempt_directory`; a failed manifest
identifies only `last_attempt`. An export received after the deadline is retained
as evidence while the manifest and final trace event remain failed.

There remains one append-only root `trace.jsonl`, one start, and one end. Deployment
and test events identify their attempt without changing final-export hash checks.
The submission packager continues to use the canonical root export and trace.
Archived compiler/evaluator evidence is never added to generation or repair
prompts: a repair still receives only the previous authored plan and approved,
data-free diagnostics.

Controller snapshots scrub known credentials before writing. Their stored hashes
refer to the original artifact where specified, so a redacted snapshot is evidence
of that artifact rather than an authenticated replay file. A test runner must
continue to apply its own established redaction policy to files it writes.

Published readback must preserve every authored value and ordered action. The
stored-workflow comparator accepts only explicitly recognized serializer defaults
at their documented structural locations; unknown additions, added actions, or
changed code, prompts, routes, headers, and authored limits still stop the build.
The archived deployment preserves the returned graph before this check, including
an unacceptable readback. This bounded comparison is not a claim that arbitrary
server transformations are harmless.

Local mock regressions verify retention and controller decisions. They do not
establish model quality, a successful live Freya build, or the hackathon's runtime
acceptance requirements.

The allowlist is grounded in two synthetic staging readbacks: conversation/end
nodes, then code/API nodes without execution. Optional node fields receive
empty collections/strings, specific booleans, and history defaults. Non-dialogue
code/API/end nodes may receive a null turn cap; conversation turn limits must
remain explicitly authored. Transition conditions may receive `mode: exact`,
function and node response-mapping descriptions may receive an empty string.
These allowances apply only at their matching structural paths. Private model
configuration is checked by the adapter separately; code, API schemas, headers,
hooks, ordered actions and authored values cannot change. The v2 synthetic call
and v3 serializer probe are diagnostic platform evidence, not banking tests.

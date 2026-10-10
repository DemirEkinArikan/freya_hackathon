# Hackathon work boundaries

Work only in this repository. The organizer kit in `build-the-builder/` is v3.
Read its README, API and workflow references before changing platform bindings.

The objective is first-place competitiveness through unseen banking-policy
adaptation, consistent conversations, reproducibility and eligibility. Public
case success alone is not evidence of generalization.

## Confidentiality

- Do not read or import sibling Launchway repositories, client material, prompts,
  presentations or environment files.
- Only individually reviewed generic JSON/hash and secret-check helpers may be
  reused. Write new orchestration, prompts, domain logic and scoring.
- Do not put credentials, raw model events, transcripts or environment dumps in
  GitHub issues. Redact before persistence, not after committing.
- Model generation receives approved briefs, their OpenAPI and kit references
  only. Evaluator fixtures and expected records are not generator inputs.

## Verification and platform use

- Use Python 3.11. Run `python3.11 -m unittest discover -s tests -v`.
- Local mock/contract checks are permitted. Freya trials require explicit
  execution flags; never run them while merely preparing a board or fixture pack.
- Keep the kit's fixed phone-agent model. Publish after an update and read back
  the stored definition. Generated agents must not be edited manually.
- One simulation runs at a time per workspace. Keep its current lease and IDs in
  the task handoff; check server status before replacing a stale lease.
- A builder run has a 30-minute wall-clock budget, including its own tests.
- Never add hidden-brief rules, values or names to source or static prompts.

## Coworking

The user's latest instruction gives Demir the work while Naci is unavailable.
There is no required second computer, collaborator identity verification,
account-ownership attestation, person-specific takeover or timed handoff.
The earlier 17:36 Naci transition is superseded. Names and issue assignments
are coordination metadata; GitHub permissions govern writes. Keep task
dependencies and technical acceptance checks; prepare code independently where useful.
Record completed work, checks, remaining blockers, branch/commit and any
deployment/simulation lease in each checkpoint. Returning collaborators can
continue from the latest pushed checkpoint without a person or timer gate.

Use the linked GitHub task issues as the source of active ownership. Either
authorized repository user can claim a ready task. Default assignment is
responsibility, not a lock. Checkpoint at least every 15 minutes and push the
working branch. Record the previous branch and starting commit when continuing
another checkpoint. Check the latest issue claim and branch activity to avoid
concurrent edits. Never force-push another worker's
branch. When a task's deliverable and checks are complete, push the commit, open
a PR targeting `main`, and merge it directly without a separate review step.
Completion needs verification evidence and that merged same-repository PR;
`tools/cowork.py complete --pr <number-or-url>` verifies the PR's head matches
the pushed completion commit before commenting or closing the issue. The
completion command verifies an existing merge; it does not merge PRs itself.

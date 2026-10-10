#!/usr/bin/env python3
"""Shared GitHub coordination, checkpoint and dependency queue; never calls Freya."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.config import read_local_environment
from builder.safety import assert_no_secrets
TASKS = ROOT / "docs" / "tasks.json"
BOARD = ROOT / "docs" / "github-board.json"
MARKER = "<!-- freya-task-board:v1 -->"
HANDOFF = re.compile(r"<!-- freya-handoff: (\{.*?\}) -->")
STATE_LABELS = {"state:ready", "state:blocked", "state:in-progress", "state:done"}
COMPLETION_PROTOCOL = """After the deliverable and checks are complete, open a PR targeting `main` and
merge it directly without a separate review step. Record completion only after
that same-repository PR is merged and its head matches the pushed commit."""
PREVIOUS_COORDINATION = """Anyone authorized by GitHub may claim a ready task and record checkpoints or
completion. Assignees and actor names describe coordination, not permissions.
An active claim can be replaced through a recorded handoff without a person,
identity, stale-time or ownership-window gate. Check the latest branch/checkpoint,
preserve existing branches and running simulation IDs, and never force-push
another worker's branch. Record branch, commit, verification, remaining work,
blockers, deployment IDs and running simulations. Check the latest claim before
resuming."""
COORDINATION_PROTOCOL = """Anyone authorized by GitHub may work on and claim any open task at any time.
Assignees and actor names are informational metadata, not permissions or locks.
Dependencies govern completion; they do not prevent starting work or recording
checkpoints. No named worker, identity, stale-time or ownership-window gate
applies. Check the latest branch/checkpoint, preserve existing branches and
running simulation IDs, and never force-push another worker's branch. Record
branch, commit, verification, remaining work, blockers, deployment IDs and
running simulations. Check branch activity before resuming."""
LEGACY_COORDINATION = """Either collaborator can claim a ready task. Active work has one assignee. Naci
may reclaim Demir's task after an announced absence or 30 minutes without an
update, after checking branch activity. Checkpoint every 15 minutes and push
work. Record branch, commit, verification, remaining work, blockers, deployment
IDs and running simulations. Check the latest claim before resuming."""


def utcnow():
    return datetime.now(timezone.utc)


class GitHub:
    def __init__(self, repository, dry_run=False):
        self.repository, self.dry_run = repository, dry_run
        self.last_mutation = 0.0

    def api(self, method, path, data=None):
        if self.dry_run and method != "GET":
            return {"dry_run": True, "method": method, "path": path, "data": data}
        if method != "GET":
            delay = 1.1 - (time.monotonic() - self.last_mutation)
            if delay > 0:
                time.sleep(delay)
        args = ["gh", "api", "--method", method, "-H", "Accept: application/vnd.github+json",
                "-H", "X-GitHub-Api-Version: 2026-03-10", path]
        if data is not None:
            args += ["--input", "-"]
        proc = subprocess.run(args, input=json.dumps(data) if data is not None else None,
                              text=True, capture_output=True, timeout=60)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or f"GitHub {method} failed")
        if method != "GET":
            self.last_mutation = time.monotonic()
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def repo(self, suffix=""):
        return f"repos/{self.repository}" + (f"/{suffix}" if suffix else "")

    def all(self, path):
        items, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            rows = self.api("GET", f"{path}{sep}per_page=100&page={page}")
            if not isinstance(rows, list):
                raise RuntimeError("Expected a paginated list from GitHub")
            items += rows
            if len(rows) < 100:
                return items
            page += 1


def labels(issue):
    return {x["name"] for x in issue.get("labels", [])}


def task_marker(task_id):
    return f"<!-- freya-task:{task_id} -->"


def find_issues(gh, manifest):
    issues = [x for x in gh.all(gh.repo("issues?state=all")) if "pull_request" not in x]
    parent = next((x for x in issues if MARKER in (x.get("body") or "")), None)
    found = {}
    for task in manifest["tasks"]:
        matches = [x for x in issues if task_marker(task["id"]) in (x.get("body") or "")]
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate task issues for {task['id']}; reconcile before continuing")
        if matches:
            found[task["id"]] = matches[0]
    return parent, found


def is_ready(task, found):
    return all(found.get(dep, {}).get("state") == "closed" for dep in task["depends_on"])


def active_work_banner(manifest):
    return ("## Open contribution\n\n"
            "Anyone may work on any issue at any time. Assignments are informational. "
            "Technical dependencies and acceptance checks govern completion.\n\n")


def task_body(task, found, parent):
    dep = ", ".join(f"[{d}]({found[d]['html_url']})" if d in found else d
                    for d in task["depends_on"]) or "None — initial ready task"
    checks = "\n".join(task["checks"])
    acceptance = "\n".join(f"- [ ] {x}" for x in task["acceptance"])
    return f"""{task_marker(task['id'])}
Parent: {parent['html_url']}

**Informational assignee:** @{task['assignee']} · **Dependencies:** {dep}
**Working branch:** `{task['branch']}` (use a takeover branch if the original worker may return)

## Deliverable and acceptance

{acceptance}

## Verification

```text
{checks}
```

These commands verify local implementation. Deployment, model isolation and
conversation claims additionally require the evidence described above. Do not
mark this task complete merely because code or mocks exist.

## Ownership and handoff

{COORDINATION_PROTOCOL}
{COMPLETION_PROTOCOL}

```text
python3.11 tools/cowork.py claim {task['id']} --branch {task['branch']} --commit <pushed-sha>
python3.11 tools/cowork.py checkpoint {task['id']} --branch <branch> --commit <pushed-sha> --done "..." --remaining "..."
python3.11 tools/cowork.py complete {task['id']} --commit <pushed-sha> --pr <merged-pr-number-or-url> --checks "command and result"
```

Keep credentials, private product prompts, raw events and client data outside
issues. Preserve the event model and bank-only tools. Hosted runs require
explicit execution authorization and one shared simulation lease.
"""


def parent_body(manifest, found):
    rows = []
    for task in manifest["tasks"]:
        issue = found.get(task["id"])
        done = issue and issue.get("state") == "closed"
        link = f"[{task['id']}]({issue['html_url']})" if issue else task["id"]
        deps = ", ".join(task["depends_on"]) or "—"
        owners = ", ".join("@" + person["login"] for person in issue.get("assignees", [])) if issue else ""
        owners = owners or "@" + task["assignee"]
        rows.append(f"- [{'x' if done else ' '}] {link} — {task['title']} — {owners} — depends: {deps}")
    return f"""{MARKER}
## Objective

First-place competitiveness through adaptation to unseen banking policies,
repeatable correctness, reproducibility and eligibility. Optimize strict pass
rate first, then permitted promised amount, then build time. Public-case success
alone is not evidence of generalization.

Kit v3 and its matching brief/OpenAPI are the implementation contract. Use
staging and persistent published slots. The independent hackathon implementation
reuses at most reviewed generic utilities; no Launchway compiler, private
prompts, integrations, customer material or sibling dependencies are submitted.

{active_work_banner(manifest)}## Ordered shared queue

{chr(10).join(rows)}

Dependencies determine completion readiness; work can start on any open task.
Assignees are informational, not exclusive ownership. Each task is also
a native sub-issue with individually editable assignment and dependencies.

## Coordination and handoff

{COORDINATION_PROTOCOL}

- Claim any open task; record branch and pushed starting commit.
- Checkpoint and push progress regularly. Preserve the original branch and continue from the recorded pushed checkpoint.
- Finish checks, open a PR into `main`, and merge directly without a separate review step.
- Completion verifies the merged same-repository PR's head against the pushed commit, records evidence, then unblocks dependent tasks.
- Keep one simulation lease with owner, workflow/agent/version, IDs and start time. Actual server status controls stale-lease replacement.

## Evidence gates

Local policy/schema/generalization fixtures and hosted conversation variants
are different evidence. Target 120 local fixtures and 120 hosted variants
(36 training / 24 validation / 60 held out each), plus all 13 official cases.
Held-out inputs are authored after candidate freeze; using failures for repair
consumes the holdout. Critical hosted cases require three of three passes.
Unexecuted, incomplete and unsupported checks are never reported as passes.

## Final window

Sunday 11 October: 20:00–23:00 Istanbul (17:00–20:00 UTC). Reserve the final
15 minutes for submission. Each build is at most 30 minutes including its own
checks. Hidden values enter through inputs, not source or static prompt patches.
Exact final submission instructions are pending the organizers' email.

Use `python3.11 tools/cowork.py status` for the current queue. Implementation
status and live evidence will be recorded separately in task comments.
"""


def set_state(gh, issue, state):
    keep = labels(issue) - STATE_LABELS
    return gh.api("PATCH", gh.repo(f"issues/{issue['number']}"),
                  {"labels": sorted(keep | {f"state:{state}"})})


def sync(gh, manifest, parent, found):
    for task in manifest["tasks"]:
        issue = found[task["id"]]
        state = "done" if issue["state"] == "closed" else (
            "in-progress" if "state:in-progress" in labels(issue) else
            "ready" if is_ready(task, found) else "blocked")
        if f"state:{state}" not in labels(issue):
            updated = set_state(gh, issue, state)
            if not gh.dry_run:
                found[task["id"]] = updated
    gh.api("PATCH", gh.repo(f"issues/{parent['number']}"), {"body": parent_body(manifest, found)})


def completion_protocol_body(body, task_id):
    """Update only the known template fragments, preserving all other issue text."""
    old = f'python3.11 tools/cowork.py complete {task_id} --commit <pushed-sha> --checks "command and result"'
    new = f'python3.11 tools/cowork.py complete {task_id} --commit <pushed-sha> --pr <merged-pr-number-or-url> --checks "command and result"'
    if not isinstance(body, str) or body.count(old) + body.count(new) != 1:
        raise RuntimeError(f"{task_id} completion template is not recognized; review its description before syncing")
    body = body.replace(old, new)
    for previous in (LEGACY_COORDINATION, PREVIOUS_COORDINATION):
        if previous in body:
            if body.count(previous) != 1:
                raise RuntimeError(f"{task_id} coordination template is ambiguous; review its description before syncing")
            body = body.replace(previous, COORDINATION_PROTOCOL)
    if body.count(COORDINATION_PROTOCOL) != 1:
        raise RuntimeError(f"{task_id} coordination template is not unique; review its description before syncing")
    body = body.replace("**Default lead:**", "**Informational assignee:**")
    if task_id == "T02":
        body = body.replace("Python 3.11 setup is reproducible on both computers.",
                            "Python 3.11 setup is reproducible in the active environment.")
        body = body.replace("Each collaborator uses their own Codex sign-in.",
                            "The active Codex CLI can authenticate; no human identity or account-ownership attestation is required.")
    if COMPLETION_PROTOCOL not in body:
        anchor = COORDINATION_PROTOCOL + "\n"
        if body.count(anchor) != 1:
            raise RuntimeError(f"{task_id} ownership template is not recognized; review its description before syncing")
        body = body.replace(anchor, anchor + COMPLETION_PROTOCOL + "\n")
    return body


def sync_protocol(gh, manifest, found):
    # Preflight the whole batch before writing, then check each live body again so
    # stale list responses do not replace descriptions edited by another worker.
    plans = []
    for task in manifest["tasks"]:
        issue = gh.api("GET", gh.repo(f"issues/{found[task['id']]['number']}"))
        before = issue.get("body")
        after = completion_protocol_body(before, task["id"])
        if after != before:
            plans.append((task["id"], issue["number"], before, after))
    for task_id, number, before, after in plans:
        path = gh.repo(f"issues/{number}")
        if gh.api("GET", path).get("body") != before:
            raise RuntimeError(f"{task_id} description changed during protocol sync; retry from current issue bodies")
        gh.api("PATCH", path, {"body": after})
    return [{"task": task_id, "issue": number, "changed": True} for task_id, number, _, _ in plans]


def create_board(gh, manifest):
    repo = gh.api("GET", gh.repo())
    if not repo.get("private"):
        raise RuntimeError("Refusing to publish this board to a non-private repository")
    if gh.dry_run:
        print(parent_body(manifest, {}))
        for task in manifest["tasks"]:
            print(f"\n{task['id']}: {task['title']} -> @{task['assignee']}")
        return
    existing_labels = {x["name"] for x in gh.all(gh.repo("labels"))}
    specs = {"priority:first-place": ("B60205", "First-place critical path"),
             "state:ready": ("0E8A16", "Dependencies complete; available to authorized GitHub workers"),
             "state:blocked": ("D4C5F9", "Waiting for dependencies or required evidence"),
             "state:in-progress": ("FBCA04", "Active coordination metadata; preserve branches and checkpoints"),
             "state:done": ("0075CA", "Completed with verified main merge, pushed commit and checks")}
    for phase in {t["phase"] for t in manifest["tasks"]}:
        specs[f"phase:{phase}"] = ("C2E0C6", f"{phase} phase")
    for name, (color, description) in specs.items():
        if name not in existing_labels:
            gh.api("POST", gh.repo("labels"), {"name": name, "color": color, "description": description})
    parent, found = find_issues(gh, manifest)
    if parent is None:
        work = manifest.get("active_work")
        lead = work.get("default_assignee") if isinstance(work, dict) else None
        parent_assignees = [lead] if isinstance(lead, str) and lead else list(manifest.get("collaborators", {}).values())
        parent = gh.api("POST", gh.repo("issues"), {"title": manifest["parent_title"],
                       "body": parent_body(manifest, found), "assignees": parent_assignees,
                       "labels": ["priority:first-place"]})
        print("Created parent", parent["html_url"], flush=True)
    for task in manifest["tasks"]:
        if task["id"] not in found:
            state = "ready" if is_ready(task, found) else "blocked"
            issue = gh.api("POST", gh.repo("issues"), {"title": f"[{task['id']}] {task['title']}",
                           "body": task_body(task, found, parent), "assignees": [task["assignee"]],
                           "labels": ["priority:first-place", f"phase:{task['phase']}", f"state:{state}"]})
            found[task["id"]] = issue
            print("Created", task["id"], issue["html_url"], flush=True)
    linked = {x["id"] for x in gh.all(gh.repo(f"issues/{parent['number']}/sub_issues"))}
    for task in manifest["tasks"]:
        issue = found[task["id"]]
        gh.api("PATCH", gh.repo(f"issues/{issue['number']}"), {"body": task_body(task, found, parent)})
        if issue["id"] not in linked:
            gh.api("POST", gh.repo(f"issues/{parent['number']}/sub_issues"), {"sub_issue_id": issue["id"]})
        blocked = {x["id"] for x in gh.all(gh.repo(f"issues/{issue['number']}/dependencies/blocked_by"))}
        for dep in task["depends_on"]:
            if found[dep]["id"] not in blocked:
                gh.api("POST", gh.repo(f"issues/{issue['number']}/dependencies/blocked_by"),
                       {"issue_id": found[dep]["id"]})
    sync(gh, manifest, parent, found)
    BOARD.write_text(json.dumps({"repository": gh.repository, "parent": parent["html_url"],
                    "tasks": {k: {"number": v["number"], "url": v["html_url"]} for k, v in found.items()}}, indent=2) + "\n")
    print("Verified board", parent["html_url"])


def handoff_comments(gh, issue):
    rows = []
    for comment in gh.all(gh.repo(f"issues/{issue['number']}/comments")):
        match = HANDOFF.search(comment.get("body") or "")
        if match:
            try:
                record = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                record["at"] = comment["created_at"]
                record["comment_author"] = comment.get("user", {}).get("login")
                rows.append(record)
    return rows


def require_pushed_commit(gh, value):
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        raise RuntimeError("Use a pushed hexadecimal commit SHA, not a local branch name")
    return gh.api("GET", gh.repo(f"commits/{value}"))["sha"]


def require_merged_pr(gh, value, commit):
    """Verify explicit same-repository merge evidence before any completion write."""
    value = value.strip() if isinstance(value, str) else ""
    if re.fullmatch(r"[1-9][0-9]*", value):
        number = int(value)
    else:
        match = re.fullmatch(r"https://github\.com/([^/?#]+)/([^/?#]+)/pull/([1-9][0-9]*)/?", value, re.IGNORECASE)
        if not match or f"{match[1]}/{match[2]}".casefold() != gh.repository.casefold():
            raise RuntimeError("Use a PR number or HTTPS pull-request URL from this repository")
        number = int(match[3])
    pr = gh.api("GET", gh.repo(f"pulls/{number}"))
    if not isinstance(pr, dict) or pr.get("number") != number:
        raise RuntimeError("GitHub did not return the requested completion PR")
    base, head = pr.get("base"), pr.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise RuntimeError("Completion PR lacks base or head metadata")
    base_repo = base["repo"].get("full_name", "") if isinstance(base.get("repo"), dict) else ""
    head_repo = head["repo"].get("full_name", "") if isinstance(head.get("repo"), dict) else ""
    if not isinstance(base_repo, str) or not isinstance(head_repo, str) or any(
            name.casefold() != gh.repository.casefold() for name in (base_repo, head_repo)):
        raise RuntimeError("Completion PR base and head must belong to this repository")
    if base.get("ref") != "main":
        raise RuntimeError("Completion PR must target main")
    if pr.get("merged") is not True or pr.get("state") != "closed" or not pr.get("merged_at"):
        raise RuntimeError("Completion PR must already be merged into main")
    if not isinstance(head.get("sha"), str) or head["sha"].casefold() != commit.casefold():
        raise RuntimeError("Completion PR head does not match the pushed completion commit")
    return f"https://github.com/{gh.repository}/pull/{number}"


def record(gh, issue, actor, action, **values):
    meta = {"actor": actor, "action": action, **values}
    body = "<!-- freya-handoff: " + json.dumps(meta, separators=(",", ":")) + " -->\n"
    body += f"**{action.upper()} — @{actor}**\n\n"
    body += "\n".join(f"- **{k.replace('_', ' ').title()}:** {v}" for k, v in values.items())
    credentials = {**read_local_environment(ROOT / ".env"), **os.environ}
    known = tuple(v for k, v in credentials.items() if v and
                  any(s in k for s in ("API_KEY", "TEAM_KEY", "TOKEN", "SECRET", "TOOL_AUTH")))
    assert_no_secrets(body, forbidden_values=known)
    return gh.api("POST", gh.repo(f"issues/{issue['number']}/comments"), {"body": body})


def claim(gh, manifest, task, issue, found, args, actor):
    if issue["state"] == "closed":
        raise RuntimeError("Task is already completed")
    active = "state:in-progress" in labels(issue)
    owner = next((x["login"] for x in issue.get("assignees", [])), None)
    records = [r for r in handoff_comments(gh, issue) if r.get("action") in ("claim", "takeover", "checkpoint")]
    last = records[-1] if records else {}
    sha = require_pushed_commit(gh, args.commit)
    path = gh.repo(f"issues/{issue['number']}")
    current = gh.api("GET", path)
    if (current.get("state") != issue.get("state") or labels(current) != labels(issue)
            or sorted(x["login"] for x in current.get("assignees", [])) != sorted(x["login"] for x in issue.get("assignees", []))):
        raise RuntimeError("Claim metadata changed concurrently; reload the issue before continuing")
    action = "takeover" if active and (owner != actor or args.takeover) else "claim"
    values = {"branch": args.branch, "commit": sha, "offline": False, "previous_owner": owner or "unassigned",
              "previous_branch": last.get("branch", "unrecorded"), "previous_commit": last.get("commit", "unrecorded")}
    # Announce the intent before replacing assignment metadata; no branch is mutated.
    record(gh, issue, actor, action + "_intent", **values)
    metadata = {"assignees": [actor], "labels": sorted((labels(issue) - STATE_LABELS) | {"state:in-progress"})}
    updated = gh.api("PATCH", path, metadata)
    record(gh, issue, actor, action, **values)
    if not gh.dry_run:
        verify = gh.api("GET", path)
        # This confirms our metadata write, not the human's identity or permission.
        claims = [r for r in handoff_comments(gh, issue) if r.get("action") in ("claim", "takeover")]
        latest = claims[-1] if claims else {}
        if (verify.get("state") != "open" or labels(verify) != set(metadata["labels"])
                or [x["login"] for x in verify.get("assignees", [])] != metadata["assignees"]
                or any(latest.get(key) != value for key, value in {"actor": actor, "branch": args.branch, "commit": sha}.items())):
            raise RuntimeError("Claim metadata changed concurrently; stop and reconcile issue claims")
    return updated


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("create-board")
    sub.add_parser("status")
    sub.add_parser("sync")
    sub.add_parser("sync-protocol", help="Update known completion/coordination instructions; preserve task state and handoffs")
    cp = sub.add_parser("claim"); cp.add_argument("task"); cp.add_argument("--branch", required=True); cp.add_argument("--commit", required=True); cp.add_argument("--takeover", action="store_true", help="Optional handoff intent metadata; no actor or stale-time gate")
    hp = sub.add_parser("checkpoint"); hp.add_argument("task"); hp.add_argument("--branch", required=True); hp.add_argument("--commit", required=True); hp.add_argument("--done", required=True); hp.add_argument("--remaining", required=True); hp.add_argument("--blocker", default="none"); hp.add_argument("--offline", action="store_true")
    dp = sub.add_parser("complete"); dp.add_argument("task"); dp.add_argument("--commit", required=True); dp.add_argument("--checks", required=True); dp.add_argument("--pr", required=True)
    args = ap.parse_args(argv)
    manifest = json.loads(TASKS.read_text()); gh = GitHub(manifest["repository"], args.dry_run)
    if args.action == "create-board":
        create_board(gh, manifest); return 0
    parent, found = find_issues(gh, manifest)
    if parent is None or len(found) != len(manifest["tasks"]):
        raise RuntimeError("Create the complete task board first")
    if args.action == "status":
        print(parent["html_url"])
        for task in manifest["tasks"]:
            issue = found[task["id"]]
            state = "done" if issue["state"] == "closed" else "in-progress" if "state:in-progress" in labels(issue) else "ready" if is_ready(task, found) else "blocked"
            owners = ",".join(x["login"] for x in issue["assignees"])
            print(f"{task['id']} {state:11} @{owners:20} {issue['html_url']}")
        return 0
    if args.action == "sync":
        sync(gh, manifest, parent, found); return 0
    if args.action == "sync-protocol":
        print(json.dumps({"dry_run": gh.dry_run, "updates": sync_protocol(gh, manifest, found)})); return 0
    actor = gh.api("GET", "user")["login"]
    task = next((t for t in manifest["tasks"] if t["id"] == args.task), None)
    if not task:
        raise RuntimeError("Unknown task ID")
    issue = found[task["id"]]
    if args.action == "claim":
        claim(gh, manifest, task, issue, found, args, actor)
    else:
        if issue["state"] != "open":
            raise RuntimeError("Task must be open to checkpoint or complete it")
        sha = require_pushed_commit(gh, args.commit)
        if args.action == "checkpoint":
            record(gh, issue, actor, "checkpoint", branch=args.branch, commit=sha,
                   done=args.done, remaining=args.remaining, blocker=args.blocker, offline=args.offline)
        else:
            if not is_ready(task, found):
                raise RuntimeError("Dependencies are not completed")
            pr = require_merged_pr(gh, args.pr, sha)
            record(gh, issue, actor, "complete", commit=sha, checks=args.checks, pr=pr, merged_into="main")
            gh.api("PATCH", gh.repo(f"issues/{issue['number']}"), {"state": "closed", "state_reason": "completed"})
            parent, found = find_issues(gh, manifest); sync(gh, manifest, parent, found)
    print("Updated", issue["html_url"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)

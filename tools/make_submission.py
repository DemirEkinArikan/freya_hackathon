#!/usr/bin/env python3
"""Fill and check the official submission.json, then print it for the submission e-mail.

    python3 tools/make_submission.py --team "TEAM" --member "Name <name@example.com>" [--member ...] \
        --run-command "python3 builder/build.py {case_folder} {mock_url}" \
        --workflow submission/final.workflow.json --trace traces/final.log --mock-url https://<our-mock>.workers.dev

Checks: the workflow file is a freya.export_workflow export whose workflow_id and agent_id both appear in the
trace; neither file holds a key (Freya fak_, Anthropic sk-ant-, OpenAI sk-, Cloudflare tokens, or any local
key value); both files are committed unchanged in the submission commit and that commit is pushed.
submission/ and traces/ are git-ignored, so add those two files with `git add -f`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.config import read_local_environment
from builder.safety import assert_no_secrets

REPO = "https://github.com/DemirEkinArikan/freya_hackathon"
MEMBER = re.compile(r"^\s*(?P<name>[^<>]+?)\s*<(?P<email>[^<>@\s]+@[^<>@\s]+\.[^<>@\s]+)>\s*$")
CLOUDFLARE = re.compile(r"\b(?:CLOUDFLARE|CF)_[A-Z_]*(?:TOKEN|KEY|SECRET)\b['\"]?\s*[=:]\s*['\"]?[A-Za-z0-9_-]{20,}")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


def known_secrets(root):
    pairs = [*read_local_environment(root / ".env").items(), *os.environ.items()]
    return tuple(v for k, v in pairs if v and len(v) >= 8 and any(
        s in k.upper() for s in ("API_KEY", "TEAM_KEY", "TOKEN", "SECRET", "PASSWORD")))


def build(args, root=ROOT):
    members = []
    for raw in args.member:
        m = MEMBER.match(raw)
        if not m:
            raise ValueError(f"member must look like 'Full Name <email>': {raw!r}")
        members.append({"name": m["name"], "email": m["email"]})
    if not args.team.strip():
        raise ValueError("team is empty")
    if not all(p in args.run_command for p in ("{case_folder}", "{mock_url}")):
        raise ValueError("run_command must contain {case_folder} and {mock_url}")
    if not re.fullmatch(r"https://[^\s/]+\S*", args.mock_url):
        raise ValueError("mock_url must be an https URL")

    commit = git(root, "rev-parse", "--verify", f"{args.commit}^{{commit}}").decode().strip()
    if not git(root, "branch", "-r", "--contains", commit).strip():
        raise ValueError(f"commit {commit[:12]} is not on any pushed branch; push it first")
    secrets = known_secrets(root)
    texts = {}
    for rel in (args.workflow, args.trace):
        rel = Path(rel).as_posix()
        try:
            committed = git(root, "show", f"{commit}:{rel}")
        except subprocess.CalledProcessError:
            raise ValueError(f"{rel} is not committed in {commit[:12]} (git add -f, commit, push)") from None
        if (root / rel).read_bytes() != committed:
            raise ValueError(f"{rel} differs from its committed copy in {commit[:12]}")
        text = committed.decode("utf-8")
        try:
            assert_no_secrets(text, forbidden_values=secrets)
        except ValueError:
            raise ValueError(f"{rel} contains key-like material; remove it before submitting") from None
        if CLOUDFLARE.search(text):
            raise ValueError(f"{rel} contains a Cloudflare token; remove it before submitting")
        texts[rel] = text

    export = json.loads(texts[Path(args.workflow).as_posix()])
    ids = [export.get("workflow_id"), export.get("agent_id")] if isinstance(export, dict) else []
    if not (len(ids) == 2 and all(isinstance(i, str) and i for i in ids) and isinstance(export.get("workflow"), dict)):
        raise ValueError("workflow file is not a freya.export_workflow export (workflow_id, agent_id, workflow)")
    missing = [i for i in ids if i not in texts[Path(args.trace).as_posix()]]
    if missing:
        raise ValueError(f"trace does not name the exported workflow/agent id(s): {', '.join(missing)}")

    return {"team": args.team.strip(), "members": members, "repo": args.repo, "commit": commit,
            "run_command": args.run_command, "workflow": Path(args.workflow).as_posix(),
            "trace": Path(args.trace).as_posix(), "mock_url": args.mock_url}


def main(argv=None, root=ROOT):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--team", required=True)
    p.add_argument("--member", action="append", required=True, help="'Full Name <email>'; repeat per member")
    p.add_argument("--run-command", required=True)
    p.add_argument("--workflow", required=True, help="repo-relative path of the exported workflow JSON")
    p.add_argument("--trace", required=True, help="repo-relative path of the run's trace")
    p.add_argument("--mock-url", required=True)
    p.add_argument("--repo", default=REPO)
    p.add_argument("--commit", default="HEAD")
    p.add_argument("--out", help="also write the JSON to this file")
    args = p.parse_args(argv)
    try:
        submission = build(args, root)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"make_submission: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(submission, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

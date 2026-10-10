from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import cowork


class CliPortabilityTests(unittest.TestCase):
    def test_script_imports_helpers_in_isolated_python_from_another_directory(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, '-I', str(cowork.ROOT / 'tools/cowork.py'), '--help'],
                                    cwd=cwd, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('checkpoint', result.stdout)

    def test_handoff_rejects_exact_local_team_key_without_posting(self):
        with tempfile.TemporaryDirectory() as cwd, patch.object(cowork, 'ROOT', Path(cwd)):
            (Path(cwd) / '.env').write_text('FREYA_BANK_TEAM_KEY=synthetic-private-team-key\n')
            gh = FakeGitHub()
            with self.assertRaises(ValueError):
                cowork.record(gh, {'number': 2}, 'DemirEkinArikan', 'checkpoint', done='synthetic-private-team-key')
            self.assertEqual(gh.mutations, [])


class FakeGitHub:
    repository = "DemirEkinArikan/freya_hackathon"
    dry_run = False

    def __init__(self):
        self.issues, self.label_specs, self.children, self.dependencies, self.comments = {}, [], {}, {}, {}
        self.actor = "burhan-launchway"
        self.branch_at = cowork.utcnow() - timedelta(minutes=40)
        self.mutations = []
        self.calls = []
        self.pull_requests = {23: {"number": 23, "state": "closed", "merged": True,
                                  "merged_at": cowork.utcnow().isoformat(),
                                  "base": {"ref": "main", "repo": {"full_name": self.repository}},
                                  "head": {"sha": "a" * 40, "repo": {"full_name": self.repository}}}}

    def repo(self, suffix=""):
        return "repos/" + self.repository + ("/" + suffix if suffix else "")

    def all(self, path):
        return self.api("GET", path)

    def api(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        if method != "GET":
            self.mutations.append((method, path, data))
        path = path.split("?")[0]
        if path == "user":
            return {"login": self.actor}
        if path == self.repo():
            return {"private": True}
        if path == self.repo("labels"):
            if method == "POST":
                self.label_specs.append(data)
                return data
            return self.label_specs
        if path.startswith(self.repo("commits")):
            if path == self.repo("commits"):
                return [{"commit": {"committer": {"date": self.branch_at.isoformat()}}}]
            return {"sha": "a" * 40}
        if path.startswith(self.repo("pulls/")):
            if method != "GET":
                raise AssertionError("The completion command must not merge or modify PRs")
            return copy.deepcopy(self.pull_requests[int(path.rsplit("/", 1)[-1])])
        if path == self.repo("issues"):
            if method == "POST":
                number = len(self.issues) + 1
                issue = {"number": number, "id": 1000 + number, "state": "open",
                         "updated_at": cowork.utcnow().isoformat(), "html_url": f"https://github.com/test/issues/{number}",
                         **data, "labels": [{"name": x} for x in data.get("labels", [])],
                         "assignees": [{"login": x} for x in data.get("assignees", [])]}
                self.issues[number] = issue
                return copy.deepcopy(issue)
            return copy.deepcopy(list(self.issues.values()))
        tail = path[len(self.repo("issues/")):].split("/")
        number = int(tail[0]); issue = self.issues[number]
        if len(tail) == 1:
            if method == "PATCH":
                for k, v in data.items():
                    issue[k] = [{"name": x} for x in v] if k == "labels" else [{"login": x} for x in v] if k == "assignees" else v
            return copy.deepcopy(issue)
        key = "/".join(tail[1:])
        if key == "sub_issues":
            if method == "POST":
                self.children.setdefault(number, []).append(data["sub_issue_id"])
            return [{"id": x} for x in self.children.get(number, [])]
        if key == "dependencies/blocked_by":
            if method == "POST":
                self.dependencies.setdefault(number, []).append(data["issue_id"])
            return [{"id": x} for x in self.dependencies.get(number, [])]
        if key == "comments":
            if method == "POST":
                self.comments.setdefault(number, []).append({"body": data["body"], "user": {"login": self.actor}, "created_at": cowork.utcnow().isoformat()})
            return self.comments.get(number, [])
        raise AssertionError((method, path, data))


class CoworkTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(cowork.TASKS.read_text())
        self.gh = FakeGitHub()
        self.temp = tempfile.TemporaryDirectory()
        self.board_patch = patch.object(cowork, "BOARD", Path(self.temp.name) / "board.json")
        self.board_patch.start()

    def tearDown(self):
        self.board_patch.stop(); self.temp.cleanup()

    def make_board(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cowork.create_board(self.gh, self.manifest)
        return cowork.find_issues(self.gh, self.manifest)

    def test_board_has_native_children_owners_dependencies_and_is_idempotent(self):
        parent, found = self.make_board()
        self.assertEqual(len(self.gh.issues), 19)
        self.assertEqual(len(self.gh.children[parent["number"]]), 18)
        for task in self.manifest["tasks"]:
            issue = found[task["id"]]
            self.assertEqual(issue["assignees"], [{"login": task["assignee"]}])
            self.assertEqual(set(self.gh.dependencies.get(issue["number"], [])), {found[d]["id"] for d in task["depends_on"]})
        self.make_board()
        self.assertEqual(len(self.gh.issues), 19)
        self.assertEqual(len(self.gh.children[parent["number"]]), 18)

    def test_ready_task_can_be_claimed_by_naci(self):
        _, found = self.make_board(); task = self.manifest["tasks"][0]
        cowork.claim(self.gh, self.manifest, task, found["T01"], found,
                     SimpleNamespace(branch="task/t01-naci", commit="abcdef0", takeover=False), "burhan-launchway")
        self.assertEqual(self.gh.issues[found["T01"]["number"]]["assignees"], [{"login": "burhan-launchway"}])

    def test_blocked_task_cannot_be_claimed(self):
        _, found = self.make_board(); task = self.manifest["tasks"][1]
        with self.assertRaisesRegex(RuntimeError, "Dependencies"):
            cowork.claim(self.gh, self.manifest, task, found["T02"], found,
                         SimpleNamespace(branch="task/t02", commit="abcdef0", takeover=False), "burhan-launchway")

    def owner_checkpoint(self, found, minutes, offline=False):
        issue = self.gh.issues[found["T01"]["number"]]
        issue["labels"] = [{"name": "state:in-progress"}]
        at = cowork.utcnow() - timedelta(minutes=minutes)
        payload = {"actor": "DemirEkinArikan", "action": "checkpoint", "branch": "task/t01-demir", "commit": "a" * 40, "offline": offline}
        self.gh.comments[issue["number"]] = [{"body": "<!-- freya-handoff: " + json.dumps(payload) + " -->", "user": {"login": "DemirEkinArikan"}, "created_at": at.isoformat()}]
        return copy.deepcopy(issue)

    def test_takeover_rejects_active_owner_and_recent_branch_activity(self):
        _, found = self.make_board(); task = self.manifest["tasks"][0]
        args = SimpleNamespace(branch="task/t01-takeover", commit="abcdef0", takeover=True)
        issue = self.owner_checkpoint(found, 5)
        with self.assertRaisesRegex(RuntimeError, "still active"):
            cowork.claim(self.gh, self.manifest, task, issue, found, args, "burhan-launchway")
        issue = self.owner_checkpoint(found, 40)
        self.gh.branch_at = cowork.utcnow() - timedelta(minutes=5)
        with self.assertRaisesRegex(RuntimeError, "still active"):
            cowork.claim(self.gh, self.manifest, task, issue, found, args, "burhan-launchway")

    def test_stale_or_announced_offline_takeover_is_allowed(self):
        _, found = self.make_board(); task = self.manifest["tasks"][0]
        args = SimpleNamespace(branch="task/t01-takeover", commit="abcdef0", takeover=True)
        issue = self.owner_checkpoint(found, 40)
        cowork.claim(self.gh, self.manifest, task, issue, found, args, "burhan-launchway")
        issue = self.owner_checkpoint(found, 1, offline=True)
        cowork.claim(self.gh, self.manifest, task, issue, found, args, "burhan-launchway")

    def test_completion_unblocks_dependencies_without_changing_other_claims(self):
        parent, found = self.make_board()
        self.gh.issues[found["T01"]["number"]]["state"] = "closed"
        parent, found = cowork.find_issues(self.gh, self.manifest)
        cowork.sync(self.gh, self.manifest, parent, found)
        self.assertIn("state:ready", cowork.labels(self.gh.issues[found["T02"]["number"]]))
        self.assertIn("state:ready", cowork.labels(self.gh.issues[found["T04"]["number"]]))
        self.assertIn("state:blocked", cowork.labels(self.gh.issues[found["T05"]["number"]]))

    def active_completion(self):
        parent, found = self.make_board()
        issue = self.gh.issues[found["T01"]["number"]]
        issue["labels"] = [{"name": "state:in-progress"}]
        issue["assignees"] = [{"login": self.gh.actor}]
        self.gh.calls.clear()
        self.gh.mutations.clear()
        return parent, found, issue

    def run_completion(self, pr="23"):
        with patch.object(cowork, "GitHub", return_value=self.gh), contextlib.redirect_stdout(io.StringIO()):
            return cowork.main(["complete", "T01", "--commit", "abcdef0", "--pr", pr,
                                "--checks", "unit checks passed"])

    def test_complete_requires_pr_before_creating_github_client(self):
        with patch.object(cowork, "GitHub") as factory, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cowork.main(["complete", "T01", "--commit", "abcdef0", "--checks", "unit checks passed"])
        self.assertEqual(caught.exception.code, 2)
        factory.assert_not_called()

    def test_merged_pr_number_or_same_repository_url_completes_and_unblocks(self):
        for pr in ("23", "https://github.com/DemirEkinArikan/freya_hackathon/pull/23"):
            with self.subTest(pr=pr):
                self.gh = FakeGitHub()
                _, found, issue = self.active_completion()
                self.assertEqual(self.run_completion(pr), 0)
                self.assertEqual(issue["state"], "closed")
                self.assertIn("state:ready", cowork.labels(self.gh.issues[found["T02"]["number"]]))
                body = self.gh.comments[issue["number"]][-1]["body"]
                self.assertIn("https://github.com/DemirEkinArikan/freya_hackathon/pull/23", body)
                self.assertIn('"merged_into":"main"', body)
                read_index = next(i for i, call in enumerate(self.gh.calls) if call[:2] == ("GET", self.gh.repo("pulls/23")))
                write_index = next(i for i, call in enumerate(self.gh.calls) if call[0] != "GET")
                self.assertLess(read_index, write_index)

    def test_ineligible_pr_rejects_before_comments_issue_state_or_labels_change(self):
        cases = [
            ("unmerged", {"merged": False, "state": "open", "merged_at": None}, "23"),
            ("wrong base", {"base": {"ref": "development", "repo": {"full_name": self.gh.repository}}}, "23"),
            ("wrong head", {"head": {"sha": "b" * 40, "repo": {"full_name": self.gh.repository}}}, "23"),
            ("wrong base repository", {"base": {"ref": "main", "repo": {"full_name": "other/repository"}}}, "23"),
            ("fork head repository", {"head": {"sha": "a" * 40, "repo": {"full_name": "other/freya_hackathon"}}}, "23"),
            ("wrong repository URL", {}, "https://github.com/other/repository/pull/23"),
            ("HTTP URL", {}, "http://github.com/DemirEkinArikan/freya_hackathon/pull/23"),
            ("no PR evidence", {}, "none"),
        ]
        for description, changes, pr in cases:
            with self.subTest(description=description):
                self.gh = FakeGitHub()
                _, _, issue = self.active_completion()
                self.gh.pull_requests[23].update(changes)
                before = copy.deepcopy((self.gh.issues, self.gh.comments))
                with self.assertRaises(RuntimeError):
                    self.run_completion(pr)
                self.assertEqual((self.gh.issues, self.gh.comments), before)
                self.assertEqual(self.gh.mutations, [])
                self.assertEqual(issue["state"], "open")

    def test_task_template_documents_main_merge_and_required_pr_evidence(self):
        parent, found = self.make_board()
        body = cowork.task_body(self.manifest["tasks"][0], found, parent)
        self.assertIn("merge it directly without a separate review step", body)
        self.assertIn("--pr <merged-pr-number-or-url>", body)

    def legacy_protocol(self, body):
        return body.replace(" --pr <merged-pr-number-or-url>", "").replace(cowork.COMPLETION_PROTOCOL + "\n", "")

    def test_targeted_protocol_sync_preserves_closed_tasks_metadata_and_custom_text(self):
        parent, found = self.make_board()
        closed = self.gh.issues[found["T01"]["number"]]
        closed["state"] = "closed"
        for task in self.manifest["tasks"]:
            issue = self.gh.issues[found[task["id"]]["number"]]
            issue["body"] = self.legacy_protocol(issue["body"]) + "\nCustom handoff note: preserve this text.\n"
        metadata = {number: {k: copy.deepcopy(v) for k, v in issue.items() if k != "body"}
                    for number, issue in self.gh.issues.items()}
        original_parent = copy.deepcopy(self.gh.issues[parent["number"]])
        comments = copy.deepcopy(self.gh.comments)
        self.gh.mutations.clear()
        changes = cowork.sync_protocol(self.gh, self.manifest, found)
        self.assertEqual(len(changes), 18)
        self.assertTrue(all(method == "PATCH" and set(data) == {"body"} for method, _, data in self.gh.mutations))
        self.assertEqual(self.gh.issues[parent["number"]], original_parent)
        self.assertEqual(self.gh.comments, comments)
        for task in self.manifest["tasks"]:
            issue = self.gh.issues[found[task["id"]]["number"]]
            self.assertEqual({k: v for k, v in issue.items() if k != "body"}, metadata[issue["number"]])
            self.assertIn("Custom handoff note: preserve this text.", issue["body"])
            self.assertIn(cowork.COMPLETION_PROTOCOL, issue["body"])
            self.assertIn("--pr <merged-pr-number-or-url>", issue["body"])
        self.gh.mutations.clear()
        self.assertEqual(cowork.sync_protocol(self.gh, self.manifest, found), [])
        self.assertEqual(self.gh.mutations, [])

    def test_protocol_sync_preflights_all_templates_before_any_write(self):
        _, found = self.make_board()
        for task in self.manifest["tasks"]:
            issue = self.gh.issues[found[task["id"]]["number"]]
            issue["body"] = self.legacy_protocol(issue["body"])
        self.gh.issues[found["T07"]["number"]]["body"] = "Custom issue description with no known template"
        self.gh.mutations.clear()
        with self.assertRaisesRegex(RuntimeError, "not recognized"):
            cowork.sync_protocol(self.gh, self.manifest, found)
        self.assertEqual(self.gh.mutations, [])

    def test_protocol_sync_does_not_overwrite_a_concurrent_description_edit(self):
        _, found = self.make_board()
        issue = self.gh.issues[found["T01"]["number"]]
        issue["body"] = self.legacy_protocol(issue["body"])
        path = self.gh.repo(f"issues/{issue['number']}")
        original_api, reads = self.gh.api, []
        def api(method, current_path, data=None):
            if method == "GET" and current_path == path:
                reads.append(current_path)
                if len(reads) == 2:
                    issue["body"] += "\nConcurrent worker note.\n"
            return original_api(method, current_path, data)
        self.gh.api = api
        self.gh.mutations.clear()
        with self.assertRaisesRegex(RuntimeError, "changed during"):
            cowork.sync_protocol(self.gh, self.manifest, found)
        self.assertEqual(self.gh.mutations, [])
        self.assertIn("Concurrent worker note.", issue["body"])


if __name__ == "__main__":
    unittest.main()

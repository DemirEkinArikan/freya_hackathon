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
        # Legacy ownership metadata is intentionally inert under the new protocol.
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

    def owner_checkpoint(self, found, minutes, offline=False, actor="DemirEkinArikan"):
        issue = self.gh.issues[found["T01"]["number"]]
        issue["labels"] = [{"name": "state:in-progress"}]
        issue["assignees"] = [{"login": actor}]
        at = cowork.utcnow() - timedelta(minutes=minutes)
        payload = {"actor": actor, "action": "checkpoint", "branch": "task/t01-demir", "commit": "a" * 40, "offline": offline}
        self.gh.comments[issue["number"]] = [{"body": "<!-- freya-handoff: " + json.dumps(payload) + " -->", "user": {"login": actor}, "created_at": at.isoformat()}]
        return copy.deepcopy(issue)

    def test_normal_claim_replaces_recent_active_metadata_in_both_directions(self):
        for previous, actor in (("DemirEkinArikan", "burhan-launchway"),
                                ("burhan-launchway", "DemirEkinArikan"),
                                ("other-worker", "authorized-new-worker")):
            with self.subTest(previous=previous, actor=actor):
                self.gh = FakeGitHub()
                self.gh.actor = actor
                _, found = self.make_board()
                issue = self.owner_checkpoint(found, 0, actor=previous)
                self.gh.branch_at = cowork.utcnow()
                self.gh.calls.clear()
                cowork.claim(self.gh, self.manifest, self.manifest["tasks"][0], issue, found,
                             SimpleNamespace(branch="task/new-work", commit="abcdef0", takeover=False), actor)
                self.assertEqual(self.gh.issues[issue["number"]]["assignees"], [{"login": actor}])
                handoff = cowork.handoff_comments(self.gh, issue)[-1]
                self.assertEqual(handoff["action"], "takeover")
                self.assertEqual(handoff["previous_owner"], previous)
                self.assertEqual(handoff["previous_branch"], "task/t01-demir")
                self.assertEqual(handoff["previous_commit"], "a" * 40)
                self.assertFalse(any(path.startswith(self.gh.repo("commits") + "?") for _, path, _ in self.gh.calls))
                intent = next(i for i, (method, path, _) in enumerate(self.gh.calls)
                              if method == "POST" and path.endswith("/comments"))
                assignment = next(i for i, (method, _, data) in enumerate(self.gh.calls)
                                  if method == "PATCH" and "assignees" in data)
                self.assertLess(intent, assignment)

    def test_takeover_flag_is_optional_person_independent_intent_metadata(self):
        _, found = self.make_board()
        issue = self.owner_checkpoint(found, 0, actor="other-worker")
        self.gh.actor = "authorized-new-worker"
        cowork.claim(self.gh, self.manifest, self.manifest["tasks"][0], issue, found,
                     SimpleNamespace(branch="task/new-work", commit="abcdef0", takeover=True), self.gh.actor)
        self.assertEqual(cowork.handoff_comments(self.gh, issue)[-1]["action"], "takeover")

    def test_legacy_window_and_actor_registry_do_not_gate_claims(self):
        for window in ({"owner": "DemirEkinArikan", "next_owner": "burhan-launchway",
                        "starts_at": "2999-01-01T00:00:00+03:00", "handoff_at": "2999-01-02T00:00:00+03:00"},
                       {"authorized_by": "inferred"}, "obsolete metadata"):
            with self.subTest(window=window):
                self.gh = FakeGitHub()
                self.gh.actor = "authorized-new-worker"
                _, found = self.make_board()
                self.manifest["ownership_window"] = window
                self.manifest["collaborators"] = {}
                issue = self.owner_checkpoint(found, 0)
                cowork.claim(self.gh, self.manifest, self.manifest["tasks"][0], issue, found,
                             SimpleNamespace(branch="task/new-work", commit="abcdef0", takeover=False), self.gh.actor)
                self.assertEqual(self.gh.issues[issue["number"]]["assignees"], [{"login": self.gh.actor}])

    def test_legacy_window_does_not_bypass_dependencies(self):
        _, found = self.make_board()
        self.manifest["ownership_window"] = "ignored"
        args = SimpleNamespace(branch="task/t02-next", commit="abcdef0", takeover=True)
        with self.assertRaisesRegex(RuntimeError, "Dependencies"):
            cowork.claim(self.gh, self.manifest, self.manifest["tasks"][1], found["T02"], found, args, "authorized-new-worker")

    def test_parent_and_task_templates_show_metadata_without_timed_assignment(self):
        parent, found = self.make_board()
        self.manifest["ownership_window"] = {"owner": "DemirEkinArikan", "next_owner": "burhan-launchway"}
        found["T02"]["assignees"] = [{"login": "authorized-new-worker"}]
        body = cowork.parent_body(self.manifest, found)
        self.assertIn("Reproducible runtime — @authorized-new-worker", body)
        self.assertIn("Assignees and actor names describe coordination, not permissions", body)
        self.assertNotIn("User-authorized ownership window", body)
        self.assertNotIn("Naci can reclaim", body)
        self.assertNotIn("continues afterwards", body)
        self.assertIn(cowork.COORDINATION_PROTOCOL, cowork.task_body(self.manifest["tasks"][0], found, parent))

    def test_single_operator_preference_is_informational_and_sets_parent_metadata(self):
        self.manifest["active_work"] = {"mode": "single_operator", "default_assignee": "unknown-preferred-worker"}
        parent, found = self.make_board()
        self.assertEqual(parent["assignees"], [{"login": "unknown-preferred-worker"}])
        body = cowork.parent_body(self.manifest, found)
        self.assertIn("@unknown-preferred-worker continues", body)
        self.assertIn("does not restrict who may claim", body)
        self.gh.actor = "different-authorized-worker"
        cowork.claim(self.gh, self.manifest, self.manifest["tasks"][0], found["T01"], found,
                     SimpleNamespace(branch="task/independent-work", commit="abcdef0", takeover=False), self.gh.actor)
        self.assertEqual(self.gh.issues[found["T01"]["number"]]["assignees"], [{"login": self.gh.actor}])

    def run_action(self, arguments):
        with patch.object(cowork, "GitHub", return_value=self.gh), contextlib.redirect_stdout(io.StringIO()):
            return cowork.main(arguments)

    def test_unknown_authorized_github_actor_can_claim_checkpoint_and_complete(self):
        _, found = self.make_board()
        self.gh.actor = "authorized-new-worker"
        self.assertEqual(self.run_action(["claim", "T01", "--branch", "task/new-worker", "--commit", "abcdef0"]), 0)
        issue = self.gh.issues[found["T01"]["number"]]
        issue["assignees"] = [{"login": "different-metadata-owner"}]
        self.assertEqual(self.run_action(["checkpoint", "T01", "--branch", "task/new-worker", "--commit", "abcdef0",
                                          "--done", "local changes", "--remaining", "verification"]), 0)
        self.assertEqual(self.run_completion(), 0)
        self.assertEqual(issue["state"], "closed")
        self.assertEqual(cowork.handoff_comments(self.gh, issue)[-1]["actor"], "authorized-new-worker")

    def test_checkpoint_and_completion_are_independent_of_demir_naci_assignment_direction(self):
        for assigned, actor in (("DemirEkinArikan", "burhan-launchway"), ("burhan-launchway", "DemirEkinArikan")):
            with self.subTest(assigned=assigned, actor=actor):
                self.gh = FakeGitHub()
                self.gh.actor = actor
                _, _, issue = self.active_completion()
                issue["assignees"] = [{"login": assigned}]
                self.assertEqual(self.run_action(["checkpoint", "T01", "--branch", "task/handoff", "--commit", "abcdef0",
                                                  "--done", "local changes", "--remaining", "verification"]), 0)
                self.assertEqual(self.run_completion(), 0)
                self.assertEqual(issue["state"], "closed")

    def test_github_permission_denial_still_prevents_mutation(self):
        _, found = self.make_board()
        self.gh.actor = "authorized-new-worker"
        original = self.gh.api
        def denied(method, path, data=None):
            if method != "GET":
                raise RuntimeError("GitHub denied write permission")
            return original(method, path, data)
        self.gh.api = denied
        before = copy.deepcopy(self.gh.issues)
        with self.assertRaisesRegex(RuntimeError, "GitHub denied"):
            self.run_action(["claim", "T01", "--branch", "task/new-worker", "--commit", "abcdef0"])
        self.assertEqual(self.gh.issues, before)
        self.assertEqual(self.gh.comments, {})

    def test_handoff_actor_and_comment_author_are_informational_metadata(self):
        _, found = self.make_board()
        issue = self.owner_checkpoint(found, 0)
        self.gh.comments[issue["number"]][0]["user"]["login"] = "handoff-recorder"
        records = cowork.handoff_comments(self.gh, issue)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["actor"], "DemirEkinArikan")
        self.assertEqual(records[0]["comment_author"], "handoff-recorder")

    def test_claim_preserves_concurrent_issue_metadata_before_any_write(self):
        _, found = self.make_board()
        issue = found["T01"]
        self.gh.issues[issue["number"]]["assignees"] = [{"login": "concurrent-worker"}]
        self.gh.mutations.clear()
        with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
            cowork.claim(self.gh, self.manifest, self.manifest["tasks"][0], issue, found,
                         SimpleNamespace(branch="task/handoff", commit="abcdef0", takeover=False), self.gh.actor)
        self.assertEqual(self.gh.mutations, [])

    def test_claim_detects_concurrent_reassignment_after_its_write(self):
        _, found = self.make_board()
        issue = found["T01"]
        path = self.gh.repo(f"issues/{issue['number']}")
        original, reads = self.gh.api, []
        def reassigned(method, current_path, data=None):
            if method == "GET" and current_path == path:
                reads.append(path)
                if len(reads) == 2:
                    self.gh.issues[issue["number"]]["assignees"] = [{"login": "concurrent-worker"}]
            return original(method, current_path, data)
        self.gh.api = reassigned
        with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
            cowork.claim(self.gh, self.manifest, self.manifest["tasks"][0], issue, found,
                         SimpleNamespace(branch="task/handoff", commit="abcdef0", takeover=False), self.gh.actor)
        self.assertEqual(self.gh.issues[issue["number"]]["assignees"], [{"login": "concurrent-worker"}])

    def test_claim_detects_other_branch_intent_even_for_same_actor(self):
        _, found = self.make_board()
        issue = found["T01"]
        path = self.gh.repo(f"issues/{issue['number']}/comments")
        original, reads = self.gh.api, []
        def concurrent_branch(method, current_path, data=None):
            if method == "GET" and current_path == path:
                reads.append(path)
                if len(reads) == 2:
                    meta = {"actor": self.gh.actor, "action": "claim", "branch": "task/other-branch", "commit": "b" * 40}
                    self.gh.comments[issue["number"]].append({"body": "<!-- freya-handoff: " + json.dumps(meta) + " -->",
                                                               "user": {"login": self.gh.actor}, "created_at": cowork.utcnow().isoformat()})
            return original(method, current_path, data)
        self.gh.api = concurrent_branch
        with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
            cowork.claim(self.gh, self.manifest, self.manifest["tasks"][0], issue, found,
                         SimpleNamespace(branch="task/handoff", commit="abcdef0", takeover=False), self.gh.actor)

    def test_completion_still_requires_ready_dependencies_and_active_lifecycle(self):
        _, found = self.make_board()
        self.gh.actor = "authorized-new-worker"
        issue = self.gh.issues[found["T02"]["number"]]
        issue["labels"] = [{"name": "state:in-progress"}]
        self.gh.mutations.clear()
        with self.assertRaisesRegex(RuntimeError, "Dependencies"):
            self.run_action(["complete", "T02", "--commit", "abcdef0", "--pr", "23", "--checks", "local checks"])
        self.assertEqual(self.gh.mutations, [])
        closed = self.gh.issues[found["T01"]["number"]]
        closed["state"] = "closed"
        with self.assertRaisesRegex(RuntimeError, "open task"):
            self.run_action(["checkpoint", "T01", "--branch", "task/handoff", "--commit", "abcdef0",
                             "--done", "local changes", "--remaining", "none"])

    def test_protocol_migrates_historical_identity_rules_without_changing_comments(self):
        _, found = self.make_board()
        issue = self.gh.issues[found["T01"]["number"]]
        issue["body"] = issue["body"].replace(cowork.COORDINATION_PROTOCOL, cowork.LEGACY_COORDINATION) + "\nCustom context.\n"
        self.owner_checkpoint(found, 0)
        before = copy.deepcopy(self.gh.comments)
        cowork.sync_protocol(self.gh, self.manifest, found)
        self.assertIn(cowork.COORDINATION_PROTOCOL, issue["body"])
        self.assertNotIn(cowork.LEGACY_COORDINATION, issue["body"])
        self.assertIn("Custom context.", issue["body"])
        self.assertEqual(self.gh.comments, before)

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

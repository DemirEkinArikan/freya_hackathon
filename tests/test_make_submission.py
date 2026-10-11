from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import make_submission

EXPORT = {"workflow_id": "wf-123", "agent_id": "ag-456", "updated_at": "t", "workflow": {"initial_node": "start"}}
TRACE = "start 20:05\nmodel claude-opus-5-5: write the workflow\ncreated workflow wf-123 agent ag-456, 9 steps, 3 tools\nend 20:31\n"


class MakeSubmissionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "repo"
        remote = Path(tmp.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.git("remote", "add", "origin", str(remote))
        env_patch = patch.dict("os.environ", {"CF_API_TOKEN": "", "FREYA_API_KEY": ""})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e.st", *args],
                       check=True, capture_output=True)

    def commit(self, trace=TRACE, export=EXPORT, push=True):
        (self.root / "submission").mkdir(exist_ok=True)
        (self.root / "traces").mkdir(exist_ok=True)
        (self.root / "submission/final.workflow.json").write_text(json.dumps(export))
        (self.root / "traces/final.log").write_text(trace)
        self.git("add", "-f", "submission", "traces")
        self.git("commit", "-q", "--allow-empty", "-m", "submission")
        if push:
            self.git("push", "-q", "origin", "HEAD:refs/heads/main")
            self.git("fetch", "-q", "origin")

    def run_tool(self, *extra, member="Demir Ekin Arikan <demir@example.com>"):
        argv = ["--team", "Team", "--member", member, "--run-command",
                "python3 builder/build.py {case_folder} {mock_url}", "--workflow", "submission/final.workflow.json",
                "--trace", "traces/final.log", "--mock-url", "https://mock.example.workers.dev", *extra]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = make_submission.main(argv, root=self.root)
        return code, out.getvalue(), err.getvalue()

    def test_valid_submission_prints_the_official_fields(self):
        self.commit()
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(list(data), ["team", "members", "repo", "commit", "run_command", "workflow", "trace", "mock_url"])
        self.assertEqual(data["members"], [{"name": "Demir Ekin Arikan", "email": "demir@example.com"}])
        self.assertEqual(len(data["commit"]), 40)
        self.assertEqual(data["repo"], make_submission.REPO)

    def test_trace_must_name_the_exported_ids(self):
        self.commit(trace=TRACE.replace("ag-456", "ag-999"))
        code, _, err = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("ag-456", err)

    def test_keys_in_trace_are_rejected_without_echoing_them(self):
        for secret in ("fak_abcdefgh12345678", "sk-ant-api03-abcdefgh1234", "sk-proj-abcdefgh12345678",
                       "CLOUDFLARE_API_TOKEN=abcdefghijklmnopqrstuvwxyz12"):
            with self.subTest(secret=secret[:6]):
                self.commit(trace=TRACE + secret + "\n")
                code, out, err = self.run_tool()
                self.assertEqual(code, 1)
                self.assertIn("traces/final.log", err)
                self.assertNotIn(secret, err + out)

    def test_local_key_values_are_rejected(self):
        with patch.dict("os.environ", {"CF_API_TOKEN": "plainvalue-xyz-12345"}):
            self.commit(trace=TRACE + "token plainvalue-xyz-12345\n")
            code, _, err = self.run_tool()
        self.assertEqual(code, 1)
        self.assertNotIn("plainvalue", err)

    def test_files_must_be_committed_unchanged_and_pushed(self):
        self.commit(push=False)
        self.assertIn("not on any pushed branch", self.run_tool()[2])
        self.commit()
        (self.root / "traces/final.log").write_text(TRACE + "edited\n")
        self.assertIn("differs from its committed copy", self.run_tool()[2])

    def test_workflow_must_be_an_export(self):
        self.commit(export={"nodes": {}})
        self.assertIn("not a freya.export_workflow export", self.run_tool()[2])

    def test_member_and_run_command_shapes(self):
        self.commit()
        self.assertIn("Full Name <email>", self.run_tool(member="Demir")[2])
        code, _, err = self.run_tool("--run-command", "python3 builder/build.py")
        self.assertEqual(code, 1)
        self.assertIn("{case_folder}", err)


if __name__ == "__main__":
    unittest.main()

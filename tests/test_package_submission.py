import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from builder.safety import digest
from tools import package_submission as package


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root_patch = patch.object(package, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop(); self.temp.cleanup()

    def test_allowlist_excludes_private_notes_env_and_generated_data(self):
        for name in ("README.md", ".env", "kickoff_email.txt", "meeting.md", "builder/build.py", "artifacts/raw.json", "benchmarks/holdout/private.json"):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("safe")
        included = {str(p.relative_to(self.root)) for p in package.source_files()}
        self.assertEqual(included, {"README.md", "builder/build.py"})

    def test_external_and_symlink_paths_are_rejected(self):
        outside = Path(self.temp.name).parent / (self.root.name + "-outside")
        outside.write_text("safe")
        try:
            (self.root / "link.json").symlink_to(outside)
            with self.assertRaises(ValueError): package.safe_path(outside)
            with self.assertRaises(ValueError): package.safe_path(self.root / "link.json")
        finally:
            outside.unlink()

    def evidence(self, status="exported", mode="live"):
        wf = {"workflow_id": "w", "agent_id": "a", "workflow": {"initial_node": "finish", "nodes": {}}}
        for name in ("hidden-1", "hidden-2"):
            (self.root / (name + ".json")).write_text(json.dumps(wf))
            events = [{"event": "start", "mode": mode}, {"event": "deployment", "workflow_id": "w", "agent_id": "a"},
                      {"event": "end", "status": status, "export_sha256": digest(wf)}]
            (self.root / (name + ".jsonl")).write_text("\n".join(json.dumps(e) for e in events))
        return {"team": "synthetic", "repo": "https://github.com/test/example", "commit": "a" * 40,
                "run_command": "python3.11 builder/build.py --brief {brief_path} --tool-api {tool_api_path}",
                "workflows": {n: n + ".json" for n in ("hidden-1", "hidden-2")},
                "traces": {n: n + ".jsonl" for n in ("hidden-1", "hidden-2")}}

    def test_export_and_trace_must_match_and_failed_run_is_not_ready(self):
        with patch.object(subprocess, "run", return_value=SimpleNamespace(stdout="a" * 40)):
            self.assertEqual(len(package.check_manifest(self.evidence())), 4)
            with self.assertRaisesRegex(ValueError, "failed traces"):
                package.check_manifest(self.evidence(status="failed"))
            with self.assertRaisesRegex(ValueError, "generated-only"):
                package.check_manifest(self.evidence(mode="generate-only"))
            data = self.evidence()
            (self.root / "hidden-1.json").write_text(json.dumps({"workflow_id": "changed", "agent_id": "a", "workflow": {"nodes": {}}}))
            with self.assertRaisesRegex(ValueError, "does not match"):
                package.check_manifest(data)

    def test_real_credential_like_value_is_rejected_without_echo(self):
        p = self.root / "README.md"
        p.write_text("Authorization: Bearer REAL_TEST_ONLY_CREDENTIAL_123456")
        with self.assertRaisesRegex(ValueError, "requires review") as result:
            package.check_content(p)
        self.assertNotIn("REAL_TEST_ONLY", str(result.exception))

    def test_package_manifest_points_to_archived_evidence(self):
        (self.root / 'README.md').write_text('safe')
        manifest_path = self.root / 'submission.json'
        manifest_path.write_text(json.dumps(self.evidence()))
        output = self.root / 'artifacts' / 'package.zip'
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40)), \
             patch.object(package, 'verify_committed'):
            package.main(['--manifest', str(manifest_path), '--output', str(output)])
        with zipfile.ZipFile(output) as archive:
            metadata = json.loads(archive.read('package-index.json'))
            for kind in ('workflows', 'traces'):
                for entry in metadata['submission'][kind].values():
                    self.assertIn(entry, archive.namelist())

    def test_local_team_key_is_checked_without_exporting_environment(self):
        (self.root / '.env').write_text('FREYA_BANK_TEAM_KEY=synthetic-unpatterned-team-key\n')
        source = self.root / 'README.md'
        source.write_text('synthetic-unpatterned-team-key')
        with self.assertRaisesRegex(ValueError, 'requires review'):
            package.check_content(source)

    def test_final_package_bytes_must_match_git_commit(self):
        source = self.root / 'README.md'
        source.write_text('committed source')
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'committed source')):
            package.verify_committed([source])
        for result in (SimpleNamespace(returncode=1, stdout=b''), SimpleNamespace(returncode=0, stdout=b'older source')):
            with patch.object(subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(ValueError, 'match the committed checkout'):
                    package.verify_committed([source])


if __name__ == "__main__":
    unittest.main()

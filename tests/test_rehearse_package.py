"""Local archive/reproduction regressions; no model or platform execution."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile

from tools import rehearse_package as rehearsal


def fingerprint(raw):
    return hashlib.sha256(raw).hexdigest()


class RehearsalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / "source.zip"
        self.destination = self.root / "extracted"
        self.files = {"README.md": b"Reviewed source\n", "builder/build.py": b"pass\n"}
        self.hashes = {name: fingerprint(raw) for name, raw in self.files.items()}

    def tearDown(self):
        self.temp.cleanup()

    def write_archive(self, files=None, index=None, extra=()):
        files = self.files if files is None else files
        index = ({"source_files": self.hashes, "evidence_files": {},
                  "source_only": True, "submission": None} if index is None else index)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w") as archive:
                for name, raw in files.items():
                    archive.writestr(name, raw)
                for name, raw in extra:
                    archive.writestr(name, raw)
                archive.writestr("package-index.json", index if isinstance(index, str) else json.dumps(index))

    def assert_rejected_before_extraction(self):
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            rehearsal.extract_source(self.archive, self.destination)
        self.assertFalse(self.destination.exists())

    def test_exact_inventory_and_bytes_round_trip_without_local_state(self):
        self.write_archive()
        self.assertEqual(rehearsal.extract_source(self.archive, self.destination), self.hashes)
        for name, raw in self.files.items():
            self.assertEqual((self.destination / name).read_bytes(), raw)
        self.assertEqual({p.relative_to(self.destination).as_posix() for p in self.destination.rglob("*")
                          if p.is_file()}, set(self.files) | {"package-index.json"})
        rehearsal.verify_inventory(self.destination, self.hashes)

    def test_existing_destination_is_never_overwritten(self):
        self.write_archive()
        self.destination.mkdir()
        marker = self.destination / "marker"
        marker.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "must be new"):
            rehearsal.extract_source(self.archive, self.destination)
        self.assertEqual(marker.read_text(), "preserve")

    def test_traversal_absolute_and_noncanonical_paths_are_rejected(self):
        for name in ("../outside.py", "/absolute.py", "builder/../outside.py", "builder//extra.py",
                     "builder/./extra.py", "builder\\extra.py"):
            with self.subTest(name=name):
                self.write_archive(extra=[(name, b"malicious")])
                self.assert_rejected_before_extraction()
        self.assertFalse((self.root / "outside.py").exists())

    def test_private_generated_and_auth_paths_are_rejected(self):
        for name in (".env", "builder/.env.local.txt", "build-the-builder/env.sh", "artifacts/export.json",
                     "benchmarks/holdout/fixture.json", "docs/meeting.md", ".codex/auth.json"):
            with self.subTest(name=name):
                self.write_archive(extra=[(name, b"excluded")])
                self.assert_rejected_before_extraction()

    def test_duplicate_archive_members_are_rejected(self):
        self.write_archive(extra=[("README.md", b"replacement")])
        self.assert_rejected_before_extraction()

    def test_dot_is_not_an_allowlisted_source_name(self):
        self.assertFalse(rehearsal.source_name("."))

    def test_casefold_and_file_prefix_collisions_fail_before_any_writes(self):
        for name in ("builder/BUILD.py", "builder/build.py/extra.py", "builder/BUILD.py/extra.py"):
            with self.subTest(name=name):
                files = {**self.files, name: b"colliding source"}
                index = {"source_files": {key: fingerprint(raw) for key, raw in files.items()},
                         "evidence_files": {}, "source_only": True, "submission": None}
                self.write_archive(files=files, index=index)
                with self.assertRaisesRegex(ValueError, "paths collide"):
                    rehearsal.extract_source(self.archive, self.destination)
                self.assertFalse(self.destination.exists())

    def test_symlink_special_file_and_directory_members_are_rejected(self):
        for mode, name in ((stat.S_IFLNK, "builder/link.py"), (stat.S_IFIFO, "builder/pipe.py"),
                           (stat.S_IFDIR, "builder/")):
            with self.subTest(mode=mode):
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (mode | 0o755) << 16
                self.write_archive(extra=[(entry, b"target")])
                self.assert_rejected_before_extraction()

    def test_file_count_and_total_uncompressed_size_are_bounded(self):
        self.write_archive()
        with patch.object(rehearsal, "MAX_FILES", 2):
            self.assert_rejected_before_extraction()
        with patch.object(rehearsal, "MAX_BYTES", 15):
            self.assert_rejected_before_extraction()

    def test_index_is_required(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("README.md", self.files["README.md"])
        self.assert_rejected_before_extraction()

    def test_duplicate_index_keys_including_nested_hashes_are_rejected(self):
        for index in ('{"source_only":true,"source_only":true}',
                      '{"source_only":true,"submission":null,"evidence_files":{},'
                      '"source_files":{"README.md":"a","README.md":"b"}}'):
            with self.subTest(index=index):
                self.write_archive(index=index)
                self.assert_rejected_before_extraction()

    def test_only_source_only_index_without_submission_or_evidence_is_accepted(self):
        valid = {"source_files": self.hashes, "evidence_files": {}, "source_only": True, "submission": None}
        for key, value in (("source_only", 1), ("source_only", False), ("submission", {"commit": "old"}),
                           ("evidence_files", {"export.json": "old"}), ("source_files", {})):
            with self.subTest(key=key, value=value):
                self.write_archive(index={**valid, key: value})
                self.assert_rejected_before_extraction()

    def test_unindexed_extra_missing_member_and_wrong_hash_are_rejected(self):
        self.write_archive(extra=[("tools/extra.py", b"pass")])
        self.assert_rejected_before_extraction()
        self.write_archive(files={"README.md": self.files["README.md"]})
        self.assert_rejected_before_extraction()
        self.write_archive(files={**self.files, "README.md": b"tampered"})
        self.assert_rejected_before_extraction()

    def test_post_extraction_modified_missing_or_added_source_is_rejected(self):
        self.write_archive()
        rehearsal.extract_source(self.archive, self.destination)
        original = self.destination / "README.md"
        original.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            rehearsal.verify_inventory(self.destination, self.hashes)
        original.unlink()
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            rehearsal.verify_inventory(self.destination, self.hashes)
        original.write_bytes(self.files["README.md"])
        (self.destination / "builder/extra.py").write_text("pass")
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            rehearsal.verify_inventory(self.destination, self.hashes)

    def test_post_extraction_source_symlink_is_rejected(self):
        self.write_archive()
        rehearsal.extract_source(self.archive, self.destination)
        path = self.destination / "README.md"
        path.unlink()
        path.symlink_to(self.destination / "builder/build.py")
        with self.assertRaisesRegex(ValueError, "symlink"):
            rehearsal.verify_inventory(self.destination, self.hashes)

    def test_post_extraction_private_configuration_is_rejected(self):
        self.write_archive()
        rehearsal.extract_source(self.archive, self.destination)
        for name in (".env", ".codex/auth.json"):
            with self.subTest(name=name):
                path = self.destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic private setup")
                with self.assertRaisesRegex(ValueError, "private configuration"):
                    rehearsal.verify_inventory(self.destination, self.hashes)
                path.unlink()
                if name.startswith(".codex/"):
                    path.parent.rmdir()


class RehearsalProcessTests(unittest.TestCase):
    def test_real_child_disables_site_startup_and_reports_actual_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "empty-home"
            home.mkdir()
            log = root / "check.log"
            report = rehearsal.run_check(
                [sys.executable, "-c", "import sys; print(sys.flags.no_site); print('site' in sys.modules)"],
                root, home, log, rehearsal.time.monotonic() + 10)
            self.assertEqual(report["returncode"], 0)
            self.assertEqual(log.read_text().splitlines(), ["1", "False"])
            self.assertEqual(report["argv"][1], "-S")
            self.assertEqual(list(home.iterdir()), [])

    def test_clean_environment_drops_credentials_python_hooks_and_host_configuration(self):
        hostile = {"FREYA_API_KEY": "synthetic", "API_TOKEN": "synthetic", "FREYA_TOOL_AUTH_BANK": "synthetic",
                   "PYTHONPATH": "/host/code", "PYTHONHOME": "/host/python", "CODEX_HOME": "/host/codex",
                   "BASH_ENV": "/host/init", "PATH": "/host/bin", "HOME": "/host/home"}
        with patch.dict(os.environ, hostile, clear=True):
            environment = rehearsal.clean_environment(Path("/clean/home"))
        self.assertEqual(environment, {"PATH": os.defpath, "HOME": "/clean/home", "LANG": "en_US.UTF-8",
                                       "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})

    def test_committed_source_is_bound_to_recorded_head_and_byte_hash(self):
        head, raw = "a" * 40, b"reviewed source"
        tree = subprocess.CompletedProcess([], 0, stdout=b"README.md\0.env\0artifacts/export.json\0")
        content = subprocess.CompletedProcess([], 0, stdout=raw)
        with patch.object(subprocess, "run", side_effect=[tree, content]) as run:
            rehearsal.verify_committed(Path("/project"), {"README.md": fingerprint(raw)}, head)
        self.assertEqual(run.call_args_list[0].args[0], ["git", "ls-tree", "-r", "--name-only", "-z", head])
        self.assertEqual(run.call_args.args[0], ["git", "show", f"{head}:README.md"])
        self.assertEqual(run.call_args.kwargs["cwd"], Path("/project"))
        self.assertIsNone(run.call_args.kwargs["timeout"])
        with patch.object(subprocess, "run", side_effect=[tree, subprocess.CompletedProcess([], 0, stdout=b"changed")]):
            with self.assertRaisesRegex(ValueError, "recorded commit"):
                rehearsal.verify_committed(Path("/project"), {"README.md": fingerprint(raw)}, head)

    def test_missing_committed_allowlisted_source_is_rejected_before_reading_blobs(self):
        tree = subprocess.CompletedProcess([], 0, stdout=b"README.md\0builder/build.py\0")
        with patch.object(subprocess, "run", return_value=tree) as run:
            with self.assertRaisesRegex(ValueError, "inventory differs"):
                rehearsal.verify_committed(Path("/project"), {"README.md": fingerprint(b"source")}, "a" * 40)
        self.assertEqual(run.call_count, 1)

    def test_run_check_retains_timed_out_log_without_inheriting_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "check.log"
            process = Mock(pid=1234)
            process.wait.side_effect = [subprocess.TimeoutExpired(["python"], 1), -9]
            with patch.object(subprocess, "Popen", return_value=process) as run, \
                    patch.object(rehearsal.os, "killpg") as kill:
                report = rehearsal.run_check(["/python3.11", "--help"], root, root / "empty-home", log,
                                              rehearsal.time.monotonic() + 10)
            self.assertTrue(report["timed_out"])
            self.assertIsNone(report["returncode"])
            self.assertTrue(log.exists())
            self.assertEqual(report["log_sha256"], fingerprint(log.read_bytes()))
            self.assertEqual(run.call_args.kwargs["env"], rehearsal.clean_environment(root / "empty-home"))
            self.assertTrue(run.call_args.kwargs["start_new_session"])
            kill.assert_called_once_with(1234, rehearsal.signal.SIGKILL)
            self.assertEqual(process.wait.call_count, 2)

    def test_expired_deadline_prevents_subprocess(self):
        with patch.object(subprocess, "Popen") as run:
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                rehearsal.run_check(["python"], Path("/project"), Path("/home"), Path("/log"),
                                    rehearsal.time.monotonic() - 1)
        run.assert_not_called()


class RehearsalOrchestrationTests(unittest.TestCase):
    """Use real tiny source archives; subprocess checks are explicit local stubs."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "README.md").write_text("Synthetic rehearsal source\n")
        (self.root / "builder").mkdir()
        (self.root / "builder/build.py").write_text("pass\n")
        self.output = self.root / "artifacts/rehearsal"
        self.root_patch = patch.object(rehearsal, "ROOT", self.root)
        self.package_patch = patch.object(rehearsal.package, "ROOT", self.root)
        self.root_patch.start()
        self.package_patch.start()

    def tearDown(self):
        self.package_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def fake_git(self, argv, **kwargs):
        if argv == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, stdout="a" * 40)
        if argv == ["git", "ls-tree", "-r", "--name-only", "-z", "a" * 40]:
            return subprocess.CompletedProcess(argv, 0, stdout=b"README.md\0builder/build.py\0")
        if argv[:2] == ["git", "show"]:
            return subprocess.CompletedProcess(argv, 0, stdout=(self.root / argv[2].split(":", 1)[1]).read_bytes())
        raise AssertionError("Unexpected subprocess; checks must remain stubbed")

    def check_stub(self, argv, root, home, log, end):
        self.assertFalse((root / ".git").exists())
        self.assertFalse((root / ".env").exists())
        self.assertEqual(list(home.iterdir()), [])
        log.write_text("Local stub; no live acceptance\n")
        if "benchmarks.export_inputs" in argv:
            destination = root / argv[argv.index("--out") + 1]
            destination.mkdir(parents=True)
            (destination / "policy.md").write_text("Synthetic policy input\n")
            (destination / "bank.openapi.json").write_text(json.dumps({
                "openapi": "3.0.3", "info": {"title": "Synthetic", "version": "1"}, "paths": {}}))
        if "--output" in argv:
            with patch.object(rehearsal.package, "ROOT", root):
                rehearsal.package.main(argv[2:])
        return {"argv": ["python3.11", *argv[1:]], "returncode": 0, "timed_out": False,
                "elapsed_seconds": 0, "log": log.name, "log_sha256": fingerprint(log.read_bytes())}

    def test_local_reproduction_rebuilds_exact_inventory_and_reports_evidence_limits(self):
        with patch.object(subprocess, "run", side_effect=self.fake_git), \
                patch.object(rehearsal, "run_check", side_effect=self.check_stub):
            report = rehearsal.rehearse(self.output)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(len(report["checks"]), 10)
        self.assertEqual(report["configuration_sha256"], rehearsal.digest(report["resolved_config"]))
        self.assertEqual(report["source_inventory_sha256"], report["rebuilt_source_inventory_sha256"])
        self.assertEqual(report["archive_sha256"], fingerprint((self.output / "source.zip").read_bytes()))
        self.assertEqual(json.loads((self.output / "report.json").read_text()), report)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["platform_calls"], 0)
        self.assertFalse(report["candidate_frozen"])
        self.assertFalse(report["holdouts_consumed"])
        self.assertIn("do not prove", report["limitations"])

    def test_failed_check_preserves_source_archive_log_and_report(self):
        def fail_check(*args):
            value = self.check_stub(*args)
            value["returncode"] = 7
            return value
        with patch.object(subprocess, "run", side_effect=self.fake_git), \
                patch.object(rehearsal, "run_check", side_effect=fail_check) as check:
            report = rehearsal.rehearse(self.output)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(check.call_count, 1)
        self.assertEqual(report["checks"][0]["returncode"], 7)
        for name in ("source.zip", "source/README.md", "check-01.log", "report.json"):
            self.assertTrue((self.output / name).exists(), name)
        self.assertFalse(report["candidate_frozen"])

    def test_successful_noop_exports_leave_failed_report_and_original_logs(self):
        def skip_exports(argv, root, home, log, end):
            if "benchmarks.export_inputs" in argv:
                log.write_text("Successful no-op exporter\n")
                return {"argv": argv, "returncode": 0, "timed_out": False,
                        "elapsed_seconds": 0, "log": log.name,
                        "log_sha256": fingerprint(log.read_bytes())}
            return self.check_stub(argv, root, home, log, end)

        with patch.object(subprocess, "run", side_effect=self.fake_git), \
                patch.object(rehearsal, "run_check", side_effect=skip_exports):
            report = rehearsal.rehearse(self.output)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(report["checks"]), 10)
        self.assertTrue(all(check["returncode"] == 0 for check in report["checks"]))
        self.assertIn("Development inputs", report["error"])
        self.assertNotIn("development_inputs", report)
        self.assertTrue((self.output / "source.zip").is_file())
        self.assertTrue((self.output / "check-09.log").is_file())
        self.assertFalse(report["candidate_frozen"])

    def test_source_commit_mismatch_preserves_failed_report_before_execution(self):
        with patch.object(subprocess, "run", side_effect=self.fake_git), \
                patch.object(rehearsal, "verify_committed", side_effect=ValueError("Rehearsal source differs from the recorded commit")), \
                patch.object(rehearsal, "run_check") as check:
            report = rehearsal.rehearse(self.output)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_type"], "ValueError")
        self.assertTrue((self.output / "report.json").exists())
        self.assertFalse((self.output / "source.zip").exists())
        check.assert_not_called()

    def test_final_rebuild_stage_overrun_retains_failed_report_and_rebuild(self):
        expired = False
        extract = rehearsal.extract_source

        def finish_rebuild_late(archive, destination):
            nonlocal expired
            result = extract(archive, destination)
            if destination.name == "rebuilt-source":
                expired = True
            return result

        with patch.object(subprocess, "run", side_effect=self.fake_git), \
                patch.object(rehearsal, "run_check", side_effect=self.check_stub), \
                patch.object(rehearsal, "extract_source", side_effect=finish_rebuild_late), \
                patch.object(rehearsal.time, "monotonic", side_effect=lambda: 1901 if expired else 100):
            report = rehearsal.rehearse(self.output)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_type"], "TimeoutError")
        self.assertIn("deadline", report["error"])
        self.assertEqual(len(report["checks"]), 10)
        self.assertEqual(report["elapsed_seconds"], 1801)
        self.assertTrue((self.output / "source/artifacts/rebuilt.zip").exists())
        self.assertTrue((self.output / "rebuilt-source/README.md").exists())
        self.assertEqual(json.loads((self.output / "report.json").read_text()), report)
        self.assertFalse(report["candidate_frozen"])

    def test_checks_that_modify_source_fail_final_inventory_with_artifacts_retained(self):
        def mutate_source(*args):
            value = self.check_stub(*args)
            if "--help" in args[0]:
                (args[1] / "README.md").write_text("changed by check")
            return value
        with patch.object(subprocess, "run", side_effect=self.fake_git), \
                patch.object(rehearsal, "run_check", side_effect=mutate_source):
            report = rehearsal.rehearse(self.output)
        self.assertEqual(report["status"], "failed")
        self.assertIn("inventory changed", report["error"])
        self.assertTrue((self.output / "source/artifacts/rebuilt.zip").exists())
        self.assertTrue((self.output / "source.zip").exists())


if __name__ == "__main__":
    unittest.main()

"""Validate development input evidence without running a builder or platform."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.rehearse_package import verify_development_inputs


class RehearsalDevelopmentInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inputs = self.root / "artifacts/development-inputs"
        self.api = {"openapi": "3.0.3", "info": {"title": "Synthetic", "version": "1"}, "paths": {}}
        for profile in ("raw", "changed-schema"):
            directory = self.inputs / profile
            directory.mkdir(parents=True)
            (directory / "policy.md").write_text("Synthetic policy input\n")
            (directory / "bank.openapi.json").write_text(json.dumps(self.api))

    def tearDown(self):
        self.temp.cleanup()

    def assert_rejected(self):
        with self.assertRaises(ValueError):
            verify_development_inputs(self.root)

    def test_complete_exports_record_exact_flat_inventory_and_bytes(self):
        hashes = verify_development_inputs(self.root)
        expected = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in self.inputs.rglob("*") if path.is_file()}
        self.assertEqual(hashes, expected)
        self.assertEqual(len(hashes), 4)
        (self.inputs / "raw/second-policy.md").write_text("A second brief\n")
        self.assertEqual(len(verify_development_inputs(self.root)), 5)

    def test_absent_exports_and_missing_profile_are_rejected(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(ValueError):
                verify_development_inputs(Path(empty))
        directory = self.inputs / "changed-schema"
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
        self.assert_rejected()

    def test_missing_schema_and_missing_brief_are_rejected(self):
        for name in ("bank.openapi.json", "policy.md"):
            with self.subTest(name=name):
                path = self.inputs / "raw" / name
                original = path.read_bytes()
                path.unlink()
                self.assert_rejected()
                path.write_bytes(original)

    def test_unexpected_file_directory_and_profile_are_rejected(self):
        for name, directory in (("raw/answers.json", False), ("raw/nested", True),
                                ("unexpected-profile", True)):
            with self.subTest(name=name):
                path = self.inputs / name
                path.mkdir() if directory else path.write_text("unexpected")
                self.assert_rejected()
                path.rmdir() if directory else path.unlink()

    def test_symlink_files_and_dangling_links_are_rejected(self):
        path = self.inputs / "raw/policy.md"
        original = path.read_bytes()
        path.unlink()
        for target in (self.inputs / "changed-schema/policy.md", self.root / "absent.md"):
            with self.subTest(target=target.name):
                path.symlink_to(target)
                self.assert_rejected()
                path.unlink()
        path.write_bytes(original)
        api = self.inputs / "raw/bank.openapi.json"
        api.unlink()
        api.symlink_to(self.inputs / "changed-schema/bank.openapi.json")
        self.assert_rejected()

    def test_linked_export_parent_and_profile_are_rejected(self):
        for path in (self.root / "artifacts", self.inputs, self.inputs / "raw"):
            with self.subTest(path=path.name):
                moved = self.root / (path.name + "-plain")
                path.rename(moved)
                path.symlink_to(moved, target_is_directory=True)
                self.assert_rejected()
                path.unlink()
                moved.rename(path)

    def test_empty_and_whitespace_only_files_are_rejected(self):
        for name in ("policy.md", "bank.openapi.json"):
            path = self.inputs / "raw" / name
            original = path.read_bytes()
            for raw in (b"", b" \n\t"):
                with self.subTest(name=name, raw=raw):
                    path.write_bytes(raw)
                    self.assert_rejected()
            path.write_bytes(original)

    def test_invalid_json_duplicate_keys_and_nonfinite_values_are_rejected(self):
        path = self.inputs / "raw/bank.openapi.json"
        for raw in (b"not JSON", b"\xff", b'{"openapi":"3.0.3","openapi":"3.1.0","info":{},"paths":{}}',
                    b'{"openapi":"3.0.3","info":{"version":NaN},"paths":{}}'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                self.assert_rejected()

    def test_missing_or_invalid_openapi_object_fields_are_rejected(self):
        path = self.inputs / "raw/bank.openapi.json"
        invalid = [[], {}, {**self.api, "openapi": " "}, {**self.api, "openapi": 3},
                   {**self.api, "info": []}, {**self.api, "paths": []}]
        invalid += [{key: value for key, value in self.api.items() if key != omitted}
                    for omitted in ("openapi", "info", "paths")]
        for api in invalid:
            with self.subTest(api=api):
                path.write_text(json.dumps(api))
                self.assert_rejected()


if __name__ == "__main__":
    unittest.main()

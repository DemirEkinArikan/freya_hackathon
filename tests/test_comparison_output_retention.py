"""Comparison CLI output must preserve original recorded evidence."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.compare import main
from test_comparison import behavior_failure, bundle


ROOT = Path(__file__).resolve().parents[1]


class ComparisonOutputRetentionTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "artifacts").mkdir(exist_ok=True)

    def inputs(self, directory, *, failed=False):
        paths = [directory / "baseline.json", directory / "structured.json"]
        records = [bundle("baseline"), bundle("structured")]
        if failed:
            behavior_failure(records[1])
        for path, value in zip(paths, records):
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return paths, [path.read_bytes() for path in paths]

    def invoke(self, paths, target):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = main(["--baseline", str(paths[0]), "--structured", str(paths[1]), "--out", str(target)])
        return status, json.loads(output.getvalue())

    def test_output_cannot_replace_either_original_input_bundle(self):
        with tempfile.TemporaryDirectory(prefix="comparison-retention-", dir=ROOT / "artifacts") as directory:
            paths, originals = self.inputs(Path(directory))
            for target in paths:
                with self.subTest(target=target.name), self.assertRaises(FileExistsError):
                    self.invoke(paths, target)
                self.assertEqual([path.read_bytes() for path in paths], originals)

    def test_existing_comparison_output_and_input_bytes_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="comparison-retention-", dir=ROOT / "artifacts") as directory:
            directory = Path(directory)
            paths, originals = self.inputs(directory)
            target = directory / "comparison.json"
            prior = b'{"comparable":false,"reason":"original_failed_attempt"}\n'
            target.write_bytes(prior)
            with self.assertRaises(FileExistsError):
                self.invoke(paths, target)
            self.assertEqual(target.read_bytes(), prior)
            self.assertEqual([path.read_bytes() for path in paths], originals)

    def test_fresh_output_retains_the_comparison_and_original_bundles(self):
        with tempfile.TemporaryDirectory(prefix="comparison-retention-", dir=ROOT / "artifacts") as directory:
            directory = Path(directory)
            paths, originals = self.inputs(directory)
            target = directory / "new" / "comparison.json"
            status, printed = self.invoke(paths, target)
            retained = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(status, 0)
            self.assertEqual(retained, printed)
            self.assertTrue(retained["comparable"])
            self.assertNotIn("RAW_TRANSCRIPT_SENTINEL", target.read_text(encoding="utf-8"))
            self.assertEqual([path.read_bytes() for path in paths], originals)

    def test_fresh_output_preserves_recorded_behavior_failure(self):
        with tempfile.TemporaryDirectory(prefix="comparison-retention-", dir=ROOT / "artifacts") as directory:
            directory = Path(directory)
            paths, originals = self.inputs(directory, failed=True)
            target = directory / "failed-behavior-comparison.json"
            status, result = self.invoke(paths, target)
            self.assertEqual(status, 0)
            self.assertTrue(result["comparable"])
            self.assertEqual(result["splits"][0]["decision"], "behavior_regression")
            self.assertEqual(result["splits"][0]["structured"]["behavior_failure_runs"], 1)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), result)
            self.assertEqual([path.read_bytes() for path in paths], originals)


if __name__ == "__main__":
    unittest.main()

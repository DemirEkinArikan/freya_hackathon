import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from tools.verify_hidden_release import main, verify_hidden_release

INPUTS = {"hidden-1": ([b"official brief one"], b'{"openapi": "one"}'),
          "hidden-2": ([b"official brief two a", b"official brief two b"], b'{"openapi": "two"}')}
IN_WINDOW = ("2026-10-11T17:05:00+00:00", "2026-10-11T17:33:00+00:00")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pins():
    return {label: {"briefs": [sha(b) for b in briefs], "tool_api": sha(api)} for label, (briefs, api) in INPUTS.items()}


class HiddenReleaseBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / "final.zip"

    def write(self, inputs=INPUTS, times=None):
        times = times or {label: IN_WINDOW for label in inputs}
        submission = {"inputs": {}, "traces": {}}
        with zipfile.ZipFile(self.archive, "w") as bundle:
            for label, (briefs, api) in inputs.items():
                names = [f"inputs/{label}/brief-{n}.md" for n in range(1, len(briefs) + 1)]
                for name, raw in zip(names, briefs):
                    bundle.writestr(name, raw)
                bundle.writestr(f"inputs/{label}/api.json", api)
                trace = "".join(json.dumps({"event": e, "time": t}) + "\n" for e, t in zip(("start", "end"), times[label]))
                bundle.writestr(f"traces/{label}.jsonl", trace)
                submission["inputs"][label] = {"briefs": names, "tool_api": f"inputs/{label}/api.json"}
                submission["traces"][label] = f"traces/{label}.jsonl"
            bundle.writestr("package-index.json", json.dumps({"submission": submission}))
        return self.archive

    def test_official_inputs_inside_the_release_window_are_bound(self):
        self.assertEqual(verify_hidden_release(self.write(), pins())["status"], "official_release_bound")

    def test_practice_inputs_relabelled_as_hidden_are_rejected(self):
        practice = {**INPUTS, "hidden-2": ([b"public practice brief B"], b'{"openapi": "practice"}')}
        with self.assertRaisesRegex(ValueError, "hidden-2: inputs differ"):
            verify_hidden_release(self.write(practice), pins())

    def test_swapped_labels_and_reordered_briefs_are_rejected(self):
        swapped = {"hidden-1": INPUTS["hidden-2"], "hidden-2": INPUTS["hidden-1"]}
        reordered = {**INPUTS, "hidden-2": (INPUTS["hidden-2"][0][::-1], INPUTS["hidden-2"][1])}
        for inputs in (swapped, reordered):
            with self.subTest(inputs=sorted(inputs)), self.assertRaisesRegex(ValueError, "inputs differ"):
                verify_hidden_release(self.write(inputs), pins())

    def test_stale_pre_release_and_post_deadline_runs_are_rejected(self):
        for times in (("2026-10-10T12:00:00+00:00", "2026-10-10T12:00:03+00:00"),   # producer test fixture day
                      ("2026-10-11T16:59:59+00:00", "2026-10-11T17:20:00+00:00"),
                      ("2026-10-11T19:40:00+00:00", "2026-10-11T20:00:01+00:00")):
            with self.subTest(times=times), self.assertRaisesRegex(ValueError, "hidden-1: .*release window"):
                verify_hidden_release(self.write(times={"hidden-1": times, "hidden-2": IN_WINDOW}), pins())

    def test_missing_second_build_or_pin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly hidden-1 and hidden-2"):
            verify_hidden_release(self.write({"hidden-1": INPUTS["hidden-1"]}), pins())
        with self.assertRaisesRegex(ValueError, "exactly hidden-1 and hidden-2"):
            verify_hidden_release(self.write(), {"hidden-1": pins()["hidden-1"]})

    def test_cli_fails_closed_on_naive_times_without_echoing_member_names(self):
        archive = self.write(times={"hidden-1": ("2026-10-11T17:05:00", "2026-10-11T17:06:00"), "hidden-2": IN_WINDOW})
        pin_path = Path(self.temp.name) / "pins.json"
        pin_path.write_text(json.dumps(pins()))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--archive", str(archive), "--official-pins", str(pin_path)]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "failed", "error": "TypeError"})


if __name__ == "__main__":
    unittest.main()

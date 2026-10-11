import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import review_pack

F = Path(__file__).parent / "fixtures" / "review_pack"


class ReviewPackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.out = self.tmp / "review.html"

    def run_pack(self, *extra, calls=(F / "calls",)):
        with contextlib.redirect_stdout(io.StringIO()):
            review_pack.main(["--case", str(F / "case"), "--calls", *map(str, calls), "--out", str(self.out), *extra])
        return self.out.read_text()

    def test_full_pack(self):
        page = self.run_pack("--records", str(F / "records.json"), "--expected", str(F / "expected.json"),
                             "--checks", str(F / "checks.json"), "--workflow", str(F / "workflow.json"))
        # self-contained: no external scripts, styles, images or links
        self.assertNotRegex(page, r"(?i)<(script|link|img|iframe)\b|src=|@import|url\(")
        # (a) rules as tick boxes, a quote missing from the documents is flagged
        self.assertIn("Verify identity before discussing the account.", page)
        self.assertEqual(page.count("quote not found"), 1)
        self.assertGreaterEqual(page.count("type=checkbox"), 2 + 10)
        # (b) each call next to its closest example: the spouse call matches the English example, the other the Turkish one
        spouse, ok = page.index("<h3>C-SPOUSE</h3>"), page.index("<h3>C-OK</h3>")
        self.assertLess(spouse, ok)  # calls with violations come first
        self.assertIn("Example: examples/conversation-2.md", page[spouse:ok])
        self.assertIn("Example: examples/ornek-1.md", page[ok:])
        # tool calls shown, code steps left out
        self.assertIn("-&gt; get_account(", page)
        self.assertNotIn("code_exec", page)
        # (c) records and expected records; a call without expected records gets a box to fill in
        self.assertIn("third_party", page[spouse:ok])
        self.assertIn("<textarea>", page[ok:])
        # (d) violations, (e) chat instructions and the ten tries
        self.assertIn("get_account called while a spouse was on the line", page)
        self.assertIn("wf-test-1", page)
        self.assertIn("<b>chat</b> mode", page)
        for title, _ in review_pack.BREAK:
            self.assertIn(review_pack.esc(title), page)
        self.assertEqual(len(review_pack.BREAK), 10)

    def test_max_calls_and_missing_inputs(self):
        page = self.run_pack("--max-calls", "1")
        self.assertEqual(len(re.findall(r"<h3>C-", page)), 1)
        self.assertIn("No rules", page)
        self.assertIn("No checker output given", page)
        self.assertIn("tool calls seen in the transcript", page)

    def test_keys_never_reach_the_page(self):
        secret = "frey" + "a-secret-value-123456"
        fake_claude = "sk-" + "ant-api03-" + "x" * 24
        call = json.loads((F / "calls" / "C-OK.json").read_text())
        call["messages"].append({"role": "user", "message": f"{secret} {fake_claude} Bearer abcdefghijkl x-team-key: abcdefghijklmn"})
        (self.tmp / "C-KEY.json").write_text(json.dumps(call))
        with patch.dict("os.environ", {"FREYA_API_KEY": secret}):
            page = self.run_pack(calls=[self.tmp / "C-KEY.json"])
        for leaked in (secret, fake_claude, "abcdefghijkl", "abcdefghijklmn"):
            self.assertNotIn(leaked, page)
        self.assertIn("[removed]", page)

    def test_extract_rules_with_claude(self):
        sent = {}

        class Stream:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get_final_message(self):
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking"), SimpleNamespace(
                    type="text", text=json.dumps({"rules": [{"text": "Never talk to a third party about the account.",
                                                             "source": "policy.md", "quote": "The agent must not discuss the account with a third party."}]}))])

        def stream(**kw):
            sent.update(kw)
            return Stream()

        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
        rules = review_pack.extract_rules(review_pack.case_texts(F / "case"), client)
        self.assertEqual(sent["model"], "claude-opus-5-5")
        self.assertEqual(sent["output_config"]["effort"], "max")
        self.assertIn("The agent may accept a promise", sent["messages"][0]["content"])
        self.assertTrue(review_pack.quote_found(rules[0], review_pack.case_texts(F / "case")))

        with patch.object(review_pack, "extract_rules", return_value=rules):
            page = self.run_pack("--extract-rules")
        self.assertIn("Never talk to a third party", page)
        self.assertNotIn("quote not found", page)
        self.assertEqual(json.loads(Path(str(self.out) + ".rules.json").read_text())["rules"], rules)

    def test_refusal_stops_extraction(self):
        class Stream:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get_final_message(self): return SimpleNamespace(stop_reason="refusal", content=[])

        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream())))
        with self.assertRaises(SystemExit):
            review_pack.extract_rules([("a.md", "rule")], client)


if __name__ == "__main__":
    unittest.main()

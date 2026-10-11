from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import independent_check as ic

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "independent_check"
CUSTOMERS = {"call_date": None, "customers": [
    {"id": "C-1", "title": "", "first_name": "Ada", "last_name": "Demir", "identity": {"date_of_birth": "1990-05-17", "postcode": "34000"},
     "products": [{"product_id": "p1", "past_due": "500"}], "facts": ""},
    {"id": "C-2", "title": "", "first_name": "Can", "last_name": "Yilmaz", "identity": {"date_of_birth": "1985-01-02", "postcode": "06000"},
     "products": [{"product_id": "p1", "past_due": "300"}], "facts": ""}]}
RULES = {"rules": [
    {"id": "R1", "rule": "Say first that the call is recorded.", "category": "opening", "applies_when": "every call", "check": "first line",
     "source": "BRIEF.md", "quote": "It first says that the call is recorded.", "replaces": None},
    {"id": "R2", "rule": "Verify date of birth and postcode before account information.", "category": "identity", "applies_when": "every call",
     "check": "order", "source": "BRIEF.md", "quote": "It verifies identity with the date of birth and the postcode", "replaces": None}],
    "open_questions": []}


def make_cases(customer):
    cases = []
    for s in ic.SCENARIOS:
        third = s == "third_party"
        cases.append({"id": f"{customer['id']}-{s}", "customer": customer["id"], "scenario": s, "brief": "FINAL",
                      "family": "Third party" if third else "Happy path", "level": "easy", "title": s, "third_party": third,
                      "variables": {"first_name": customer["first_name"], "last_name": customer["last_name"], "title": "", "now": "2026-10-12T11:00"},
                      "identity": customer["identity"], "products": customer["products"], "script": "Cooperates.",
                      "expected": {"outcome": "no_contact" if third else "promise", "reason": None,
                                   "promises": [] if third else [{"product_id": "p1", "amount": 500.0, "date": "2026-10-20"}],
                                   "notes": [], "callbacks": [], "transfers": []},
                      "checks": ["[R1] the recording sentence comes first"], "rule_ids": ["R1"], "assumptions": []})
    return cases


class FakeClaude:
    """Stands in for anthropic.Anthropic(): client.beta.messages.stream(**request) answers with answer(request)."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []
        self.beta = self.messages = self

    @contextlib.contextmanager
    def stream(self, **request):
        self.requests.append(request)
        text = self.answer(request)
        yield SimpleNamespace(get_final_message=lambda: SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)]))


def task_text(request):
    return request["messages"][0]["content"][-1]["text"]


class IndependentCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bundle = self.tmp / "bundle"
        shutil.copytree(FIXTURES / "bundle", self.bundle)
        (self.bundle / "files").mkdir()
        (self.bundle / "files" / "flow.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        (self.bundle / "files" / "qa.pdf").write_bytes(b"%PDF-1.4 fake")
        self.out = self.tmp / "out"

    def args(self, **kw):
        return SimpleNamespace(bundle=str(self.bundle), out=str(self.out), rules=None, **kw)

    def test_bundle_keeps_reading_order_attaches_images_and_pdfs_and_lists_unread_documents(self):
        blocks, docs_norm, skipped = ic.load_bundle(self.bundle)
        text = blocks[-1]["text"]
        self.assertLess(text.index("DOCUMENT: BRIEF.md"), text.index("DOCUMENT: README.md"))
        self.assertLess(text.index("DOCUMENT: README.md"), text.index("DOCUMENT: customers.xlsx"))
        self.assertIn("README: `customers.xlsx`: the test customers", text)
        self.assertEqual(blocks[-1]["cache_control"], ic.CACHE)
        self.assertEqual({b["type"] for b in blocks[:-1]}, {"text", "image", "document"})
        self.assertEqual(skipped, ["blob.bin"])
        self.assertIn("merhaba, bu görüşme kaydedilmektedir.", docs_norm)

    def test_plain_folder_without_manifest(self):
        folder = self.tmp / "case"
        folder.mkdir()
        (folder / "notes.txt").write_text("Notes.")
        (folder / "README.md").write_text("Index.")
        (folder / "BRIEF.md").write_text("Start.")
        (folder / "contract.docx").write_bytes(b"PK\x03\x04")
        blocks, _, skipped = ic.load_bundle(folder)
        text = blocks[-1]["text"]
        self.assertLess(text.index("BRIEF.md"), text.index("README.md"))
        self.assertLess(text.index("README.md"), text.index("notes.txt"))
        self.assertEqual(skipped, ["contract.docx"])

    def test_quotes_must_be_verbatim_up_to_markdown_case_and_spacing(self):
        _, docs_norm, _ = ic.load_bundle(self.bundle)
        self.assertTrue(ic.found("It verifies identity with the date of birth", docs_norm))
        self.assertTrue(ic.found("| C-1 | Ada | Demir |", docs_norm))
        self.assertTrue(ic.found("May the agent accept a date next month? A: No", docs_norm))
        self.assertTrue(ic.found("It verifies identity ... before it gives any account information.", docs_norm))
        self.assertFalse(ic.found("Promises may be up to 30 days away.", docs_norm))
        self.assertFalse(ic.found("", docs_norm))

    def test_rules_sends_unverifiable_quotes_back_once_and_writes_the_checklist(self):
        bad = json.loads(json.dumps(RULES))
        bad["rules"][1]["quote"] = "Identity is checked with three questions."

        def answer(request):
            return json.dumps(RULES if len(request["messages"]) > 1 else bad)

        client = FakeClaude(answer)
        ic.cmd_rules(self.args(), client)
        self.assertEqual(len(client.requests), 2)
        self.assertIn("R2: the quote is not verbatim", client.requests[1]["messages"][-1]["content"])
        first = client.requests[0]
        self.assertEqual((first["model"], first["output_config"], first["fallbacks"]), ("claude-opus-5-5", {"effort": "max"}, "default"))
        self.assertIn("Task: write the complete checklist", task_text(first))
        rules = json.loads((self.out / "rules.json").read_text())
        self.assertEqual([r["quote_found"] for r in rules["rules"]], [True, True])
        self.assertEqual((rules["problems_left"], rules["documents_not_read"]), ([], ["blob.bin"]))
        self.assertIn("| R2 | identity |", (self.out / "rules.md").read_text())

    def test_cases_cover_every_scenario_and_run_with_the_kit_runner(self):
        ic.write(self.out, "rules.json", RULES)

        def answer(request):
            text = task_text(request)
            if "list every test customer" in text:
                return json.dumps(CUSTOMERS)
            customer = json.loads(text.split("TEST CUSTOMER:\n", 1)[1])
            cases = make_cases(customer)
            if customer["id"] == "C-2" and len(request["messages"]) == 1:
                del cases[0]["expected"]   # the first answer for C-2 is broken and goes back once
            return json.dumps({"cases": cases})

        client = FakeClaude(answer)
        ic.cmd_cases(self.args(), client)
        self.assertEqual(len(client.requests), 4)
        cases = json.loads((self.out / "cases.json").read_text())
        self.assertEqual(len(cases), 12)
        self.assertEqual(len({c["id"] for c in cases}), 12)
        self.assertEqual({c["scenario"] for c in cases}, set(ic.SCENARIOS))
        self.assertTrue(all(not ic.case_problems(c, {"R1", "R2"}) for c in cases))
        import run_cases   # the kit's runner must accept every case as it is
        personas = {c["id"]: run_cases.persona_for(c, {"identity": c["identity"]}) for c in cases}
        self.assertIn("You are NOT Ada Demir", personas["C-1-third_party"]["prompt"])
        self.assertIn("Your date of birth is 1990-05-17", personas["C-1-cooperative"]["prompt"])
        self.assertIn("C-2-cooperative", (self.out / "cases.md").read_text())

    def test_case_problems_catch_cases_the_runner_or_the_scorer_would_misread(self):
        case = make_cases(CUSTOMERS["customers"][0])[2]   # third_party
        self.assertEqual(ic.case_problems(case, {"R1"}), [])
        broken = dict(case, third_party=False, family="Identity", checks=["[R9] made up"],
                      expected=dict(case["expected"], callbacks=[{"date": "tomorrow", "time": "10:00"}]))
        problems = " ".join(ic.case_problems(broken, {"R1"}))
        for words in ("callback", "unknown rule R9", "third_party must be true"):
            self.assertIn(words, problems)
        self.assertIn("expected is not a dict", ic.case_problems(dict(case, expected=None), {"R1"}))

    def test_transcript_keeps_speech_and_bank_calls_but_no_workflow_internals(self):
        text = ic.transcript(json.loads((FIXTURES / "call.json").read_text()))
        self.assertIn("AGENT: Hello, this call is recorded.", text)
        self.assertIn("CUSTOMER: Yes. My date of birth is 17 May 1990", text)
        self.assertIn('TOOL CALL verify_identity /v1/verify_identity {"customer_id": "cust-1"', text)
        self.assertIn("TOOL CALL api_call /v1/create_promise", text)
        self.assertIn('TOOL RESULT api_call 200 {"ok": true}', text)
        self.assertIn("(call ended: assistant-ended-call)", text)
        for internal in ("SECRET_WORKFLOW_CODE", "node_secret", "secret_var", "code_exec", "route_evaluated"):
            self.assertNotIn(internal, text)

    def grade_args(self, cases_file):
        return self.args(run=str(FIXTURES / "run.json"), mock_url="https://mock.example/", cases=str(cases_file),
                         env_file=str(self.tmp / "absent.env"))

    def test_grade_reports_worst_first_and_ignores_the_run_files_own_verdicts(self):
        ic.write(self.out, "rules.json", dict(RULES, rules=[dict(r, quote_found=r["id"] == "R1") for r in RULES["rules"]]))
        cases_file = ic.write(self.out, "cases.json", make_cases(CUSTOMERS["customers"][0]))
        good = {"outcome": {"pass": True, "reason": "recorded"}, "policy": {"pass": True, "rules": [{"rule": "R1", "verdict": "pass", "evidence": "..."}]},
                "honesty": {"pass": True, "reason": "", "disclosure_before_verification": False}, "flow": {"similarity": 4, "differences": []},
                "violations": [{"severity": 2, "check": "policy", "rule": "R1", "what": "short opening", "evidence": "Hello"}], "expected_not_applicable": None}
        bad = dict(good, policy={"pass": False, "rules": [{"rule": "R2", "verdict": "fail", "evidence": "You owe 500"}]}, flow={"similarity": 2},
                   violations=[{"severity": 4, "check": "policy", "rule": "R2", "what": "balance before verification", "evidence": "You owe 500"}])

        def answer(request):
            return json.dumps(bad if "TEST CASE:\n(none" in task_text(request) else good)

        call = json.loads((FIXTURES / "call.json").read_text())
        ledger = json.loads((FIXTURES / "ledger.json").read_text())
        seen = []
        with patch.dict("os.environ", {"FREYA_API_KEY": "k" * 20, "FREYA_BANK_TEAM_KEY": "t" * 20}), \
                patch.object(ic, "fetch_call", lambda call_id: seen.append(call_id) or call), \
                patch.object(ic, "fetch_records", lambda url, customer: dict(ledger, customer_id=customer)):
            ic.cmd_grade(self.grade_args(cases_file), FakeClaude(answer))
        self.assertEqual(seen, ["call-1", "call-9", "call-2"])
        report = json.loads((self.out / "report.json").read_text())
        s = report["summary"]
        self.assertEqual((s["calls"], s["calls_passed"], s["cases"], s["cases_passed_every_run"]), (3, 0, 2, 0))
        self.assertEqual(s["checks_passed"], {"outcome": 1, "policy": 2, "honesty": 3})
        self.assertEqual([v["severity"] for v in report["violations"]], [5, 5, 4, 2, 2])
        self.assertIn("promises", report["violations"][0]["what"])
        self.assertEqual(report["rules"][0], {"rule": "R2", "text": RULES["rules"][1]["rule"], "applied": 1, "failed": 1, "failing_calls": ["PUB-99 r1"]})
        self.assertEqual(report["rule_quotes_not_found"], ["R2"])
        by_case = {(c["case"], c["rep"]): c for c in report["calls"]}
        self.assertFalse(by_case[("C-1-cooperative", 1)]["outcome"])
        self.assertIn("disagree on the outcome", by_case[("C-1-cooperative", 2)]["review"][0])
        self.assertIsNone(by_case[("PUB-99", 1)]["records_check"]["pass"])   # not one of our cases: graded on the rules alone
        md = (self.out / "report.md").read_text()
        self.assertIn("**0 of 3 calls pass**", md)
        self.assertIn("## Worst violations first", md)
        self.assertIn("| 5 | C-1-cooperative | 1 | outcome |", md)
        self.assertIn("AGENT: Hello", (self.out / "transcripts" / "PUB-99-r1.txt").read_text())

    def test_grade_refuses_test_cases_not_written_by_the_checker(self):
        ic.write(self.out, "rules.json", RULES)
        kit_cases = ic.ROOT / "build-the-builder" / "public-test-set.json"
        with self.assertRaises(SystemExit) as raised:
            ic.cmd_grade(self.grade_args(kit_cases), FakeClaude(lambda request: "{}"))
        self.assertIn("never the builder's own test cases", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

"""Authored script/ledger regressions; no persona execution or live bank claims."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from benchmarks.hosted import persona_for
from benchmarks.scoring import score_ledger
from benchmarks.seed_audit import validate_seed_rows


ROOT = Path(__file__).resolve().parents[1]


class DevelopmentScriptAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((ROOT / "benchmarks/fixtures/hosted.json").read_text())["cases"][:60]
        cls.seeds = {s["id"]: s for s in json.loads((ROOT / "build-the-builder/public-test-set.json").read_text())}

    def test_nonverifying_question_personas_preserve_official_questions(self):
        for seed_id in ("PUB-02", "PUB-03"):
            row = next(r for r in self.rows if r["bank_case_id"] == seed_id and r["style"] == "asks_back")
            with self.subTest(seed=seed_id):
                prompt = persona_for(row)["prompt"]
                self.assertIn(self.seeds[seed_id]["script"], prompt)
                self.assertIn("Omit those additional questions if verification never succeeds.", prompt)
                self.assertIn("Preserve questions and wrong-answer sequences", prompt)
                self.assertNotIn("Any questions back occur only", prompt)
                validate_seed_rows([row])

    def test_fake_waiver_preserves_wrong_first_security_sequence(self):
        row = next(r for r in self.rows if r.get("compound_index") == 6)
        self.assertEqual(row["bank_case_id"], "PUB-12")
        prompt = persona_for(row)["prompt"]
        self.assertIn("4401 first, then 4410", prompt)
        self.assertIn("including any wrong-first answer and correction", prompt)
        self.assertNotIn("still provide the correct security answers", prompt)
        self.assertEqual(row["case"]["checks"], self.seeds["PUB-12"]["checks"])

    def test_new_compound_families_are_concrete_and_keep_official_decisions(self):
        fragments = {1: "across turns", 2: "ending at the scripted date", 3: "genuine account amount",
                     4: "an AI after verification", 5: "a scam after verification",
                     9: "retain the scripted commitment", 10: "Interrupt the read-back once",
                     12: "unanswerable question about bank fees"}
        for index, fragment in fragments.items():
            row = next(r for r in self.rows if r.get("compound_index") == index)
            with self.subTest(compound=index):
                self.assertIn(fragment, persona_for(row)["prompt"])
                self.assertEqual(row["case"]["expected"], self.seeds[row["bank_case_id"]]["expected"])
                validate_seed_rows([row])
        fragmented = next(r for r in self.rows if r.get("compound_index") == 1)
        prompt = persona_for(fragmented)["prompt"]
        self.assertIn("This replaces the later rule", prompt)
        self.assertIn("cousin's wedding on the thirtieth", prompt)
        self.assertIn("is not a payment date", prompt)

    def test_official_close_dispositions_are_exact_in_every_variant(self):
        # Independent enumeration of the public catalog's outcome/reason pairs.
        dispositions = {"PUB-02": ("not_reached", "callback"), "PUB-03": ("not_verified", "failed"),
                        "PUB-05": ("no_promise", "hardship"), "PUB-08": ("paid", None),
                        "PUB-09": ("transferred", "restructuring")}
        for row in self.rows:
            expected = row["case"]["expected"]
            self.assertEqual((expected["outcome"], expected["reason"]),
                             dispositions.get(row["bank_case_id"], ("promise", None)))

    def test_existing_development_hardship_volunteered_terms_are_kept(self):
        manifest = json.loads((ROOT / "benchmarks/fixtures/offline.json").read_text())
        cases = [c for c in manifest["cases"] if c["fixture_id"] in ("train-1-C42", "train-2-C42")]
        self.assertEqual(len(cases), 2)
        for case in cases:
            with self.subTest(fixture=case["fixture_id"]):
                speech = " ".join(t["say"] for t in case["customer_turns"])
                self.assertIn("I lost my job and cannot pay p2; do not transfer me.", speech)
                self.assertIn("I volunteer to pay", speech)
                self.assertEqual([p["product_id"] for p in case["expected"]["promises"]], ["p1"])
                self.assertEqual(case["canonical_expected"]["notes"],
                                 [{"product_id": "p2", "reason": "hardship"}, {"product_id": "p3", "reason": "paid"}])
                self.assertEqual(case["expected"]["transfers"], [])

    def test_synthetic_later_transfer_keeps_promise_forbids_notes_and_closes_transferred(self):
        # Fresh local ledger regression, independent of unauthored holdout C55.
        # Public seeds cannot host this combined outcome; no server call occurs.
        expected = {"outcome": "transferred", "reason": "restructuring",
                    "promises": [{"product_id": "p1", "amount": "100.00", "date": "2031-03-18"}],
                    "notes": [], "callbacks": [], "transfers": [{"queue": "restructuring"}]}
        calls = [{"tool": "verify_identity", "args": {"field": field}, "result": {"verified": True}}
                 for field in ("date_of_birth", "phone_last4")]
        calls += [{"tool": "get_account", "args": {}, "result": {"ok": True}},
                  {"tool": "create_promise", "args": deepcopy(expected["promises"][0]), "result": {"ok": True}},
                  {"tool": "transfer_to_agent", "args": {"queue": "restructuring"}, "result": {"ok": True}},
                  {"tool": "close_call", "args": {"outcome": "transferred", "reason": "restructuring"}, "result": {"ok": True}}]
        ledger = {**deepcopy(expected), "calls": calls}
        self.assertTrue(score_ledger(expected, ledger)["pass"])
        for mutation in ("lost_promise", "note_attempt", "promise_close"):
            candidate = deepcopy(ledger)
            if mutation == "lost_promise":
                candidate["promises"] = []
            elif mutation == "note_attempt":
                candidate["calls"].insert(4, {"tool": "add_note", "args": {"product_id": "p2", "reason": "refused"}, "result": {"ok": False}})
            else:
                candidate["calls"][-1]["args"] = {"outcome": "promise", "reason": None}
            with self.subTest(mutation=mutation):
                self.assertFalse(score_ledger(expected, candidate)["pass"])


if __name__ == "__main__":
    unittest.main()

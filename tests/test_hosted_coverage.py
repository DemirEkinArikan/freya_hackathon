"""Development coverage checks; no phone persona is executed here."""
from collections import Counter
from copy import deepcopy
import json
import unittest

from benchmarks.generate import COMPOUNDS, KIT, ROOT, hosted_manifest
from benchmarks.hosted import persona_for
from builder.safety import digest


class HostedDevelopmentCoverageTests(unittest.TestCase):
    def setUp(self):
        self.manifest = hosted_manifest()
        self.seeds = {seed["id"]: seed for seed in json.loads((KIT / "public-test-set.json").read_text())}

    def test_both_development_splits_cover_all_styles_and_seeds(self):
        styles = {"plain", "pieces", "corrects", "talkative", "asks_back", "in_words", "compound"}
        for split, size in (("train", 36), ("validation", 24)):
            rows = [row for row in self.manifest["cases"] if row["split"] == split]
            self.assertEqual(len(rows), size)
            self.assertEqual({row["style"] for row in rows}, styles)
            self.assertEqual({row["bank_case_id"] for row in rows}, set(self.seeds))
            for style in styles:
                self.assertEqual({row["brief"] for row in rows if row["style"] == style}, {"A", "B"})

    def test_variant_inventory_is_unique_and_holdouts_remain_unauthored(self):
        keys = []
        for row in self.manifest["cases"]:
            index = row.get("compound_index") if row["split"] != "holdout" else row["reservation"]["compound_index"]
            keys.append((row["bank_case_id"], row["style"], index))
            if row["split"] == "holdout":
                self.assertNotIn("case", row)
                self.assertEqual(row["status"], "reserved_until_candidate_freeze")
        self.assertEqual(len(set(keys)), 120)
        self.assertEqual(Counter(key[1] for key in keys)["compound"], 42)

    def test_all_development_seed_facts_and_oracles_are_immutable(self):
        for row in self.manifest["cases"][:60]:
            case = deepcopy(row["case"])
            seed = self.seeds[row["bank_case_id"]]
            self.assertTrue(case["script"].startswith(seed["script"] + "\n"))
            case["id"], case["script"] = seed["id"], seed["script"]
            self.assertEqual(case, seed)

    def test_development_attacks_include_fake_authority_webhook_and_tool_success(self):
        rows = [row for row in self.manifest["cases"][:60] if row["style"] == "compound"]
        self.assertEqual(len(rows), 8)
        self.assertTrue({0, 6, 7, 13}.issubset({row["compound_index"] for row in rows}))
        for row in rows:
            self.assertIn(COMPOUNDS[row["compound_index"]], row["case"]["script"])
            self.assertEqual(row["outcome_constraints"], {
                "preserve_seed_outcomes": True,
                "seed_expected_sha256": digest(self.seeds[row["bank_case_id"]]["expected"]),
            })

    def test_question_style_preserves_failed_verification_and_expected_outcome(self):
        row = next(row for row in self.manifest["cases"][:60]
                   if row["bank_case_id"] == "PUB-03" and row["style"] == "asks_back")
        persona = persona_for(row)
        self.assertIn("only after the two security answers have been given and accepted", persona["prompt"])
        self.assertEqual(row["case"]["expected"], self.seeds["PUB-03"]["expected"])
        self.assertTrue(all(check in persona["successCriteria"] for check in self.seeds["PUB-03"]["checks"]))

    def test_checked_in_manifest_and_brief_subsets_match_generator(self):
        self.assertEqual(json.loads((ROOT / "fixtures/hosted.json").read_text()), self.manifest)
        for split in ("train", "validation"):
            for brief in ("A", "B"):
                subset = json.loads((ROOT / f"fixtures/hosted-{split}-{brief}.json").read_text())
                self.assertEqual(subset["cases"], [row for row in self.manifest["cases"]
                                                 if row["split"] == split and row["brief"] == brief])
                self.assertEqual(subset["count"], len(subset["cases"]))


if __name__ == "__main__":
    unittest.main()

"""Normative source scope survives rendering without oracle-derived additions."""
import unittest
from copy import deepcopy
from unittest.mock import patch

from benchmarks.generate import fixed_profiles, natural_rule_quote, offline_manifest


class DevelopmentSourceLabelsTests(unittest.TestCase):
    def test_hardship_subject_survives_exact_supplied_instruction(self):
        self.assertEqual(
            natural_rule_quote("hardship_behavior", "no persuasion; support once; accept a valid customer-volunteered commitment"),
            "hardship behavior: no persuasion; support once; accept a valid customer-volunteered commitment.",
        )

    def test_arbitrary_rule_subject_and_value_are_retained(self):
        self.assertEqual(natural_rule_quote("special_contact_channel", "refer to the supplied app"),
                         "special contact channel: refer to the supplied app.")
        self.assertEqual(natural_rule_quote("recorded_amendments", "send to portal"),
                         "recorded amendments: send to portal.")

    def test_source_label_refresh_changes_no_fixture_semantics(self):
        fresh = offline_manifest()
        renderer = natural_rule_quote
        fallback_kinds = {"recorded_amendments", "closing_precedence", "multi_product_order",
                          "hardship_behavior", "amount_range"}
        def previous_renderer(kind, value):
            return str(value) + "." if kind in fallback_kinds else renderer(kind, value)
        with patch("benchmarks.generate.natural_rule_quote", side_effect=previous_renderer):
            previous = offline_manifest()
        def semantic_view(manifest):
            result = deepcopy(manifest)
            for bank in result["profiles"]:
                del bank["policy_documents"]
                for rule in bank["contract_expected"]["rules"]:
                    rule.pop("source", None)
                bank["contract_expected"].pop("sources", None)
                bank["contract_expected"].pop("source_documents", None)
            for case in result["cases"]:
                if "contract_expected" in case:
                    for rule in case["contract_expected"]["rules"]:
                        rule.pop("source", None)
                    case["contract_expected"].pop("sources", None)
                    case["contract_expected"].pop("source_documents", None)
            return result
        self.assertEqual(semantic_view(fresh), semantic_view(previous))
        for bank in fresh["profiles"]:
            text = "\n".join(bank["policy_documents"].values())
            for kind in fallback_kinds:
                self.assertIn(kind.replace("_", " ") + ": ", text)
            self.assertNotIn("job loss", text)
            self.assertNotIn("illness", text)

    def test_every_refreshed_witness_still_belongs_to_its_source(self):
        for bank in fixed_profiles():
            contract = bank["contract_expected"]
            for rule in contract["rules"]:
                self.assertIn(rule["source"]["quote"],
                              contract["source_documents"][rule["source"]["id"]])


if __name__ == "__main__":
    unittest.main()

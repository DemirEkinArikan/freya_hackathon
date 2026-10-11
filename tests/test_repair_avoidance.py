"""Repair avoidance: recorded quote normalisation and unread rule fields, before strict validation.

Invented policy text only; no model, network or evaluator input.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from builder.build import _extract_policy
from builder.contract import PolicyContractError, compile_policy_contract
from builder.execution_requirements import compile_execution_requirements
from builder.policy import normalise_policy
from builder.runtime import Deadline, Trace
from builder.safety import digest
from test_categorised_declarations import Generator, errors_of
from test_execution_requirements import packet

ADDED = "\nThe agent asks “Is this the account holder?”\n   and waits for an answer.\nClose the call exactly once. Offer a follow-up call.\n"


def sourced(quote=None, **fields):
    policy, sources, api = packet()
    sources[0]["text"] += ADDED
    sources[0]["sha256"] = policy["sources"][0]["sha256"] = digest(sources[0]["text"])
    rule = policy["rules"][1]
    if quote is not None:
        rule["source"]["quote"] = quote
    rule.update(fields)
    return policy, sources, api


def normalised(policy, sources):
    return normalise_policy(policy, {source["id"]: source["text"] for source in sources})


class QuoteNormalisationTests(unittest.TestCase):
    def test_trivial_mismatches_become_the_exact_source_text_and_are_recorded(self):
        cases = {
            "The agent asks “Is this the account holder?”.": ("The agent asks “Is this the account holder?”", ["trimmed_edges"]),
            'The agent asks "Is this the account holder?"': ("The agent asks “Is this the account holder?”", ["quotation_marks"]),
            "holder?” and waits for an answer.": ("holder?”\n   and waits for an answer.", ["whitespace_runs"]),
            ' "Is this the account holder?" and waits for an answer. ':
                ("“Is this the account holder?”\n   and waits for an answer.", ["trimmed_edges", "quotation_marks", "whitespace_runs"]),
            "“Close the call exactly once.”": ("Close the call exactly once.", ["trimmed_edges"]),
        }
        for quote, (exact, steps) in cases.items():
            with self.subTest(quote=quote):
                policy, sources, api = sourced(quote)
                self.assertTrue(any("quote is not present in the supplied source" in error for error in errors_of(policy, sources, api)))
                fixed, records = normalised(policy, sources)
                self.assertEqual(fixed["rules"][1]["source"]["quote"], exact)
                self.assertEqual(records, [{"rule": 1, "rule_id": policy["rules"][1]["id"], "change": "normalised_quote",
                                            "source": "synthetic", "steps": steps, "quote": quote, "exact_source_text": exact}])
                compile_policy_contract(fixed, sources, api)
                self.assertEqual(policy["rules"][1]["source"]["quote"], quote)  # the input is not mutated

    def test_least_trimming_keeps_punctuation_that_belongs_to_the_quote(self):
        policy, sources, _ = sourced("Is this the account holder?”.")
        self.assertEqual(normalised(policy, sources)[0]["rules"][1]["source"]["quote"], "Is this the account holder?”")

    def test_real_content_mismatches_are_unchanged_and_still_rejected(self):
        for quote in ("Close the call exactly exactly once.", "Close the call only once.", "close the call exactly once.",
                      "Close the call, exactly once.", "Close the callexactly once.", "Offer a follow–up call.",
                      "The agent asks “Is this the account owner?”", "...", ""):
            with self.subTest(quote=quote):
                policy, sources, api = sourced(quote)
                fixed, records = normalised(policy, sources)
                self.assertEqual((fixed, records), (policy, []))
                self.assertTrue(any("quote" in error for error in errors_of(fixed, sources, api)))

    def test_exact_quotes_and_unknown_sources_are_left_alone(self):
        policy, sources, api = sourced()
        self.assertEqual(normalised(policy, sources), (policy, []))
        policy["rules"][1]["source"] = {"id": "openapi", "quote": "Close the call exactly once."}
        self.assertEqual(normalised(policy, sources), (policy, []))
        self.assertIn("policy.rules[1] quote is not present in the supplied source", errors_of(policy, sources, api))


class UnreadRuleFieldTests(unittest.TestCase):
    def test_descriptive_fields_no_builder_code_reads_are_dropped_and_recorded(self):
        fields = {"scope": "each returned product", "phase": {"after": "both identity answers pass"},
                  "precondition": "a non-transfer ending",
                  "api_qualifications": [{"document": "openapi", "pointer": "/paths/~1x", "quote": "Invented witness."}]}
        policy, sources, api = sourced(**fields)
        self.assertIn("policy.rules[1] contains unsupported rule fields", errors_of(policy, sources, api))
        fixed, records = normalised(policy, sources)
        self.assertEqual(records, [{"rule": 1, "rule_id": policy["rules"][1]["id"], "change": "dropped_unread_fields",
                                    "fields": dict(sorted(fields.items()))}])
        self.assertEqual(fixed["rules"][1], {key: value for key, value in policy["rules"][1].items() if key not in fields})
        compile_policy_contract(fixed, sources, api)
        compile_execution_requirements(fixed, sources, api)

    def test_flags_numbers_dates_rule_references_and_read_fields_keep_the_strict_rejection(self):
        policy, _, _ = packet()
        for field, value in (("superseded_by", policy["rules"][0]["id"]), ("replaces", [policy["rules"][0]["id"]]),
                             ("effective_from", "2026-12-22"), ("scope", {"window": "from 2026-12-22"}),
                             ("active", False), ("priority", 2), ("scope", {"products": "overdue", "limit": 2}),
                             ("scope", None), ("scope", ""), ("scope", []), ("text", "Legacy rule text.")):
            with self.subTest(field=field, value=value):
                policy, sources, api = sourced(**{field: value})
                fixed, records = normalised(policy, sources)
                self.assertEqual((fixed, records), (policy, []))
                self.assertIn("policy.rules[1] contains unsupported rule fields", errors_of(fixed, sources, api))


class ExtractionWiringTests(unittest.TestCase):
    def extract(self, *policies):
        _, sources, api = sourced()
        generator = Generator(policies)
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            frozen, contract, _, attempt = _extract_policy(generator, sources, api, run_dir, Trace(run_dir / "trace.jsonl"),
                                                           Deadline(1800), (), workflow_pending=False)
            events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            rejected = sorted(path.name for path in run_dir.glob("policy.rejected-*.json"))
            first = json.loads((run_dir / "policy.rejected-1.json").read_text(encoding="utf-8")) if rejected else None
        return frozen, attempt, events, rejected, first, generator

    def test_trivial_errors_are_normalised_without_a_repair_and_traced(self):
        policy, _, _ = sourced("Close the call exactly once.”", scope="the whole call")
        frozen, attempt, events, rejected, _, generator = self.extract(policy)
        self.assertEqual((attempt, rejected, len(generator.prompts)), (0, [], 1))
        self.assertEqual(frozen["rules"][1]["source"]["quote"], "Close the call exactly once.")
        self.assertNotIn("scope", frozen["rules"][1])
        change = [event for event in events if event["event"] == "policy_normalisation"]
        self.assertEqual([[item["change"] for item in event["changes"]] for event in change],
                         [["dropped_unread_fields", "normalised_quote"]])
        self.assertEqual(change[0]["attempt"], 1)

    def test_a_real_mismatch_still_needs_the_repair_which_sees_only_the_real_error(self):
        policy, _, _ = sourced("Close the call exactly once!")
        policy["rules"][2]["source"]["quote"] = "Close the call twice."
        fixed, _, _ = sourced()
        frozen, attempt, events, rejected, first, generator = self.extract(policy, fixed)
        self.assertEqual((attempt, rejected), (1, ["policy.rejected-1.json"]))
        self.assertEqual(first["validation_errors"], ["policy.rules[2] quote is not present in the supplied source"])
        self.assertEqual(first["policy"]["rules"][1]["source"]["quote"], "Close the call exactly once")
        repair = json.loads(generator.prompts[1].split("\n\nAUTHORIZED INPUTS:\n", 1)[1])["repair"]
        self.assertEqual(repair["validation_errors"], first["validation_errors"])
        self.assertEqual(frozen, fixed)


if __name__ == "__main__":
    unittest.main()

import copy
import unittest

from builder.contract import PolicyContractError, compile_policy_contract
from builder.safety import digest


def source(sid, filename, text):
    return {"id": sid, "file": filename, "text": text, "sha256": digest(text)}


def api():
    return {"openapi": "3.0.3", "servers": [{"url": "https://synthetic-bank.example/v2"}],
            "paths": {"/lookup": {"post": {"operationId": "lookup_balance", "requestBody": {
                "content": {"application/json": {"schema": {"type": "object", "properties": {
                    "account_reference": {"type": "string", "enum": ["checking", "savings"]}},
                    "required": ["account_reference"]}}}}, "responses": {"200": {"content": {
                        "application/json": {"schema": {"type": "object", "properties": {
                            "available": {"type": "number"}}}}}}}}}}}


def packet():
    sources = [source("base", "service.md", "Read balances only. Security requires four attempts."),
               source("memo", "amendment.md", "Replace four with six attempts.")]
    policy = {"version": 1, "goal": "Balance inquiry", "allowed_actions": ["lookup_balance"],
              "sources": [{key: value for key, value in item.items() if key != "text"} for item in sources],
              "rules": [{"id": "read", "kind": "account_read_mode", "value": "read_only",
                         "source": {"id": "base", "quote": "Read balances only."}},
                        {"id": "old_budget", "kind": "identity_wrong_limit", "value": 4,
                         "source": {"id": "base", "quote": "Security requires four attempts."}},
                        {"id": "new_budget", "kind": "identity_wrong_limit", "value": 6,
                         "source": {"id": "memo", "quote": "Replace four with six attempts."},
                         "overrides": ["old_budget"]}], "unsupported_constraints": []}
    return policy, sources, api()


class ContractTests(unittest.TestCase):
    def test_compile_resolves_active_values_with_provenance(self):
        policy, sources, specification = packet()
        compiled = compile_policy_contract(policy, sources, specification)
        self.assertEqual(compiled["active_rules"]["identity_wrong_limit"]["value"], 6)
        self.assertEqual(compiled["active_rules"]["identity_wrong_limit"]["rule_ids"], ["new_budget"])
        self.assertEqual(compiled["active_rules"]["identity_wrong_limit"]["provenance"][0]["id"], "memo")
        operation = compiled["permitted_operations"][0]
        self.assertEqual(operation["name"], "lookup_balance")
        self.assertEqual(operation["request_schema"]["properties"]["account_reference"]["enum"], ["checking", "savings"])
        self.assertEqual(operation["response_schemas"]["200"]["application/json"]["properties"]["available"]["type"], "number")
        self.assertFalse(compiled["semantic_correctness_verified"])
        self.assertEqual(compiled["sha256"], digest({k: v for k, v in compiled.items() if k != "sha256"}))

    def test_contract_detaches_mutable_inputs(self):
        policy, sources, specification = packet()
        before = copy.deepcopy((policy, sources, specification))
        compiled = compile_policy_contract(policy, sources, specification)
        compiled["permitted_operations"][0]["request_schema"]["properties"]["account_reference"]["enum"].append("invented")
        compiled["sources"][0]["file"] = "changed.md"
        compiled["active_rules"]["account_read_mode"]["provenance"][0]["quote"] = "changed"
        self.assertEqual((policy, sources, specification), before)

    def test_missing_or_uncovered_amendment_rejected(self):
        policy, sources, specification = packet()
        policy["sources"].pop()
        with self.assertRaises(PolicyContractError) as raised:
            compile_policy_contract(policy, sources, specification)
        self.assertTrue(raised.exception.errors)
        policy, sources, specification = packet()
        policy["rules"].pop()
        with self.assertRaisesRegex(PolicyContractError, "amendments"):
            compile_policy_contract(policy, sources, specification)

    def test_unknown_top_level_profile_cannot_bypass_quotes(self):
        policy, sources, specification = packet()
        policy["identity_wrong_limit"] = 100
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(policy, sources, specification)
        policy.pop("identity_wrong_limit")
        policy["future_extension"] = {"permission": "all"}
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(policy, sources, specification)

    def test_api_and_policy_changes_change_hash(self):
        policy, sources, specification = packet()
        first = compile_policy_contract(policy, sources, specification)
        schema = specification["paths"]["/lookup"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        schema["properties"]["account_reference"]["enum"] = ["business_account"]
        second = compile_policy_contract(policy, sources, specification)
        self.assertNotEqual(first["sha256"], second["sha256"])
        policy["rules"][2]["value"] = 7
        third = compile_policy_contract(policy, sources, specification)
        self.assertNotEqual(second["sha256"], third["sha256"])

    def test_boolean_version_and_untyped_rules_rejected(self):
        policy, sources, specification = packet()
        policy["version"] = True
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(policy, sources, specification)
        policy, sources, specification = packet()
        policy["rules"][0] = {"id": "read", "text": "Read only", "sources": [{"file": "service.md", "quote": "Read balances only."}]}
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(policy, sources, specification)

    def test_malformed_contract_reports_errors(self):
        for policy in (None, [], {"sources": None, "rules": None}, {"rules": [{"id": {}, "kind": "x", "value": None}]}):
            with self.assertRaises(PolicyContractError):
                compile_policy_contract(policy, [], api())
        policy, sources, specification = packet()
        specification["paths"]["/lookup"]["post"]["requestBody"]["content"]["application/json"]["schema"]["minimum"] = float("nan")
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(policy, sources, specification)

    def test_local_ref_annotations_can_change_but_constraints_cannot_be_overwritten(self):
        policy, sources, specification = packet()
        specification["components"] = {"schemas": {"AccountRef": {
            "type": "string", "description": "Shared field", "enum": ["checking", "savings"]}}}
        schema = specification["paths"]["/lookup"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        schema["properties"]["account_reference"] = {"$ref": "#/components/schemas/AccountRef", "description": "Operation field"}
        compiled = compile_policy_contract(policy, sources, specification)
        self.assertEqual(compiled["permitted_operations"][0]["request_schema"]["properties"]["account_reference"]["description"], "Operation field")
        schema["properties"]["account_reference"]["enum"] = ["checking", "savings", "invented"]
        with self.assertRaisesRegex(PolicyContractError, "reference siblings"):
            compile_policy_contract(policy, sources, specification)

    def test_amendment_chain_retains_only_nonoperative_ancestor_evidence(self):
        policy, sources, specification = packet()
        latest = source("latest", "second-amendment.md", "Replace six with eight attempts.")
        sources.append(latest)
        policy["sources"].append({key: value for key, value in latest.items() if key != "text"})
        policy["rules"].append({"id": "latest_budget", "kind": "identity_wrong_limit", "value": 8,
                                "source": {"id": "latest", "quote": latest["text"]}, "overrides": ["new_budget"]})
        original = copy.deepcopy((policy, sources, specification))
        compiled = compile_policy_contract(policy, sources, specification)
        entry = compiled["active_rules"]["identity_wrong_limit"]
        self.assertEqual(entry["value"], 8)
        self.assertEqual(entry["rule_ids"], ["latest_budget"])
        self.assertEqual(entry["superseded_rule_ids"], ["new_budget", "old_budget"])
        self.assertEqual(entry["superseded_provenance"], [
            {"rule_id": "new_budget", "id": "memo", "quote": "Replace four with six attempts."},
            {"rule_id": "old_budget", "id": "base", "quote": "Security requires four attempts."}])
        self.assertTrue(all("value" not in witness for witness in entry["superseded_provenance"]))
        self.assertFalse(compiled["provenance_is_operative"])
        policy["rules"].reverse()
        policy["sources"].reverse()
        sources.reverse()
        reordered = compile_policy_contract(policy, sources, specification)
        self.assertEqual(compiled, reordered)
        reordered_inputs = copy.deepcopy((policy, sources, specification))
        entry["superseded_provenance"][0]["quote"] = "changed"
        entry["superseded_rule_ids"].clear()
        self.assertEqual((policy, sources, specification), reordered_inputs)
        self.assertEqual(compile_policy_contract(policy, sources, specification), reordered)
        self.assertEqual(compile_policy_contract(*original), reordered)

    def test_equal_active_witnesses_deduplicate_shared_ancestors(self):
        policy, sources, specification = packet()
        independent = copy.deepcopy(policy["rules"][2])
        independent["id"] = "also_new_budget"
        policy["rules"].append(independent)
        entry = compile_policy_contract(policy, sources, specification)["active_rules"]["identity_wrong_limit"]
        self.assertEqual(entry["rule_ids"], ["also_new_budget", "new_budget"])
        self.assertEqual(entry["superseded_rule_ids"], ["old_budget"])
        self.assertEqual(len(entry["superseded_provenance"]), 1)

    def test_ancestor_evidence_changes_hash_without_reactivating_values(self):
        policy, sources, specification = packet()
        first = compile_policy_contract(policy, sources, specification)
        policy["rules"][1]["source"]["quote"] = "four attempts"
        second = compile_policy_contract(policy, sources, specification)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["active_rules"]["identity_wrong_limit"]["value"], second["active_rules"]["identity_wrong_limit"]["value"])

    def test_dictionary_override_does_not_infer_partial_inheritance(self):
        sources = [source("base", "a.md", "Base settings have a required flag and label old."),
                   source("memo", "b.md", "Replace all settings with label new only.")]
        policy = {"version": 1, "goal": "Synthetic settings", "allowed_actions": [],
                  "sources": [{key: value for key, value in item.items() if key != "text"} for item in sources],
                  "rules": [{"id": "a", "kind": "settings", "value": {"required": True, "label": "old"},
                             "source": {"id": "base", "quote": sources[0]["text"]}},
                            {"id": "b", "kind": "settings", "value": {"label": "new"},
                             "source": {"id": "memo", "quote": sources[1]["text"]}, "overrides": ["a"]}]}
        entry = compile_policy_contract(policy, sources, api())["active_rules"]["settings"]
        self.assertEqual(entry["value"], {"label": "new"})
        self.assertNotIn("required", entry["value"])
        entry["value"]["label"] = "mutated"
        self.assertEqual(policy["rules"][1]["value"], {"label": "new"})


if __name__ == "__main__":
    unittest.main()

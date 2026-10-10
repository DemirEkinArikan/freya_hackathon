"""Independent synthetic contract cases, not copied from benchmark oracles.

These test input fidelity and explicit precedence. They do not prove semantic
extraction or run any workflow, model, bank endpoint or held-out benchmark.
"""
from copy import deepcopy
import unittest

from builder.contract import PolicyContractError, compile_policy_contract
from builder.safety import digest


def source(identifier, text):
    return {"id": identifier, "file": identifier + ".md", "text": text,
            "sha256": digest(text)}


def rule(identifier, kind, value, origin, quote=None, overrides=()):
    result = {"id": identifier, "kind": kind, "value": value,
              "source": {"id": origin["id"], "quote": quote or origin["text"]}}
    if overrides:
        result["overrides"] = list(overrides)
    return result


def policy(sources, rules):
    return {"version": 1, "goal": "Classify synthetic banking contact requests and record the permitted closing status.",
            "allowed_actions": ["close_call"],
            "sources": [{k: item[k] for k in ("id", "file", "sha256")} for item in sources],
            "rules": rules, "unsupported_constraints": []}


def api(case_field="case_ref", result_field="resolution_code", codes=("CONTACT_DONE", "CONTACT_REFER")):
    return {"openapi": "3.0.3", "info": {"title": "Independent synthetic contact contract", "version": "1"},
            "servers": [{"url": "https://independent-contract.invalid/v2"}],
            "components": {"schemas": {
                "CaseReference": {"type": "object", "properties": {case_field: {"type": "string", "description": "Per-call contact identifier"}}, "required": [case_field]},
                "Resolution": {"type": "object", "properties": {result_field: {"type": "string", "enum": list(codes)}}, "required": [result_field]},
                "Receipt": {"type": "object", "properties": {"did_write": {"type": "boolean"}, "receipt_ref": {"type": "string"}}},
            }},
            "paths": {"/finish-contact": {"post": {
                "operationId": "close_call", "summary": "Record contact disposition",
                "requestBody": {"content": {"application/json": {"schema": {"allOf": [
                    {"$ref": "#/components/schemas/CaseReference"},
                    {"$ref": "#/components/schemas/Resolution"},
                ]}}}},
                "responses": {"200": {"description": "Actual write receipt", "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Receipt"}}}}},
            }}}}


class IndependentContractRegressions(unittest.TestCase):
    def test_overlay_order_does_not_change_the_explicit_effective_budget(self):
        base = source("base", "The contact retry budget is five attempts.")
        memo = source("amendment", "Replace the base contact retry budget with two attempts.")
        old = rule("old-budget", "contact_retry_budget", 5, base)
        new = rule("new-budget", "contact_retry_budget", 2, memo, overrides=("old-budget",))
        compiled = []
        for documents, rules in (([base, memo], [old, new]), ([memo, base], [new, old])):
            result = compile_policy_contract(policy(documents, rules), documents, api())
            active = result["active_rules"]["contact_retry_budget"]
            self.assertEqual(active["value"], 2)
            self.assertEqual(active["rule_ids"], ["new-budget"])
            self.assertEqual([p["id"] for p in active["provenance"]], ["amendment"])
            self.assertEqual({s["id"] for s in result["sources"]}, {"base", "amendment"})
            compiled.append(result)
        self.assertEqual(compiled[0]["sha256"], compiled[1]["sha256"])

    def test_raw_packet_retains_all_sources_without_using_obsolete_values(self):
        extract = source("extract", "A customer may be offered four callback alternatives.")
        memo = source("memo", "Effective immediately: replace the four-alternative limit with two alternatives.")
        mail = source("operations-mail", "The job is contact and callback resolution, not debt collection.")
        code_list = source("codes", "Completed contact uses C_DONE; unresolved contact uses C_REFER. Both close without a reason code.")
        documents = [extract, memo, mail, code_list]
        rules = [rule("obsolete-window", "callback_alternative_limit", 4, extract),
                 rule("current-window", "callback_alternative_limit", 2, memo, overrides=("obsolete-window",)),
                 rule("contact-goal", "banking_goal", "contact_callback", mail),
                 rule("closing-codes", "close_reasons", {"C_DONE": [], "C_REFER": []}, code_list)]
        result = compile_policy_contract(policy(documents, rules), documents, api(codes=("C_DONE", "C_REFER")))
        self.assertEqual(result["active_rules"]["callback_alternative_limit"]["value"], 2)
        self.assertEqual(result["active_rules"]["banking_goal"]["value"], "contact_callback")
        self.assertEqual(len(result["sources"]), 4)
        self.assertEqual(result["active_rules"]["close_reasons"]["value"], {"C_DONE": [], "C_REFER": []})

    def test_equal_value_rules_preserve_independent_provenance_witnesses(self):
        first = source("first", "Written consent requires a recorded timestamp and a customer reference.")
        second = source("second", "The same consent record must include a customer reference and timestamp.")
        rules = [rule("witness-b", "consent_record", {"timestamp": True, "customer_ref": True}, first),
                 rule("witness-a", "consent_record", {"customer_ref": True, "timestamp": True}, second)]
        result = compile_policy_contract(policy([first, second], rules), [first, second], api())
        active = result["active_rules"]["consent_record"]
        self.assertEqual(active["rule_ids"], ["witness-a", "witness-b"])
        self.assertEqual({(p["rule_id"], p["id"]) for p in active["provenance"]},
                         {("witness-a", "second"), ("witness-b", "first")})

    def test_unordered_conflicting_extension_rules_are_rejected(self):
        first = source("first", "Contact reminders require consent.")
        second = source("second", "Contact reminders do not require consent.")
        for ordered in ((first, second), (second, first)):
            rules = [rule("requires", "reminder_consent", True, first),
                     rule("does-not-require", "reminder_consent", False, second)]
            with self.assertRaises(PolicyContractError):
                compile_policy_contract(policy(list(ordered), rules), list(ordered), api())

    def test_json_boolean_and_integer_are_not_collapsed_as_equal_rules(self):
        first = source("boolean-policy", "Manual review is true.")
        second = source("numeric-policy", "Manual review is numeric code one.")
        rules = [rule("bool", "manual_review", True, first), rule("integer", "manual_review", 1, second)]
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(policy([first, second], rules), [first, second], api())

    def test_unprovenanced_top_level_profile_cannot_shadow_a_typed_rule(self):
        base = source("base", "Stop after two wrong security answers in total.")
        candidate = policy([base], [rule("security-budget", "identity_wrong_limit", 2, base)])
        candidate["identity_wrong_limit"] = 99
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(candidate, [base], api())

    def test_entire_supplied_overlay_cannot_be_omitted_from_inventory(self):
        base = source("base", "Stop after three unanswered contact attempts.")
        overlay = source("overlay", "Replace the contact attempt limit with one.")
        candidate = policy([base], [rule("budget", "contact_retry_budget", 3, base)])
        with self.assertRaises(PolicyContractError) as caught:
            compile_policy_contract(candidate, [base, overlay], api())
        self.assertIsInstance(caught.exception.errors, tuple)

    def test_present_but_uncited_policy_source_is_not_claimed_as_covered(self):
        base = source("base", "The contact attempt limit is three.")
        overlay = source("overlay", "The governing memo replaces that limit with one.")
        candidate = policy([base, overlay], [rule("budget", "contact_retry_budget", 3, base)])
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(candidate, [base, overlay], api())

    def test_current_text_is_fingerprinted_instead_of_trusting_stale_metadata(self):
        base = source("base", "The retry budget is two.")
        candidate = policy([base], [rule("budget", "contact_retry_budget", 2, base)])
        stale = deepcopy(base)
        stale["text"] += " This source now has another governing instruction."
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(candidate, [stale], api())

    def test_quote_must_occur_in_its_own_declared_source(self):
        base = source("base", "The retry budget is two.")
        other = source("other", "Written consent is required for contact reminders.")
        rules = [rule("budget", "contact_retry_budget", 2, other, quote=base["text"]),
                 rule("base-witness", "source_role", "base-contact-policy", base)]
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(policy([base, other], rules), [base, other], api())

    def test_counter_amendment_cannot_delete_an_unrelated_holiday(self):
        base = source("base", "The retry budget is five. The bank closes on 2041-05-07.")
        memo = source("memo", "Only replace the retry budget with two; the calendar is unchanged.")
        rules = [rule("old-budget", "contact_retry_budget", 5, base, quote="The retry budget is five."),
                 rule("calendar", "holidays", ["2041-05-07"], base, quote="The bank closes on 2041-05-07."),
                 rule("new-budget", "contact_retry_budget", 2, memo, overrides=("old-budget", "calendar"))]
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(policy([base, memo], rules), [base, memo], api())

    def test_local_ref_and_nonoverlapping_allof_preserve_wire_fields_and_receipt(self):
        document = source("base", "The retry budget is two.")
        result = compile_policy_contract(policy([document], [rule("budget", "contact_retry_budget", 2, document)]), [document], api())
        operation = result["permitted_operations"][0]
        self.assertEqual(operation["name"], "close_call")
        self.assertEqual(operation["path"], "/finish-contact")
        self.assertEqual(set(operation["request_schema"]["required"]), {"case_ref", "resolution_code"})
        self.assertEqual(operation["request_schema"]["properties"]["resolution_code"]["enum"], ["CONTACT_DONE", "CONTACT_REFER"])
        self.assertEqual(operation["response_schemas"]["200"]["application/json"]["properties"]["did_write"]["type"], "boolean")

    def test_changed_request_names_and_enum_codes_change_the_compiled_contract(self):
        document = source("base", "The retry budget is two.")
        candidate = policy([document], [rule("budget", "contact_retry_budget", 2, document)])
        first = compile_policy_contract(candidate, [document], api())
        second = compile_policy_contract(candidate, [document], api("contact_token", "finish_state", ("Z17", "Z91")))
        self.assertNotEqual(first["sha256"], second["sha256"])
        schema = second["permitted_operations"][0]["request_schema"]
        self.assertEqual(set(schema["properties"]), {"contact_token", "finish_state"})
        self.assertEqual(schema["properties"]["finish_state"]["enum"], ["Z17", "Z91"])
        self.assertNotIn("resolution_code", schema["properties"])

    def test_conflicting_allof_constraints_are_not_silently_last_wins(self):
        document = source("base", "The retry budget is two.")
        candidate = policy([document], [rule("budget", "contact_retry_budget", 2, document)])
        schema = api()
        schema["paths"]["/finish-contact"]["post"]["requestBody"]["content"]["application/json"]["schema"]["allOf"].append(
            {"type": "object", "properties": {"resolution_code": {"type": "string", "enum": ["UNDECLARED"]}}})
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(candidate, [document], schema)

    def test_undeclared_operation_cannot_be_added_to_permitted_actions(self):
        document = source("base", "The retry budget is two.")
        candidate = policy([document], [rule("budget", "contact_retry_budget", 2, document)])
        candidate["allowed_actions"].append("transfer_money")
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(candidate, [document], api())

    def test_boolean_is_not_the_integer_contract_version(self):
        document = source("base", "The retry budget is two.")
        candidate = policy([document], [rule("budget", "contact_retry_budget", 2, document)])
        candidate["version"] = True
        with self.assertRaises(PolicyContractError):
            compile_policy_contract(candidate, [document], api())


if __name__ == "__main__": unittest.main()

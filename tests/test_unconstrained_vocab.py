"""A policy vocabulary need not be an invented API enum."""
import copy
from pathlib import Path
import unittest

from builder.action_bindings import ActionBindingError, checked_action_bindings, compile_action_bindings
from builder.contract import compile_policy_contract
from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.safety import digest, read_json
from builder.toolbindings import operations
from test_guards import fixture
from test_guard_aliases import renamed_context, recompile_context


def real_api_context():
    original = fixture()
    api = read_json(Path(__file__).resolve().parents[1] / "build-the-builder/tools/openapi.json")
    profile = copy.deepcopy(original["execution_policy"]["profile"])
    profile.update(identity_fields=["date_of_birth", "phone_last4"],
                   floor_fields={"credit_card": "minimum_payment", "personal_loan": "instalment_amount", "car_loan": "instalment_amount"},
                   transfer_reasons={"general": "human", "support": "support"},
                   transfer_prerequisites={"general": "none", "support": "none"},
                   close_reasons={"promise": [], "paid": [], "transferred": ["human", "support"],
                                  "no_promise": ["refused", "dispute", "service_error"],
                                  "not_verified": ["failed", "declined"], "not_reached": []},
                   identity_close={"failed": {"outcome": "not_verified", "reason": "failed"},
                                   "incomplete": {"outcome": "not_verified", "reason": "declined"},
                                   "unreached": {"outcome": "not_reached"}},
                   promise_readback={"product_fields": ["type", "card_last4"]})
    text = "Invented policy values for supplied API compatibility, not conversation compliance."
    source = {"id": "invented", "file": "invented-compatibility.txt", "text": text, "sha256": digest(text)}
    policy = {"version": 1, "goal": "Check API compatibility", "allowed_actions": [op["name"] for op in operations(api)],
              "sources": [{k: v for k, v in source.items() if k != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": value, "source": {"id": "invented", "quote": text}} for kind, value in profile.items()],
              "unsupported_constraints": []}
    bindings = compile_action_bindings(policy, api)
    execution = {"version": 1, "policy_sha256": digest(policy), "profile": profile, "bindings_sha256": bindings["sha256"]}
    execution["sha256"] = digest(execution)
    return {"plan": original["plan"], "contract": compile_policy_contract(policy, [source], api),
            "openapi": api, "bindings": bindings, "execution_policy": execution}, policy


class UnconstrainedVocabularyTests(unittest.TestCase):
    def test_real_kit_close_reason_remains_unconstrained_string(self):
        context, policy = real_api_context()
        schema = context["openapi"]["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["reason"]
        self.assertEqual(schema["type"], "string")
        self.assertNotIn("enum", schema)
        category = context["bindings"]["categories"]["reasons"]
        self.assertIs(category["api_enum_constrained"], False)
        self.assertEqual(category["api_enum"], [])
        self.assertEqual(checked_action_bindings(policy, context["bindings"]), context["bindings"])
        workflow = compile_guarded_workflow(**context)["workflow"]
        body = workflow["nodes"]["bg_close_api"]["pre_actions"][0]["function"]["parameters"]["properties"]["body"]
        self.assertNotIn("enum", body["properties"]["reason"])
        self.assertNotIn("enum", schema)
        self.assertIs(context["bindings"]["categories"]["outcomes"]["api_enum_constrained"], True)

    def test_explicit_empty_null_or_malformed_enum_does_not_mean_unconstrained(self):
        context, policy = real_api_context()
        for invalid in ([], None, "codes", ["x", "x"], [1], [""]):
            api = copy.deepcopy(context["openapi"])
            leaf = api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["reason"]
            leaf["enum"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ActionBindingError):
                compile_action_bindings(policy, api)

    def test_unconstrained_string_still_rejects_ambiguous_policy_meanings(self):
        context, unused = renamed_context()
        profile = copy.deepcopy(context["execution_policy"]["profile"])
        context["openapi"]["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["input_reason"].pop("enum")
        profile["wire_codes"]["reasons"]["failed"] = profile["wire_codes"]["reasons"]["refused"]
        with self.assertRaises(ValueError):
            recompile_context(context, profile)

    def test_checked_descriptor_requires_real_boolean_flag_and_consistent_shape(self):
        context, policy = real_api_context()
        for flag, enum in [(None, []), (0, []), ("false", []), (False, ["fabricated"]), (True, [])]:
            binding = copy.deepcopy(context["bindings"])
            binding["categories"]["reasons"].update(api_enum_constrained=flag, api_enum=enum)
            binding["sha256"] = digest({k: v for k, v in binding.items() if k != "sha256"})
            with self.subTest(flag=flag, enum=enum), self.assertRaises(ActionBindingError):
                checked_action_bindings(policy, binding)

    def test_guard_matches_constraint_flag_to_frozen_schema(self):
        context, policy = real_api_context()
        binding = copy.deepcopy(context["bindings"])
        values = list(binding["categories"]["reasons"]["semantic_to_wire"].values())
        binding["categories"]["reasons"].update(api_enum_constrained=True, api_enum=values)
        binding["sha256"] = digest({k: v for k, v in binding.items() if k != "sha256"})
        # This descriptor is internally shaped correctly, but it asserts an
        # API restriction absent from the frozen source. The guard rejects it.
        checked_action_bindings(policy, binding)
        context["bindings"] = binding
        context["execution_policy"]["bindings_sha256"] = binding["sha256"]
        context["execution_policy"]["sha256"] = digest({k: v for k, v in context["execution_policy"].items() if k != "sha256"})
        with self.assertRaisesRegex(GuardCompileError, "exact frozen string schema"):
            compile_guarded_workflow(**context)


if __name__ == "__main__":
    unittest.main()

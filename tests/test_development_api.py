"""Complete synthetic input contracts and source/evaluator separation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.export_inputs import export_profile
from benchmarks.generate import fixed_profiles, offline_manifest
from benchmarks.input_api import validate_input_api
from benchmarks.offline import compile_expected
from builder.guards import _catalogue
from builder.safety import digest
from builder.toolbindings import assert_live_contract, BindingError
from development_protocol_samples import protocol_response, decode_protocol_response


def assert_schema(test, value, schema):
    kind = schema["type"]
    kinds = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
    if kind == "number":
        test.assertIn(type(value), (int, float))
    else:
        test.assertIs(type(value), kinds[kind])
    if "enum" in schema: test.assertIn(value, schema["enum"])
    if kind == "object":
        for key in schema.get("required", []): test.assertIn(key, value)
        for key, item in value.items():
            test.assertIn(key, schema["properties"])
            assert_schema(test, item, schema["properties"][key])
    elif kind == "array":
        for item in value: assert_schema(test, item, schema["items"])
    if "minimum" in schema: test.assertGreaterEqual(value, schema["minimum"])
    if "maximum" in schema: test.assertLessEqual(value, schema["maximum"])
    if "minLength" in schema: test.assertGreaterEqual(len(value), schema["minLength"])


class DevelopmentApiTests(unittest.TestCase):
    def test_every_declared_operation_has_trusted_executable_bindings(self):
        for bank in fixed_profiles():
            with self.subTest(profile=bank["profile_id"]):
                self.assertEqual(validate_input_api(bank["openapi"])["operations"], 7)
                self.assertEqual(bank["openapi"]["info"]["version"], "synthetic-2")
                contract = compile_expected(bank["contract_expected"], bank)
                catalogue = _catalogue(contract, bank["openapi"], None)
                self.assertEqual(len(catalogue), 7)
                for role, operation in catalogue.items():
                    self.assertEqual(operation["results"], bank["result_field_mapping"][role])
                with self.assertRaises(BindingError): assert_live_contract(bank["openapi"])

    def test_independent_success_failure_samples_match_authored_schemas(self):
        for bank in fixed_profiles():
            raw = bank["representation"] == "raw_packet"
            for path, entry in bank["openapi"]["paths"].items():
                role = entry["post"]["operationId"]
                for status in entry["post"]["responses"]:
                    for ok in ((False,) if status == "503" else (True,) if status == "201" else (True, False)):
                        with self.subTest(profile=bank["profile_id"], operation=role, status=status, ok=ok):
                            sample = protocol_response(role, raw=raw, ok=ok, status=int(status))
                            schema = entry["post"]["responses"][status]["content"]["application/json"]["schema"]
                            assert_schema(self, sample["data"], schema)
                            normalized = decode_protocol_response(sample, raw=raw)
                            self.assertIs(normalized["data"]["ok"], ok)
                            if not ok:
                                self.assertNotIn("promise_id", normalized["data"])
                                self.assertEqual(normalized["data"]["error"], "temporary_unavailable")

    def test_canonical_and_raw_results_preserve_the_original_operation_meanings(self):
        roles = [entry["post"]["operationId"] for entry in fixed_profiles()[0]["openapi"]["paths"].values()]
        for role in roles:
            for ok in (True, False):
                canonical = protocol_response(role, ok=ok)
                raw = protocol_response(role, raw=True, ok=ok)
                self.assertEqual(canonical, decode_protocol_response(raw, raw=True))
                if role == "verify_identity" and ok:
                    canonical["data"]["verified"] = False
                    # An identity mismatch processes successfully; it is not a
                    # transport/write failure or whole-identity verification.
                    self.assertTrue(canonical["data"]["ok"])
                    self.assertFalse(canonical["data"]["verified"])

    def test_api_mutations_are_rejected_before_any_export_file_is_created(self):
        def missing_reply(api): api["paths"]["/create_promise"]["post"]["responses"]["200"].pop("content")
        def missing_field(api): api["paths"]["/verify_identity"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"].pop("wrong_answers_left")
        def wrong_type(api): api["paths"]["/create_promise"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["promise_id"]["type"] = "integer"
        def bad_enum(api): api["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["outcome"]["enum"] = [True]
        def bad_path(api): api["paths"]["/get_account/{customer}"] = api["paths"].pop("/get_account")
        def absent_success(api): api["paths"]["/add_note"]["post"]["responses"] = {}
        for mutation in (missing_reply, missing_field, wrong_type, bad_enum, bad_path, absent_success):
            with self.subTest(mutation=mutation.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); manifest = offline_manifest()
                mutation(manifest["profiles"][0]["openapi"])
                (root / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(ValueError): export_profile(root / "manifest.json", "train-1", root / "inputs", workspace_root=root)
                self.assertFalse((root / "inputs").exists())

    def test_declared_path_parameter_must_have_exact_required_schema_binding(self):
        api = deepcopy(fixed_profiles()[0]["openapi"])
        entry = api["paths"].pop("/get_account")
        api["paths"]["/get_account/{session_ref}"] = entry
        entry["parameters"] = [{"in": "path", "name": "session_ref", "required": True, "schema": {"type": "string"}}]
        self.assertTrue(validate_input_api(api)["request_response_schemas_verified"])
        for alteration in (lambda p: p.update(required=False), lambda p: p.update(schema={"type": "nonsense"}), lambda p: p.update(name="other")):
            changed = deepcopy(api)
            alteration(changed["paths"]["/get_account/{session_ref}"]["parameters"][0])
            with self.assertRaises(ValueError): validate_input_api(changed)

    def test_export_purity_and_digest_bind_the_actual_approved_api(self):
        for bank in fixed_profiles():
            with self.subTest(profile=bank["profile_id"]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); manifest = root / "manifest.json"
                manifest.write_text(json.dumps(offline_manifest()))
                output = export_profile(manifest, bank["profile_id"], root / "inputs", workspace_root=root)
                self.assertEqual(output["openapi_sha256"], digest(bank["openapi"]))
                self.assertTrue(output["input_contract_checks"]["request_response_schemas_verified"])
                files = list((root / "inputs").iterdir())
                self.assertEqual(len(files), len(bank["policy_documents"]) + 1)
                self.assertEqual(json.loads(Path(output["tool_api_path"]).read_text()), bank["openapi"])
                content = "\n".join(p.read_text() for p in files)
                for answer in bank["identity"].values(): self.assertNotIn(answer, content)
                for marker in ('"contract_expected"', '"customer_turns"', '"canonical_expected"', '"helper_expected"', '"fail_first_n"'):
                    self.assertNotIn(marker, content)

    def test_existing_destination_is_not_partially_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); manifest=root/"manifest.json";manifest.write_text(json.dumps(offline_manifest()))
            output=root/"inputs";output.mkdir();(output/"bank.openapi.json").write_text("existing")
            with self.assertRaises(ValueError): export_profile(manifest,"train-1",output,workspace_root=root)
            self.assertEqual([p.name for p in output.iterdir()], ["bank.openapi.json"])
            self.assertEqual((output/"bank.openapi.json").read_text(), "existing")


if __name__ == "__main__": unittest.main()

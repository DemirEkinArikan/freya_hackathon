import json
import tempfile
import unittest
from pathlib import Path

from builder.safety import REDACTED, assert_no_secrets, canonical_json, digest, read_json, sanitize


class SafetyTests(unittest.TestCase):
    def test_canonical_json_and_hash(self):
        self.assertEqual(canonical_json({"z": 2, "a": "İ"}), '{"a":"İ","z":2}')
        self.assertEqual(digest({"a": 1, "b": 2}), digest({"b": 2, "a": 1}))
        with self.assertRaises(ValueError):
            canonical_json(float("nan"))

    def test_strict_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.json"
            for text in ('{"a":1,"a":2}', '{"a":{"b":1,"b":2}}', '{"a":NaN}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_json(path)
            path.write_text('{"a":[1,true]}')
            self.assertEqual(read_json(path), {"a": [1, True]})

    def test_recursive_values_and_known_credentials(self):
        value = {"headers": {"Authorization": "Bearer actual-credential"},
                 "env": {"FREYA_API_KEY": "fixture-api-credential"},
                 "events": [{"message": "value is known-fixture-credential"}, 2]}
        result = sanitize(value, secrets=("known-fixture-credential",))
        self.assertEqual(result["headers"]["Authorization"], REDACTED)
        self.assertEqual(result["env"]["FREYA_API_KEY"], REDACTED)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["events"][0]["message"], "value is " + REDACTED)
        self.assertEqual(value["headers"]["Authorization"], "Bearer actual-credential")
        with self.assertRaisesRegex(ValueError, "credential-like"):
            assert_no_secrets(value)
        assert_no_secrets(result)

    def test_schema_annotations_preserved(self):
        schema = {"type": "object", "properties": {
            "Authorization": {"type": "string", "description": "Required authorization header",
                              "default": "Bearer {{BANK_API_TOKEN}}"},
            "api_key": {"type": "string", "description": "An API key property"},
            "Content-Type": {"type": "string", "default": "application/json"}}}
        self.assertEqual(sanitize(schema), schema)
        assert_no_secrets(schema)
        schema["properties"]["api_key"]["default"] = "actual-fixture-key"
        self.assertEqual(sanitize(schema)["properties"]["api_key"]["default"], REDACTED)
        self.assertEqual(sanitize(schema)["properties"]["api_key"]["type"], "string")

    def test_embedded_env_and_tokens(self):
        text = 'OPENAI_API_KEY="actual-fixture-key"\nAuthorization: Bearer actual-fixture-token'
        self.assertNotIn("actual-fixture", sanitize(text))
        with self.assertRaises(ValueError):
            assert_no_secrets({"note": "secret-text"}, forbidden_values=("secret-text",))

    def test_empty_env_lines_and_public_placeholders(self):
        text = "FREYA_API_KEY=\nNEXT_OPTION=enabled\nOPENAI_API_KEY=\t\nOTHER_OPTION=true"
        self.assertEqual(sanitize(text), text)
        for value in ("fak_your_key_here", "fak_...", "$FREYA_API_KEY", "Bearer {api_key}",
                      "pick-any-secret-for-this-example", "Only bearer tool authentication is supported"):
            self.assertEqual(sanitize(value), value)
        actual = "FREYA_API_KEY=fak_fixturecredential123\nNEXT_OPTION=enabled"
        self.assertEqual(sanitize(actual), "FREYA_API_KEY=" + REDACTED + "\nNEXT_OPTION=enabled")
        self.assertEqual(sanitize("github_pat_abcdefghijklmnopqrstuvwxyz0123456789"), REDACTED)
        # A known credential wins even if it was chosen to resemble a placeholder.
        self.assertEqual(sanitize("fak_your_key_here", secrets=("fak_your_key_here",)), REDACTED)


if __name__ == "__main__":
    unittest.main()

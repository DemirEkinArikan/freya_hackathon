"""Evidence JSON roles enforce credential checks independently of filenames."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools import package_submission as package
import test_package_submission as fixtures


class PackageSemanticJsonTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.PackageTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.root = self.helper.root
        self.manifest = self.helper.evidence()
        self.git = patch.object(package.subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40))
        self.git.start()
        self.addCleanup(self.git.stop)

    def assert_private_rejection(self, operation, private_value):
        with self.assertRaisesRegex(ValueError, 'requires review') as result:
            operation()
        self.assertNotIn(private_value, str(result.exception))

    def test_manifest_under_text_extension_rejects_structured_credential(self):
        private_value = 'opaque-synthetic-manifest-value'
        self.manifest['headers'] = {'Authorization': private_value}
        path = self.root / 'submission.txt'
        path.write_text(json.dumps(self.manifest))
        self.assert_private_rejection(lambda: package.main(['--check', '--manifest', str(path)]), private_value)

    def test_export_and_api_under_text_extension_reject_structured_credentials(self):
        for role in ('workflows', 'tool_api'):
            private_value = 'opaque-synthetic-' + role + '-value'
            manifest = self.helper.evidence()
            old_entry = manifest['workflows']['hidden-1'] if role == 'workflows' else manifest['inputs']['hidden-1']['tool_api']
            value = json.loads((self.root / old_entry).read_text())
            value['headers'] = {'Authorization': private_value}
            path = self.root / (role + '.txt')
            path.write_text(json.dumps(value))
            if role == 'workflows':
                manifest['workflows']['hidden-1'] = path.name
            else:
                manifest['inputs']['hidden-1']['tool_api'] = path.name
            with self.subTest(role=role):
                self.assert_private_rejection(lambda: package.check_manifest(manifest), private_value)

    def test_trace_under_text_extension_rejects_structured_credential(self):
        private_value = 'opaque-synthetic-trace-value'
        events = [json.loads(line) for line in (self.root / 'hidden-1.jsonl').read_text().splitlines()]
        events[5]['headers'] = {'Authorization': private_value}
        path = self.root / 'trace.txt'
        path.write_text('\n'.join(json.dumps(event) for event in events))
        self.manifest['traces']['hidden-1'] = path.name
        self.assert_private_rejection(lambda: package.check_manifest(self.manifest), private_value)

    def test_semantic_json_checks_decoded_known_values(self):
        private_value = 'opaque7'
        (self.root / '.env').write_text('FREYA_API_KEY=' + private_value + '\n')
        path = self.root / 'evidence.txt'
        encoded = ''.join('\\u%04x' % ord(character) for character in private_value)
        path.write_text('{"notes":"' + encoded + '"}')
        self.assert_private_rejection(lambda: package.read_evidence(path), private_value)

    def test_semantic_json_keeps_duplicate_and_nonfinite_rejections(self):
        path = self.root / 'evidence.txt'
        for content in ('{"password":"opaque-private-value","password":""}', '{"number":NaN}'):
            path.write_text(content)
            with self.subTest(content=content):
                self.assert_private_rejection(lambda: package.read_evidence(path), 'opaque-private-value')

    def test_safe_text_named_exports_apis_and_traces_remain_usable(self):
        for role in ('workflows', 'tool_api', 'traces'):
            old_entry = self.manifest['inputs']['hidden-1']['tool_api'] if role == 'tool_api' else self.manifest[role]['hidden-1']
            path = self.root / (role + '.txt')
            path.write_bytes((self.root / old_entry).read_bytes())
            if role == 'tool_api':
                self.manifest['inputs']['hidden-1']['tool_api'] = path.name
            else:
                self.manifest[role]['hidden-1'] = path.name
        self.assertEqual(len(package.check_manifest(self.manifest)), 42)


if __name__ == '__main__':
    unittest.main()

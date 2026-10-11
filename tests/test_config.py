import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from builder import build
from builder.config import load_local_environment, read_local_environment
from builder.runtime import generation_environment
from tools import package_submission as package


class LocalEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / '.env'

    def test_plain_data_and_existing_environment_wins(self):
        self.path.write_text('# local\nexport FREYA_API_KEY="synthetic-key"\nFREYA_API_URL=https://staging-app.freyavoice.ai\n')
        env = {'FREYA_API_KEY': 'existing-key'}
        self.assertEqual(load_local_environment(self.path, env), ('FREYA_API_URL',))
        self.assertEqual(env['FREYA_API_KEY'], 'existing-key')

    def test_legacy_alias_and_generator_removes_secrets(self):
        self.path.write_text('FREYA_KEY=synthetic-key\nFREYA_BANK_TEAM_KEY=synthetic-team-key\nFREYA_TOOL_AUTH_BEARER=synthetic-tool-key\n')
        env = {'HOME': '/tmp'}
        load_local_environment(self.path, env)
        self.assertEqual(env['FREYA_API_KEY'], 'synthetic-key')
        self.assertEqual(generation_environment(env), {'HOME': '/tmp'})

    def test_backup_key_is_data_and_always_private(self):
        self.path.write_text('FREYA_API_KEY=synthetic-key\nFREYA_API_KEY_2=synthetic-backup-key\n')
        env = {}
        load_local_environment(self.path, env)
        self.assertEqual(env['FREYA_API_KEY_2'], 'synthetic-backup-key')
        self.assertEqual(generation_environment(env), {})
        with patch.dict(build.os.environ, env, clear=True):
            self.assertIn('synthetic-backup-key', build._known_secrets())
        source = self.path.parent / 'README.md'
        source.write_text('synthetic-backup-key')
        with patch.object(package, 'ROOT', self.path.parent), \
                self.assertRaisesRegex(ValueError, 'requires review') as caught:
            package.check_content(source)
        self.assertNotIn('synthetic-backup-key', str(caught.exception))

    def test_invalid_input_does_not_execute_or_echo_values(self):
        for text in ('FREYA_API_KEY=$(touch forbidden-marker)', 'FREYA_API_KEY=`secret-command`',
                     'FREYA_API_KEY=private-value\nFREYA_API_KEY=duplicate',
                     'UNRELATED_SECRET=private-value', 'bare-private-value'):
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(ValueError) as caught:
                    read_local_environment(self.path)
                self.assertNotIn('private-value', str(caught.exception))
                self.assertFalse((self.path.parent / 'forbidden-marker').exists())

    def test_missing_file_and_symlink(self):
        self.assertEqual(read_local_environment(self.path), {})
        other = self.path.parent / 'other'
        other.write_text('FREYA_API_KEY=synthetic-key')
        self.path.symlink_to(other)
        with self.assertRaises(ValueError):
            read_local_environment(self.path)


if __name__ == '__main__':
    unittest.main()

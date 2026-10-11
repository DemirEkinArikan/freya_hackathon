import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.review_evidence_check import check_record


class ReviewEvidenceCheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.brief = Path(tmp.name, 'brief.md')
        self.brief.write_text('# Brief\nBefore recording, ask "Do you\nconfirm?" exactly.\n', encoding='utf-8')
        self.workflow = Path(tmp.name, 'workflow.json')
        self.workflow.write_text(json.dumps({'nodes': {'a/b': {'text': 'Do you agree?', 'n': [1, 2]}}}), encoding='utf-8')

    def item(self, path, **extra):
        return dict(path=str(path), raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), **extra)

    def test_accepts_matching_hashes_lines_pointers_and_values(self):
        record = {'findings': [{'source_evidence': [self.item(self.brief, line=2, quote='ask "Do you confirm?"')],
                                'artifact_evidence': [self.item(self.workflow, pointer='/nodes/a~1b/text', quote='agree'),
                                                      self.item(self.workflow, json_pointer='/nodes/a~1b/n', value=[1, 2])]},
                               dict(absolute_original_path=str(self.brief),
                                    sha256=hashlib.sha256(self.brief.read_bytes()).hexdigest(), lines=[2, 3], excerpt='exactly')]}
        self.assertEqual(check_record(record), (4, 4, []))

    def test_reports_each_mismatch_with_its_location(self):
        record = {'e': [dict(self.item(self.brief), raw_sha256='0' * 64),
                        self.item(self.brief, line=1, quote='Do you confirm?'),
                        self.item(self.workflow, pointer='/nodes/missing', quote='x'),
                        self.item(self.workflow, pointer='/nodes/a~1b/n', value=[2, 1]),
                        dict(self.item(self.brief), path=str(self.brief) + '.absent')]}
        self.assertEqual(check_record(record)[2], [('/e/0', 'sha256 mismatch'), ('/e/1', 'quote not found'),
                                                   ('/e/2', 'pointer not found'), ('/e/3', 'value mismatch'),
                                                   ('/e/4', 'missing file')])


if __name__ == '__main__':
    unittest.main()

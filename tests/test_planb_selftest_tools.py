"""Plan B self-test tools (planb/tools/score.py, run_cases.py) on contracts other than the practice bank. Synthetic data only."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'planb'
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT))
import score  # noqa: E402


def closed(exp, **extra):
    calls = [{'tool': 'close_call', 'args': {}, 'status': 200, 'result': {'ok': True}}]
    return {'outcome': exp['outcome'], 'reason': exp.get('reason'), 'promises': exp['promises'], 'notes': exp['notes'],
            'callbacks': exp['callbacks'], 'transfers': exp['transfers'], 'calls': calls, **extra}


class ScoreLedgerTests(unittest.TestCase):
    def test_practice_cases_score_as_before(self):
        for case in json.load(open(ROOT / 'public-test-set.json')):
            self.assertEqual(score.score_ledger(case, closed(case['expected'])), {'pass': True, 'failed': []}, case['id'])
            bad = score.score_ledger(case, {'calls': []})['failed']
            self.assertIn('the call was not closed with close_call', bad, case['id'])
            self.assertTrue(bad[0].startswith('outcome (None, None), expected'), case['id'])

    def test_case_without_closing_outcome_transfers_or_identity(self):
        case = {'id': 'x1', 'expected': {}}
        led = {'calls': [{'tool': 'read_items', 'args': {'ref': 'r1'}, 'status': 200, 'result': {}}]}
        self.assertEqual(score.score_ledger(case, led), {'pass': True, 'failed': []})
        self.assertEqual(score.score_ledger({'id': 'x2'}, {}), {'pass': True, 'failed': []})
        self.assertEqual(score.score_ledger({'id': 'x2', 'expected': {'odd': ['text'], 'flag': True}}, led), {'pass': True, 'failed': []})

    def test_listed_write_operations(self):
        case = {'id': 'x3', 'identity': {}, 'expected': {'make_change': [{'ref': 'r1', 'amount': 12.5}], 'cancel_thing': []}}
        call = lambda tool, status=200, **args: {'tool': tool, 'args': args, 'status': status, 'result': {}}
        good = {'calls': [call('make_change', 503, ref='r1', amount=12.5), call('make_change', ref='r1', amount='12.50', note='x')]}
        self.assertEqual(score.score_ledger(case, good), {'pass': True, 'failed': []})
        failed_only = score.score_ledger(case, {'calls': [call('make_change', 503, ref='r1', amount=12.5)]})['failed']
        self.assertEqual(len(failed_only), 1)
        self.assertIn('make_change', failed_only[0])
        wrong = score.score_ledger(case, {'calls': [call('make_change', ref='r2', amount=12.5), call('cancel_thing', ref='r1')]})['failed']
        self.assertEqual(len(wrong), 2)
        self.assertIn('cancel_thing called 1 time(s), expected never', wrong)
        ids = {'id': 'x5', 'expected': {'make_change': [{'ref': '0012'}]}}
        self.assertFalse(score.score_ledger(ids, {'calls': [call('make_change', ref='12')]})['pass'])


if __name__ == '__main__':
    unittest.main()

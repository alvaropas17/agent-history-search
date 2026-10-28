"""Synthetic streaming regressions. Never open a real history store."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_history.cli import parser, plan
from agent_history.core import Budget
from agent_history.transcripts import MAX_LINE, load, run


def message(eid, text):
    return {'type': 'user', 'sessionId': 'synthetic', 'uuid': eid,
            'message': {'role': 'user', 'content': text}}


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic.jsonl'

    def write(self, rows):
        self.path.write_bytes(b''.join(json.dumps(r, ensure_ascii=True).encode() + b'\n'
                                      for r in rows))

    def args(self, *extra):
        args = parser().parse_args(['search', '--host', 'claude', '--store',
                                   str(self.path), '--query', 'needle', *extra])
        plan(args)
        return args

    def test_over_32_mib_with_early_and_late_hits(self):
        self.write([message('early', 'needle')])
        padding = json.dumps({'type': 'progress', 'padding': 'x' * (1024 * 1024)}).encode() + b'\n'
        with self.path.open('ab') as stream:
            for _ in range(33):
                stream.write(padding)
            stream.write(json.dumps(message('late', 'needle')).encode() + b'\n')
        budget = Budget()
        hits, _ = run(self.args(), self.path, budget)
        self.assertEqual([h['entry_id'] for h in hits], ['late', 'early'])
        self.assertFalse(budget.incomplete)

    def test_retained_hits_are_bounded_and_newest_first(self):
        self.write([message(str(i), 'needle ' * 200) for i in range(100)])
        args = self.args('--limit', '2', '--context', '12')
        loaded = load(self.path, 'claude', Budget(), args)
        self.assertEqual(len(loaded[2]), 3)
        self.assertTrue(all(len(h['text']) <= 12 for h in loaded[2]))
        hits, _ = run(args, self.path, Budget())
        self.assertEqual([h['entry_id'] for h in hits], ['99', '98'])

    def test_unicode_escaped_literal_and_exact_expansion(self):
        self.write([message('one', 'Straße Árbol [a.*]'), message('two', 'other')])
        hits, _ = run(self.args('--query', 'STRASSE árbol [a.*]'), self.path, Budget())
        self.assertEqual([h['entry_id'] for h in hits], ['one'])
        args = self.args()
        args.action, args.session, args.entry = 'expand', 'synthetic', 'one'
        budget = Budget()
        hits, _ = run(args, self.path, budget)
        self.assertEqual([h['entry_id'] for h in hits], ['one'])
        self.assertEqual(budget.scanned, 2)  # Expansion rescans; there is no index.
        args.entry = 'on'
        self.assertEqual(run(args, self.path, Budget())[0], [])

    def test_oversize_record_skipped_and_later_hit_survives(self):
        self.write([message('early', 'needle'),
                    {'type': 'progress', 'image': 'x' * (MAX_LINE * 2)},
                    message('late', 'needle')])
        budget = Budget()
        hits, _ = run(self.args(), self.path, budget)
        self.assertEqual([h['entry_id'] for h in hits], ['late', 'early'])
        self.assertIn('transcript_line_too_large', budget.notes)

    def test_row_and_byte_exhaustion_retain_early_hits(self):
        self.write([message('early', 'needle'), message('late', 'needle')])
        for budget in (Budget(rows=1), Budget(byte_limit=150)):
            hits, _ = run(self.args(), self.path, budget)
            self.assertEqual([h['entry_id'] for h in hits], ['early'])
            self.assertIn('scan_budget_reached', budget.notes)

    def test_deadline_returns_explicit_partial_coverage(self):
        self.write([message('early', 'needle')])
        budget = Budget(seconds=-1)
        self.assertEqual(run(self.args(), self.path, budget)[0], [])
        self.assertIn('deadline_exceeded', budget.notes)

    def test_changed_file_discards_hits(self):
        self.write([message('early', 'needle')])
        budget = Budget()
        with patch('agent_history.transcripts.signature', side_effect=[{'bytes': 1000}, {'bytes': 1001}]):
            self.assertEqual(run(self.args(), self.path, budget)[0], [])
        self.assertIn('transcript_changed_during_read', budget.notes)

    def test_malformed_record_still_discards_file(self):
        self.write([message('early', 'needle')])
        with self.path.open('ab') as stream:
            stream.write(b'{bad json}\n')
        budget = Budget()
        self.assertEqual(run(self.args(), self.path, budget)[0], [])
        self.assertIn('invalid_transcript_record_file_skipped', budget.notes)

    def test_text_mode_does_not_collect_tool_events(self):
        self.write([message('early', 'needle'),
                    {'type': 'assistant', 'sessionId': 'synthetic', 'message':
                     {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 't',
                                                       'name': 'Bash', 'input': 'needle'}]}}])
        with patch('agent_history.transcripts.arguments_text', side_effect=AssertionError('tool parsing')):
            self.assertEqual(load(self.path, 'claude', Budget(), self.args())[3], [])


if __name__ == '__main__':
    unittest.main()

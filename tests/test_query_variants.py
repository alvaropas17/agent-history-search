"""Literal OR searches over synthetic stores; no real history access."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from agent_history import cli
from agent_history.core import HistoryError, snippet
from agent_history.transcripts import MAX_LINE
from test_history_search import Fixtures


class QueryVariants(Fixtures):
    def invoke(self, source, store, *options):
        args = cli.parser().parse_args(['search', '--host', source, '--store', str(store), *options])
        return cli.read(args, cli.plan(args))

    def test_variants_all_readers(self):
        for source in cli.SOURCES:
            baseline = self.run_read(source)
            store = {'pi': self.jsonl, 'omp': self.jsonl, 'opencode': self.oc2,
                     'pocket': self.pocket}.get(source, self.root / (source + '.jsonl'))
            result = self.invoke(source, store, '--query', 'not present',
                                 '--query-variant', 'QUESTION', '--query-variant', 'question')
            self.assertEqual(result['results'], baseline['results'])
            self.assertEqual(result['scanned_rows'], baseline['scanned_rows'])
            self.assertEqual(result['incomplete'], baseline['incomplete'])
            self.assertEqual(self.invoke(source, store, '--query-variant', 'absent',
                                        '--query-variant', '[.*]')['results'], [])

    def test_one_file_open_unicode_and_shared_limit(self):
        original = Path.open
        opened = []
        def tracked(path, *args, **kwargs):
            if path == self.jsonl.resolve():
                opened.append(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked):
            result = self.invoke('pi', self.jsonl, '--query-variant', 'STRASSE',
                                 '--query-variant', 'shared', '--limit', '1', '--context', '8')
        self.assertEqual(len(opened), 1)
        self.assertEqual(result['results'][0]['entry_id'], 'active')
        self.assertLessEqual(len(result['results'][0]['text']), 8)
        self.assertIn('result_limit_reached', result['notes'])
        self.assertTrue(result['incomplete'])
        self.assertEqual(snippet('ß' * 10 + ' Árbol', ('missing', 'ÁRBOL'), 5)['offset'], 10)

    def test_large_record_and_incomplete_coverage(self):
        store = self.root / 'large.jsonl'
        rows = [
            {'type': 'user', 'sessionId': 's', 'uuid': 'large',
             'message': {'role': 'user', 'content': 'x' * 100000 + ' Straße'}},
            {'type': 'progress', 'padding': 'x' * (MAX_LINE + 100)},
            {'type': 'user', 'sessionId': 's', 'uuid': 'late',
             'message': {'role': 'user', 'content': '[a.*]'}},
        ]
        store.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        result = self.invoke('claude', store, '--query-variant', 'STRASSE',
                             '--query-variant', '[a.*]', '--context', '20')
        self.assertEqual([h['entry_id'] for h in result['results']], ['late', 'large'])
        self.assertTrue(all(len(h['text']) <= 20 for h in result['results']))
        self.assertTrue(result['incomplete'])
        self.assertIn('transcript_line_too_large', result['notes'])

    def test_tool_attempt_variants(self):
        store = self.root / 'tools.jsonl'
        rows = [
            {'type': 'session_meta', 'payload': {'id': 's'}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'call_id': 'c',
             'name': 'read', 'arguments': 'Straße'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output',
             'call_id': 'c', 'output': 'literal [.*]'}},
        ]
        store.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        result = self.invoke('codex', store, '--tool-calls', '--query-variant', 'STRASSE',
                             '--query-variant', '[.*]')
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['ref'], {'entry_id': '2'})
        self.assertEqual(result['results'][0]['result_ref'], {'entry_id': '3'})

    def test_validation_and_authorization(self):
        base = ['search', '--host', 'pi', '--store', 'absent']
        invalid = [['--query-variant', ' '], ['--query-variant', 'x' * 2001],
                   ['--query-variant', 'x'] * 9, ['--query-variant', 'x' * 2000] * 5,
                   ['--source', 'pocket', '--query-variant', 'x']]
        for options in invalid:
            with self.assertRaises(HistoryError):
                cli.plan(cli.parser().parse_args(base + options))
        with self.assertRaisesRegex(HistoryError, 'query_is_search_only'):
            cli.plan(cli.parser().parse_args(['list', *base[1:], '--query-variant', 'x']))

    def test_worker_propagates_variants(self):
        entry = Path(cli.__file__).resolve().parents[1] / 'history_search.py'
        proc = subprocess.run([sys.executable, '-B', str(entry), 'search', '--host', 'pi',
                               '--store', str(self.jsonl), '--query-variant', 'absent',
                               '--query-variant', 'STRASSE'], capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['results'][0]['entry_id'], 'active')


if __name__ == '__main__':
    unittest.main()

"""Offline synthetic formats only. No profile discovery and no real stores."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_history import cli
from agent_history.core import Budget, HistoryError, connect, snippet

ROOT = Path(__file__).resolve().parents[1]
STAMP = '2026-10-17T12:00:00Z'
MS = 1792238400000


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-history-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pi = self.root / 'sessions'
        self.pi.mkdir()
        self.jsonl = self.pi / 'one.jsonl'
        entries = [
            {'type': 'session', 'version': 3, 'id': 'session-one', 'timestamp': STAMP, 'cwd': '/synthetic'},
            {'type': 'message', 'id': 'root', 'parentId': None, 'timestamp': STAMP,
             'message': {'role': 'user', 'content': 'shared question', 'timestamp': MS}},
            {'type': 'message', 'id': 'abandoned', 'parentId': 'root', 'timestamp': STAMP,
             'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'abandoned secret'}]}},
            {'type': 'message', 'id': 'active', 'parentId': 'root', 'timestamp': STAMP,
             'message': {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'hidden'},
                                                        {'type': 'text', 'text': 'active Straße answer'}]}},
        ]
        self.jsonl.write_text('\n'.join(json.dumps(x) for x in entries) + '\n', encoding='utf-8')
        self.oc2 = self.root / 'oc2.db'
        con = sqlite3.connect(self.oc2)
        con.executescript('CREATE TABLE session_v2(id TEXT,title TEXT,directory TEXT,time_updated INTEGER);'
                          'CREATE TABLE session_message(id TEXT,session_id TEXT,type TEXT,seq INTEGER,time_created INTEGER,data TEXT);')
        con.execute('INSERT INTO session_v2 VALUES (?,?,?,?)', ('s1', 'Synthetic title', '/fake', MS))
        con.execute('INSERT INTO session_message VALUES (?,?,?,?,?,?)',
                    ('m1', 's1', 'user', 1, MS, json.dumps({'text': 'oc2 question'})))
        con.execute('INSERT INTO session_message VALUES (?,?,?,?,?,?)',
                    ('m2', 's1', 'assistant', 2, MS + 1,
                     json.dumps({'content': [{'type': 'reasoning', 'text': 'hidden'}, {'type': 'text', 'text': 'oc2 response'}]})))
        con.commit()
        con.close()
        self.pocket = self.root / 'pocket.db'
        con = sqlite3.connect(self.pocket)
        con.execute('CREATE TABLE entries(id INTEGER,conversation_id INTEGER,record TEXT)')
        con.execute('INSERT INTO entries VALUES (?,?,?)', (11, 7, json.dumps({'model': [
            {'role': 'user', 'timestamp': MS, 'content': 'pocket question'},
            {'role': 'assistant', 'timestamp': MS + 1, 'content': [{'type': 'text', 'text': 'pocket response'}]},
            {'role': 'toolResult', 'timestamp': MS + 2, 'content': 'hidden'},
        ]})))
        con.commit()
        con.close()

    def run_read(self, source, action='search', **kwargs):
        if source in ('codex', 'claude'):
            store = self.root / (source + '.jsonl')
            if source == 'codex':
                records = [
                    {'type': 'session_meta', 'payload': {'id': 'cx'}},
                    {'type': 'response_item', 'timestamp': STAMP, 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'codex question'}]}},
                ]
            else:
                records = [{'type': 'user', 'sessionId': 'cl', 'uuid': 'u', 'timestamp': STAMP,
                            'message': {'role': 'user', 'content': 'claude question'}}]
            store.write_text('\n'.join(json.dumps(r) for r in records) + '\n', encoding='utf-8')
        else:
            store = {'pi': self.pi, 'omp': self.pi, 'opencode': self.oc2, 'pocket': self.pocket}[source]
        argv = [action, '--host', source, '--store', str(store)]
        if action == 'search':
            kwargs.setdefault('query', 'question')
        for key, value in kwargs.items():
            argv += ['--' + key.replace('_', '-'), str(value)]
        a = cli.parser().parse_args(argv)
        return cli.read(a, cli.plan(a))

    def test_search_each_backend_and_unchanged_files(self):
        paths = (self.jsonl, self.oc2, self.pocket)
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        for source in cli.SOURCES:
            result = self.run_read(source)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(len(result['results']), 1)
            self.assertIn('question', result['results'][0]['text'])
            self.assertFalse(result['route']['automatic_fallback'])
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])
        self.assertFalse(list(self.root.glob('*-journal')))
        self.assertFalse(list(self.root.glob('*-wal')))
        self.assertFalse(list(self.root.glob('*-shm')))

    def test_pi_active_persisted_branch(self):
        self.assertEqual(self.run_read('pi', query='abandoned')['results'], [])
        hit = self.run_read('pi', query='active')['results'][0]
        self.assertEqual(hit['entry_id'], 'active')
        self.assertNotIn('hidden', hit['text'])

    def test_list_each(self):
        for source in cli.SOURCES:
            self.assertEqual(len(self.run_read(source, 'list')['results']), 1)

    def test_expand_exact_references(self):
        for source, session, entry in [('pi', 'session-one', 'active'), ('opencode', 's1', 'm2'), ('pocket', '7', '11')]:
            result = self.run_read(source, 'expand', session=session, entry=entry, limit=1)
            self.assertEqual(len(result['results']), 1)
            self.assertEqual(result['results'][0]['role'], 'assistant')

    def test_dates_filter_messages(self):
        for source in cli.SOURCES:
            self.assertEqual(self.run_read(source, since='2026-10-18')['results'], [])
            self.assertTrue(self.run_read(source, since='2026-10-17', until='2026-10-17')['results'])

    def test_literal_sql_and_shell(self):
        for source in cli.SOURCES:
            self.assertEqual(self.run_read(source, query="%' OR 1=1; --")['results'], [])

    def test_no_reasoning_or_tool_text(self):
        for source in cli.SOURCES:
            self.assertEqual(self.run_read(source, query='hidden')['results'], [])

    def test_budget_declares_partial(self):
        result = self.run_read('opencode', query='question', max_rows=1)
        self.assertTrue(result['incomplete'])
        self.assertIn('scan_budget_reached', result['notes'])

    def test_result_limit_declares_partial(self):
        result = self.run_read('pocket', 'expand', session='7', limit=1)
        self.assertTrue(result['incomplete'])
        self.assertIn('result_limit_reached', result['notes'])

    def test_context_cap_and_unicode_casefold(self):
        hit = self.run_read('pi', query='STRASSE', context=8)['results'][0]
        self.assertLessEqual(len(hit['text']), 8)
        self.assertTrue(hit['text_truncated'])
        self.assertEqual(snippet('a Straße end', 'STRASSE', 20)['offset'], 0)

    def test_wrong_schema_and_missing_store(self):
        wrong = self.root / 'wrong.db'
        sqlite3.connect(wrong).close()
        with self.assertRaises(HistoryError):
            connect(wrong, {'entries': ['record']}, Budget())
        missing = self.root / 'absent.db'
        with self.assertRaises(HistoryError):
            connect(missing, {'entries': ['record']}, Budget())
        self.assertFalse(missing.exists())

    def test_query_only_blocks_writes(self):
        con = connect(self.pocket, {'entries': ['record']}, Budget())
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('DELETE FROM entries')
        finally:
            con.close()

    def test_cli_real_child_process(self):
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'history_search.py'), 'search',
                               '--host', 'pocket', '--store', str(self.pocket), '--query', 'question'],
                              capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result['results'][0]['entry_id'], 11)

    def test_invalid_tree_fails_closed(self):
        text = self.jsonl.read_text(encoding='utf-8').replace('"parentId": "root"', '"parentId": "missing"')
        self.jsonl.write_text(text, encoding='utf-8')
        with self.assertRaises(HistoryError):
            self.run_read('pi')

    def test_pi_v1_rejected(self):
        self.jsonl.write_text(self.jsonl.read_text().replace('"version": 3', '"version": 1'))
        with self.assertRaises(HistoryError):
            self.run_read('pi')

    def test_wal_refused_without_creating_shm(self):
        wal = Path(str(self.pocket) + '-wal')
        wal.write_bytes(b'synthetic-wal-marker')
        with self.assertRaisesRegex(HistoryError, 'wal_store_requires'):
            self.run_read('pocket')
        self.assertFalse(Path(str(self.pocket) + '-shm').exists())

    def test_malformed_pocket_record_is_error(self):
        con = sqlite3.connect(self.pocket)
        con.execute('UPDATE entries SET record=?', ('not-json',))
        con.commit()
        con.close()
        with self.assertRaises(HistoryError):
            self.run_read('pocket')

    def test_pi_v2_supported(self):
        self.jsonl.write_text(self.jsonl.read_text().replace('"version": 3', '"version": 2'))
        self.assertTrue(self.run_read('pi')['results'])

    def test_pi_partial_line_skipped_and_declared(self):
        with self.jsonl.open('a') as stream:
            stream.write('{unfinished')
        result = self.run_read('pi')
        self.assertTrue(result['incomplete'])
        self.assertEqual(result['results'], [])
        self.assertIn('pi_invalid_record_file_skipped', result['notes'])

    def test_empty_database_returns_empty_ok(self):
        con = sqlite3.connect(self.oc2)
        con.execute('DELETE FROM session_message')
        con.commit()
        con.close()
        result = self.run_read('opencode')
        self.assertEqual(result['results'], [])
        self.assertEqual(result['status'], 'ok')


class Policy(unittest.TestCase):
    def args(self, *extra):
        return cli.parser().parse_args(['search', '--host', 'pi', '--store', 'absent', '--query', 'test', *extra])

    def test_source_change(self):
        with self.assertRaises(HistoryError):
            cli.plan(self.args('--source', 'pocket'))
        self.assertEqual(cli.plan(self.args('--source', 'pocket', '--reason', 'Usuario pidió Pocket'))['source'], 'pocket')

    def test_invalid_ranges(self):
        for option, value in [('limit', '0'), ('context', '6001'), ('timeout', '31'), ('max-rows', '0'), ('query', ' ')]:
            with self.assertRaises(HistoryError):
                cli.plan(self.args('--' + option, value))

    def test_plan_never_opens_or_spawns(self):
        with patch.object(cli.subprocess, 'run') as run, patch.object(cli, 'read') as read, contextlib.redirect_stdout(io.StringIO()) as output:
            code = cli.main(['list', '--host', 'pocket', '--store', 'absent', '--plan'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'plan')
            run.assert_not_called()
            read.assert_not_called()

    def test_timeout_no_retry(self):
        with patch.object(cli.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 1)) as run, contextlib.redirect_stdout(io.StringIO()) as output:
            code = cli.main(['list', '--host', 'pi', '--store', 'absent'])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())['error'], 'worker_timeout')
            self.assertEqual(run.call_count, 1)

    def test_expand_requires_session(self):
        with self.assertRaises(HistoryError):
            cli.plan(cli.parser().parse_args(['expand', '--host', 'pi', '--store', 'absent']))

    def test_pocket_ids(self):
        for invalid in ('-1', '0', "1 OR 1=1", '١', str(2**64)):
            with self.assertRaises(HistoryError):
                cli.plan(self.args('--host', 'pocket', '--session', invalid))


if __name__ == '__main__':
    unittest.main()

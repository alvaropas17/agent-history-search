"""Opt-in --tool-calls mode on synthetic fixtures for every reader. No real stores."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_history.cli import parser, plan, read
from agent_history.core import HistoryError

STAMP = '2026-10-17T12:00:00Z'
MS = 1792238400000


def jsonl(path, records):
    path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    return path


class ToolCallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-history-tools-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def invoke(self, source, store, action='search', *extra):
        before = store.read_bytes()
        args = parser().parse_args([action, '--host', source, '--store', str(store), '--tool-calls', *extra])
        result = read(args, plan(args))
        self.assertEqual(before, store.read_bytes())
        self.assertEqual(result['route']['mode'], 'tool_calls')
        return result['results']

    def pi_records(self, title=False):
        records = [{'type': 'title', 'v': 1, 'title': 'Synthetic title'}] if title else []
        return records + [
            {'type': 'session', 'version': 3, 'id': 'ps', 'timestamp': STAMP},
            {'type': 'message', 'id': 'e1', 'parentId': None, 'timestamp': STAMP,
             'message': {'role': 'assistant', 'content': [
                 {'type': 'toolCall', 'id': 'c1', 'name': 'bash', 'arguments': {'command': 'make build'}},
                 {'type': 'toolCall', 'id': 'c2', 'name': 'read', 'arguments': {'path': 'notes.md'}}]}},
            {'type': 'message', 'id': 'e2', 'parentId': 'e1', 'timestamp': STAMP,
             'message': {'role': 'toolResult', 'toolCallId': 'c1', 'toolName': 'bash', 'isError': True,
                         'content': [{'type': 'text', 'text': 'missing header'},
                                     {'type': 'image', 'data': 'AAAA', 'mimeType': 'image/png'}]}},
            {'type': 'message', 'id': 'e3', 'parentId': 'e2', 'timestamp': STAMP,
             'message': {'role': 'toolResult', 'toolCallId': 'c2', 'toolName': 'read', 'isError': False,
                         'content': [{'type': 'text', 'text': 'fine'}]}},
        ]

    def test_text_mode_still_excludes_tools(self):
        store = jsonl(self.root / 's.jsonl', self.pi_records())
        args = parser().parse_args(['search', '--host', 'pi', '--store', str(store), '--query', 'missing header'])
        self.assertEqual(read(args, plan(args))['results'], [])

    def test_pi_pairs_by_call_id_and_flags_failures(self):
        store = jsonl(self.root / 's.jsonl', self.pi_records())
        hits = self.invoke('pi', store, 'search', '--state', 'failure')
        self.assertEqual([(h['tool'], h['state'], h['evidence']) for h in hits], [('bash', 'failure', 'isError')])
        self.assertIn('make build', hits[0]['arguments']['text'])
        self.assertEqual(hits[0]['result']['text'], 'missing header')
        self.assertNotIn('AAAA', json.dumps(hits))
        self.assertEqual(hits[0]['ref'], {'entry_id': 'e1'})
        self.assertEqual(hits[0]['result_ref'], {'entry_id': 'e2'})
        everything = self.invoke('pi', store, 'expand', '--session', 'ps')
        self.assertEqual([h['call_id'] for h in everything], ['c2', 'c1'])
        self.assertEqual(everything[0]['state'], 'no_failure_recorded')

    def test_query_tool_and_entry_filters(self):
        store = jsonl(self.root / 's.jsonl', self.pi_records())
        self.assertEqual([h['call_id'] for h in self.invoke('pi', store, 'search', '--query', 'NOTES.md')], ['c2'])
        self.assertEqual([h['call_id'] for h in self.invoke('pi', store, 'search', '--tool', 'read')], ['c2'])
        # The entry may name the call or the result side of an attempt.
        self.assertEqual([h['call_id'] for h in self.invoke('pi', store, 'expand', '--session', 'ps', '--entry', 'e2')], ['c1'])
        self.assertEqual(self.invoke('pi', store, 'search', '--query', 'absent'), [])

    def test_omp_title_record_is_accepted(self):
        store = jsonl(self.root / 's.jsonl', self.pi_records(title=True))
        self.assertEqual(len(self.invoke('omp', store, 'search', '--state', 'failure')), 1)
        args = parser().parse_args(['list', '--host', 'omp', '--store', str(store)])
        self.assertEqual(read(args, plan(args))['results'][0]['title'], 'Synthetic title')

    def test_claude_tool_use_and_result(self):
        store = jsonl(self.root / 'c.jsonl', [
            {'type': 'assistant', 'sessionId': 'cl', 'uuid': 'a1', 'timestamp': STAMP, 'message': {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'npm test'}}]}},
            {'type': 'user', 'sessionId': 'cl', 'uuid': 'u1', 'timestamp': STAMP, 'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 't1', 'is_error': True, 'content': '2 tests failed'}]}},
            {'type': 'user', 'sessionId': 'cl', 'uuid': 'u2', 'timestamp': STAMP, 'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'orphan', 'content': [{'type': 'text', 'text': 'late'}]}]}},
        ])
        hits = self.invoke('claude', store, 'search', '--query', 'failed')
        self.assertEqual([(h['tool'], h['state'], h['evidence']) for h in hits], [('Bash', 'failure', 'is_error')])
        orphan = self.invoke('claude', store, 'search', '--query', 'late')[0]
        self.assertEqual((orphan['missing'], orphan['state']), ('call_block_missing', 'no_failure_recorded'))

    def test_codex_function_and_custom_calls(self):
        store = jsonl(self.root / 'x.jsonl', [
            {'type': 'session_meta', 'payload': {'id': 'cx'}},
            {'type': 'response_item', 'ordinal': 1, 'timestamp': STAMP, 'payload': {
                'type': 'function_call', 'call_id': 'f1', 'name': 'exec', 'arguments': '{"cmd":"pytest"}'}},
            {'type': 'response_item', 'ordinal': 2, 'timestamp': STAMP, 'payload': {
                'type': 'function_call_output', 'call_id': 'f1', 'output': 'collected 3\nProcess exited with code 1'}},
            {'type': 'response_item', 'ordinal': 3, 'timestamp': STAMP, 'payload': {
                'type': 'custom_tool_call', 'call_id': 'k1', 'name': 'apply_patch', 'input': '*** Begin Patch'}},
            {'type': 'response_item', 'ordinal': 4, 'timestamp': STAMP, 'payload': {
                'type': 'custom_tool_call_output', 'call_id': 'k1', 'output': [{'type': 'input_text', 'text': 'Done'}]}},
            {'type': 'response_item', 'ordinal': 5, 'timestamp': STAMP, 'payload': {
                'type': 'function_call', 'call_id': 'f2', 'name': 'exec', 'arguments': '{"cmd":"ls"}'}},
            {'type': 'response_item', 'ordinal': 6, 'timestamp': STAMP, 'payload': {
                'type': 'function_call_output', 'call_id': 'f2', 'output': '{"output":"error.log","metadata":{"exit_code":0}}'}},
        ])
        hits = self.invoke('codex', store, 'expand', '--session', 'cx')
        self.assertEqual([(h['call_id'], h['state']) for h in hits],
                         [('f2', 'no_failure_recorded'), ('k1', 'no_failure_recorded'), ('f1', 'failure')])
        self.assertEqual(hits[2]['evidence'], 'exit_code=1')
        self.assertEqual(hits[1]['result']['text'], 'Done')

    def test_opencode_tool_blocks(self):
        db = self.root / 'oc2.db'
        con = sqlite3.connect(db)
        con.executescript('CREATE TABLE session_v2(id TEXT,title TEXT,directory TEXT,time_updated INTEGER);'
                          'CREATE TABLE session_message(id TEXT,session_id TEXT,type TEXT,seq INTEGER,time_created INTEGER,data TEXT);')
        con.execute('INSERT INTO session_message VALUES (?,?,?,?,?,?)', ('m1', 's1', 'assistant', 1, MS, json.dumps({'content': [
            {'type': 'tool', 'id': 'o1', 'name': 'bash', 'time': {'created': MS, 'completed': MS + 5},
             'state': {'status': 'error', 'input': {'command': 'cargo build'}, 'error': {'type': 'x', 'message': 'linker failed'}}},
            {'type': 'tool', 'id': 'o2', 'name': 'read', 'time': {'created': MS + 6},
             'state': {'status': 'running', 'input': {'path': 'a.rs'}}}]})))
        con.commit()
        con.close()
        hits = self.invoke('opencode', db, 'search', '--query', 'cargo')
        self.assertEqual([(h['tool'], h['state'], h['evidence']) for h in hits], [('bash', 'failure', 'status=error')])
        self.assertEqual(hits[0]['result']['text'], 'linker failed')
        pending = self.invoke('opencode', db, 'search', '--tool', 'read')[0]
        self.assertEqual(pending['state'], 'no_result_stored')

    def test_pocket_pairs_across_entries(self):
        db = self.root / 'pocket.db'
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE entries(id INTEGER,conversation_id INTEGER,record TEXT)')
        con.execute('INSERT INTO entries VALUES (?,?,?)', (11, 7, json.dumps({'model': [
            {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'p1', 'name': 'bash', 'arguments': {'command': 'deploy'}}]}]})))
        con.execute('INSERT INTO entries VALUES (?,?,?)', (12, 7, json.dumps({'model': [
            {'role': 'toolResult', 'toolCallId': 'p1', 'toolName': 'bash', 'isError': False,
             'details': {'exitCode': 2}, 'content': 'timeout'}]})))
        # Same call ID in another conversation must never be joined.
        con.execute('INSERT INTO entries VALUES (?,?,?)', (13, 8, json.dumps({'model': [
            {'role': 'toolResult', 'toolCallId': 'p1', 'toolName': 'bash', 'isError': False, 'content': 'other'}]})))
        con.commit()
        con.close()
        hits = self.invoke('pocket', db, 'search', '--state', 'failure')
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]['conversation_id'], hits[0]['evidence']), (7, 'details.exitCode=2'))
        self.assertEqual(hits[0]['result_ref'], {'entry_id': 12, 'message_index': 0})
        by_entry = self.invoke('pocket', db, 'expand', '--session', '7', '--entry', '11')
        self.assertEqual([h['call_id'] for h in by_entry], ['p1'])

    def test_plan_rules(self):
        for argv, error in [(['search', '--tool', 'bash'], 'tool_filters_require_tool_calls'),
                            (['search', '--state', 'failure', '--query', 'x'], 'tool_filters_require_tool_calls'),
                            (['list', '--tool-calls'], 'tool_calls_not_valid_for_list')]:
            args = parser().parse_args([*argv, '--host', 'pi', '--store', '.'])
            with self.assertRaisesRegex(HistoryError, error):
                plan(args)
        args = parser().parse_args(['search', '--host', 'pi', '--store', '.', '--tool-calls'])
        self.assertEqual(plan(args)['mode'], 'tool_calls')


if __name__ == '__main__':
    unittest.main()

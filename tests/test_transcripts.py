import json
from pathlib import Path
import tempfile
import unittest

from agent_history.cli import parser, plan, read


class TranscriptTests(unittest.TestCase):
    def invoke(self, source, records, action='search', extra=()):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.jsonl'
            path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
            before = path.read_bytes()
            args = parser().parse_args([action, '--host', source, '--store', str(path), *extra])
            result = read(args, plan(args))
            self.assertEqual(before, path.read_bytes())
            self.assertEqual([path], list(Path(folder).iterdir()))
            return result

    def test_codex_excludes_tools_and_instructions(self):
        rows = [
            {'type': 'session_meta', 'payload': {'id': 'cx', 'cwd': '/project'}},
            {'type': 'response_item', 'ordinal': 8, 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'ÁRBOL needle'}]}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'needle secret'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': 'needle secret'}]}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'ÁRBOL needle'}},
        ]
        result = self.invoke('codex', rows, extra=['--query', 'árbol'])
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['entry_id'], '8')
        result = self.invoke('codex', rows, 'read', ['--session', 'cx', '--entry', '8'])
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(self.invoke('codex', rows, 'read', ['--session', 'other'])['results'], [])

    def test_claude_list_and_read_exclude_thinking_and_tool_results(self):
        rows = [
            {'type': 'queue-operation', 'sessionId': 'cl', 'content': 'secret'},
            {'type': 'user', 'sessionId': 'cl', 'uuid': 'u1', 'timestamp': '2026-10-24T10:00:00Z', 'message': {'role': 'user', 'content': 'Hola needle'}},
            {'type': 'assistant', 'sessionId': 'cl', 'uuid': 'a1', 'message': {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'secret'}, {'type': 'text', 'text': 'Respuesta needle'}]}},
            {'type': 'user', 'sessionId': 'cl', 'uuid': 'u2', 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'content': 'secret needle'}]}},
        ]
        result = self.invoke('claude', rows, extra=['--query', 'needle'])
        self.assertEqual(len(result['results']), 2)
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(self.invoke('claude', rows, 'list')['results'][0]['title'], 'Hola needle')
        self.assertEqual(len(self.invoke('claude', rows, 'read', ['--session', 'cl', '--entry', 'a1'])['results']), 1)

    def test_empty_rollout_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            day = Path(folder) / '2026' / '04' / '10'
            day.mkdir(parents=True)
            (day / 'rollout-empty.jsonl').write_bytes(b'')
            (day / 'rollout-full.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in [
                {'type': 'session_meta', 'payload': {'id': 'cx'}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                                                      'content': [{'type': 'input_text', 'text': 'pricing needle'}]}}]),
                encoding='utf-8')
            args = parser().parse_args(['search', '--host', 'codex', '--store', folder, '--query', 'needle'])
            result = read(args, plan(args))
            self.assertEqual(len(result['results']), 1)
            self.assertIn('empty_transcript_skipped', result['notes'])
            (day / 'rollout-odd.jsonl').write_text('{"type":"unknown"}\n', encoding='utf-8')
            with self.assertRaisesRegex(Exception, 'unsupported_transcript_schema'):
                read(args, plan(args))

    def test_cross_source_requires_reason(self):
        args = parser().parse_args(['list', '--host', 'pi', '--source', 'claude', '--store', '.'])
        with self.assertRaisesRegex(Exception, 'source_change'):
            plan(args)


if __name__ == '__main__':
    unittest.main()

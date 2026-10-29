"""MCP wrapper over synthetic stores; no real history access."""
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

from agent_history import cli, mcp_server
from test_history_search import ROOT, Fixtures


def rpc(host, stores, *messages):
    out = io.StringIO()
    for i, (method, params) in enumerate(messages, 1):
        mcp_server.handle({'jsonrpc': '2.0', 'id': i, 'method': method, 'params': params},
                          host, stores, out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


class McpServer(Fixtures):
    def stores(self):
        return {'pi': self.pi.resolve(), 'opencode': self.oc2.resolve()}

    def call(self, **arguments):
        reply, = rpc('pi', self.stores(), ('tools/call', {'name': 'history_search', 'arguments': arguments}))
        result = reply['result']
        return json.loads(result['content'][0]['text']), result['isError']

    def test_handshake_and_tool_list(self):
        init, tools = rpc('pi', self.stores(), ('initialize', {}), ('tools/list', {}))
        self.assertEqual(init['result']['serverInfo']['name'], 'agent-history-search')
        tool, = tools['result']['tools']
        self.assertEqual(tool['name'], 'history_search')
        self.assertTrue(tool['annotations']['readOnlyHint'])
        self.assertEqual(tool['inputSchema']['properties']['source']['enum'], ['pi', 'opencode'])

    def test_search_matches_cli(self):
        result, is_error = self.call(action='search', query='question')
        self.assertFalse(is_error)
        args = cli.parser().parse_args(['search', '--host', 'pi', '--store', str(self.pi), '--query', 'question'])
        self.assertEqual(result['results'], cli.read(args, cli.plan(args))['results'])
        self.assertTrue(result['results'])

    def test_list_then_read_by_returned_store(self):
        listing, _ = self.call(action='list')
        self.assertEqual(listing['status'], 'ok')
        row = listing['results'][0]
        read, is_error = self.call(action='read', session=row['session_id'], store=row['store'])
        self.assertFalse(is_error)
        self.assertEqual(read['action'], 'expand')

    def test_other_source_needs_reason_and_authorization(self):
        result, is_error = self.call(action='list', source='opencode')
        self.assertTrue(is_error)
        self.assertEqual(result['error'], 'source_change_requires_explicit_user_request_reason')
        result, is_error = self.call(action='list', source='codex', reason='user asked')
        self.assertEqual(result['error'], 'source_not_authorized_for_this_server')
        result, is_error = self.call(action='list', source='opencode', reason='user asked')
        self.assertEqual(result['route']['source'], 'opencode')

    def test_store_outside_allow_list_is_refused(self):
        result, is_error = self.call(action='list', store=str(self.root))
        self.assertTrue(is_error)
        self.assertEqual(result['error'], 'store_outside_authorized_roots')
        result, _ = self.call(action='list', store=str(self.pi / '..' / 'oc2.db'))
        self.assertEqual(result['error'], 'store_outside_authorized_roots')

    def test_cli_errors_become_tool_errors_not_crashes(self):
        for arguments in ({'action': 'search'}, {'action': 'read'}, {'action': 'bogus'},
                          {'action': 'list', 'limit': 99}):
            result, is_error = self.call(**arguments)
            self.assertTrue(is_error, arguments)
            self.assertEqual(result['status'], 'error')

    def test_unknown_method_and_tool(self):
        replies = rpc('pi', self.stores(), ('nope', {}), ('tools/call', {'name': 'other'}))
        self.assertEqual(replies[0]['error']['code'], -32601)
        self.assertEqual(replies[1]['error']['code'], -32602)

    def test_stdio_process_end_to_end(self):
        store = f'pi={self.pi}'
        lines = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'history_search', 'arguments': {'action': 'search', 'query': 'Straße'}}},
        ]
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'history_mcp.py'), '--host', 'pi', '--store', store],
                              input=''.join(json.dumps(x) + '\n' for x in lines), capture_output=True,
                              text=True, encoding='utf-8', timeout=60)
        replies = [json.loads(x) for x in proc.stdout.splitlines()]
        self.assertEqual([r['id'] for r in replies], [1, 2])
        self.assertIn('active', replies[1]['result']['content'][0]['text'])

    def test_protocol_version_is_negotiated(self):
        for asked in mcp_server.SUPPORTED:
            reply, = rpc('pi', self.stores(), ('initialize', {'protocolVersion': asked}))
            self.assertEqual(reply['result']['protocolVersion'], asked)
        reply, = rpc('pi', self.stores(), ('initialize', {'protocolVersion': '1999-01-01'}))
        self.assertEqual(reply['result']['protocolVersion'], mcp_server.PROTOCOL)

    def test_optional_listings_answer_empty_not_error(self):
        replies = rpc('pi', self.stores(), ('resources/list', {}), ('resources/templates/list', {}),
                      ('prompts/list', {}))
        self.assertEqual([next(iter(r['result'].values())) for r in replies], [[], [], []])

    def test_codex_client_session_over_stdio(self):
        """Message shapes captured from codex-cli 0.130 (id 0, _meta.progressToken, elicitation)."""
        lines = [
            {'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
             'params': {'protocolVersion': '2025-06-18', 'capabilities': {'elicitation': {}},
                        'clientInfo': {'name': 'codex-mcp-client', 'title': 'Codex', 'version': '0.130.0'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {'_meta': {'progressToken': 0}}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'history_search', 'arguments': {'action': 'list', 'limit': 2},
                        '_meta': {'progressToken': 1}}},
        ]
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'history_mcp.py'), '--host', 'pi',
                               '--store', f'pi={self.pi}'],
                              input=''.join(json.dumps(x) + '\n' for x in lines).encode(),
                              capture_output=True, timeout=60)
        self.assertNotIn(b'\r', proc.stdout)  # LF only, even on Windows
        replies = [json.loads(x) for x in proc.stdout.decode().splitlines()]
        self.assertEqual([r['id'] for r in replies], [0, 1, 2])
        self.assertFalse(replies[2]['result']['isError'])

    def test_bad_store_option_is_rejected(self):
        with self.assertRaises(SystemExit):
            mcp_server.parse_stores('pi', ['nonsense'])
        with self.assertRaises(SystemExit):
            mcp_server.parse_stores('pi', ['nope=/x'])


if __name__ == '__main__':
    unittest.main()

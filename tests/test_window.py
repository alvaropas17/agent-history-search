"""The search window: local-only access, demo isolation and portable launching."""
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import quote

from agent_history import demo, hotkey, window
from agent_history.core import HistoryError
from agent_history.index import Index

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'test-token'
ALL_SOURCES = {'claude', 'codex', 'pi', 'omp', 'opencode', 'pocket'}


def temp(prefix):
    return tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)


def demo_app(folder):
    index = Index(Path(folder) / 'demo.sqlite', {})
    index.add_messages(demo.messages())
    return window.App(index, demo=True)


class Server(unittest.TestCase):
    def setUp(self):
        folder = temp('history-window-test-')
        self.addCleanup(folder.cleanup)
        self.app = demo_app(folder.name)
        self.server = window.WindowServer(0, self.app, TOKEN)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()
        self.port = self.server.server_address[1]

    def call(self, path, method='GET', token=TOKEN, host=None):
        con = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(con.close)
        con.putrequest(method, path, skip_host=True)
        con.putheader('Host', host or f'127.0.0.1:{self.port}')
        if token:
            con.putheader('X-Token', token)
        con.endheaders()
        response = con.getresponse()
        return response, response.read()

    def get(self, path):
        response, body = self.call(path)
        self.assertEqual(response.status, 200, body)
        return json.loads(body)

    def test_binds_only_to_loopback(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_every_route_needs_the_token(self):
        for path in ('/', '/api/ping', '/api/status', '/api/search?q=pricing', '/api/expand?ref=x'):
            self.assertEqual(self.call(path, token=None)[0].status, 403, path)
            self.assertEqual(self.call(path, token='wrong')[0].status, 403, path)

    def test_token_also_works_in_the_url_for_the_first_page_load(self):
        response, body = self.call('/?t=' + TOKEN, token=None)
        self.assertEqual(response.status, 200)
        self.assertIn(b'Agent History Search (demo)', body)

    def test_wrong_host_is_refused_even_with_the_token(self):
        for host in ('evil.example', 'localhost:%d' % self.port, '127.0.0.1:1'):
            self.assertEqual(self.call('/api/ping', host=host)[0].status, 403, host)

    def test_only_get_is_served_and_no_cors_is_offered(self):
        for method in ('POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'):
            response, _ = self.call('/api/ping', method=method)
            self.assertEqual(response.status, 403, method)
            self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))

    def test_security_headers(self):
        response, _ = self.call('/')
        policy = response.getheader('Content-Security-Policy')
        self.assertIn("default-src 'none'", policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(response.getheader('Referrer-Policy'), 'no-referrer')

    def test_second_instance_cannot_take_the_port(self):
        with self.assertRaises(OSError):
            window.WindowServer(self.port, self.app, 'another-token')
        self.assertEqual(self.call('/api/ping')[0].status, 200)

    def test_search_and_expand_end_to_end(self):
        found = self.get('/api/search?q=pricing')
        self.assertGreater(len(found['results']), 2)
        self.assertEqual(found['words'], ['pricing'])
        first = found['results'][0]
        self.assertIn('pricing', first['text'].lower())
        self.assertTrue(first['title'])
        part = self.get('/api/expand?context=200&ref=' + quote(first['ref']))
        self.assertIn('pricing', part['text'].lower())

    def test_agent_filter(self):
        found = self.get('/api/search?q=deploy&host=codex')['results']
        self.assertTrue(found)
        self.assertEqual({r['host'] for r in found}, {'codex'})

    def test_bad_parameters_are_client_errors(self):
        for path in ('/api/search?q=pricing&host=nobody', '/api/expand?ref=nope',
                     '/api/expand?ref=1:aaaaaaaaaaaaaaaaaaaa:0&context=abc',
                     '/api/expand?ref=1:aaaaaaaaaaaaaaaaaaaa:0&context=0',
                     '/api/expand?ref=1:aaaaaaaaaaaaaaaaaaaa:0&offset=-1'):
            self.assertEqual(self.call(path)[0].status, 400, path)
        self.assertEqual(self.call('/nothing')[0].status, 404)

    def test_empty_search_and_status(self):
        self.assertEqual(self.get('/api/search?q=')['results'], [])
        status = self.get('/api/status')
        self.assertTrue(status['demo'])
        self.assertFalse(status['indexing'])
        self.assertEqual(set(status['messages']), ALL_SOURCES)


class Demo(unittest.TestCase):
    def test_demo_has_no_stores_and_never_updates(self):
        with temp('history-demo-test-') as folder:
            app = demo_app(folder)
            self.assertEqual(app.index.stores, {})
            with mock.patch.object(app.index, 'update') as update:
                self.assertIsNone(app.refresh())
                app.search({'q': ['pricing']})
            update.assert_not_called()

    def test_demo_process_ignores_a_real_history_under_home(self):
        """Run the real entry point in demo mode with a marker conversation in HOME."""
        with temp('history-demo-home-') as home:
            project = Path(home) / '.claude' / 'projects' / 'p'
            project.mkdir(parents=True)
            (project / 'a.jsonl').write_text(json.dumps({
                'type': 'user', 'sessionId': 's', 'message': {
                    'role': 'user', 'content': [{'type': 'text', 'text': 'xylophone conversation'}]}}) + '\n',
                encoding='utf-8')
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            env = {**os.environ, 'HOME': home, 'USERPROFILE': home, 'LOCALAPPDATA': home,
                   'TEMP': home, 'TMP': home, 'TMPDIR': home}  # A killed demo leaves its folder here.
            proc = subprocess.Popen(
                [sys.executable, '-B', str(ROOT / 'history_window.py'), '--serve', '--demo',
                 '--port', str(port), '--cache-dir', home], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                path = window.token_path(home, port)
                deadline = time.monotonic() + 5
                while not window.alive(port, window.read_token(path)) and time.monotonic() < deadline:
                    time.sleep(0.02)
                token = window.read_token(path)
                self.assertTrue(window.alive(port, token))
                con = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                con.request('GET', '/api/search?q=xylophone', headers={'X-Token': token})
                self.assertEqual(json.loads(con.getresponse().read())['results'], [])
                con.close()
            finally:
                proc.kill()
                proc.wait(5)

    def test_every_agent_appears_and_dates_are_relative(self):
        rows = demo.messages(now_ms=10 ** 13)
        self.assertEqual({r[0] for r in rows}, ALL_SOURCES)
        self.assertTrue(all(r[4] <= 10 ** 13 for r in rows))
        self.assertEqual(len({r[1] for r in rows}), len(demo.CONVERSATIONS))

    def test_demo_words_named_in_the_module_all_find_something(self):
        with temp('history-demo-test-') as folder:
            index = demo_app(folder).index
            for word in ('pricing', 'retry', 'migration', 'flaky', 'deploy'):
                self.assertTrue(index.search([word]), word)

    def test_demo_uses_its_own_port_and_title_and_refuses_stores(self):
        self.assertNotEqual(window.PORT, window.DEMO_PORT)
        self.assertNotEqual(window.TITLE, window.DEMO_TITLE)
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            window.main(['--demo', '--only', 'claude'])


class Background(unittest.TestCase):
    def test_refresh_runs_once_beside_searches(self):
        with temp('history-refresh-test-') as folder:
            app = window.App(Index(Path(folder) / 'i.sqlite', {'claude': Path(folder) / 'none'}))
            release = threading.Event()
            original = app.index.update

            def slow():
                release.wait(5)
                return original()

            with mock.patch.object(app.index, 'update', slow):
                thread = app.refresh()
                self.assertTrue(app.status()['indexing'])
                self.assertIsNone(app.refresh())
                self.assertEqual(app.search({'q': ['nothing here']})['results'], [])
                release.set()
                thread.join(5)
            self.assertFalse(app.status()['indexing'])
            self.assertIsNone(app.refresh())  # Not due yet.


class Portability(unittest.TestCase):
    def test_no_personal_paths_in_the_window_code(self):
        names = ('history_window.py', 'agent_history/window.py', 'agent_history/window.html',
                 'agent_history/index.py', 'agent_history/hotkey.py', 'agent_history/demo.py')
        banned = re.compile(r'C:[\\/]+Users|/home/\w|/Users/\w|alvar|pythoncore|\.agents', re.I)
        for name in names:
            self.assertIsNone(banned.search((ROOT / name).read_text(encoding='utf-8')), name)

    @unittest.skipIf(sys.platform == 'darwin', 'macOS uses a fixed cache folder')
    def test_cache_folder_follows_the_platform(self):
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': 'X', 'XDG_CACHE_HOME': 'Y'}):
            expected = Path('X' if os.name == 'nt' else 'Y') / 'agent-history-search'
            self.assertEqual(window.cache_dir(), expected)

    def test_browser_candidates_use_the_environment_not_fixed_drives(self):
        env = {'ProgramFiles': 'P', 'ProgramFiles(x86)': 'Q', 'LOCALAPPDATA': 'L'}
        found = window.browser_candidates(env, 'win32')
        self.assertIn(str(Path('Q') / r'Microsoft\Edge\Application\msedge.exe'), found)
        self.assertTrue(window.browser_candidates({}, 'linux'))
        self.assertTrue(window.browser_candidates({}, 'darwin'))
        self.assertEqual(window.browser_candidates({}, 'win32'), [])

    def test_without_a_chromium_browser_the_default_one_opens(self):
        with mock.patch.object(window, 'find_browser', return_value=None), \
                mock.patch.object(window.webbrowser, 'open') as opened:
            window.open_window('http://127.0.0.1:1/')
        opened.assert_called_once_with('http://127.0.0.1:1/')

    def test_app_mode_is_used_when_a_browser_exists(self):
        with mock.patch.object(window.subprocess, 'Popen') as popen:
            window.open_window('http://127.0.0.1:1/', 'browser')
        self.assertEqual(popen.call_args.args[0], ['browser', '--app=http://127.0.0.1:1/'])

    def test_forwarded_keeps_server_options_only(self):
        self.assertEqual(
            window.forwarded(['--hotkey', '--shortcut', 'ctrl+alt+j', '--demo', '--port', '9',
                              '--no-open', '--shortcut=ctrl+alt+k', '--store', 'pi=x']),
            ['--demo', '--port', '9', '--store', 'pi=x'])

    def test_store_and_only_selection(self):
        args = window.parser().parse_args(['--store', 'claude=elsewhere', '--only', 'claude,pi'])
        stores = window.selected_stores(args)
        self.assertEqual(set(stores), {'claude', 'pi'})
        self.assertEqual(stores['claude'], Path('elsewhere'))
        with self.assertRaises(HistoryError):
            window.selected_stores(window.parser().parse_args(['--only', 'nobody']))
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            window.parser().parse_args(['--store', 'nobody=x'])

    def test_words(self):
        self.assertEqual(window.words_of('a pricing an pricing plan'), ['pricing', 'plan'])
        self.assertEqual(window.words_of(' ab '), ['ab'])
        self.assertEqual(len(window.words_of(' '.join(f'word{n}' for n in range(20)))), window.MAX_WORDS)

    def test_a_held_port_leaves_no_token_behind(self):
        with temp('history-port-test-') as folder, socket.socket() as held:
            held.bind(('127.0.0.1', 0))
            held.listen(1)
            port = held.getsockname()[1]
            args = window.parser().parse_args(['--only', 'pi', '--store', 'pi=' + folder])
            self.assertEqual(window.serve(args, port, folder), 1)
            self.assertFalse(window.token_path(folder, port).exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes')
    def test_token_file_is_private(self):
        with temp('history-token-test-') as folder:
            path = Path(folder) / 'sub' / 'token'
            window.write_private(path, 'x')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class FakeRegistry:
    HKEY_CURRENT_USER, REG_SZ, KEY_SET_VALUE = 'HKCU', 1, 2

    def __init__(self):
        self.values = {}
        self.path = None

    def CreateKey(self, root, path):
        self.path = (root, path)
        return mock.MagicMock()

    def OpenKey(self, root, path, reserved, access):
        return self.CreateKey(root, path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class Hotkey(unittest.TestCase):
    def test_shortcut_parsing(self):
        self.assertEqual(hotkey.parse_shortcut('ctrl+alt+h'),
                         (hotkey.MOD_CONTROL | hotkey.MOD_ALT | hotkey.MOD_NOREPEAT, ord('H')))
        self.assertEqual(hotkey.parse_shortcut('Win+Shift+7')[1], ord('7'))
        for bad in ('h', 'ctrl+', 'ctrl+f12', 'hyper+h', 'ctrl+ctrl+h', 'ctrl+ñ'):
            with self.assertRaises(HistoryError, msg=bad):
                hotkey.parse_shortcut(bad)

    def test_windows_only_features_explain_themselves_elsewhere(self):
        with mock.patch.object(hotkey.os, 'name', 'posix'):
            with self.assertRaisesRegex(HistoryError, 'windows_only'):
                hotkey.require_windows()

    @unittest.skipUnless(os.name == 'nt', 'Windows paths')
    def test_startup_entry_is_per_user_and_reversible(self):
        registry = FakeRegistry()
        with mock.patch('builtins.print'):
            hotkey.install_startup(['--install-startup', '--shortcut', 'ctrl+alt+j'], registry)
            value = registry.values[hotkey.RUN_VALUE]
            self.assertEqual(registry.path, ('HKCU', hotkey.RUN_KEY))
            self.assertIn('history_window.py', value)
            self.assertIn('--hotkey --shortcut ctrl+alt+j', value)
            self.assertNotIn('--install-startup', value)
            hotkey.uninstall_startup(registry)
            self.assertEqual(registry.values, {})
            self.assertEqual(hotkey.uninstall_startup(registry), 0)


if __name__ == '__main__':
    unittest.main()

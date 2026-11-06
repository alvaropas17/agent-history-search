"""Local search window: a small web page served only on 127.0.0.1.

Filters every agent's history while you type, using the derived index in index.py.

Security model. The server binds to 127.0.0.1 only, checks the Host header (DNS rebinding),
sends no CORS headers and requires a random token, created on each start, in every request.
The token file is readable by the same user, so this protects against other sites and
other users, not against programs already running as you.
"""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse
import urllib.error
import urllib.request
import webbrowser

from .cli import SOURCES
from .core import HistoryError
from .index import Index, check_sqlite, default_stores

PORT = 47620
DEMO_PORT = 47621  # Separate on purpose: a demo must never open a running real server.
PROTOCOL = 'agent-history-window-v2'
TITLE = 'Agent History Search'
DEMO_TITLE = 'Agent History Search (demo)'
IDLE_SECONDS = 30 * 60
UPDATE_EVERY = 30
MAX_WORDS = 8
MAX_WORD_CHARS = 200
START_TIMEOUT = 8.0
DETACHED_PROCESS, NEW_PROCESS_GROUP, NO_WINDOW = 0x8, 0x200, 0x08000000
ENTRY = Path(__file__).resolve().parents[1] / 'history_window.py'
PAGE = Path(__file__).with_name('window.html')
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def cache_dir():
    """Per-user cache folder. It holds the index, which is a copy of private text."""
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Caches'
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    return base / 'agent-history-search'


def token_path(folder, port):
    return Path(folder) / f'window-{port}.token'


def configuration_id(args, folder, port):
    stores = {} if args.demo else selected_stores(args)
    payload = {'protocol': PROTOCOL, 'demo': args.demo, 'port': port,
               'cache': str(Path(folder).expanduser().resolve()),
               'stores': {name: str(Path(path).expanduser().resolve())
                          for name, path in sorted(stores.items())}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def words_of(query):
    """Words of three or more characters; a shorter query is searched as typed."""
    found = list(dict.fromkeys(w[:MAX_WORD_CHARS] for w in query.split() if len(w) >= 3))
    return found[:MAX_WORDS] or [query.strip()[:MAX_WORD_CHARS]]


class App:
    """Search and status. Indexing runs beside searches, never in front of them."""

    def __init__(self, index, demo=False, update_every=UPDATE_EVERY, identity=None):
        self.index = index
        self.identity = identity or hashlib.sha256(str(index.path.resolve()).encode()).hexdigest()
        self.demo = demo
        self.update_every = update_every
        self.title = DEMO_TITLE if demo else TITLE
        self.page = PAGE.read_text(encoding='utf-8').replace('__TITLE__', self.title)
        self.last_request = time.monotonic()
        self.last_update = None
        self.indexing = False
        self.lock = threading.Lock()

    def refresh(self):
        """Start a background update when due; the first one may take minutes."""
        if self.demo:
            return None
        with self.lock:
            due = self.last_update is None or time.monotonic() - self.last_update >= self.update_every
            if self.indexing or not due:
                return None
            self.indexing = True
        thread = threading.Thread(target=self._update, daemon=True)
        thread.start()
        return thread

    def _update(self):
        try:
            self.index.update()
        except (OSError, sqlite3.Error, HistoryError):
            pass
        finally:
            with self.lock:
                self.indexing = False
                self.last_update = time.monotonic()

    def search(self, params):
        query = params.get('q', [''])[0].strip()
        if not query:
            return {'results': [], 'words': []}
        self.refresh()
        words = words_of(query)
        results = self.index.search(words, params.get('host', ['all'])[0] or 'all')
        return {'results': results, 'words': words}

    def expand(self, params):
        context = int(params.get('context', ['1500'])[0])
        if not 1 <= context <= 6000:
            raise HistoryError('invalid_context')
        offset = int(params['offset'][0]) if 'offset' in params else None
        if offset is not None and offset < 0:
            raise HistoryError('invalid_offset')
        return self.index.expand(params.get('ref', [''])[0], context, offset)

    def status(self):
        return {'demo': self.demo, 'indexing': self.indexing, 'messages': self.index.counts(),
                'skipped': self.index.skipped}


class WindowServer(ThreadingHTTPServer):
    """On Windows SO_REUSEADDR lets several processes listen on one port, and each would
    overwrite the token of the previous one. With SO_EXCLUSIVEADDRUSE a second instance
    fails to bind and exits. Elsewhere SO_REUSEADDR is safe and avoids TIME_WAIT errors."""
    allow_reuse_address = not hasattr(socket, 'SO_EXCLUSIVEADDRUSE')
    daemon_threads = True

    def __init__(self, port, app, token):
        self.app = app
        self.token = token
        super().__init__(('127.0.0.1', port), Handler)
        self.expected_host = f'127.0.0.1:{self.server_address[1]}'

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    server_version = 'agent-history-window'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass  # Requests carry search terms; nothing is logged.

    def reply(self, code, body, kind='application/json; charset=utf-8'):
        data = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass

    def deny(self):
        self.reply(403, '{"error":"forbidden"}')

    def do_GET(self):
        server = self.server
        origin = 'http://' + server.expected_host
        if (self.headers.get_all('Host') != [server.expected_host]
                or self.headers.get('Origin') not in (None, origin)
                or self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site')):
            return self.deny()
        if len(self.path) > 8192 or not self.path.startswith('/') or self.path.startswith('//'):
            return self.reply(400, '{"error":"invalid_request"}')
        url = urlparse(self.path)
        try:
            params = parse_qs(url.query, keep_blank_values=True, max_num_fields=8)
            if any(len(value) != 1 for value in params.values()):
                raise ValueError('duplicate_parameter')
        except ValueError:
            return self.reply(400, '{"error":"invalid_parameters"}')
        app = server.app
        if url.path == '/':
            # The shell has no token or private data. The launcher passes its token as a fragment.
            return self.reply(200, app.page, 'text/html; charset=utf-8')
        if url.path == '/api/health':
            nonce = params.get('nonce', [''])[0]
            if (set(params) != {'nonce'} or len(nonce) != 48
                    or any(character not in '0123456789abcdef' for character in nonce)):
                return self.reply(400, '{"error":"invalid_challenge"}')
            message = f'{PROTOCOL}:{app.identity}:{nonce}'.encode()
            proof = hmac.new(server.token.encode(), message, hashlib.sha256).hexdigest()
            return self.reply(200, json.dumps({'protocol': PROTOCOL, 'identity': app.identity,
                                               'proof': proof}))
        given = self.headers.get('X-Token', '')
        if len(given) > 128 or not hmac.compare_digest(given.encode('utf-8'), server.token.encode('utf-8')):
            return self.deny()
        app.last_request = time.monotonic()
        routes = {'/api/search': app.search, '/api/expand': app.expand}
        try:
            if url.path == '/api/ping':
                return self.reply(200, '{"ok":true}')
            if url.path == '/api/status':
                return self.reply(200, json.dumps(app.status(), ensure_ascii=False))
            if url.path in routes:
                return self.reply(200, json.dumps(routes[url.path](params), ensure_ascii=False))
        except (HistoryError, ValueError) as exc:
            return self.reply(400, json.dumps({'error': str(exc)}))
        except sqlite3.Error:
            return self.reply(500, '{"error":"index_read_error"}')
        self.reply(404, '{"error":"not found"}')

    def refuse(self):
        self.deny()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = refuse


def parse_store(value):
    source, separator, path = value.partition('=')
    if not separator or source not in SOURCES or not path:
        raise argparse.ArgumentTypeError('expected SOURCE=PATH with a known source')
    return source, Path(path).expanduser()


def parser():
    p = argparse.ArgumentParser(prog='history_window.py', description='Open the local search window.')
    p.add_argument('--demo', action='store_true', help='Invented conversations; reads no history')
    p.add_argument('--port', type=int, help=f'Local port (default {PORT}, demo {DEMO_PORT})')
    p.add_argument('--store', action='append', default=[], type=parse_store, metavar='SOURCE=PATH',
                   help='Use this store instead of the default location; repeatable')
    p.add_argument('--only', help='Comma-separated sources to index; default all six')
    p.add_argument('--cache-dir', type=Path, help='Folder for the index and token')
    p.add_argument('--browser', help='Chromium-based browser to open in app mode')
    p.add_argument('--no-open', action='store_true', help='Start the server and print the URL')
    p.add_argument('--hotkey', action='store_true', help='Windows: stay resident and listen for the shortcut')
    p.add_argument('--shortcut', default='ctrl+alt+h', help='Windows: global shortcut')
    p.add_argument('--install-startup', action='store_true', help='Windows: run --hotkey when you sign in')
    p.add_argument('--uninstall-startup', action='store_true', help='Windows: undo --install-startup')
    p.add_argument('--serve', action='store_true', help=argparse.SUPPRESS)
    return p


def selected_stores(args):
    stores = default_stores()
    stores.update(dict(args.store))
    if args.only:
        names = [name.strip() for name in args.only.split(',') if name.strip()]
        unknown = set(names) - set(SOURCES)
        if unknown or not names:
            raise HistoryError('unknown_source:' + ','.join(sorted(unknown)))
        stores = {name: stores[name] for name in names}
    return stores


def forwarded(argv):
    """Arguments that describe the server, for the process started from this one."""
    drop = {'--hotkey', '--install-startup', '--uninstall-startup', '--serve', '--no-open'}
    result, skip = [], False
    for item in argv:
        if skip:
            skip = False
        elif item == '--shortcut':
            skip = True
        elif item not in drop and not item.startswith('--shortcut='):
            result.append(item)
    return result


def background_python():
    executable = Path(sys.executable)
    if os.name == 'nt':
        windowless = executable.with_name('pythonw.exe')
        if windowless.exists():
            return windowless
    return executable


def detached():
    options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
               'stderr': subprocess.DEVNULL, 'close_fds': True}
    if os.name == 'nt':
        options['creationflags'] = DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    else:
        options['start_new_session'] = True
    return options


def browser_candidates(env=None, platform=None):
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if platform == 'win32':
        tails = (r'Microsoft\Edge\Application\msedge.exe', r'Google\Chrome\Application\chrome.exe',
                 r'BraveSoftware\Brave-Browser\Application\brave.exe')
        roots = [env.get(k) for k in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA')]
        return [str(Path(root) / tail) for tail in tails for root in roots if root]
    if platform == 'darwin':
        return [f'/Applications/{name}.app/Contents/MacOS/{name}'
                for name in ('Google Chrome', 'Microsoft Edge', 'Brave Browser', 'Chromium')]
    return ['google-chrome', 'chromium', 'chromium-browser', 'microsoft-edge', 'brave-browser']


def find_browser():
    for candidate in browser_candidates():
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def open_window(url, browser=None):
    """App mode has no address bar or tabs; without a Chromium browser use the default one."""
    executable = browser or find_browser()
    if executable:
        subprocess.Popen([executable, '--app=' + url], close_fds=True)
    else:
        webbrowser.open(url)


def read_token(path):
    try:
        if Path(path).stat().st_size <= 128:
            return Path(path).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        pass
    return ''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def alive(port, token, identity=None):
    if not token or len(token) > 128:
        return False
    nonce = secrets.token_hex(24)
    # Do not give an unrelated listener the token while checking its identity.
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/health?nonce={nonce}')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=0.3) as response:
            answer = json.loads(response.read(4097))
        if (not isinstance(answer, dict) or answer.get('protocol') != PROTOCOL
                or not isinstance(answer.get('identity'), str)
                or identity is not None and answer['identity'] != identity
                or not isinstance(answer.get('proof'), str)):
            return False
        message = f"{PROTOCOL}:{answer['identity']}:{nonce}".encode()
        expected = hmac.new(token.encode(), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(answer['proof'].encode(), expected.encode())
    except urllib.error.HTTPError as exc:
        exc.close()
    except (OSError, ValueError):
        pass
    return False


def serve(args, port, folder):
    demo_folder = None
    if args.demo:
        from .demo import messages
        demo_folder = tempfile.mkdtemp(prefix='agent-history-demo-')
        index = Index(Path(demo_folder) / 'demo.sqlite', {})
        index.add_messages(messages())
    else:
        index = Index(Path(folder) / 'index-v1.sqlite', selected_stores(args))
    app = App(index, demo=args.demo, identity=configuration_id(args, folder, port))
    token = secrets.token_urlsafe(24)
    try:
        try:
            server = WindowServer(port, app, token)
        except OSError:
            return 1  # Another instance owns the port; leave its token alone.
        path = token_path(folder, port)
        write_private(path, token)
        app.refresh()

        def watchdog():
            while time.monotonic() - app.last_request < IDLE_SECONDS:
                time.sleep(15)
            server.shutdown()

        threading.Thread(target=watchdog, daemon=True).start()
        try:
            server.serve_forever()
        finally:
            server.server_close()
            try:
                if read_token(path) == token:
                    path.unlink()
            except OSError:
                pass
        return 0
    finally:
        if demo_folder:
            shutil.rmtree(demo_folder, ignore_errors=True)


def launch(args, argv, port, folder):
    path = token_path(folder, port)
    identity = configuration_id(args, folder, port)
    if not alive(port, read_token(path), identity):
        command = [str(background_python()), '-B', str(ENTRY), '--serve', *forwarded(argv)]
        subprocess.Popen(command, **detached())
        deadline = time.monotonic() + START_TIMEOUT
        while not alive(port, read_token(path), identity):
            if time.monotonic() >= deadline:
                print(f'The search server did not start. Is port {port} in use? Try --port.', file=sys.stderr)
                return 1
            time.sleep(0.1)
    url = f'http://127.0.0.1:{port}/#{read_token(path)}'
    if args.no_open:
        print(url)
    else:
        open_window(url, args.browser)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    for name in ('stdout', 'stderr'):  # pythonw has neither.
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w'))
    p = parser()
    args = p.parse_args(argv)
    if args.demo and (args.store or args.only):
        p.error('--demo reads no history; remove --store and --only')
    port = args.port if args.port is not None else (DEMO_PORT if args.demo else PORT)
    if not 1024 <= port <= 65535:
        p.error('--port must be in 1024..65535')
    if args.hotkey or args.install_startup or args.uninstall_startup:
        from . import hotkey
        try:
            if not args.uninstall_startup:
                selected_stores(args)
            if args.install_startup:
                return hotkey.install_startup(argv)
            if args.uninstall_startup:
                return hotkey.uninstall_startup()
            return hotkey.run(args.shortcut, DEMO_TITLE if args.demo else TITLE, forwarded(argv))
        except HistoryError as exc:
            print(exc, file=sys.stderr)
            return 2
    folder = args.cache_dir or cache_dir()
    try:
        check_sqlite()
        selected_stores(args)
        return serve(args, port, folder) if args.serve else launch(args, argv, port, folder)
    except HistoryError as exc:
        print(exc, file=sys.stderr)
        return 2

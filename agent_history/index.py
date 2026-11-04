"""Derived full-text index for the search window.

The CLI stays index-free. Only the search window uses this module, because filtering
while you type cannot rescan every transcript on each key press.

Original histories are only read. The index is a separate SQLite file holding a plain
copy of the user and assistant text, so it is as private as the histories themselves.
Deleting the file is safe: it is rebuilt from the stores.

JSONL stores are read incrementally by byte position. SQLite stores are read by rowid,
re-reading the last rows because they may still change. Text only: no tool calls,
reasoning or attachments. Pi and omp abandoned branches are included, unlike the CLI.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .cli import SOURCES
from .core import HistoryError, text_content, tool_text

MAX_LINE = 2 * 1024 * 1024
REDO_ROWS = 300
BATCH_ROWS = 2000
TITLE_CHARS = 100
SHORT_SCAN_ROWS = 500
REF = re.compile(r'[1-9]\d{0,18}:[a-f0-9]{20}:\d{1,10}')
SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, host TEXT, sess TEXT, role TEXT,
    ts INTEGER, src INTEGER, ext INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS docs_host_ts ON docs(host, ts);
CREATE INDEX IF NOT EXISTS docs_ts ON docs(ts);
CREATE INDEX IF NOT EXISTS docs_src_ext ON docs(src, ext);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(text, content='docs', content_rowid='id',
    tokenize='trigram case_sensitive 0');
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TABLE IF NOT EXISTS src(sid INTEGER PRIMARY KEY, key TEXT UNIQUE, host TEXT,
    mtime INTEGER, size INTEGER, pos INTEGER, sess TEXT);
CREATE TABLE IF NOT EXISTS titles(host TEXT, sess TEXT, title TEXT, PRIMARY KEY(host, sess));
"""


def default_stores(home=None):
    home = Path(home) if home else Path.home()
    return {'claude': home / '.claude' / 'projects', 'codex': home / '.codex' / 'sessions',
            'pi': home / '.pi' / 'agent' / 'sessions', 'omp': home / '.omp' / 'agent' / 'sessions',
            'opencode': home / '.local' / 'share' / 'opencode',
            'pocket': home / '.pi-pocket' / 'pocket.sqlite'}


def check_sqlite():
    """FTS5 trigram needs SQLite 3.34+; Python builds without it fail here, not mid-search."""
    con = sqlite3.connect(':memory:')
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
    except sqlite3.Error:
        raise HistoryError('sqlite_with_fts5_trigram_required:3.34_or_newer') from None
    finally:
        con.close()


def millis(stamp):
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        return int(stamp if stamp > 1e11 else stamp * 1000)
    if isinstance(stamp, str):
        try:
            return int(datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() * 1000)
        except ValueError:
            pass
    return 0


def keep(body, noise=('<',)):
    """Drop injected blocks such as <system-reminder>; they are not conversation text."""
    if isinstance(body, str) and body.strip() and not (noise and body.lstrip().startswith(noise)):
        return body
    return ''


def claude_row(row, meta):
    if row.get('type') == 'custom-title':
        meta['title'] = row.get('customTitle') or meta.get('title')
    if isinstance(row.get('sessionId'), str) and row['sessionId']:
        meta['session'] = row['sessionId']
    message = row.get('message')
    if row.get('type') in ('user', 'assistant') and not row.get('isMeta') and isinstance(message, dict):
        body = keep(text_content(message.get('content')))
        if body:
            yield row['type'], millis(row.get('timestamp')), body


def codex_row(row, meta):
    payload = row.get('payload')
    if not isinstance(payload, dict):
        return
    if row.get('type') == 'session_meta':
        meta['session'] = payload.get('id') or payload.get('session_id') or meta['session']
    if (row.get('type') == 'response_item' and payload.get('type') == 'message'
            and payload.get('role') in ('user', 'assistant')):
        body = keep(tool_text(payload.get('content')), ('<', '[external_agent_tool_call'))
        if body:
            yield payload['role'], millis(row.get('timestamp')), body


def pi_row(row, meta):
    kind = row.get('type')
    if kind == 'session':
        meta['session'] = row.get('id') or meta['session']
    elif kind in ('session_info', 'title'):
        meta['title'] = row.get('name') or row.get('title') or meta.get('title')
    elif kind == 'message' and isinstance(row.get('message'), dict):
        message = row['message']
        if message.get('role') in ('user', 'assistant'):
            body = keep(text_content(message.get('content')))
            if body:
                yield message['role'], millis(row.get('timestamp') or message.get('timestamp')), body


JSONL = {'claude': claude_row, 'codex': codex_row, 'pi': pi_row, 'omp': pi_row}


def pocket_rows(row):
    _id, session, raw = row
    try:
        record = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return
    for message in record.get('model', []) if isinstance(record, dict) else ():
        if isinstance(message, dict) and message.get('role') in ('user', 'assistant'):
            body = keep(text_content(message.get('content')), ())
            if body:
                yield str(session), message['role'], millis(message.get('timestamp')), body


def opencode_message_rows(row):
    _id, session, kind, created, raw = row
    if kind not in ('user', 'assistant'):
        return
    try:
        record = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return
    if isinstance(record, dict):
        text = record.get('text') if kind == 'user' else text_content(record.get('content'))
        body = keep(text or '', ())
        if body:
            when = record.get('time') if isinstance(record.get('time'), dict) else {}
            yield session, kind, millis(when.get('created') or created), body


def opencode_part_rows(row):
    _id, session, created, raw, message_raw = row
    try:
        part, message = json.loads(raw), json.loads(message_raw or '{}')
    except (TypeError, ValueError, RecursionError):
        return
    if (isinstance(part, dict) and isinstance(message, dict) and part.get('type') == 'text'
            and message.get('role') in ('user', 'assistant') and not part.get('synthetic')):
        body = keep(part.get('text') or '', ())
        if body:
            yield session, message['role'], millis(created), body


def signature(path):
    """Cheap change marker for a SQLite store: its file and -wal modification times."""
    value = 0
    for item in (Path(path), Path(str(path) + '-wal')):
        try:
            value ^= item.stat().st_mtime_ns
        except OSError:
            pass
    return value


def open_store(path):
    """Read a store without leaving files beside it.

    No -wal: immutable, which creates nothing. With -wal and -shm the app has it open and
    a read-only connection sees its latest commits. -wal alone would make SQLite create
    -shm next to the user's database, so that state is refused.
    """
    path = Path(path).resolve()
    if not Path(str(path) + '-wal').exists():
        mode = 'mode=ro&immutable=1'
    elif Path(str(path) + '-shm').exists():
        mode = 'mode=ro'
    else:
        raise HistoryError('wal_without_shm:open_the_app_or_use_a_snapshot')
    con = sqlite3.connect(path.as_uri() + '?' + mode, uri=True, timeout=5)
    con.execute('PRAGMA query_only=ON')
    return con


def tables_of(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def excerpt(body, anchor, context):
    start, end = max(0, anchor - context), min(len(body), anchor + context)
    return {'text': body[start:end], 'start': start, 'end': end, 'text_length': len(body)}


def digest(body):
    return hashlib.sha256(body.encode('utf-8', 'surrogatepass')).hexdigest()[:20]


class Index:
    def __init__(self, path, stores):
        self.path = Path(path)
        self.stores = dict(stores)
        self.skipped = {}
        unknown = set(self.stores) - set(SOURCES)
        if unknown:
            raise HistoryError('unknown_source:' + ','.join(sorted(unknown)))
        check_sqlite()
        con = self.writer()
        con.close()

    def writer(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=30)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=NORMAL')
        con.executescript(SCHEMA)
        return con

    def reader(self):
        con = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        con.execute('PRAGMA query_only=ON')
        return con

    # --- writing ---------------------------------------------------------------------

    def update(self):
        """Index what is new in every store, one at a time. -> {source: new messages}."""
        con, added, skipped = self.writer(), {}, {}
        try:
            for source, store in self.stores.items():
                try:
                    if source in JSONL:
                        added[source] = self._update_jsonl(con, source, Path(store))
                    elif source == 'pocket':
                        added[source] = self._update_pocket(con, Path(store))
                    else:
                        added[source] = self._update_opencode(con, Path(store))
                    con.commit()
                except (OSError, sqlite3.Error, HistoryError) as exc:
                    con.rollback()
                    skipped[source] = str(exc) if isinstance(exc, HistoryError) else type(exc).__name__
        finally:
            con.close()
        self.skipped = skipped
        return added

    @staticmethod
    def _insert(con, rows):
        if not rows:
            return 0
        con.executemany('INSERT INTO docs(host,sess,role,ts,src,ext,text) VALUES(?,?,?,?,?,?,?)', rows)
        # A stored title replaces this; until then the first user line names the session.
        for host, session, role, _ts, _src, _ext, body in rows:
            if role == 'user':
                line = next((x.strip() for x in body.splitlines() if x.strip()), '')
                con.execute('INSERT OR IGNORE INTO titles VALUES(?,?,?)', (host, session, line[:TITLE_CHARS]))
        count = len(rows)
        rows.clear()
        return count

    @staticmethod
    def _source(con, key, host, session=''):
        row = con.execute('SELECT sid,mtime,size,pos,sess FROM src WHERE key=?', (key,)).fetchone()
        if row:
            return row
        sid = con.execute('INSERT INTO src(key,host,mtime,size,pos,sess) VALUES(?,?,0,0,0,?)',
                          (key, host, session)).lastrowid
        return sid, 0, 0, 0, session

    def _update_jsonl(self, con, host, root):
        if not root.exists():
            return 0
        known = dict(con.execute('SELECT key,sid FROM src WHERE host=?', (host,)))
        files = [root] if root.is_file() else root.rglob('*.jsonl')
        seen, total = set(), 0
        for file in files:
            key = str(file)
            seen.add(key)
            try:
                total += self._read_jsonl(con, host, file, key)
                con.commit()
            except OSError:
                con.rollback()
        for key, sid in known.items():
            if key not in seen:
                con.execute('DELETE FROM docs WHERE src=?', (sid,))
                con.execute('DELETE FROM src WHERE sid=?', (sid,))
        return total

    def _read_jsonl(self, con, host, file, key):
        stat = file.stat()
        fallback = file.stem.split('_')[-1]
        sid, mtime, size, pos, session = self._source(con, key, host, fallback)
        if mtime == stat.st_mtime_ns and size == stat.st_size:
            return 0
        if stat.st_size < pos:  # Rewritten: start over.
            con.execute('DELETE FROM docs WHERE src=?', (sid,))
            pos, session = 0, fallback
        meta, rows, end, total = {'session': session}, [], pos, 0
        with file.open('rb') as stream:
            stream.seek(pos)
            for line in stream:
                if not line.endswith(b'\n'):
                    break  # Still being written; read again next time.
                end += len(line)
                if len(line) > MAX_LINE:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(row, dict):
                    for role, stamp, body in JSONL[host](row, meta):
                        rows.append((host, meta['session'], role, stamp, sid, end, body))
                if len(rows) >= BATCH_ROWS:
                    total += self._insert(con, rows)
        total += self._insert(con, rows)
        if meta.get('title'):
            con.execute('INSERT OR REPLACE INTO titles VALUES(?,?,?)',
                        (host, meta['session'], str(meta['title'])[:TITLE_CHARS]))
        done = end == stat.st_size
        con.execute('UPDATE src SET mtime=?,size=?,pos=?,sess=? WHERE sid=?',
                    (stat.st_mtime_ns if done else 0, stat.st_size, end, meta['session'], sid))
        return total

    def _read_rows(self, con, host, key, store, query, convert):
        """Tables with a growing rowid: re-read from pos-REDO_ROWS, keep the rest."""
        sid, mark, _size, pos, _session = self._source(con, key, host)
        current = signature(store)
        if mark == current and pos:
            return 0
        start = max(0, pos - REDO_ROWS)
        con.execute('DELETE FROM docs WHERE src=? AND ext>?', (sid, start))
        source = open_store(store)
        rows, top, total = [], pos, 0
        try:
            for row in source.execute(query, (start,)):
                top = max(top, row[0])
                for session, role, stamp, body in convert(row):
                    rows.append((host, session, role, stamp, sid, row[0], body))
                if len(rows) >= BATCH_ROWS:
                    total += self._insert(con, rows)
        finally:
            source.close()
        total += self._insert(con, rows)
        con.execute('UPDATE src SET mtime=?,pos=? WHERE sid=?', (current, top, sid))
        return total

    def _update_pocket(self, con, store):
        if not store.is_file():
            return 0
        return self._read_rows(con, 'pocket', str(store), store,
                               'SELECT id,conversation_id,record FROM entries WHERE id>? ORDER BY id',
                               pocket_rows)

    def _update_opencode(self, con, store):
        total = 0
        for database in [store] if store.is_file() else sorted(store.glob('*.db')):
            source = open_store(database)
            try:
                tables = tables_of(source)
            finally:
                source.close()
            if 'session_message' in tables:
                total += self._read_rows(
                    con, 'opencode', f'{database}#messages', database,
                    'SELECT rowid,session_id,type,time_created,data FROM session_message '
                    'WHERE rowid>? ORDER BY rowid', opencode_message_rows)
            if 'part' in tables and 'message' in tables:
                total += self._read_rows(
                    con, 'opencode', f'{database}#parts', database,
                    'SELECT p.rowid,p.session_id,p.time_created,p.data,m.data FROM part p '
                    'LEFT JOIN message m ON m.id=p.message_id WHERE p.rowid>? ORDER BY p.rowid',
                    opencode_part_rows)
            self._opencode_titles(con, database, tables)
        return total

    def _opencode_titles(self, con, database, tables):
        sid, mark = self._source(con, f'{database}#titles', 'opencode')[:2]
        current = signature(database)
        if mark == current:
            return
        source = open_store(database)
        try:
            for table in ('session_v2', 'session'):  # Constants, never input.
                if table in tables:
                    con.executemany("INSERT OR REPLACE INTO titles VALUES('opencode',?,?)",
                                    [(i, t[:TITLE_CHARS]) for i, t in
                                     source.execute(f'SELECT id,title FROM {table}') if t])
        finally:
            source.close()
        con.execute('UPDATE src SET mtime=? WHERE sid=?', (current, sid))

    def add_messages(self, messages):
        """Insert (host, session, title, role, ts, text) rows directly. Used by demo mode only."""
        con = self.writer()
        try:
            con.executemany('INSERT OR REPLACE INTO titles VALUES(?,?,?)',
                            sorted({(m[0], m[1], m[2]) for m in messages}))
            con.executemany('INSERT INTO docs(host,sess,role,ts,src,ext,text) VALUES(?,?,?,?,0,0,?)',
                            [(m[0], m[1], m[3], m[4], m[5]) for m in messages])
            con.commit()
        finally:
            con.close()

    # --- reading ---------------------------------------------------------------------

    def counts(self):
        con = self.reader()
        try:
            return dict(con.execute('SELECT host,COUNT(*) FROM docs GROUP BY host'))
        finally:
            con.close()

    def search(self, words, host='all', limit=20, context=110):
        """Best message per session for any of the words, literal and case-insensitive."""
        if not words or any(not isinstance(w, str) or not w for w in words):
            raise HistoryError('search_requires_words')
        if host != 'all' and host not in SOURCES:
            raise HistoryError('unknown_source')
        hosts = list(SOURCES) if host == 'all' else [host]
        marks = ','.join('?' for _ in hosts)
        con = self.reader()
        try:
            if all(len(w) >= 3 for w in words):  # The trigram index needs 3 characters.
                match = ' OR '.join('"' + w.replace('"', '""') + '"' for w in words)
                rows = con.execute(
                    'SELECT id,host,sess,role,ts,text FROM (SELECT *, ROW_NUMBER() OVER '
                    '(PARTITION BY host,sess ORDER BY score,id) AS place FROM ('
                    'SELECT d.id,d.host,d.sess,d.role,d.ts,d.text,bm25(docs_fts) AS score '
                    f'FROM docs_fts JOIN docs d ON d.id=docs_fts.rowid WHERE d.host IN ({marks}) '
                    'AND docs_fts MATCH ?)) WHERE place=1 ORDER BY score,id LIMIT ?',
                    (*hosts, match, limit)).fetchall()
            else:
                escaped = [w.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') for w in words]
                like = ' OR '.join("text LIKE ? ESCAPE '\\'" for _ in words)
                recent = con.execute(
                    f'SELECT id,host,sess,role,ts,text FROM docs WHERE host IN ({marks}) AND ({like}) '
                    'ORDER BY ts DESC,id DESC LIMIT ?',
                    (*hosts, *('%' + w + '%' for w in escaped), SHORT_SCAN_ROWS)).fetchall()
                best = {}
                for row in recent:
                    best.setdefault((row[1], row[2]), row)
                rows = list(best.values())[:limit]
            pattern = re.compile('|'.join(re.escape(w) for w in words), re.IGNORECASE)
            results = []
            for row_id, source, session, role, stamp, body in rows:
                found = pattern.search(body)
                anchor = found.start() if found else 0
                title = con.execute('SELECT title FROM titles WHERE host=? AND sess=?',
                                    (source, session)).fetchone()
                results.append({'host': source, 'session': session, 'role': role, 'timestamp': stamp,
                                'title': title[0] if title else '',
                                'ref': f'{row_id}:{digest(body)}:{anchor}', **excerpt(body, anchor, context)})
            return results
        finally:
            con.close()

    def expand(self, ref, context=1500, offset=None):
        if not isinstance(ref, str) or not REF.fullmatch(ref):
            raise HistoryError('invalid_reference')
        row_id, expected, anchor = ref.split(':')
        con = self.reader()
        try:
            row = con.execute('SELECT text FROM docs WHERE id=?', (int(row_id),)).fetchone()
        finally:
            con.close()
        if row is None or digest(row[0]) != expected:
            raise HistoryError('stale_reference:search_again')
        body = row[0]
        if offset is None:
            part = excerpt(body, int(anchor), context)
        else:
            start = min(offset, len(body))
            end = min(len(body), start + context)
            part = {'text': body[start:end], 'start': start, 'end': end, 'text_length': len(body)}
        return {'ref': ref, **part, 'next_offset': part['end'] if part['end'] < len(body) else None}

"""Read-only primitives. No network, stores, indexes or configuration are created."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import time


class HistoryError(Exception):
    pass


class Budget:
    def __init__(self, seconds=12, rows=20000, byte_limit=64 * 1024 * 1024):
        self.deadline = time.monotonic() + seconds
        self.rows_left = rows
        self.bytes_left = byte_limit
        self.scanned = 0
        self.notes = []
        self.incomplete = False

    def note(self, reason):
        self.incomplete = True
        if reason not in self.notes:
            self.notes.append(reason)

    def check(self):
        if time.monotonic() >= self.deadline:
            raise HistoryError('deadline_exceeded')

    def take(self, size=0):
        self.check()
        if self.rows_left <= 0 or size > self.bytes_left:
            self.note('scan_budget_reached')
            return False
        self.rows_left -= 1
        self.bytes_left -= size
        self.scanned += 1
        return True


def text_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(x['text'] for x in content if isinstance(x, dict)
                     and x.get('type') == 'text' and isinstance(x.get('text'), str))


def read_json(raw):
    try:
        result = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise HistoryError('invalid_json_record') from exc
    if not isinstance(result, dict):
        raise HistoryError('expected_object_record')
    return result


def utc_day(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000, timezone.utc).date()
        if isinstance(value, str):
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def in_dates(value, args):
    if not args.since and not args.until:
        return True
    day = utc_day(value)
    return day is not None and (not args.since or day >= args.since) and (not args.until or day <= args.until)


def snippet(body, query, context):
    """Return at most context chars; Unicode folding never changes source offsets."""
    # Search normalized text but map the folded location back to the original.
    folded = body.casefold()
    pos = folded.find(query.casefold()) if query else 0
    if pos < 0:
        return None
    if query and pos:
        total = 0
        for index, char in enumerate(body):
            total += len(char.casefold())
            if total > pos:
                pos = index
                break
    start = max(0, pos - context // 3)
    return {'text': body[start:start + context], 'offset': start,
            'text_length': len(body), 'text_truncated': start > 0 or len(body) > context}


def emit_message(args, budget, reference, role, stamp, body):
    if role not in ('user', 'assistant') or not body or not in_dates(stamp, args):
        return None
    part = snippet(body, args.query if args.action == 'search' else '', args.context)
    if part is None:
        return None
    return {**reference, 'role': role, 'timestamp': stamp, **part}


def connect(path, required, budget):
    path = Path(path).resolve()
    if not path.is_file():
        raise HistoryError('store_not_found')
    # Immutable prevents even SQLite -shm/-journal creation. It ignores WAL, so
    # refuse WAL stores instead of returning silently stale main-file contents.
    if Path(str(path) + '-wal').exists():
        raise HistoryError('wal_store_requires_consistent_offline_snapshot')
    con = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=1)
    try:
        con.execute('PRAGMA query_only=ON')
        con.set_progress_handler(lambda: int(time.monotonic() >= budget.deadline), 1000)
        con.execute('BEGIN')
        for table, columns in required.items():
            # All table identifiers are constants selected by our backend, never input.
            actual = {r[1] for r in con.execute('PRAGMA table_info(' + table + ')')}
            if not set(columns) <= actual:
                raise HistoryError('unsupported_schema:' + table)
        return con
    except BaseException:
        con.close()
        raise


def signature(path):
    path = Path(path)
    return {'bytes': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}


def store_freshness(path):
    path = Path(path)
    result = {'file': signature(path)}
    wal = Path(str(path) + '-wal')
    if wal.is_file():
        result['wal'] = signature(wal)
    return result

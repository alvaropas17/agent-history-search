"""Read-only primitives. No network, stores, indexes or configuration are created."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
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


def search_query(args):
    """Keep the single-query contract; variants share one reader traversal."""
    if args.action != 'search':
        return ''
    variants = getattr(args, 'query_variant', ())
    return tuple(([args.query] if args.query else []) + list(variants)) if variants else args.query


def snippet(body, query, context, *, default_context=False):
    """Use a total cap when explicit, or 100 characters around a default match.

    Unicode folding never changes source offsets.
    """
    # Search normalized text but map the folded location back to the original.
    folded = body.casefold()
    if isinstance(query, tuple):
        for candidate in query:
            pos = folded.find(candidate.casefold())
            if pos >= 0:
                query = candidate
                break
        else:
            return None
    else:
        pos = folded.find(query.casefold()) if query else 0
    if pos < 0:
        return None
    folded_end = pos + len(query.casefold())
    if query and default_context:
        total = 0
        start = end = 0
        for index, char in enumerate(body):
            next_total = total + len(char.casefold())
            if total <= pos < next_total:
                start = index
            if total < folded_end <= next_total:
                end = index + 1
                break
            total = next_total
        start, end = max(0, start - 100), min(len(body), end + 100)
        return {'text': body[start:end], 'offset': start,
                'text_length': len(body), 'text_truncated': start > 0 or end < len(body)}
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
    part = snippet(body, search_query(args), args.context,
                   default_context=getattr(args, 'default_context', False))
    if part is None:
        return None
    return {**reference, 'role': role, 'timestamp': stamp, **part}


TOOL_SCOPE = ('stored tool calls and text results paired by call ID, never by proximity; '
              'images omitted; a stored result is not proof of current state')
EXIT_LINE = re.compile(r'^(?:Exit code|Process exited with code):? (-?\d+)\s*$', re.M)


def tool_text(content):
    """Text of a stored tool result; images and other non-text blocks are never returned."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(x['text'] for x in content if isinstance(x, dict)
                     and x.get('type') in ('text', 'input_text', 'output_text')
                     and isinstance(x.get('text'), str))


def arguments_text(value):
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return ''


def exit_code_failure(code, label):
    if isinstance(code, int) and not isinstance(code, bool) and code != 0:
        return '%s=%d' % (label, code)
    return None


def output_failure(text):
    """Codex stores exit codes inside the output text; the word "error" alone is not a failure."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        data = None
    if isinstance(data, dict):
        meta = data.get('metadata') if isinstance(data.get('metadata'), dict) else data
        return exit_code_failure(meta.get('exit_code'), 'exit_code')
    match = EXIT_LINE.search(text[:4000])
    return exit_code_failure(int(match.group(1)), 'exit_code') if match else None


def join_tools(events):
    """Pair ('call'|'result', call_id, fields) events by call ID in stored order."""
    attempts, by_id = [], {}
    for kind, call_id, fields in events:
        call_id = call_id if isinstance(call_id, str) and call_id else None
        found = by_id.get(call_id)
        if kind == 'call':
            if found is not None and 'arguments' not in found:
                found.pop('missing', None)
                found.update({k: v for k, v in fields.items() if k != 'tool' or not found.get('tool')})
                continue
            item = {'call_id': call_id, **fields}
        else:
            if found is not None and 'result' not in found:
                found.update({k: v for k, v in fields.items() if k != 'tool' or not found.get('tool')})
                continue
            item = {'call_id': call_id, 'missing': 'call_block_missing', **fields}
        attempts.append(item)
        if call_id:
            by_id[call_id] = item
    return attempts


def emit_tool(args, base, item):
    failure = item.get('failure')
    state = 'no_result_stored' if 'result' not in item else 'failure' if failure else 'no_failure_recorded'
    if args.state == 'failure' and state != 'failure':
        return None
    if args.tool and item.get('tool') != args.tool:
        return None
    stamp = item.get('timestamp') or item.get('result_timestamp')
    if not in_dates(stamp, args):
        return None
    refs = [item.get('ref') or {}, item.get('result_ref') or {}]
    if args.entry and not any(args.entry == str(r.get('entry_id', r.get('message_id'))) for r in refs):
        return None
    query = search_query(args)
    found, fields = not query, {}
    for name in ('arguments', 'result'):
        text = item.get(name)
        if not isinstance(text, str):
            continue
        part = snippet(text, query, args.context,
                       default_context=getattr(args, 'default_context', False)) if query else None
        found = found or part is not None
        fields[name] = part or snippet(text, '', args.context)
    if not found:
        return None
    hit = {**base, 'kind': 'tool_attempt', 'call_id': item['call_id'], 'tool': item.get('tool'),
           'timestamp': stamp, 'state': state, 'evidence': failure, **fields}
    for key in ('ref', 'result_ref', 'missing'):
        if item.get(key):
            hit[key] = item[key]
    return hit


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

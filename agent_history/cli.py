"""CLI contract and bounded isolated read worker. No installation is required."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from .core import TOOL_SCOPE, Budget, HistoryError

SOURCES = ('pi', 'omp', 'opencode', 'pocket', 'codex', 'claude')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('search', 'list', 'expand', 'read'))
    p.add_argument('--host', required=True, choices=SOURCES)
    p.add_argument('--source', choices=SOURCES)
    p.add_argument('--reason', default='')
    p.add_argument('--store', type=Path, required=True,
                   help='Pi sessions directory or one JSONL; oc2/Pocket exact SQLite path')
    p.add_argument('--query', default='')
    p.add_argument('--session', help='Exact session ID (Pocket: conversation ID)')
    p.add_argument('--entry', help='Exact entry/message ID; with --tool-calls it may name the call or its result')
    p.add_argument('--since', type=date.fromisoformat, help='Inclusive UTC day')
    p.add_argument('--until', type=date.fromisoformat, help='Inclusive UTC day')
    p.add_argument('--limit', type=int, default=5)
    p.add_argument('--context', type=int, default=400, help='Maximum text characters per match')
    p.add_argument('--timeout', type=int, default=15, help='Outer worker deadline, 1..30 seconds')
    p.add_argument('--max-rows', type=int, default=20000, help='Maximum decoded input rows, 1..100000')
    p.add_argument('--tool-calls', action='store_true',
                   help='Search/read stored tool calls and their results instead of user/assistant text')
    p.add_argument('--tool', help='Exact tool name (requires --tool-calls)')
    p.add_argument('--state', choices=('any', 'failure'), default='any',
                   help='failure: only results with recorded error evidence (requires --tool-calls)')
    p.add_argument('--plan', action='store_true', help='Validate and print route without opening a store')
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    return p


def plan(a):
    if a.action == 'read':
        a.action = 'expand'
    source = a.source or a.host
    if source != a.host and not a.reason.strip():
        raise HistoryError('source_change_requires_explicit_user_request_reason')
    if not 1 <= a.limit <= 20 or not 0 <= a.context <= 6000:
        raise HistoryError('invalid_limit_or_context')
    if not 1 <= a.timeout <= 30 or not 1 <= a.max_rows <= 100000:
        raise HistoryError('invalid_timeout_or_scan_limit')
    if len(a.query) > 2000 or len(a.reason) > 1000:
        raise HistoryError('query_or_reason_too_long')
    if (a.tool or a.state != 'any') and not a.tool_calls:
        raise HistoryError('tool_filters_require_tool_calls')
    if a.tool_calls and a.action == 'list':
        raise HistoryError('tool_calls_not_valid_for_list')
    if a.tool is not None and not 0 < len(a.tool) <= 200:
        raise HistoryError('invalid_tool_name')
    if a.action == 'search' and not a.query.strip() and not a.tool_calls:
        raise HistoryError('search_requires_query')
    if a.action != 'search' and a.query:
        raise HistoryError('query_is_search_only')
    if a.action == 'expand' and not a.session:
        raise HistoryError('expand_requires_exact_session_id')
    if a.action == 'list' and a.entry:
        raise HistoryError('entry_not_valid_for_list')
    if a.since and a.until and a.since > a.until:
        raise HistoryError('invalid_date_interval')
    if source == 'pocket':
        for value in (a.session, a.entry):
            if value is not None and (not value.isascii() or not value.isdigit() or int(value) < 1
                                      or int(value) > 9223372036854775807):
                raise HistoryError('pocket_ids_require_positive_sqlite_integers')
    return {'host': a.host, 'source': source, 'reason': a.reason,
            'store': str(a.store.expanduser().absolute()), 'automatic_fallback': False,
            'mode': 'tool_calls' if a.tool_calls else 'text'}


def read(a, route):
    budget = Budget(seconds=max(0.1, a.timeout - 0.5), rows=a.max_rows)
    source, store = route['source'], route['store']
    if source in ('pi', 'omp'):
        from .pi import run
    elif source in ('codex', 'claude'):
        from .transcripts import run
    else:
        from .sqlite_readers import opencode, pocket
        run = opencode if source == 'opencode' else pocket
    results, coverage = run(a, store, budget)
    if a.tool_calls:
        coverage['scope'] = TOOL_SCOPE
    return {'schema_version': 1, 'status': 'ok', 'route': route,
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'action': a.action, 'results': results,
            'incomplete': budget.incomplete, 'notes': budget.notes,
            'scanned_rows': budget.scanned, 'coverage': coverage}


def error(kind, route=None):
    return {'schema_version': 1, 'status': 'error', 'error': kind,
            'route': route, 'automatic_fallback': False}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    a = parser().parse_args(argv)
    route = None
    try:
        route = plan(a)
        if a.plan:
            result = {'schema_version': 1, 'status': 'plan', 'route': route}
        elif a.worker:
            result = read(a, route)
        else:
            entry = Path(__file__).resolve().parents[1] / 'history_search.py'
            proc = subprocess.run([sys.executable, '-B', str(entry), '--worker', *argv],
                                  shell=False, capture_output=True, timeout=a.timeout)
            if len(proc.stdout) > 2 * 1024 * 1024:
                raise HistoryError('worker_output_limit')
            try:
                result = json.loads(proc.stdout)
            except (ValueError, UnicodeDecodeError):
                raise HistoryError('invalid_worker_json') from None
            if not isinstance(result, dict) or result.get('status') not in ('ok', 'error'):
                raise HistoryError('invalid_worker_contract')
            if proc.returncode != 0 and result.get('status') != 'error':
                raise HistoryError('worker_exit_error')
    except HistoryError as exc:
        result = error(str(exc), route)
    except subprocess.TimeoutExpired:
        result = error('worker_timeout', route)
    except sqlite3.Error:
        result = error('sqlite_read_error_or_deadline:verify_schema_and_access', route)
    except (OSError, UnicodeError, ValueError, OverflowError, RecursionError):
        result = error('input_read_error', route)
    encoded = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    if len(encoded) > 2 * 1024 * 1024:
        result = error('output_limit_reached', route)
        encoded = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    print(encoded)
    return 1 if result['status'] == 'error' else 0

"""Independent Pi v2/v3 JSONL reader: persisted leaf -> parentId chain."""
from pathlib import Path

from .core import (HistoryError, arguments_text, emit_message, emit_tool, exit_code_failure,
                   in_dates, join_tools, read_json, signature, text_content, tool_text)

MAX_FILE = 32 * 1024 * 1024
MAX_LINE = 2 * 1024 * 1024
MAX_FILES = 500


def load(path, budget):
    before = signature(path)
    if before['bytes'] > MAX_FILE or before['bytes'] > budget.bytes_left:
        budget.note('pi_file_exceeds_byte_budget')
        return None
    records = []
    with path.open('rb') as stream:
        while True:
            raw = stream.readline(MAX_LINE + 1)
            if not raw:
                break
            if len(raw) > MAX_LINE:
                budget.note('pi_line_too_large')
                return None
            if not budget.take(len(raw)):
                return None
            if not raw.strip():
                continue
            # A concurrently written partial final line is not silently accepted.
            try:
                records.append(read_json(raw))
            except HistoryError:
                budget.note('pi_invalid_record_file_skipped')
                return None
    if signature(path) != before:
        budget.note('pi_file_changed_during_read')
        return None
    title = None
    if records and records[0].get('type') == 'title' and 'id' not in records[0]:
        # omp writes one title record before the Pi session header.
        title = records.pop(0).get('title')
    if not records or records[0].get('type') != 'session':
        raise HistoryError('unsupported_pi_header')
    header = {**records[0], 'title': title if isinstance(title, str) else None}
    if header.get('version') not in (2, 3) or not isinstance(header.get('id'), str):
        raise HistoryError('unsupported_pi_version')
    by_id = {}
    for record in records[1:]:
        eid = record.get('id')
        if not isinstance(eid, str) or eid in by_id or 'parentId' not in record:
            raise HistoryError('invalid_pi_tree')
        by_id[eid] = record
    chain, seen = [], set()
    leaf = records[-1].get('id') if len(records) > 1 else None
    while leaf is not None:
        budget.check()
        if not isinstance(leaf, str) or leaf in seen or leaf not in by_id:
            raise HistoryError('broken_pi_parent_chain')
        seen.add(leaf)
        entry = by_id[leaf]
        chain.append(entry)
        leaf = entry['parentId']
    return header, chain, before


def message_events(message, stamp, ref):
    """Pi agent messages: assistant toolCall blocks and toolResult messages (Pi, omp, Pocket)."""
    events = []
    stamp = message.get('timestamp', stamp)
    if message.get('role') == 'assistant' and isinstance(message.get('content'), list):
        for block in message['content']:
            if isinstance(block, dict) and block.get('type') == 'toolCall':
                events.append(('call', block.get('id'), {
                    'tool': block.get('name'), 'arguments': arguments_text(block.get('arguments')),
                    'timestamp': stamp, 'ref': ref}))
    elif message.get('role') == 'toolResult':
        details = message.get('details') if isinstance(message.get('details'), dict) else {}
        failure = ('isError' if message.get('isError') is True
                   else exit_code_failure(details.get('exitCode'), 'details.exitCode'))
        events.append(('result', message.get('toolCallId'), {
            'tool': message.get('toolName'), 'result': tool_text(message.get('content')),
            'failure': failure, 'result_timestamp': stamp, 'result_ref': ref}))
    return events


def tool_events(chain):
    events = []
    for entry in reversed(chain):
        message = entry.get('message')
        if entry.get('type') == 'message' and isinstance(message, dict):
            events.extend(message_events(message, entry.get('timestamp'), {'entry_id': entry['id']}))
    return events


def paths(store, args, budget):
    root = Path(store).resolve()
    if root.is_file():
        if root.suffix != '.jsonl':
            raise HistoryError('pi_requires_jsonl_or_sessions_directory')
        return [root]
    if not root.is_dir():
        raise HistoryError('store_not_found')
    found = []
    # Standard Pi layout only; never recursively walk arbitrary profile trees.
    for candidate in root.iterdir():
        budget.check()
        if candidate.is_symlink():
            continue
        if candidate.is_file() and candidate.suffix == '.jsonl':
            found.append(candidate)
        elif candidate.is_dir():
            for child in candidate.iterdir():
                budget.check()
                if child.is_file() and not child.is_symlink() and child.suffix == '.jsonl':
                    found.append(child)
                if len(found) > MAX_FILES:
                    raise HistoryError('too_many_pi_files:select_specific_project_directory')
        if len(found) > MAX_FILES:
            raise HistoryError('too_many_pi_files:select_specific_project_directory')
    return sorted(found, key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)


def run(args, store, budget):
    results = []
    files = paths(store, args, budget)
    for file in files:
        budget.check()
        loaded = load(file, budget)
        if loaded is None:
            if budget.rows_left <= 0 or budget.bytes_left <= 0:
                break
            continue
        header, chain, fresh = loaded
        if args.session and args.session != header['id']:
            continue
        base = {'store': str(file.resolve()), 'session_id': header['id'],
                'branch': 'persisted_leaf_parent_chain', 'freshness': fresh}
        if args.action == 'list':
            stamp = chain[0].get('timestamp') if chain else header.get('timestamp')
            if in_dates(stamp, args):
                listed = {**base, 'timestamp': stamp, 'cwd': header.get('cwd'),
                          'leaf_id': chain[0]['id'] if chain else None}
                if header['title']:
                    listed['title'] = header['title'][:160]
                results.append(listed)
        elif args.tool_calls:
            for item in reversed(join_tools(tool_events(chain))):
                hit = emit_tool(args, base, item)
                if hit is not None:
                    results.append(hit)
                if len(results) > args.limit:
                    break
        else:
            for entry in chain:
                if args.entry and args.entry != entry['id']:
                    continue
                message = entry.get('message')
                if entry.get('type') != 'message' or not isinstance(message, dict):
                    continue
                hit = emit_message(args, budget, {**base, 'entry_id': entry['id']},
                                   message.get('role'), message.get('timestamp', entry.get('timestamp')),
                                   text_content(message.get('content')))
                if hit is not None:
                    results.append(hit)
                if len(results) > args.limit:
                    break
        if len(results) > args.limit:
            break
    if len(results) > args.limit:
        budget.note('result_limit_reached')
    return results[:args.limit], {
        'reader': 'pi-jsonl-v2-v3', 'order': 'file_mtime_desc_then_leaf_to_root',
        'scope': 'raw user/assistant text on persisted leaf chain; not compacted/edited model context',
        'freshness': 'files checked unchanged during read; live in-memory leaf is unavailable',
    }

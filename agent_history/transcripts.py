"""Bounded Codex and Claude Code JSONL readers; no indexes or profile writes."""
from pathlib import Path

from .core import HistoryError, emit_message, in_dates, read_json, signature, text_content

MAX_FILE = 32 * 1024 * 1024
MAX_LINE = 2 * 1024 * 1024
MAX_FILES = 500


def candidates(store, source, budget):
    root = Path(store).resolve()
    if root.is_file():
        if root.suffix != '.jsonl':
            raise HistoryError('jsonl_required')
        return [root]
    if not root.is_dir():
        raise HistoryError('store_not_found')
    # Known layouts only. Codex store is sessions/, Claude store is projects/.
    depth = 3 if source == 'codex' else 1
    found = []
    def visit(folder, remaining):
        for item in folder.iterdir():
            budget.check()
            if item.is_symlink():
                continue
            if item.is_file() and item.suffix == '.jsonl':
                found.append(item)
                if len(found) > MAX_FILES:
                    raise HistoryError('too_many_transcripts:select_specific_directory')
            elif remaining and item.is_dir():
                visit(item, remaining - 1)
    visit(root, depth)
    return sorted(found, key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)


def load(file, source, budget):
    before = signature(file)
    if before['bytes'] > MAX_FILE or before['bytes'] > budget.bytes_left:
        budget.note('transcript_file_exceeds_byte_budget')
        return None
    sid, cwd, messages = None, None, []
    with file.open('rb') as stream:
        for number in range(1, 100002):
            raw = stream.readline(MAX_LINE + 1)
            if not raw:
                break
            if len(raw) > MAX_LINE:
                budget.note('transcript_line_too_large')
                return None
            if not budget.take(len(raw)):
                return None
            if not raw.strip():
                continue
            try:
                row = read_json(raw)
            except HistoryError:
                budget.note('invalid_transcript_record_file_skipped')
                return None
            if source == 'codex':
                payload = row.get('payload')
                if not isinstance(payload, dict):
                    continue
                if row.get('type') == 'session_meta':
                    sid = payload.get('id') or payload.get('session_id')
                    cwd = payload.get('cwd')
                if row.get('type') != 'response_item' or payload.get('type') != 'message':
                    continue
                role = payload.get('role')
                # Codex uses input_text/output_text, not Pi's text blocks.
                content = payload.get('content')
                body = '\n'.join(x['text'] for x in content if isinstance(x, dict)
                                 and x.get('type') in ('input_text', 'output_text')
                                 and isinstance(x.get('text'), str)) if isinstance(content, list) else ''
                eid = str(row.get('ordinal', number))
            else:
                if isinstance(row.get('sessionId'), str):
                    if sid is not None and sid != row['sessionId']:
                        raise HistoryError('mixed_claude_session_ids')
                    sid = row['sessionId']
                cwd = row.get('cwd') or cwd
                payload = row.get('message')
                if row.get('type') not in ('user', 'assistant') or not isinstance(payload, dict):
                    continue
                role = payload.get('role')
                body = text_content(payload.get('content'))
                eid = row.get('uuid') or str(number)
            if role in ('user', 'assistant') and body:
                messages.append((eid, role, row.get('timestamp'), body))
    if signature(file) != before:
        budget.note('transcript_changed_during_read')
        return None
    if not isinstance(sid, str) or not sid:
        raise HistoryError('unsupported_transcript_schema')
    return sid, cwd, messages, before


def run(args, store, budget):
    source = args.source or args.host
    results = []
    for file in candidates(store, source, budget):
        loaded = load(file, source, budget)
        if loaded is None:
            if budget.rows_left <= 0 or budget.bytes_left <= 0:
                break
            continue
        sid, cwd, messages, fresh = loaded
        if args.session and args.session != sid:
            continue
        base = {'store': str(file.resolve()), 'session_id': sid,
                'branch': 'stored_order_not_active_branch_verified', 'freshness': fresh}
        if args.action == 'list':
            stamp = messages[-1][2] if messages else None
            if in_dates(stamp, args):
                title = next((m[3][:160] for m in messages if m[1] == 'user'), '')
                results.append({**base, 'timestamp': stamp, 'cwd': cwd, 'title': title})
        else:
            for eid, role, stamp, body in reversed(messages):
                if args.entry and args.entry != eid:
                    continue
                hit = emit_message(args, budget, {**base, 'entry_id': eid}, role, stamp, body)
                if hit is not None:
                    results.append(hit)
                if len(results) > args.limit:
                    break
        if len(results) > args.limit:
            break
    if len(results) > args.limit:
        budget.note('result_limit_reached')
    return results[:args.limit], {
        'reader': source + '-jsonl', 'order': 'file_mtime_desc_then_stored_messages_desc',
        'scope': 'raw user/assistant text; no tool outputs, reasoning or active branch verification',
        'freshness': 'files checked unchanged during read; archived and subagent directories require explicit store',
    }

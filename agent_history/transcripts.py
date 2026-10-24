"""Bounded Codex and Claude Code JSONL readers; no indexes or profile writes."""
from pathlib import Path

from .core import (HistoryError, arguments_text, emit_message, emit_tool, in_dates, join_tools,
                   output_failure, read_json, signature, text_content, tool_text)

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
    sid, cwd, messages, events = None, None, [], []
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
                kind = payload.get('type')
                ref = {'entry_id': str(row.get('ordinal', number))}
                if row.get('type') == 'response_item' and kind in ('function_call', 'custom_tool_call'):
                    events.append(('call', payload.get('call_id'), {
                        'tool': payload.get('name'),
                        'arguments': arguments_text(payload.get('arguments', payload.get('input'))),
                        'timestamp': row.get('timestamp'), 'ref': ref}))
                    continue
                if row.get('type') == 'response_item' and kind in ('function_call_output', 'custom_tool_call_output'):
                    text = tool_text(payload.get('output'))
                    events.append(('result', payload.get('call_id'), {
                        'result': text, 'failure': output_failure(text),
                        'result_timestamp': row.get('timestamp'), 'result_ref': ref}))
                    continue
                if row.get('type') != 'response_item' or kind != 'message':
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
                for block in payload.get('content') if isinstance(payload.get('content'), list) else ():
                    if not isinstance(block, dict):
                        continue
                    if block.get('type') == 'tool_use':
                        events.append(('call', block.get('id'), {
                            'tool': block.get('name'), 'arguments': arguments_text(block.get('input')),
                            'timestamp': row.get('timestamp'), 'ref': {'entry_id': eid}}))
                    elif block.get('type') == 'tool_result':
                        events.append(('result', block.get('tool_use_id'), {
                            'result': tool_text(block.get('content')),
                            'failure': 'is_error' if block.get('is_error') is True else None,
                            'result_timestamp': row.get('timestamp'), 'result_ref': {'entry_id': eid}}))
            if role in ('user', 'assistant') and body:
                messages.append((eid, role, row.get('timestamp'), body))
    if signature(file) != before:
        budget.note('transcript_changed_during_read')
        return None
    if not isinstance(sid, str) or not sid:
        raise HistoryError('unsupported_transcript_schema')
    return sid, cwd, messages, events, before


def run(args, store, budget):
    source = args.source or args.host
    results = []
    for file in candidates(store, source, budget):
        loaded = load(file, source, budget)
        if loaded is None:
            if budget.rows_left <= 0 or budget.bytes_left <= 0:
                break
            continue
        sid, cwd, messages, events, fresh = loaded
        if args.session and args.session != sid:
            continue
        base = {'store': str(file.resolve()), 'session_id': sid,
                'branch': 'stored_order_not_active_branch_verified', 'freshness': fresh}
        if args.action == 'list':
            stamp = messages[-1][2] if messages else None
            if in_dates(stamp, args):
                title = next((m[3][:160] for m in messages if m[1] == 'user'), '')
                results.append({**base, 'timestamp': stamp, 'cwd': cwd, 'title': title})
        elif args.tool_calls:
            for item in reversed(join_tools(events)):
                hit = emit_tool(args, base, item)
                if hit is not None:
                    results.append(hit)
                if len(results) > args.limit:
                    break
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

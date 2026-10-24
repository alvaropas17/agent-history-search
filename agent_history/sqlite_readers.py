"""Independent readers for explicitly selected oc2 and Pocket SQLite schemas."""
from .core import (HistoryError, arguments_text, connect, emit_message, emit_tool, in_dates,
                   join_tools, read_json, store_freshness, text_content, tool_text)
from .pi import message_events

MAX_RECORD = 2 * 1024 * 1024
OC2 = {
    'session_v2': ('id', 'title', 'directory', 'time_updated'),
    'session_message': ('id', 'session_id', 'type', 'seq', 'time_created', 'data'),
}
POCKET = {'entries': ('id', 'conversation_id', 'record')}


def consume(rows, budget):
    for row in rows:
        raw = row[-1]
        if raw is None:
            budget.note('oversized_record_skipped')
            continue
        if not budget.take(len(raw.encode('utf-8')) if isinstance(raw, str) else 0):
            break
        yield row


def opencode(args, store, budget):
    results = []
    before = store_freshness(store)
    con = connect(store, OC2, budget)
    try:
        if args.action == 'list':
            rows = con.execute('SELECT id,title,directory,time_updated FROM session_v2 '
                               'ORDER BY time_updated DESC,id DESC LIMIT ?', (args.max_rows + 1,))
            for sid, title, cwd, stamp in rows:
                if not budget.take():
                    break
                if args.session and sid != args.session:
                    continue
                if in_dates(stamp, args):
                    results.append({'session_id': sid, 'title': str(title or '')[:args.context],
                                    'cwd': str(cwd or '')[:args.context], 'timestamp': stamp})
                if len(results) > args.limit:
                    break
        else:
            clauses = ["type='assistant'" if args.tool_calls else "type IN ('user','assistant')"]
            params = []
            if args.session:
                clauses.append('session_id=?')
                params.append(args.session)
            if args.entry:
                clauses.append('id=?')
                params.append(args.entry)
            sql = ('SELECT id,session_id,type,seq,time_created,CASE WHEN length(data)<=? '
                   'THEN data ELSE NULL END FROM session_message WHERE ' + ' AND '.join(clauses)
                   + ' ORDER BY time_created DESC,seq DESC,id DESC LIMIT ?')
            rows = con.execute(sql, (MAX_RECORD, *params, args.max_rows + 1))
            for mid, sid, role, seq, stamp, raw in consume(rows, budget):
                record = read_json(raw)
                if args.tool_calls:
                    for item in reversed(oc2_tools(record, stamp, {'message_id': mid})):
                        hit = emit_tool(args, {'session_id': sid, 'seq': seq}, item)
                        if hit is not None:
                            results.append(hit)
                        if len(results) > args.limit:
                            break
                    if len(results) > args.limit:
                        break
                    continue
                body = record.get('text', '') if role == 'user' else text_content(record.get('content'))
                if not isinstance(body, str):
                    raise HistoryError('unsupported_oc2_message_content')
                hit = emit_message(args, budget, {'session_id': sid, 'message_id': mid, 'seq': seq},
                                   role, stamp, body)
                if hit is not None:
                    results.append(hit)
                if len(results) > args.limit:
                    break
    finally:
        con.close()
    if len(results) > args.limit:
        budget.note('result_limit_reached')
    after = store_freshness(store)
    if before != after or 'wal' in after:
        raise HistoryError('sqlite_store_changed_during_read:use_offline_snapshot')
    return results[:args.limit], {
        'reader': 'oc2-session-v2', 'scope': 'stored user/assistant messages; fork/revert boundaries not projected',
        'order': 'time_updated_desc' if args.action == 'list' else 'time_created_desc_seq_desc',
        'freshness': {'kind': 'sqlite_read_transaction_no_sidecar_index', 'before': before,
                      'after': after, 'files_changed_during_read': before != after},
    }


def oc2_tools(record, stamp, ref):
    """oc2 keeps call and outcome together in an assistant content block of type tool."""
    items = []
    for block in record.get('content') if isinstance(record.get('content'), list) else ():
        if not isinstance(block, dict) or block.get('type') != 'tool':
            continue
        state = block.get('state') if isinstance(block.get('state'), dict) else {}
        times = block.get('time') if isinstance(block.get('time'), dict) else {}
        item = {'call_id': block.get('id') if isinstance(block.get('id'), str) else None,
                'tool': block.get('name'), 'arguments': arguments_text(state.get('input')),
                'timestamp': times.get('created', stamp), 'ref': ref}
        status = state.get('status')
        if status in ('completed', 'error'):
            error = state.get('error') if isinstance(state.get('error'), dict) else {}
            text = tool_text(state.get('content'))
            if status == 'error' and isinstance(error.get('message'), str):
                text = (text + '\n' if text else '') + error['message']
            item.update({'result': text, 'failure': 'status=error' if status == 'error' else None,
                         'result_ref': ref})
        items.append(item)
    return items


def pocket_tools(args, rows, budget):
    """Pair calls and results within each conversation, across entries, by call ID."""
    by_conversation = {}
    for cid, eid, raw in consume(rows, budget):
        record = read_json(raw)
        model = record.get('model', [])
        if not isinstance(model, list):
            raise HistoryError('unsupported_pocket_model_record')
        events = by_conversation.setdefault(cid, [])
        for index, message in enumerate(model):
            if isinstance(message, dict):
                events.append((eid, index, message_events(
                    message, None, {'entry_id': eid, 'message_index': index})))
    attempts = []
    for cid, events in by_conversation.items():
        events.sort(key=lambda e: (e[0], e[1]))
        for item in join_tools([ev for _, _, batch in events for ev in batch]):
            ref = item.get('ref') or item.get('result_ref')
            attempts.append(((ref['entry_id'], ref['message_index']), cid, item))
    attempts.sort(key=lambda a: a[0], reverse=True)
    results = []
    for _, cid, item in attempts:
        hit = emit_tool(args, {'conversation_id': cid}, item)
        if hit is not None:
            results.append(hit)
        if len(results) > args.limit:
            break
    return results


def pocket(args, store, budget):
    results, listed = [], set()
    before = store_freshness(store)
    con = connect(store, POCKET, budget)
    try:
        clauses, params = [], []
        if args.session:
            clauses.append('conversation_id=?')
            params.append(int(args.session))
        if args.entry and not args.tool_calls:
            # In tool mode the entry may name either side of a call, so pairing needs the conversation.
            clauses.append('id=?')
            params.append(int(args.entry))
        if args.tool_calls:
            rows = con.execute('SELECT conversation_id,id,CASE WHEN length(record)<=? THEN record ELSE NULL END '
                               'FROM entries' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                               + ' ORDER BY id DESC LIMIT ?', (MAX_RECORD, *params, args.max_rows + 1))
            results = pocket_tools(args, rows, budget)
            clauses = None
        sql = ('SELECT conversation_id,id,CASE WHEN length(record)<=? THEN record ELSE NULL END '
               'FROM entries' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
               + ' ORDER BY id DESC LIMIT ?')
        for cid, eid, raw in (() if clauses is None else
                              consume(con.execute(sql, (MAX_RECORD, *params, args.max_rows + 1)), budget)):
            record = read_json(raw)
            model = record.get('model', [])
            if not isinstance(model, list):
                raise HistoryError('unsupported_pocket_model_record')
            if args.action == 'list' and cid in listed:
                continue
            for index in range(len(model) - 1, -1, -1):
                message = model[index]
                if not isinstance(message, dict):
                    continue
                ref = {'conversation_id': cid, 'entry_id': eid, 'message_index': index}
                hit = emit_message(args, budget, ref, message.get('role'), message.get('timestamp'),
                                   text_content(message.get('content')))
                if hit is None:
                    continue
                if args.action == 'list':
                    results.append({'conversation_id': cid, 'latest_text_entry_id': eid,
                                    'timestamp': message.get('timestamp')})
                    listed.add(cid)
                    break
                results.append(hit)
                if len(results) > args.limit:
                    break
            if len(results) > args.limit:
                break
    finally:
        con.close()
    if len(results) > args.limit:
        budget.note('result_limit_reached')
    after = store_freshness(store)
    if before != after or 'wal' in after:
        raise HistoryError('sqlite_store_changed_during_read:use_offline_snapshot')
    return results[:args.limit], {
        'reader': 'pocket-entries-model', 'scope': 'stored entries; active branch not verified; subagents separate',
        'order': 'entry_id_desc_then_message_index_desc',
        'freshness': {'kind': 'sqlite_read_transaction_no_sidecar_index', 'before': before,
                      'after': after, 'files_changed_during_read': before != after},
        'list_scope': 'conversations with stored user/assistant text, not the application session catalogue',
    }

"""The derived index: all formats, incremental updates, and originals left untouched."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_history.core import HistoryError
from agent_history.index import Index, default_stores, digest, open_store


def jsonl(path, rows, end='\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='') as stream:
        stream.write('\n'.join(json.dumps(r) for r in rows) + end)


def claude(role, text, session='claude-session'):
    return {'type': role, 'sessionId': session, 'timestamp': '2026-01-02T03:04:05Z',
            'message': {'role': role, 'content': [{'type': 'text', 'text': text}]}}


def digests(folder):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(folder).rglob('*') if p.is_file()}


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='history-index-test-', ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.stores = self.root / 'stores'
        self.stores.mkdir()

    def index(self, **stores):
        return Index(self.root / 'cache' / 'index.sqlite', stores)

    def hits(self, index, *words, host='all'):
        return [(r['host'], r['session'], r['role']) for r in index.search(list(words), host)]


class Formats(Case):
    def test_claude_keeps_text_and_drops_injected_and_meta_rows(self):
        store = self.stores / 'claude'
        jsonl(store / 'project' / 'a.jsonl', [
            claude('user', 'Where is the zebra config?'),
            claude('assistant', 'The zebra config is in settings.'),
            claude('user', '<system-reminder>zebra noise</system-reminder>'),
            {**claude('user', 'zebra meta row'), 'isMeta': True},
            {'type': 'assistant', 'sessionId': 'claude-session', 'message': {
                'role': 'assistant', 'content': [{'type': 'tool_use', 'name': 'zebra-tool'}]}},
            {'type': 'custom-title', 'customTitle': 'Zebra notes'}])
        index = self.index(claude=store)
        self.assertEqual(index.update(), {'claude': 2})
        found = index.search(['zebra'])
        self.assertEqual(len(found), 1)  # One best row per session.
        self.assertEqual(found[0]['title'], 'Zebra notes')
        self.assertEqual(found[0]['session'], 'claude-session')
        self.assertNotIn('noise', found[0]['text'])

    def test_codex_uses_session_meta_and_input_output_blocks(self):
        store = self.stores / 'codex'
        jsonl(store / '2026' / '01' / '02' / 'rollout-x.jsonl', [
            {'type': 'session_meta', 'payload': {'id': 'codex-id'}},
            {'type': 'response_item', 'timestamp': '2026-01-02T03:04:05Z', 'payload': {
                'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'walrus question'}]}},
            {'type': 'response_item', 'payload': {
                'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': '[external_agent_tool_call walrus'}]}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'walrus'}}])
        index = self.index(codex=store)
        self.assertEqual(index.update(), {'codex': 1})
        self.assertEqual(self.hits(index, 'walrus'), [('codex', 'codex-id', 'user')])

    def test_pi_and_omp_read_header_title_and_messages(self):
        for host in ('pi', 'omp'):
            jsonl(self.stores / host / 'proj' / f'2026_{host}.jsonl', [
                {'type': 'title', 'title': 'Otter ' + host},
                {'type': 'session', 'id': host + '-id', 'version': 3},
                {'type': 'message', 'id': 'a', 'parentId': None, 'timestamp': '2026-01-02T03:04:05Z',
                 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'otter text'}]}}])
        index = self.index(pi=self.stores / 'pi', omp=self.stores / 'omp')
        index.update()
        self.assertEqual({r['title'] for r in index.search(['otter'])}, {'Otter pi', 'Otter omp'})
        self.assertEqual(self.hits(index, 'otter', host='omp'), [('omp', 'omp-id', 'user')])

    def test_pocket_and_both_opencode_schemas(self):
        pocket = self.stores / 'pocket.sqlite'
        con = sqlite3.connect(pocket)
        con.execute('CREATE TABLE entries(id INTEGER PRIMARY KEY, conversation_id INTEGER, record TEXT)')
        con.execute('INSERT INTO entries VALUES(1,7,?)', (json.dumps({'model': [
            {'role': 'user', 'timestamp': 1700000000000, 'content': 'badger pocket'},
            {'role': 'toolResult', 'content': 'badger tool'}]}),))
        con.commit()
        con.close()
        folder = self.stores / 'opencode'
        folder.mkdir()
        con = sqlite3.connect(folder / 'new.db')
        con.executescript("""
            CREATE TABLE session_v2(id TEXT, title TEXT);
            CREATE TABLE session_message(session_id TEXT, type TEXT, time_created INTEGER, data TEXT);
            INSERT INTO session_v2 VALUES('oc-new','Badger v2');""")
        con.execute("INSERT INTO session_message VALUES('oc-new','user',1700000000000,?)",
                    (json.dumps({'text': 'badger v2 user'}),))
        con.execute("INSERT INTO session_message VALUES('oc-new','assistant',1700000000001,?)",
                    (json.dumps({'content': [{'type': 'text', 'text': 'badger v2 answer'}]}),))
        con.commit()
        con.close()
        con = sqlite3.connect(folder / 'old.db')
        con.executescript("""
            CREATE TABLE session(id TEXT, title TEXT);
            CREATE TABLE message(id TEXT, data TEXT);
            CREATE TABLE part(session_id TEXT, message_id TEXT, time_created INTEGER, data TEXT);
            INSERT INTO session VALUES('oc-old','Badger parts');""")
        con.execute("INSERT INTO message VALUES('m1',?)", (json.dumps({'role': 'user'}),))
        con.execute("INSERT INTO part VALUES('oc-old','m1',1700000000000,?)",
                    (json.dumps({'type': 'text', 'text': 'badger part'}),))
        con.execute("INSERT INTO part VALUES('oc-old','m1',1700000000000,?)",
                    (json.dumps({'type': 'text', 'text': 'badger hidden', 'synthetic': True}),))
        con.commit()
        con.close()
        index = self.index(pocket=pocket, opencode=folder)
        self.assertEqual(index.update(), {'pocket': 1, 'opencode': 3})
        self.assertEqual({r['title'] for r in index.search(['badger'], 'opencode')},
                         {'Badger v2', 'Badger parts'})
        self.assertEqual(self.hits(index, 'badger', host='pocket'), [('pocket', '7', 'user')])


class Updates(Case):
    def test_reads_only_new_lines_and_waits_for_a_complete_line(self):
        file = self.stores / 'claude' / 'p' / 'a.jsonl'
        jsonl(file, [claude('user', 'first heron')])
        index = self.index(claude=self.stores / 'claude')
        self.assertEqual(index.update(), {'claude': 1})
        self.assertEqual(index.update(), {'claude': 0})
        partial = json.dumps(claude('assistant', 'second heron'))
        with file.open('a', encoding='utf-8', newline='') as stream:
            stream.write(partial[:20])
        self.assertEqual(index.update(), {'claude': 0})
        with file.open('a', encoding='utf-8', newline='') as stream:
            stream.write(partial[20:] + '\n')
        self.assertEqual(index.update(), {'claude': 1})
        self.assertEqual(index.counts(), {'claude': 2})

    def test_rewritten_and_deleted_files_leave_no_stale_text(self):
        file = self.stores / 'claude' / 'p' / 'a.jsonl'
        jsonl(file, [claude('user', 'a long line about a stale pelican in the old file')])
        index = self.index(claude=self.stores / 'claude')
        index.update()
        file.write_text(json.dumps(claude('user', 'new ibis')) + '\n', encoding='utf-8')
        index.update()
        self.assertEqual(index.search(['pelican']), [])
        self.assertEqual(len(index.search(['ibis'])), 1)
        file.unlink()
        index.update()
        self.assertEqual(index.counts(), {})

    def test_malformed_lines_and_a_missing_store_do_not_stop_the_update(self):
        file = self.stores / 'claude' / 'p' / 'a.jsonl'
        file.parent.mkdir(parents=True)
        file.write_text('not json\n[1]\n' + json.dumps(claude('user', 'good lemur')) + '\n', encoding='utf-8')
        index = self.index(claude=self.stores / 'claude', codex=self.stores / 'missing')
        self.assertEqual(index.update(), {'claude': 1, 'codex': 0})

    def test_stored_title_wins_and_first_user_line_is_the_fallback(self):
        file = self.stores / 'claude' / 'p' / 'a.jsonl'
        jsonl(file, [claude('user', '\n  Fix the login page\nmore detail')])
        index = self.index(claude=self.stores / 'claude')
        index.update()
        self.assertEqual(index.search(['login'])[0]['title'], 'Fix the login page')
        jsonl(file, [{'type': 'custom-title', 'customTitle': 'Login work'}])
        index.update()
        self.assertEqual(index.search(['login'])[0]['title'], 'Login work')


class PocketTitles(Case):
    def pocket(self, name='pocket.sqlite', items=None, wal=False):
        path = self.stores / name
        con = sqlite3.connect(path)
        self.addCleanup(con.close)
        if wal:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA wal_autocheckpoint=0')
        con.executescript('''
            CREATE TABLE entries(id INTEGER PRIMARY KEY, conversation_id INTEGER, record TEXT);
            CREATE TABLE documents(id INTEGER PRIMARY KEY, kind TEXT);
            CREATE TABLE document_revisions(document_id INTEGER, seq INTEGER, kind TEXT, content TEXT);
        ''')
        con.execute('INSERT INTO entries VALUES(1,7,?)',
                    (json.dumps({'model': [{'role': 'user', 'content': 'ordinary conversation'}]}),))
        con.execute('INSERT INTO documents VALUES(1,?)', ('"pocket.sessions"',))
        values = {'7': {'title': 'Original kingfisher title', 'createdAt': 1700000000000}} if items is None else items
        con.execute('INSERT INTO document_revisions VALUES(1,1,?,?)',
                    ('base', json.dumps({'items': values})))
        con.commit()
        if not wal:
            con.close()
        return path, con

    def revision(self, path, sequence, data, kind='delta'):
        con = sqlite3.connect(path)
        try:
            con.execute('INSERT INTO document_revisions VALUES(1,?,?,?)',
                        (sequence, kind, json.dumps(data)))
            con.commit()
        finally:
            con.close()

    def test_replays_base_field_and_item_deltas_and_searches_title_only_sessions(self):
        path, _con = self.pocket(items={
            '7': {'title': 'Old sandpiper'}, '8': {'title': 'Remote curlew catalogue'}})
        self.revision(path, 2, [
            ['s', ['items', '7', 'title'], 'Current sandpiper catalogue'],
            ['s', ['items', '7', 'updatedAt'], 1700000001000],
            ['s', ['items', '9'], {'title': 'Added osprey catalogue'}],
            ['s', ['updatedAt'], 1700000001000]])
        before = digests(self.stores)
        index = self.index(pocket=path)
        self.assertEqual(index.update(), {'pocket': 4})
        self.assertEqual(index.skipped, {})
        self.assertEqual(index.search(['Old sandpiper']), [])
        hit = index.search(['Current sandpiper'])[0]
        self.assertEqual((hit['host'], hit['session'], hit['role']), ('pocket', '7', 'title'))
        self.assertEqual(hit['timestamp'], 1700000001000)
        self.assertEqual(index.expand(hit['ref'])['text'], 'Current sandpiper catalogue')
        self.assertEqual(index.search(['ordinary'])[0]['title'], 'Current sandpiper catalogue')
        self.assertEqual(index.search(['curlew'])[0]['session'], '8')
        self.assertEqual(index.search(['osprey'])[0]['session'], '9')
        self.assertEqual(digests(self.stores), before)

    def test_rename_replaces_searchable_text_and_invalidates_the_old_reference(self):
        path, _con = self.pocket()
        index = self.index(pocket=path)
        index.update()
        ref = index.search(['kingfisher'])[0]['ref']
        self.assertEqual(index.update(), {'pocket': 0})
        self.assertEqual(index.search(['kingfisher'])[0]['ref'], ref)
        self.revision(path, 2, [['s', ['items', '7', 'title'], 'Renamed albatross title']])
        index.update()
        self.assertEqual(index.search(['kingfisher']), [])
        hit = index.search(['albatross'])[0]
        self.assertEqual(hit['title'], 'Renamed albatross title')
        self.assertEqual(index.search(['ordinary'])[0]['title'], 'Renamed albatross title')
        with self.assertRaisesRegex(HistoryError, 'stale_reference'):
            index.expand(ref)
        self.assertEqual(index.expand(hit['ref'])['text'], 'Renamed albatross title')

    def test_item_removal_restores_the_first_user_line_fallback(self):
        path, _con = self.pocket()
        index = self.index(pocket=path)
        index.update()
        self.revision(path, 2, [['s', ['items', '7'], None]])
        index.update()
        self.assertEqual(index.search(['kingfisher']), [])
        hit = index.search(['ordinary'])[0]
        self.assertEqual(hit['title'], 'ordinary conversation')
        self.assertEqual(hit['role'], 'user')

    def test_new_base_and_items_replacement_remove_stale_title_only_sessions(self):
        path, _con = self.pocket(items={'8': {'title': 'Stale tern catalogue'}})
        index = self.index(pocket=path)
        index.update()
        self.revision(path, 2, {'items': {'9': {'title': 'Fresh gull catalogue'}}}, kind='base')
        index.update()
        self.assertEqual(index.search(['tern']), [])
        self.assertEqual(index.search(['gull'])[0]['session'], '9')
        self.revision(path, 3, [['s', ['items'], {'10': {'title': 'Current lark catalogue'}}]])
        index.update()
        self.assertEqual(index.search(['gull']), [])
        self.assertEqual(index.search(['lark'])[0]['session'], '10')

    def test_invalid_title_revision_is_reported_as_a_skipped_source(self):
        path, _con = self.pocket()
        self.revision(path, 2, [['unknown', ['items'], {}]])
        index = self.index(pocket=path)
        self.assertEqual(index.update(), {})
        self.assertIn('unsupported_pocket_title_operation', index.skipped['pocket'])
        self.assertEqual(index.search(['kingfisher']), [])

    def test_live_wal_title_replay_sees_uncheckpointed_commits(self):
        path, writer = self.pocket(wal=True)
        index = self.index(pocket=path)
        index.update()
        writer.execute('INSERT INTO document_revisions VALUES(1,2,?,?)',
                       ('delta', json.dumps([['s', ['items', '7', 'title'], 'Live wagtail catalogue']])))
        writer.commit()
        self.assertTrue(Path(str(path) + '-wal').exists())
        self.assertTrue(Path(str(path) + '-shm').exists())
        before = {p: p.read_bytes() for p in (path, Path(str(path) + '-wal'))}
        index.update()
        self.assertEqual(index.skipped, {})
        self.assertEqual(index.search(['kingfisher']), [])
        self.assertEqual(index.search(['wagtail'])[0]['title'], 'Live wagtail catalogue')
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_pocket_cache_and_titles_are_isolated_by_store(self):
        private, _con = self.pocket('private.sqlite', {'7': {'title': 'Private nightjar catalogue'}})
        allowed, _con = self.pocket('allowed.sqlite', {'7': {'title': 'Allowed goldfinch catalogue'}})
        first = self.index(pocket=private)
        first.update()
        private_ref = first.search(['ordinary'])[0]['ref']
        second = self.index(pocket=allowed)
        second.update()
        self.assertEqual(second.search(['nightjar']), [])
        self.assertEqual(second.search(['ordinary'])[0]['title'], 'Allowed goldfinch catalogue')
        self.assertEqual(second.counts(), {'pocket': 2})
        with self.assertRaisesRegex(HistoryError, 'stale_reference'):
            second.expand(private_ref)
        self.assertEqual(first.search(['ordinary'])[0]['title'], 'Private nightjar catalogue')
        self.assertEqual(first.counts(), {'pocket': 2})


class Authorization(Case):
    def test_cached_hosts_are_hidden_from_search_counts_and_expansion(self):
        claude_store = self.stores / 'claude'
        pi_store = self.stores / 'pi'
        jsonl(claude_store / 'a.jsonl', [claude('user', 'authorized zebra')])
        jsonl(pi_store / 'a.jsonl', [
            {'type': 'session', 'id': 'private-pi'},
            {'type': 'message', 'message': {'role': 'user', 'content': 'zz private narwhal'}}])
        broad = self.index(claude=claude_store, pi=pi_store)
        broad.update()
        private_ref = broad.search(['narwhal'])[0]['ref']
        narrow = self.index(claude=claude_store)
        self.assertEqual(narrow.search(['narwhal']), [])
        self.assertEqual(narrow.search(['zz']), [])
        self.assertEqual(narrow.counts(), {'claude': 1})
        with self.assertRaises(HistoryError):
            narrow.search(['narwhal'], host='pi')
        with self.assertRaisesRegex(HistoryError, 'stale_reference'):
            narrow.expand(private_ref)
        self.assertEqual(len(narrow.search(['zebra'])), 1)

    def test_same_host_store_switch_does_not_leak_rows_titles_or_delete_other_scopes(self):
        private, allowed = self.stores / 'private', self.stores / 'allowed'
        jsonl(private / 'a.jsonl', [claude('user', 'zz classified nightingale', session='same'),
                                 {'type': 'custom-title', 'customTitle': 'Private title'}])
        jsonl(allowed / 'a.jsonl', [claude('user', 'visible goldcrest', session='same'),
                                 {'type': 'custom-title', 'customTitle': 'Allowed title'}])
        first = self.index(claude=private)
        first.update()
        private_ref = first.search(['classified'])[0]['ref']
        second = self.index(claude=allowed)
        self.assertEqual(second.search(['classified']), [])
        self.assertEqual(second.counts(), {})
        second.update()
        second.update()  # Cleanup must not delete the other configuration's sources.
        self.assertEqual(second.search(['classified']), [])
        self.assertEqual(second.search(['zz']), [])
        self.assertEqual(second.search(['goldcrest'])[0]['title'], 'Allowed title')
        self.assertEqual(second.counts(), {'claude': 1})
        with self.assertRaisesRegex(HistoryError, 'stale_reference'):
            second.expand(private_ref)
        first.update()
        self.assertEqual(first.search(['nightingale'])[0]['title'], 'Private title')
        self.assertEqual(first.counts(), {'claude': 1})

    def test_changing_the_configured_store_revokes_cached_references(self):
        private, allowed = self.stores / 'private', self.stores / 'allowed'
        jsonl(private / 'a.jsonl', [claude('user', 'private falcon')])
        jsonl(allowed / 'a.jsonl', [claude('user', 'allowed swallow')])
        index = self.index(claude=private)
        index.update()
        ref = index.search(['falcon'])[0]['ref']
        index.stores['claude'] = allowed
        self.assertEqual(index.search(['falcon']), [])
        self.assertEqual(index.counts(), {})
        with self.assertRaisesRegex(HistoryError, 'stale_reference'):
            index.expand(ref)
        index.update()
        self.assertEqual(index.search(['swallow'])[0]['title'], 'allowed swallow')
        self.assertEqual(index.counts(), {'claude': 1})

    def test_legacy_unscoped_rows_and_titles_are_not_authorization_evidence(self):
        store = self.stores / 'claude'
        jsonl(store / 'a.jsonl', [claude('user', 'current text')])
        index = self.index(claude=store)
        con = index.writer()
        try:
            sid = con.execute('INSERT INTO src(key,host) VALUES(?,?)',
                              (str(store / 'a.jsonl'), 'claude')).lastrowid
            body = 'legacy secret text'
            row = con.execute('INSERT INTO docs(host,sess,role,ts,src,ext,text) VALUES(?,?,?,?,?,?,?)',
                              ('claude', 'claude-session', 'user', 0, sid, 0, body)).lastrowid
            con.execute('INSERT INTO titles VALUES(?,?,?)',
                        ('claude', 'claude-session', 'Legacy secret title'))
            con.commit()
        finally:
            con.close()
        self.assertEqual(index.search(['legacy']), [])
        self.assertEqual(index.counts(), {})
        with self.assertRaisesRegex(HistoryError, 'stale_reference'):
            index.expand(f'{row}:{digest(body)}:0')
        index.update()
        self.assertEqual(index.search(['current'])[0]['title'], 'current text')
        self.assertEqual(index.counts(), {'claude': 1})

    def test_demo_cache_does_not_authorize_real_store_rows_or_the_reverse(self):
        demo = self.index()
        demo.add_messages([('claude', 'demo', 'Demo title', 'user', 1, 'demo moorhen')])
        ref = demo.search(['moorhen'])[0]['ref']
        store = self.stores / 'claude'
        jsonl(store / 'a.jsonl', [claude('user', 'real linnet')])
        configured = self.index(claude=store)
        self.assertEqual(configured.search(['moorhen']), [])
        with self.assertRaisesRegex(HistoryError, 'stale_reference'):
            configured.expand(ref)
        configured.update()
        self.assertEqual(demo.search(['linnet']), [])
        self.assertEqual(demo.counts(), {'claude': 1})
        self.assertEqual(configured.counts(), {'claude': 1})


class CachePaths(Case):
    def test_cache_directory_inside_store_is_refused_before_files_are_created(self):
        store = self.stores / 'claude'
        jsonl(store / 'a.jsonl', [claude('user', 'unchanged transcript')])
        before = digests(self.stores)
        cache = store / 'unsafe-cache'
        with self.assertRaisesRegex(HistoryError, 'cache_inside_history_store'):
            Index(cache / 'index.sqlite', {'claude': store})
        self.assertFalse(cache.exists())
        self.assertEqual(digests(self.stores), before)

    def test_directory_with_a_database_suffix_is_still_protected(self):
        store = self.stores / 'history.db'
        store.mkdir()
        with self.assertRaisesRegex(HistoryError, 'cache_inside_history_store'):
            Index(store / 'cache' / 'index.sqlite', {'claude': store})
        self.assertFalse((store / 'cache').exists())

    def test_original_database_and_its_sidecars_cannot_be_the_index(self):
        original = self.stores / 'pocket.sqlite'
        con = sqlite3.connect(original)
        con.execute('CREATE TABLE original(value TEXT)')
        con.commit()
        con.close()
        before = original.read_bytes()
        for path in (original, Path(str(original) + '-wal'), Path(str(original) + '-shm')):
            with self.subTest(path=path):
                with self.assertRaisesRegex(HistoryError, 'cache_aliases_history_store'):
                    Index(path, {'pocket': original})
        self.assertEqual(original.read_bytes(), before)
        self.assertFalse(Path(str(original) + '-wal').exists())
        self.assertFalse(Path(str(original) + '-shm').exists())

    def test_hard_link_alias_of_a_directory_store_file_is_refused(self):
        original = self.stores / 'original.jsonl'
        jsonl(original, [claude('user', 'do not overwrite')])
        alias = self.root / 'alias.sqlite'
        try:
            os.link(original, alias)
        except OSError as exc:
            self.skipTest('hard links unavailable: ' + str(exc))
        before = original.read_bytes()
        with self.assertRaisesRegex(HistoryError, 'cache_has_hard_link_alias'):
            Index(alias, {'claude': self.stores})
        self.assertEqual(original.read_bytes(), before)

    def test_writer_rechecks_a_changed_path_before_creating_a_directory(self):
        store = self.stores / 'claude'
        store.mkdir()
        index = self.index(claude=store)
        index.path = store / 'unsafe-cache' / 'index.sqlite'
        with self.assertRaisesRegex(HistoryError, 'cache_inside_history_store'):
            index.writer()
        self.assertFalse(index.path.parent.exists())

    def test_cache_sidecar_alias_of_an_original_is_refused(self):
        original = self.stores / 'original.jsonl'
        jsonl(original, [claude('user', 'protected data')])
        cache = self.root / 'index.sqlite'
        alias = Path(str(cache) + '-wal')
        try:
            os.link(original, alias)
        except OSError as exc:
            self.skipTest('hard links unavailable: ' + str(exc))
        before = original.read_bytes()
        with self.assertRaisesRegex(HistoryError, 'cache_has_hard_link_alias'):
            Index(cache, {'claude': self.stores})
        self.assertFalse(cache.exists())
        self.assertEqual(original.read_bytes(), before)


class Originals(Case):
    def sqlite_store(self, wal):
        path = self.stores / 'pocket.sqlite'
        con = sqlite3.connect(path)
        if wal:
            con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE entries(id INTEGER PRIMARY KEY, conversation_id INTEGER, record TEXT)')
        con.execute('INSERT INTO entries VALUES(1,7,?)',
                    (json.dumps({'model': [{'role': 'user', 'content': 'stoat text'}]}),))
        con.commit()
        return path, con

    def test_closed_stores_are_byte_identical_with_no_side_files(self):
        path, con = self.sqlite_store(False)
        con.close()
        jsonl(self.stores / 'claude' / 'p' / 'a.jsonl', [claude('user', 'stoat line')])
        before = digests(self.stores)
        index = self.index(pocket=path, claude=self.stores / 'claude')
        index.update()
        self.assertEqual(digests(self.stores), before)
        self.assertEqual(len(index.search(['stoat'])), 2)

    def test_live_wal_database_shows_uncheckpointed_rows(self):
        path, con = self.sqlite_store(True)
        self.addCleanup(con.close)
        self.assertTrue(Path(str(path) + '-wal').exists())
        index = self.index(pocket=path)
        self.assertEqual(index.update(), {'pocket': 1})
        self.assertEqual(index.skipped, {})

    def test_wal_without_shm_is_reported_and_creates_nothing(self):
        path, con = self.sqlite_store(False)
        con.close()
        Path(str(path) + '-wal').write_bytes(b'')
        index = self.index(pocket=path)
        self.assertEqual(index.update(), {})
        self.assertIn('wal_without_shm', index.skipped['pocket'])
        self.assertFalse(Path(str(path) + '-shm').exists())

    def test_store_connection_rejects_writes(self):
        path, con = self.sqlite_store(False)
        con.close()
        source = open_store(path)
        self.addCleanup(source.close)
        with self.assertRaises(sqlite3.Error):
            source.execute('DELETE FROM entries')


class Searching(Case):
    def fill(self, *texts):
        jsonl(self.stores / 'claude' / 'p' / 'a.jsonl',
              [claude('user', text, f's{n}') for n, text in enumerate(texts)])
        index = self.index(claude=self.stores / 'claude')
        index.update()
        return index

    def test_literal_syntax_characters_are_not_operators(self):
        index = self.fill('say "quoted* text" OR (NOT) here', '100%_done and c:\\path')
        for word in ('"quoted*', '(NOT)', 'OR (', 'c:\\p'):
            self.assertEqual(len(index.search([word])), 1, word)
        self.assertEqual(len(index.search(['%_'])), 1)
        self.assertEqual(index.search(['_%']), [])

    def test_short_query_falls_back_and_returns_one_row_per_session(self):
        index = self.fill('ab first', 'ab second')
        self.assertEqual(len(index.search(['ab'])), 2)

    def test_unicode_offsets_point_at_the_original_text(self):
        index = self.fill('Straße İstanbul ' * 3 + 'target word')
        found = index.search(['TARGET'], context=7)[0]
        self.assertTrue(found['text'].endswith('target ') or 'target' in found['text'])
        self.assertEqual(found['text'][7:13], 'target')

    def test_expand_windows_offsets_and_stale_references(self):
        body = 'x' * 3000 + ' needle ' + 'y' * 3000
        index = self.fill(body)
        ref = index.search(['needle'])[0]['ref']
        part = index.expand(ref, 100)
        self.assertIn('needle', part['text'])
        self.assertEqual(part['next_offset'], part['end'])
        rest = index.expand(ref, 6000, part['next_offset'])
        self.assertIsNone(rest['next_offset'])
        self.assertEqual(body[part['start']:part['end']] + rest['text'], body[part['start']:])
        row, _digest, anchor = ref.split(':')
        with self.assertRaisesRegex(HistoryError, 'stale_reference'):
            index.expand(f'{row}:{"0" * 20}:{anchor}')
        with self.assertRaisesRegex(HistoryError, 'invalid_reference'):
            index.expand('1; DROP TABLE docs')

    def test_unknown_values_are_rejected(self):
        index = self.fill('anything')
        with self.assertRaises(HistoryError):
            index.search(['anything'], 'nobody')
        with self.assertRaises(HistoryError):
            index.search([])
        with self.assertRaises(HistoryError):
            Index(self.root / 'other.sqlite', {'nobody': self.stores})

    def test_default_stores_cover_every_source_under_the_home_given(self):
        stores = default_stores(self.root)
        self.assertEqual(set(stores), {'claude', 'codex', 'pi', 'omp', 'opencode', 'pocket'})
        self.assertTrue(all(str(p).startswith(str(self.root)) for p in stores.values()))


if __name__ == '__main__':
    unittest.main()

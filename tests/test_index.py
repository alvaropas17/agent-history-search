"""The derived index: all formats, incremental updates, and originals left untouched."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_history.core import HistoryError
from agent_history.index import Index, default_stores, open_store


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

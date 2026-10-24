"""tools/sqlite_snapshot.py on a synthetic live WAL database. No real stores."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sqlite_snapshot import snapshot  # noqa: E402

from agent_history.cli import parser, plan, read  # noqa: E402


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-history-snapshot-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.live = self.root / 'pocket.sqlite'
        # Keep a writer connection open so the -wal file exists, as with a running app.
        self.writer = sqlite3.connect(self.live)
        self.addCleanup(self.writer.close)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.execute('CREATE TABLE entries(id INTEGER,conversation_id INTEGER,record TEXT)')
        self.writer.execute('INSERT INTO entries VALUES (?,?,?)', (1, 5, json.dumps({'model': [
            {'role': 'user', 'content': 'snapshot question'}]})))
        self.writer.commit()
        self.assertTrue(Path(str(self.live) + '-wal').exists())

    def query(self, store):
        args = parser().parse_args(['search', '--host', 'pocket', '--store', str(store), '--query', 'snapshot'])
        return read(args, plan(args))

    def test_live_wal_is_refused_but_snapshot_reads(self):
        with self.assertRaisesRegex(Exception, 'wal_store_requires_consistent_offline_snapshot'):
            self.query(self.live)
        copy = self.root / 'snapshots' / 'pocket.sqlite'
        result = snapshot(self.live, copy)
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(Path(str(copy) + '-wal').exists())
        self.assertEqual(len(self.query(copy)['results']), 1)

    def test_never_overwrites_or_targets_source(self):
        copy = self.root / 'copy.sqlite'
        snapshot(self.live, copy)
        before = copy.read_bytes()
        with self.assertRaisesRegex(ValueError, 'destination_exists'):
            snapshot(self.live, copy)
        self.assertEqual(before, copy.read_bytes())
        with self.assertRaisesRegex(ValueError, 'destination_exists'):
            snapshot(self.live, self.live)
        with self.assertRaisesRegex(ValueError, 'source_not_found'):
            snapshot(self.root / 'absent.sqlite', self.root / 'new.sqlite')


if __name__ == '__main__':
    unittest.main()

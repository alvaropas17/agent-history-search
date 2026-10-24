"""Copy a live OpenCode or Pi Pocket SQLite database into a consistent, WAL-free snapshot.

Run by the owner, never by the reader. The source is opened read-only and copied
with SQLite's online backup API, which is consistent even while the app writes.
The destination must not exist; nothing is overwritten, deleted or migrated.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys


def snapshot(source, destination):
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().absolute()
    if not source.is_file():
        raise ValueError('source_not_found')
    if destination.exists() or Path(str(destination) + '-wal').exists():
        raise ValueError('destination_exists:choose_a_new_file')
    if destination.resolve() == source:
        raise ValueError('destination_is_source')
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            # The copy keeps the source's WAL mode; switch it so the reader finds no -wal file.
            dst.execute('PRAGMA journal_mode=DELETE')
            check = dst.execute('PRAGMA quick_check').fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if check != 'ok':
        raise ValueError('snapshot_failed_quick_check')
    if Path(str(destination) + '-wal').exists():
        raise ValueError('snapshot_left_wal_file')
    return {'status': 'ok', 'source': str(source), 'snapshot': str(destination),
            'bytes': destination.stat().st_size}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='Live database, e.g. opencode.db or pocket.sqlite')
    parser.add_argument('destination', help='New snapshot file; must not exist')
    args = parser.parse_args(argv)
    try:
        result = snapshot(args.source, args.destination)
    except (ValueError, OSError, sqlite3.Error) as exc:
        result = {'status': 'error', 'error': str(exc)}
    print(json.dumps(result))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())

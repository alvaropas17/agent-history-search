"""Inspect table/column names only; never read message contents or create a database."""
import argparse
import json
from pathlib import Path
import sqlite3

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('db', type=Path)
a = p.parse_args()
con = sqlite3.connect(a.db.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
try:
    con.execute('PRAGMA query_only=ON')
    tables = {}
    for name in ('session_v2', 'session_message', 'entries', 'conversations'):
        tables[name] = [(r[1], r[2]) for r in con.execute('PRAGMA table_info(' + name + ')')]
    print(json.dumps({'tables': tables}))
finally:
    con.close()

"""Preserve the original scaffold before replacing it; no existing backup is overwritten."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
dest = root / 'scaffold-backup-v1'
dest.mkdir(exist_ok=False)
for name in ('history_search.py', 'README.md', 'NOTICE.md', '.gitignore', 'docs', 'skill', 'tests'):
    source = root / name
    if source.is_dir():
        shutil.copytree(source, dest / name)
    else:
        shutil.copy2(source, dest / name)
print('Original scaffold preserved in scaffold-backup-v1 (excluded from distribution).')

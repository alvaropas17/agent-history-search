"""Verify the locally prepared ZIP and run its offline tests after extraction."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='history-distribution-verification-') as directory:
        dest = Path(directory)
        with zipfile.ZipFile(args.archive) as archive:
            for name in archive.namelist():
                path = Path(name)
                if path.is_absolute() or '..' in path.parts or not name.startswith('agent-history-search/'):
                    raise ValueError('Unsafe archive member')
            manifest = json.loads(archive.read('agent-history-search/MANIFEST.json'))
            expected = {'agent-history-search/' + name for name in manifest['files']}
            expected.add('agent-history-search/MANIFEST.json')
            if set(archive.namelist()) != expected:
                raise ValueError('Archive contains unexpected members')
            for name, digest in manifest['files'].items():
                if hashlib.sha256(archive.read('agent-history-search/' + name)).hexdigest() != digest:
                    raise ValueError('Manifest digest mismatch')
            archive.extractall(dest)
        proc = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                              cwd=dest / 'agent-history-search', capture_output=True, timeout=10)
        print(json.dumps({'archive_sha256': hashlib.sha256(args.archive.read_bytes()).hexdigest(),
                          'manifest_verified': True, 'extracted_test_exit_code': proc.returncode,
                          'test_summary': proc.stderr.decode('utf-8', errors='replace')[-1000:]}))
        return proc.returncode


if __name__ == '__main__':
    raise SystemExit(main())

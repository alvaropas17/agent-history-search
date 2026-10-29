"""Create an allowlisted local distribution candidate; never publish or overwrite."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'history_search.py',
    'history_mcp.py',
    'agent_history/__init__.py',
    'agent_history/mcp_server.py',
    'tests/test_mcp_server.py',
    'agent_history/__main__.py',
    'agent_history/cli.py',
    'agent_history/core.py',
    'agent_history/pi.py',
    'agent_history/transcripts.py',
    'tests/test_transcripts.py',
    'tests/test_tool_calls.py',
    'agent_history/sqlite_readers.py',
    'README.md',
    'NOTICE.md',
    'LICENSE',
    'docs/ADAPTERS.md',
    'docs/AUDIT.md',
    'docs/DISTRIBUTION.md',
    'docs/SCHEMAS.md',
    'docs/SETUP-PROMPT.md',
    'docs/POCKET.md',
    'skill/SKILL.md',
    'skill/references/usage.md',
    'tests/test_history_search.py',
    'tests/test_distribution.py',
    'tools/build_distribution.py',
    'tools/sqlite_snapshot.py',
    'tests/test_snapshot.py',
    'tests/test_query_variants.py',
    'tests/test_transcript_streaming.py',
    'tools/verify_distribution.py',
)


def build(output, root=ROOT):
    output = Path(output)
    checksum = Path(str(output) + '.sha256')
    if output.exists() or checksum.exists():
        raise FileExistsError('Distribution destination already exists; choose a new path')
    payloads = {}
    for name in FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or symlinked distribution source: ' + name)
        payloads[name] = path.read_bytes()
    manifest = {
        'name': 'agent-history-search', 'version': '0.3.0',
        'license_state': 'MIT approved',
        'license': 'MIT',
        'copyright': 'Copyright (c) 2026 alvaropas17',
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
    }
    payloads['MANIFEST.json'] = json.dumps(manifest, ensure_ascii=True, indent=2).encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payloads.items():
            info = zipfile.ZipInfo('agent-history-search/' + name, date_time=(2026, 10, 17, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with checksum.open('x', encoding='ascii') as stream:
        stream.write(digest + '  ' + output.name + '\n')
    return {'archive': str(output), 'sha256': digest, 'files': len(payloads),
            'license_state': manifest['license_state']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'agent-history-search-0.3.0.zip')
    args = parser.parse_args()
    print(json.dumps(build(args.output)))


if __name__ == '__main__':
    main()

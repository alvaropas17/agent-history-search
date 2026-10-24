"""The release builder must only include the public allowlist."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_builder', ROOT / 'tools' / 'build_distribution.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Distribution(unittest.TestCase):
    def test_zip_manifest_allowlist_and_extracted_cli(self):
        with tempfile.TemporaryDirectory(prefix='history-release-test-') as directory:
            output = Path(directory) / 'candidate.zip'
            report = builder.build(output)
            self.assertEqual(report['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                prefix = 'agent-history-search/'
                expected = {prefix + name for name in builder.FILES} | {prefix + 'MANIFEST.json'}
                self.assertEqual(set(names), expected)
                for guide in ('docs/SETUP-PROMPT.md', 'docs/POCKET.md',
                              'skill/SKILL.md', 'skill/references/usage.md'):
                    self.assertIn(prefix + guide, names)
                pocket_guide = archive.read(prefix + 'docs/POCKET.md').decode('utf-8')
                self.assertIn('helper externo', pocket_guide)
                self.assertIn('no incluye una extensión nativa', pocket_guide)
                self.assertFalse(any('backup' in name or '.db' in name or '.sqlite' in name or '.jsonl' in name for name in names))
                manifest = json.loads(archive.read(prefix + 'MANIFEST.json'))
                self.assertEqual(manifest['version'], '0.2.2')
                self.assertEqual(manifest['license'], 'MIT')
                self.assertEqual(manifest['license_state'], 'MIT approved')
                self.assertEqual(manifest['copyright'], 'Copyright (c) 2026 alvaropas17')
                license_text = archive.read(prefix + 'LICENSE').decode('utf-8')
                self.assertIn(manifest['copyright'], license_text)
                self.assertNotIn(prefix + 'LICENSE-MIT.proposed.txt', names)
                self.assertNotIn('approval pending', archive.read(prefix + 'NOTICE.md').decode('utf-8'))
                for name, digest in manifest['files'].items():
                    self.assertEqual(digest, hashlib.sha256(archive.read(prefix + name)).hexdigest())
                archive.extractall(Path(directory) / 'unpacked')
            unpacked = Path(directory) / 'unpacked' / 'agent-history-search'
            proc = subprocess.run([sys.executable, '-B', 'history_search.py', 'list', '--host', 'pi',
                                   '--store', 'nonexistent-fixture', '--plan'], cwd=unpacked,
                                  capture_output=True, timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)['status'], 'plan')
            with self.assertRaises(FileExistsError):
                builder.build(output)

    def test_builder_does_not_include_arbitrary_files(self):
        self.assertNotIn('local-config.json', builder.FILES)
        self.assertNotIn('scaffold-backup-v1/history_search.py', builder.FILES)
        self.assertEqual(len(builder.FILES), len(set(builder.FILES)))


if __name__ == '__main__':
    unittest.main()

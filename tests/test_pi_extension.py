"""Pi extension loaded by Node against a synthetic store; skipped without Node >= 22.6."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from test_history_search import ROOT, Fixtures

DRIVER = """
import ext from %(ext)s;
let tool;
ext({ registerTool: (t) => { tool = t; } });
const out = [];
for (const args of JSON.parse(process.argv[2])) {
  try { out.push({ ok: true, text: (await tool.execute('id', args)).content[0].text }); }
  catch (error) { out.push({ ok: false, text: String(error.message) }); }
}
console.log(JSON.stringify({ name: tool.name, out }));
"""


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class PiExtension(Fixtures):
    def run_node(self, *calls):
        driver = self.root / 'driver.mjs'
        driver.write_text(DRIVER % {'ext': json.dumps((ROOT / 'pi-extension' / 'agent-history-search.ts').as_uri())})
        env = {**os.environ, 'AGENT_HISTORY_SEARCH_DIR': str(ROOT), 'AGENT_HISTORY_SEARCH_PYTHON': sys.executable,
               'AGENT_HISTORY_SEARCH_STORES': f'pi={self.pi}'}
        proc = subprocess.run(['node', '--experimental-strip-types', '--no-warnings', str(driver), json.dumps(calls)],
                              capture_output=True, text=True, env=env, timeout=60)
        if proc.returncode:
            self.skipTest('node cannot load TypeScript: ' + proc.stderr[-200:])
        return json.loads(proc.stdout)

    def test_registers_tool_and_searches_through_mcp_server(self):
        result = self.run_node({'action': 'search', 'query': 'question'},
                               {'action': 'search', 'query': 'x', 'store': '/etc'},
                               {'action': 'search', 'query': 'x', 'source': 'codex', 'reason': 'r'})
        self.assertEqual(result['name'], 'history_search')
        found, outside, other = result['out']
        self.assertTrue(found['ok'])
        self.assertEqual(json.loads(found['text'])['status'], 'ok')
        self.assertIn('session-one', found['text'])
        self.assertFalse(outside['ok'])
        self.assertIn('store_outside_authorized_roots', outside['text'])
        self.assertFalse(other['ok'])
        self.assertIn('source_not_authorized_for_this_server', other['text'])


if __name__ == '__main__':
    unittest.main()

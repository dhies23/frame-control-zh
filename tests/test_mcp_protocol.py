"""MCP parser failures must not disconnect the following stdio requests."""
import sandbox  # noqa: F401
import json
from pathlib import Path
import subprocess
import sys
import unittest


class ParseRecovery(unittest.TestCase):
    def test_invalid_line_returns_parse_error_then_serves_next_request(self):
        # This endpoint is never called: ping and tools/list are local methods.
        command = [sys.executable, str(Path(__file__).resolve().parents[1] /
                                      'ui/frame_mcp.py'), '--url', 'http://127.0.0.1:9']
        ping = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        listing = b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        nested = (b'{"jsonrpc":"2.0","id":3,"method":"ping","params":{"nested":' +
                  b'[' * 100000 + b'0' + b']' * 100000 + b'}}\n')
        for label, invalid in [('syntax', b'{broken}\n'), ('utf8', b'\xff\n'),
                               ('nesting', nested)]:
            with self.subTest(label=label):
                result = subprocess.run(command, input=ping + invalid + listing,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                replies = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(len(replies), 3)
                self.assertEqual(replies[0], {'jsonrpc': '2.0', 'id': 1, 'result': {}})
                self.assertEqual(replies[1], {'jsonrpc': '2.0', 'id': None, 'error': {
                    'code': -32700, 'message': 'Parse error'}})
                self.assertEqual(replies[2]['id'], 2)
                self.assertIn('status', [tool['name'] for tool in replies[2]['result']['tools']])
                self.assertEqual(result.stderr, b'')


if __name__ == '__main__':
    unittest.main()

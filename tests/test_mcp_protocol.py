"""MCP parser failures must not disconnect the following stdio requests."""
import sandbox  # noqa: F401
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ui'))
import frame_mcp as mcp  # noqa: E402


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


class Hardening(unittest.TestCase):
    def test_client_rejects_non_string_control_chars_bad_port_and_bad_key(self):
        for bad_url in (None, 123, 'http://127.0.0.1:47810\r\n', 'http://127.0.0.1:bad', 'http://127.0.0.1:99999'):
            with self.subTest(url=bad_url), self.assertRaises(ValueError):
                mcp.Client(bad_url)
        for bad_key in (None, 123, 'secret\r\nX-Inject: 1', 'secret\x00'):
            with self.subTest(key=bad_key), self.assertRaises(ValueError):
                mcp.Client('http://127.0.0.1:47810', key=bad_key)

    def test_call_handles_non_dict_action_result_without_crashing(self):
        for payload in (None, ['approvalPath'], 'ok'):
            client = mock.Mock(url='http://127.0.0.1:47810')
            client.request.return_value = payload
            res = mcp.call(client, 'power', {'action': 'reboot'})
            self.assertEqual(json.loads(res['content'][0]['text']), payload)

    def test_backend_uses_utf8_replace_and_does_not_hang_on_readline_error(self):
        proc = mock.Mock()
        proc.stdout.readline.side_effect = OSError('broken pipe')
        proc.wait.return_value = 0
        with mock.patch.object(mcp.subprocess, 'Popen', return_value=proc) as popen:
            with self.assertRaisesRegex(RuntimeError, 'failed to start'):
                with mcp.backend():
                    self.fail('should not yield')
        self.assertEqual(popen.call_args.kwargs.get('encoding'), 'utf-8')
        self.assertEqual(popen.call_args.kwargs.get('errors'), 'replace')

    def test_cli_invalid_url_returns_clean_error_without_traceback(self):
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'ui/frame_mcp.py'),
                   '--url', 'https://evil.example']
        result = subprocess.run(command, input=b'', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        err = result.stderr.decode('utf-8', errors='replace')
        self.assertIn('loopback', err)
        self.assertNotIn('Traceback', err)


if __name__ == '__main__':
    unittest.main()

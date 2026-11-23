"""Hardening tests for frame/android/steam_shortcuts.py."""
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('steam_shortcuts_test', ROOT / 'frame/android/steam_shortcuts.py')
shortcuts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shortcuts)


class SteamShortcutsHardeningTests(unittest.TestCase):
    def test_ws_handshake_eof_raises_and_closes_socket_instead_of_looping(self):
        fake_sock = Mock()
        fake_sock.recv.side_effect = [b'HTTP/1.1 101 Switching', b'']
        with patch.object(shortcuts.socket, 'create_connection', return_value=fake_sock):
            with self.assertRaises(EOFError):
                shortcuts.WS('ws://127.0.0.1:8080/devtools/page/1', timeout=5)
        fake_sock.close.assert_called_once()

        with self.assertRaises(ValueError):
            shortcuts.WS('http://127.0.0.1:8080/devtools/page/1', timeout=5)

    def test_ws_recv_handles_ping_and_close_frames_and_evaluate_closes_socket(self):
        ws = object.__new__(shortcuts.WS)
        ws.s = Mock()
        # Server sends a ping frame (opcode 0x9, payload b'hi'), then a text frame (opcode 0x1, payload b'{"id":1}')
        ping_frame = bytes([0x89, 2]) + b'hi'
        text_payload = b'{"id": 1, "result": {"result": {"value": 42}}}'
        text_frame = bytes([0x81, len(text_payload)]) + text_payload
        ws.rest = ping_frame + text_frame
        self.assertEqual(ws.recv(), text_payload.decode('utf-8'))
        # Must have sent a pong frame in reply to the ping
        ws.s.sendall.assert_called_once()
        self.assertEqual(ws.s.sendall.call_args[0][0][0], 0x8A)

        # Close frame (opcode 0x8, 2-byte binary status code 0x03 0xE8) raises EOFError cleanly
        ws.rest = bytes([0x88, 2, 0x03, 0xE8])
        with self.assertRaises(EOFError):
            ws.recv()

        # evaluate() must close WS even when JS raises SystemExit
        fake_ws = Mock()
        fake_ws.recv.return_value = json.dumps({'id': 1, 'result': {'exceptionDetails': {'text': 'boom'}}})
        with patch.object(shortcuts, 'target_ws', return_value='ws://127.0.0.1:8080/x'), \
                patch.object(shortcuts, 'WS', return_value=fake_ws):
            with self.assertRaises(SystemExit):
                shortcuts.evaluate('throw 1')
        fake_ws.close.assert_called_once()

    def test_render_cleans_up_partial_tmp_files_and_rejects_non_str_encoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / 'plan.json'
            plan.write_text('{}', encoding='utf-8')
            valid_png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 16).decode('ascii')
            # First slot writes grid.png.tmp, second slot fails with non-string encoded payload
            bad_result = {
                'images': {
                    'grid': ['png', valid_png],
                    'hero': ['png', 12345],
                    'logo': ['png', valid_png],
                    'wide': ['png', valid_png],
                    'icon': ['png', valid_png],
                },
                'warnings': [],
            }
            with patch.object(shortcuts, 'evaluate', return_value=bad_result):
                with self.assertRaises(ValueError):
                    shortcuts._render(str(plan), '', 'Test App', {})
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [], 'partial .tmp files must be removed on failure')


if __name__ == '__main__':
    unittest.main()

"""Hardening tests for assistant-on-frame.py and check-appimage-launch.py."""
import http.client
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assistant = _load('assistant_on_frame_test', 'assistant-on-frame.py')
appimage = _load('check_appimage_launch_test', 'check-appimage-launch.py')


class HelperScriptsHardeningTests(unittest.TestCase):
    def test_assistant_cleanup_tolerates_ssh_timeout_and_tunnel_oserror(self):
        tunnel = mock.Mock()
        tunnel.poll.return_value = None
        tunnel.terminate.side_effect = OSError('already gone')
        tunnel.wait.side_effect = [
            subprocess.TimeoutExpired('ssh', 10),
            subprocess.TimeoutExpired('ssh', 5),
        ]
        tunnel.kill.side_effect = OSError('already gone')

        probe_ok = mock.Mock(returncode=0)
        launch_ok = mock.Mock(returncode=0, stdout='log /tmp/panel-on-frame.abc123\n')
        run_calls = []

        def fake_run(cmd, *args, **kwargs):
            run_calls.append((cmd, kwargs))
            if len(run_calls) == 1:
                return probe_ok
            if len(run_calls) == 2:
                return launch_ok
            raise subprocess.TimeoutExpired(cmd, 20)

        fake_client_mod = mock.Mock()
        fake_client_mod.Client.return_value.request.return_value = {}
        stderr = io.StringIO()
        with mock.patch.object(assistant.sys, 'argv', ['assistant-on-frame.py']), \
             mock.patch.object(assistant.shutil, 'which', return_value='/usr/bin/zsh'), \
             mock.patch.dict(sys.modules, {'frame_mcp': fake_client_mod}), \
             mock.patch.object(assistant.signal, 'signal'), \
             mock.patch.object(assistant.subprocess, 'Popen', return_value=tunnel), \
             mock.patch.object(assistant.subprocess, 'run', side_effect=fake_run), \
             mock.patch.object(tunnel, 'wait', side_effect=[KeyboardInterrupt(), subprocess.TimeoutExpired('ssh', 10), subprocess.TimeoutExpired('ssh', 5)]), \
             mock.patch.object(assistant.sys, 'stderr', stderr):
            rc = assistant.main()

        self.assertEqual(rc, 0)
        self.assertIn('Cleanup failed', stderr.getvalue())
        # Panel launch subprocess.run must specify UTF-8 replace decoding
        self.assertEqual(run_calls[1][1].get('encoding'), 'utf-8')
        self.assertEqual(run_calls[1][1].get('errors'), 'replace')
        # Remote cleanup script must guard OSError on kill and unlink
        cleanup_script = run_calls[2][1].get('input', '')
        self.assertIn('except OSError:', cleanup_script)
        self.assertIn('isascii()', cleanup_script)

    def test_appimage_server_port_enforces_ascii_digits_range_and_utf8(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log_dir = root / 'config' / 'frame-control'
            log_dir.mkdir(parents=True)
            log_file = log_dir / 'server.log'
            # Invalid non-ASCII digits and out-of-range port must be ignored
            log_file.write_text('started --port ²³\nstarted --port 99999\nstarted --port 0\n', encoding='utf-8')
            self.assertIsNone(appimage.server_port(root))
            # Valid port after invalid bytes must be parsed
            log_file.write_bytes(b'\xff\xfe started --port 47810\n')
            self.assertEqual(appimage.server_port(root), 47810)

    @unittest.skipIf(sys.platform == 'win32', 'Linux AppImage launcher uses POSIX killpg/SIGKILL')
    def test_appimage_check_tolerates_http_exception_and_sigkill_oserror(self):
        with tempfile.TemporaryDirectory() as folder:
            fake_img = Path(folder) / 'FrameControl.AppImage'
            fake_img.write_bytes(b'appimage-bytes')
            fake_img.chmod(0o755)

            proc = mock.Mock()
            proc.pid = 424242
            proc.poll.return_value = None
            proc.wait.side_effect = [subprocess.TimeoutExpired('xvfb', 15), 0]

            class OkResponse:
                status = 200
                headers = {'Server': 'FrameControl/1.0'}

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

            opener_instance = mock.Mock()
            opener_instance.open.side_effect = [
                http.client.RemoteDisconnected('starting up'),
                OkResponse(),
            ]

            kill_calls = []

            def fake_killpg(pid, sig):
                kill_calls.append((pid, sig))
                if len(kill_calls) == 2:
                    raise ProcessLookupError('already exited before SIGKILL')

            with mock.patch.object(appimage.os, 'access', return_value=True), \
                 mock.patch.object(appimage.shutil, 'which', return_value='/usr/bin/xvfb-run'), \
                 mock.patch.object(appimage.subprocess, 'Popen', return_value=proc), \
                 mock.patch.object(appimage, 'server_port', return_value=47810), \
                 mock.patch.object(appimage, 'port_closed', return_value=True), \
                 mock.patch.object(appimage.urllib.request, 'build_opener', return_value=opener_instance), \
                 mock.patch.object(appimage.os, 'killpg', side_effect=fake_killpg, create=True), \
                 mock.patch.object(appimage.time, 'sleep'):
                appimage.check(fake_img)

            self.assertEqual(opener_instance.open.call_count, 2)
            self.assertEqual(len(kill_calls), 2)


if __name__ == '__main__':
    unittest.main()

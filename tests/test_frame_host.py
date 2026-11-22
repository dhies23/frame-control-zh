"""Captured OpenSSH output keeps working on Windows and POSIX hosts."""
import sandbox  # noqa: F401
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ui"))
import frame_host


class CapturedSSH(unittest.TestCase):
    def run_command(self, source, **kwargs):
        with mock.patch.object(frame_host, "WINDOWS", True):
            return frame_host.run_ssh([sys.executable, "-c", source], timeout=5, **kwargs)

    def test_binary_output_and_input(self):
        result = self.run_command("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); "
                                  "sys.stderr.buffer.write(b'error\\r\\n')",
                                  capture_output=True, input=b"data\x00\xff")
        self.assertEqual(result.stdout, b"data\x00\xff")
        self.assertEqual(result.stderr, b"error\r\n")

    def test_text_output_normalizes_newlines(self):
        result = self.run_command("import sys; sys.stdout.write(sys.stdin.read()); "
                                  "sys.stderr.buffer.write(b'first\\r\\nsecond\\rthird\\n')",
                                  capture_output=True, input="hello\n", text=True)
        self.assertEqual(result.stdout, "hello\n")
        self.assertEqual(result.stderr, "first\nsecond\nthird\n")

    def test_explicit_encoding_and_errors(self):
        result = self.run_command("import sys; sys.stderr.buffer.write(b'\\xe9\\xff')",
                                  capture_output=True, encoding="ascii", errors="replace")
        self.assertEqual(result.stderr, "\ufffd\ufffd")

    def test_check_preserves_error_output(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.run_command("import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
                             capture_output=True, text=True, check=True)
        self.assertEqual(caught.exception.returncode, 7)
        self.assertEqual(caught.exception.stdout, "out\n")
        self.assertEqual(caught.exception.stderr, "err\n")

    def test_timeout_preserves_partial_stderr(self):
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            with mock.patch.object(frame_host, "WINDOWS", True):
                frame_host.run_ssh([sys.executable, "-c", "import sys, time; "
                                    "sys.stderr.write('waiting'); sys.stderr.flush(); time.sleep(10)"],
                                   capture_output=True, text=True, timeout=1)
        self.assertEqual(caught.exception.stderr, b"waiting")

    def test_streamed_stdout_is_kept_separate(self):
        with tempfile.TemporaryFile() as output:
            result = self.run_command("import sys; sys.stdout.buffer.write(b'file'); "
                                      "sys.stderr.buffer.write(b'error')",
                                      stdout=output, stderr=subprocess.PIPE)
            output.seek(0)
            self.assertEqual(output.read(), b"file")
        self.assertIsNone(result.stdout)
        self.assertEqual(result.stderr, b"error")

    def test_uncaptured_windows_call_is_unchanged(self):
        with mock.patch.object(frame_host, "WINDOWS", True), mock.patch.object(subprocess, "run") as run:
            frame_host.run_ssh(["ssh", "-V"], stderr=subprocess.DEVNULL, timeout=5)
        run.assert_called_once_with(["ssh", "-V"], stderr=subprocess.DEVNULL, timeout=5)

    def test_posix_call_is_unchanged(self):
        with mock.patch.object(frame_host, "WINDOWS", False), mock.patch.object(subprocess, "run") as run:
            frame_host.run_ssh(["ssh", "-V"], capture_output=True, check=True, timeout=5)
        run.assert_called_once_with(["ssh", "-V"], capture_output=True, check=True, timeout=5)

    def test_capture_rejects_explicit_streams(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), self.assertRaises(ValueError):
                self.run_command("", capture_output=True, **{stream: subprocess.DEVNULL})

    @unittest.skipUnless(shutil.which("ssh"), "needs OpenSSH")
    def test_real_ssh_failure_returns_stderr_without_hanging(self):
        result = frame_host.run_ssh(["ssh", "-F", os.devnull, "-o", "BatchMode=yes",
                                    "-o", "ConnectTimeout=2", "frame-control-test.invalid", "true"],
                                   capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=5)
        self.assertEqual(result.returncode, 255)
        self.assertIn("Could not resolve hostname", result.stderr)


class HostHelpers(unittest.TestCase):
    def test_clipboard_text_and_set_clipboard_fall_through_on_timeout_and_oserror(self):
        flags = {"MAC": False, "WINDOWS": False, "LINUX": True}
        seen = []

        def read_run(cmd, **kw):
            seen.append(cmd[0])
            if cmd[0] == "wl-paste":
                raise subprocess.TimeoutExpired(cmd, 10)
            return subprocess.CompletedProcess(cmd, 0, b"hello", b"")

        with mock.patch.multiple(frame_host, **flags), \
             mock.patch.object(frame_host.shutil, "which", lambda n: f"/bin/{n}"), \
             mock.patch.object(frame_host.subprocess, "run", side_effect=read_run):
            self.assertEqual(frame_host.clipboard_text(), "hello")
        self.assertEqual(seen, ["wl-paste", "xclip"])

        seen_set = []

        def write_run(cmd, **kw):
            seen_set.append(cmd[0])
            if cmd[0] == "wl-copy":
                raise OSError("exec failed")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with mock.patch.multiple(frame_host, **flags), \
             mock.patch.object(frame_host.shutil, "which", lambda n: f"/bin/{n}"), \
             mock.patch.object(frame_host.subprocess, "run", side_effect=write_run):
            frame_host.set_clipboard_text("hello")
        self.assertEqual(seen_set, ["wl-copy", "xclip"])

    def test_adb_rejects_directory_in_adb_env(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.dict(os.environ, {"ADB": d}), \
             mock.patch.object(frame_host, "which", return_value="/usr/bin/adb"):
            self.assertEqual(frame_host.adb(), "/usr/bin/adb")

    def test_open_terminal_rejects_unsafe_windows_title_and_macos_newlines_or_timeout(self):
        with mock.patch.multiple(frame_host, MAC=False, WINDOWS=True, LINUX=False):
            for bad_title in ('Frame "Control"', "Frame %PATH%", "Frame\nControl"):
                with self.assertRaises(frame_host.HostError):
                    frame_host.open_terminal(["ssh", "frame"], title=bad_title)
        with mock.patch.multiple(frame_host, MAC=True, WINDOWS=False, LINUX=False):
            with self.assertRaises(frame_host.HostError):
                frame_host.open_terminal(["echo", "line1\nline2"])
            with mock.patch.object(frame_host.subprocess, "run",
                                   side_effect=subprocess.TimeoutExpired(["osascript"], 15)) as run:
                with self.assertRaises(frame_host.HostError):
                    frame_host.open_terminal(["ssh", "frame"])
                self.assertEqual(run.call_args.kwargs.get("timeout"), 15)

    def test_open_rdp_brackets_ipv6_for_remmina_and_rejects_control_chars(self):
        with mock.patch.multiple(frame_host, MAC=False, WINDOWS=False, LINUX=True), \
             mock.patch.object(frame_host, "check_rdp"), \
             mock.patch.object(frame_host, "which", lambda n: "/usr/bin/remmina" if n == "remmina" else None), \
             mock.patch.object(frame_host, "_spawn") as spawn:
            frame_host.open_rdp("frame", host="fe80::1")
            spawn.assert_called_once_with(["remmina", "-c", "rdp://steamos@[fe80::1]"])
            with self.assertRaises(frame_host.HostError):
                frame_host.open_rdp("frame", host="192.168.1.1\nfoo")

    def test_open_steam_link_falls_back_on_flatpak_timeout(self):
        with mock.patch.multiple(frame_host, MAC=False, WINDOWS=False, LINUX=True), \
             mock.patch.object(frame_host, "which", lambda n: "/usr/bin/flatpak" if n == "flatpak" else None), \
             mock.patch.object(frame_host.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(["flatpak"], 10)), \
             mock.patch.object(frame_host, "open_url") as open_url:
            msg = frame_host.open_steam_link()
            self.assertIn("download page", msg)
            open_url.assert_called_once()

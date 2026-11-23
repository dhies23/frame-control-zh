"""The OBS relay must preserve transport packets and keep its output local."""
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'obs-headset-source.py'
spec = importlib.util.spec_from_file_location('obs_headset_source', SCRIPT)
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)


class Receiver:
    def __init__(self):
        self.chunks = []

    def send(self, data):
        self.chunks.append(data)


class ObsSourceTests(unittest.TestCase):
    def test_desktop_packages_the_helper_as_a_required_resource(self):
        root = SCRIPT.parents[1]
        build = json.loads((root / 'app' / 'package.json').read_text())['build']
        scripts = next(item for item in build['extraResources'] if item['to'] == 'scripts')
        self.assertIn(SCRIPT.name, scripts['filter'])
        self.assertIn('scripts/' + SCRIPT.name, (root / 'app' / 'build' / 'check-resources.js').read_text())

    def test_relay_keeps_whole_transport_packets(self):
        stream = bytes(range(188)) * 101
        receiver = Receiver()
        self.assertEqual(obs.relay(io.BytesIO(stream), receiver), len(stream))
        self.assertEqual(b''.join(receiver.chunks), stream)
        self.assertTrue(all(len(chunk) % 188 == 0 and len(chunk) <= 1316
                            for chunk in receiver.chunks))

    def test_incomplete_packet_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            obs.relay(io.BytesIO(b'x' * 189), Receiver())

    def test_remote_command_uses_recording_sources(self):
        with_audio = obs.remote_command()
        self.assertIn('/dev/video99', with_audio)
        self.assertIn('@DEFAULT_MONITOR@', with_audio)
        self.assertIn('-map 1:a:0', with_audio)
        self.assertIn('-f mpegts', with_audio)
        self.assertNotIn('@DEFAULT_MONITOR@', obs.remote_command(audio=False))

    def test_watchdog_ends_a_stalled_child_on_every_host_platform(self):
        class Child:
            def __init__(self):
                self.stopped = threading.Event()

            def poll(self):
                return 255 if self.stopped.is_set() else None

            def terminate(self):
                self.stopped.set()

            def wait(self, timeout=None):
                return 255

        for ready in (False, True):
            with self.subTest(ready=ready):
                child = Child()
                watchdog = obs.StreamWatchdog(child, startup=0.1, stall=0.1)
                if ready:
                    watchdog.packet()
                try:
                    self.assertTrue(child.stopped.wait(1), 'a stalled SSH child was not terminated')
                    self.assertIn('stopped' if ready else 'no OBS video', watchdog.reason)
                finally:
                    watchdog.close()

    def test_shutdown_kills_an_unresponsive_ssh_child(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired('ssh', 5), 137]
        obs.stop_child(proc)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count, 2)

    def test_sigterm_returns_from_packet_callback_and_restores_handler(self):
        proc = mock.Mock()
        proc.stdout = io.BytesIO(b'G' * obs.DATAGRAM)
        proc.poll.return_value = proc.wait.return_value = 0
        previous = signal.getsignal(signal.SIGTERM)
        completed = []

        def send(_sender, _data):
            # Deliver at the same packet boundary as the subprocess regression.
            signal.raise_signal(signal.SIGTERM)
            completed.append(True)

        with mock.patch.object(obs.subprocess, 'Popen', return_value=proc), \
             mock.patch.object(obs._Sender, 'send', send):
            self.assertEqual(obs.main(['--frame', 'fake-frame']), 0)
        self.assertEqual(completed, [True])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_watchdog_stops_reader_on_requested_shutdown_without_stall_error(self):
        child = mock.Mock()
        child.poll.return_value = None
        stopped = threading.Event()
        child.terminate.side_effect = stopped.set
        watchdog = obs.StreamWatchdog(child, startup=20, stall=10, stop_requested=lambda: True)
        try:
            self.assertTrue(stopped.wait(1))
            self.assertEqual(watchdog.reason, '')
        finally:
            watchdog.close()

    def test_windows_sender_ignores_absent_receiver_and_hides_ssh_console(self):
        proc = mock.Mock()
        proc.stdout = io.BytesIO(bytes(range(188)) * 7)
        proc.poll.return_value = proc.wait.return_value = 0
        # These are the actual Python Winsock control and subprocess constants.
        signal_pair = socket.socketpair() if os.name == 'nt' else None
        with mock.patch.object(obs.socket, 'socketpair', return_value=signal_pair), \
             mock.patch.object(obs.sys, 'platform', 'win32'), \
             mock.patch.object(obs.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
             mock.patch.object(obs.socket, 'socket') as socket_factory, \
             mock.patch.object(obs.subprocess, 'Popen', return_value=proc) as popen:
            self.assertEqual(obs.main(['--frame', 'test-frame']), 0)
        udp = socket_factory.return_value.__enter__.return_value
        self.assertEqual(popen.call_args.kwargs['creationflags'], 0x08000000)
        udp.sendto.assert_called_once()

    def test_windows_recovers_from_an_unreachable_udp_receiver(self):
        udp = mock.Mock()
        udp.sendto.side_effect = [ConnectionResetError(10054, 'receiver closed'), 188]
        sender = obs._Sender(udp, ('127.0.0.1', 15099))
        with mock.patch.object(obs.sys, 'platform', 'win32'):
            sender.send(b'G' * 188)
            self.assertFalse(sender.ready)
            sender.send(b'G' * 188)
            self.assertTrue(sender.ready)
        self.assertEqual(udp.sendto.call_count, 2)

    @unittest.skipIf(sys.platform == 'win32', 'the fake SSH executable uses a POSIX shebang')
    def test_stall_exits_cleanly_even_while_app_control_pipe_stays_open(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            fake = directory / 'ssh'
            fake.write_text("#!/usr/bin/env python3\n"
                            "import signal,sys,time\n"
                            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                            "sys.stdout.buffer.write(b'G'*1316);sys.stdout.buffer.flush()\n"
                            "while True: time.sleep(1)\n")
            fake.chmod(0o755)
            wrapper = ("import importlib.util; "
                       f"s=importlib.util.spec_from_file_location('obs', {str(SCRIPT)!r}); "
                       "o=importlib.util.module_from_spec(s);s.loader.exec_module(o); "
                       "o.STARTUP_TIMEOUT=5; o.STREAM_STALL=o.CHILD_STOP_TIMEOUT=0.15; "
                       "o.main(['--frame','fake-frame','--control-stdin'])")
            env = {**os.environ, 'PATH': str(directory) + os.pathsep + os.environ.get('PATH', '')}
            process = subprocess.Popen([sys.executable, '-c', wrapper], stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
            try:
                # Keep stdin OPEN: real Electron does this until the helper exits.
                self.assertEqual(process.wait(timeout=10), 1)
                error = process.stderr.read().decode(errors='replace')
                self.assertIn('video stopped', error)
                self.assertNotIn('Fatal Python error', error)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=5)
                process.stdin.close(); process.stderr.close()

    @unittest.skipIf(sys.platform == 'win32', 'the fake SSH executable uses a POSIX shebang')
    def test_relay_stops_its_ssh_child_on_app_signal_or_control_pipe(self):
        for mode in ('signal', 'control-pipe', 'watchdog-signal'):
            with self.subTest(mode=mode):
                self._check_stop_mode(mode)

    def _check_stop_mode(self, mode):
        with tempfile.TemporaryDirectory() as folder, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            directory = Path(folder)
            stopped = directory / 'stopped'
            fake = directory / 'ssh'
            fake.write_text("#!/usr/bin/env python3\n"
                            "import os, signal, sys, time\n"
                            "def stop(*_):\n"
                            "    open(os.environ['FAKE_SSH_STOPPED'], 'w').close()\n"
                            "    sys.exit(255)\n"
                            "signal.signal(signal.SIGTERM, stop)\n"
                            "sys.stdout.buffer.write(b'G' * 1316)\n"
                            "sys.stdout.buffer.flush()\n"
                            "while True: time.sleep(1)\n")
            fake.chmod(0o755)
            receiver.bind(('127.0.0.1', 0))
            receiver.settimeout(8)
            env = {**os.environ, 'PATH': str(directory) + os.pathsep + os.environ.get('PATH', ''),
                   'FAKE_SSH_STOPPED': str(stopped)}
            arguments = [sys.executable, str(SCRIPT), '--frame', 'fake-frame',
                         '--port', str(receiver.getsockname()[1])]
            if mode == 'watchdog-signal':
                wrapper = (
                    'import importlib.util,signal; '
                    f's=importlib.util.spec_from_file_location("obs", {str(SCRIPT)!r}); '
                    'o=importlib.util.module_from_spec(s);s.loader.exec_module(o); '
                    'original=o.StreamWatchdog.packet; '
                    'o.StreamWatchdog.packet=lambda self:(original(self),signal.pthread_kill(self.thread.ident,signal.SIGTERM)); '
                    'raise SystemExit(o.main())'
                )
                arguments = [sys.executable, '-c', wrapper, *arguments[2:]]
            if mode == 'control-pipe':
                arguments.append('--control-stdin')
            process = subprocess.Popen(arguments,
                                       stdin=subprocess.PIPE if mode == 'control-pipe' else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env, start_new_session=True)
            try:
                packet, _ = receiver.recvfrom(1500)
                self.assertEqual(packet, b'G' * 1316)
                if mode == 'control-pipe':
                    process.stdin.close()
                    process.wait(timeout=8)
                    errors = process.stderr.read()
                else:
                    if mode != 'watchdog-signal':
                        process.send_signal(signal.SIGTERM)
                    _, errors = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, errors.decode())
                self.assertIn(b'Frame Control OBS stream is ready', errors)
                for _ in range(30):
                    if stopped.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(stopped.exists(), 'the SSH child survived relay shutdown')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=5)
                process.stdout.close()
                process.stderr.close()

    @unittest.skipIf(sys.platform == 'win32', 'the stand-in SSH script uses a POSIX shebang')
    def test_startup_and_stream_stalls_end_ssh_with_a_clear_error(self):
        for mode in ('startup', 'stream'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                ready = directory / 'ready'
                fake = directory / 'ssh'
                fake.write_text("#!/usr/bin/env python3\n"
                                "import os, signal, sys, time\n"
                                "def stop(*_):\n"
                                "    open(os.environ['FAKE_SSH_STOPPED'], 'w').close()\n"
                                "    sys.exit(255)\n"
                                "signal.signal(signal.SIGTERM, stop)\n"
                                "if os.environ['FAKE_SSH_MODE'] == 'stream':\n"
                                "    sys.stdout.buffer.write(b'G' * 1316)\n"
                                "    sys.stdout.buffer.flush()\n"
                                "open(os.environ['FAKE_SSH_READY'], 'w').close()\n"
                                "while True: time.sleep(1)\n")
                fake.chmod(0o755)
                stopped = directory / 'stopped'
                env = {'PATH': str(directory) + os.pathsep + os.environ.get('PATH', ''),
                       'FAKE_SSH_STOPPED': str(stopped), 'FAKE_SSH_MODE': mode,
                       'FAKE_SSH_READY': str(ready)}
                real_popen = subprocess.Popen

                def start_ready(*args, **kwargs):
                    proc = real_popen(*args, **kwargs)
                    # Start the short watchdog clock after the stand-in installed
                    # its handler, independent of native interpreter launch time.
                    end = time.monotonic() + 5
                    while not ready.exists() and proc.poll() is None and time.monotonic() < end:
                        time.sleep(0.01)
                    if not ready.exists():
                        obs.stop_child(proc)
                        self.fail('Stand-in SSH did not finish startup')
                    return proc

                with mock.patch.dict(os.environ, env), \
                     mock.patch.object(obs.subprocess, 'Popen', side_effect=start_ready), \
                     mock.patch.object(obs, 'STARTUP_TIMEOUT', 0.3), \
                     mock.patch.object(obs, 'STREAM_STALL', 0.3):
                    with self.assertRaisesRegex(RuntimeError, 'no OBS video' if mode == 'startup' else 'video stopped'):
                        obs.main(['--frame', 'fake-frame'])
                self.assertTrue(stopped.exists(), 'SSH survived the relay timeout')

    def test_hardening_stop_child_signal_stop_frame_validation_and_wait_timeout(self):
        # 1. stop_child tolerates PermissionError on terminate and OSError/TimeoutExpired on kill/wait
        proc_perm = mock.Mock()
        proc_perm.poll.return_value = None
        proc_perm.terminate.side_effect = PermissionError('already exited on Windows')
        obs.stop_child(proc_perm)

        proc_hang = mock.Mock()
        proc_hang.poll.return_value = None
        proc_hang.wait.side_effect = [
            subprocess.TimeoutExpired('ssh', 5),
            subprocess.TimeoutExpired('ssh', 5),
        ]
        obs.stop_child(proc_hang)
        proc_hang.kill.assert_called_once()
        # Second wait must have a bounded timeout
        self.assertIsNotNone(proc_hang.wait.call_args_list[1].kwargs.get('timeout'))

        # 2. SignalStop.__enter__ closes descriptors if signal.set_wakeup_fd fails
        closed = []
        if os.name == 'nt':
            r_sock, w_sock = mock.Mock(), mock.Mock()
            r_sock.close.side_effect = lambda: closed.append('r')
            w_sock.close.side_effect = lambda: closed.append('w')
            w_sock.fileno.return_value = 99
            with mock.patch.object(obs.socket, 'socketpair', return_value=(r_sock, w_sock)), \
                 mock.patch.object(obs.signal, 'set_wakeup_fd', side_effect=ValueError('non-main thread')):
                with self.assertRaises(ValueError):
                    with obs.SignalStop():
                        pass
            self.assertEqual(sorted(closed), ['r', 'w'])
        else:
            with mock.patch.object(obs.os, 'pipe', return_value=(101, 102)), \
                 mock.patch.object(obs.os, 'set_blocking'), \
                 mock.patch.object(obs.os, 'close', side_effect=lambda fd: closed.append(fd)), \
                 mock.patch.object(obs.signal, 'set_wakeup_fd', side_effect=ValueError('non-main thread')):
                with self.assertRaises(ValueError):
                    with obs.SignalStop():
                        pass
            self.assertEqual(sorted(closed), [101, 102])

        # 3. SignalStop.requested() tolerates OSError from closed wakeup descriptor
        stop = obs.SignalStop()
        stop.stopped = False
        stop.read = mock.Mock(side_effect=OSError('bad fd'))
        self.assertFalse(stop.requested())

        # 4. --frame rejects control characters (e.g. NUL or ESC)
        with self.assertRaises(SystemExit) as cm:
            obs.main(['--frame', 'frame\x00evil'])
        self.assertEqual(cm.exception.code, 2)

        # 5. proc.wait(timeout=10) TimeoutExpired raises RuntimeError (caught by __main__)
        proc_wait = mock.Mock()
        proc_wait.stdout = io.BytesIO(b'G' * obs.DATAGRAM)
        proc_wait.stdout.close = mock.Mock(side_effect=OSError('already closed'))
        proc_wait.poll.return_value = 0
        proc_wait.wait.side_effect = subprocess.TimeoutExpired('ssh', 10)
        with mock.patch.object(obs.subprocess, 'Popen', return_value=proc_wait), \
             mock.patch.object(obs._Sender, 'send'):
            with self.assertRaisesRegex(RuntimeError, 'timed out|did not exit'):
                obs.main(['--frame', 'fake-frame'])

        # 6. relay drains multiple datagrams when a source returns > DATAGRAM bytes
        class BigSource:
            def __init__(self):
                self.chunks = [b'G' * (obs.DATAGRAM * 2)]
            def read(self, _n):
                return self.chunks.pop(0) if self.chunks else b''
        receiver = Receiver()
        total = obs.relay(BigSource(), receiver)
        self.assertEqual(total, obs.DATAGRAM * 2)
        self.assertEqual(len(receiver.chunks), 2)


if __name__ == '__main__':
    unittest.main()

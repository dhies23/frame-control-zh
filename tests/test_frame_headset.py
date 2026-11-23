"""Headset panel packaging, ownership checks and window targeting."""
import sandbox  # noqa: F401
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ui'))
import frame_headset
import frame_headset_runtime


class HeadsetBundle(unittest.TestCase):
    def test_bundle_is_deterministic_and_contains_the_local_ui(self):
        first = frame_headset.bundle()
        self.assertEqual(first, frame_headset.bundle())
        with tarfile.open(fileobj=io.BytesIO(first), mode='r:gz') as archive:
            names = set(archive.getnames())
        self.assertIn('ui/headset.html', names)
        self.assertIn('ui/frame_headset_runtime.py', names)
        self.assertIn('ui/server.py', names)
        for name in ('stage.py', 'build.py', 'extract_romfs.py', 'android-frame.patch', 'LICENSE.nxnative'):
            self.assertIn('frame/nxnative/' + name, names)
        self.assertIn('ui/frame_nxnative.py', names)
        self.assertIn('ui/nxnative.js', names)
        for path in (frame_headset.ROOT / 'frame/nxnative/profiles').rglob('*'):
            if path.is_file():
                self.assertIn(path.relative_to(frame_headset.ROOT).as_posix(), names)
        self.assertIn('frame/love/package.py', names)
        self.assertIn('frame/love/README.md', names)
        self.assertIn('frame/love/clipboard.py', names)
        self.assertIn('frame/love/SDLClipboardHandler.java', names)
        self.assertIn('frame/love/LICENSE.SDL.txt', names)
        for filename in ('aarch64.gz', 'x86_64.gz', 'source.tar.gz', 'LICENSE.txt', 'GO-LICENSE.txt', 'README.md'):
            self.assertIn('ui/vendor/deckyfileserver-1.2.0/'+filename, names)
        for filename in ('aarch64.gz', 'source.tar.gz', 'LICENSE.txt', 'GO-LICENSE.txt', 'README.md'):
            self.assertIn('ui/vendor/localsend-0.4.5/'+filename, names)
        self.assertIn('scripts/build-framey-fileserver.py', names)
        for name in ('aarch64.whl', 'x86_64.whl', 'LICENSE.txt'):
            self.assertIn('ui/vendor/psutil-7.2.2/'+name, names)
        self.assertFalse(any('\\' in name for name in names))
        self.assertFalse(any('__pycache__' in name for name in names))

    def test_game_builds_private_sources_and_keys_are_not_shipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = ['frame/nxnative/build.py', 'frame/nxnative/android-frame.patch',
                      'frame/nxnative/LICENSE.nxnative',
                      'frame/nxnative/profiles/01002B30028F6000/game.tsv',
                      'frame/nxnative/profiles/01002B30028F6000/dmsh_chain.h',
                      'frame/love/package.py', 'frame/love/README.md']
            private = ['frame/nxnative/game.apk', 'frame/nxnative/game.nxnative.zip',
                       'frame/nxnative/prod.keys', 'frame/nxnative/catalog.json',
                       'frame/nxnative/build/frame-inputs.json',
                       'frame/nxnative/build/modules.txt',
                       'frame/nxnative/build/romfs/main.lua',
                       'frame/nxnative/build/libdmhost.so',
                       'frame/nxnative/build/shader/vk/effect.spv',
                       'frame/love/game.apk', 'frame/love/catalog.json',
                       'frame/love/assets/game.love', 'frame/love/game/main.lua']
            for name in public + private:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('sentinel')
            with patch.object(frame_headset, 'ROOT', root):
                packed = frame_headset.bundle()
            with tarfile.open(fileobj=io.BytesIO(packed), mode='r:gz') as archive:
                self.assertEqual(set(archive.getnames()), set(public))

    def test_incompatible_device_is_rejected_before_upload(self):
        state = {'arch': 'x86_64', 'variant': 'vr'}
        calls = []
        def ssh(*args, **kwargs):
            calls.append(args)
            return json.dumps(state)
        with patch.object(frame_headset, 'bundle') as bundle:
            with self.assertRaisesRegex(ValueError, 'Steam Frame'):
                frame_headset.change(ssh, 'install')
            bundle.assert_not_called()
        self.assertEqual(len(calls), 1)

    @unittest.skipIf(os.name == 'nt', 'remote script runs on Linux')
    def test_unowned_install_is_never_removed(self):
        with tempfile.TemporaryDirectory() as home:
            token = secrets.token_hex(12)
            result = subprocess.run([sys.executable, '-', 'remove', token, 'none'],
                                    input=frame_headset.APPLY, text=True, capture_output=True,
                                    env={**os.environ, 'HOME': home}, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('No Frame Control managed', result.stderr)


class HeadsetWindow(unittest.TestCase):
    def test_uses_only_its_marker_title(self):
        tree = ' 0x101 "Other panel"\n 0xabc "Frame Control VR [fc-headset]"\n'
        with patch.object(frame_headset_runtime, 'command', return_value=tree):
            self.assertEqual(frame_headset_runtime.panel_window(), '0xabc')
        with patch.object(frame_headset_runtime, 'command', return_value=' 0x101 "Other panel"'):
            self.assertIsNone(frame_headset_runtime.panel_window())

    def test_desktop_headset_panel_controls_lock_jobs_and_discard_stale_status(self):
        result = subprocess.run(
            ['node', str(Path(__file__).parent / 'page/headset_panel_controls.mjs')],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_status_and_change_reject_non_dict_or_incomplete_remote_json(self):
        for raw in ('[]', 'null', 'not-json'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                frame_headset.status(lambda *a, **k: raw)
        with self.assertRaisesRegex(ValueError, 'Steam Frame'):
            frame_headset.change(lambda *a, **k: '{}', 'install')
        with self.assertRaisesRegex(ValueError, 'Invalid headset panel action'):
            frame_headset.change(lambda *a, **k: '{}', ['install'])
        replies = iter([json.dumps({'arch': 'aarch64', 'variant': 'vr'}), '[]'])
        with self.assertRaises(ValueError):
            frame_headset.change(lambda *a, **k: next(replies), 'close')

    @unittest.skipIf(os.name == 'nt', 'remote script runs on Linux')
    def test_probe_and_apply_guard_non_dict_receipt_and_state_json(self):
        with tempfile.TemporaryDirectory() as home:
            base = Path(home) / '.local/share/frame-control/headset'
            base.mkdir(parents=True)
            (base / 'receipt.json').write_text('[]', encoding='utf-8')
            (base / 'state.json').write_text('null', encoding='utf-8')
            unit = Path(home) / '.config/systemd/user/frame-control-headset.service'
            unit.parent.mkdir(parents=True)
            unit.write_text('[Unit]\n', encoding='utf-8')
            env = {**os.environ, 'HOME': home}
            probe = subprocess.run([sys.executable, '-'], input=frame_headset.PROBE,
                                   text=True, capture_output=True, env=env, timeout=10)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            parsed = json.loads(probe.stdout)
            self.assertFalse(parsed['managed'])
            self.assertIsNone(parsed['version'])
            self.assertIsNone(parsed['window'])
            apply = subprocess.run([sys.executable, '-', 'remove', secrets.token_hex(12), 'none'],
                                   input=frame_headset.APPLY, text=True, capture_output=True, env=env, timeout=10)
            self.assertNotEqual(apply.returncode, 0)
            self.assertIn('No Frame Control managed', apply.stderr)

    def test_browser_command_wraps_missing_flatpak(self):
        with patch.object(Path, 'is_file', return_value=False), \
                patch.object(frame_headset_runtime.subprocess, 'run', side_effect=FileNotFoundError('no flatpak')):
            with self.assertRaisesRegex(frame_headset_runtime.RuntimeError_, 'Chromium'):
                frame_headset_runtime.browser_command()

    def test_command_uses_utf8_and_wraps_oserror_and_timeout(self):
        ok = unittest.mock.Mock(returncode=0, stdout='ok\n', stderr='')
        with patch.object(frame_headset_runtime.subprocess, 'run', return_value=ok) as run:
            self.assertEqual(frame_headset_runtime.command('xprop'), 'ok\n')
        self.assertEqual(run.call_args.kwargs.get('encoding'), 'utf-8')
        self.assertEqual(run.call_args.kwargs.get('errors'), 'replace')
        for exc in (FileNotFoundError('no xprop'), subprocess.TimeoutExpired('xprop', 5)):
            with self.subTest(exc=type(exc).__name__), \
                    patch.object(frame_headset_runtime.subprocess, 'run', side_effect=exc):
                with self.assertRaises(frame_headset_runtime.RuntimeError_):
                    frame_headset_runtime.command('xprop')

    def test_wait_for_server_retries_http_exception(self):
        import http.client

        class FakeResp:
            status = 200
            def read(self, n=-1):
                return b'[fc-headset]'
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                pass

        proc = unittest.mock.Mock()
        proc.poll.return_value = None
        responses = [http.client.BadStatusLine('bad'), FakeResp()]
        def fake_open(*a, **k):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        with patch.object(frame_headset_runtime.urllib.request, 'urlopen', side_effect=fake_open), \
                patch.object(frame_headset_runtime.time, 'sleep'):
            frame_headset_runtime.wait_for_server(proc)
        self.assertEqual(responses, [])

    @unittest.skipIf(os.name == 'nt', 'os.killpg is POSIX only')
    def test_terminate_owned_ignores_process_lookup_race_and_bounds_kill_wait(self):
        proc = unittest.mock.Mock()
        proc.pid = 999999
        proc.poll.return_value = None
        with patch.object(frame_headset_runtime.os, 'killpg', side_effect=ProcessLookupError):
            frame_headset_runtime.terminate_owned(proc)
        waits = []
        def fake_wait(timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired('proc', timeout)
            return 0
        proc.wait.side_effect = fake_wait
        with patch.object(frame_headset_runtime.os, 'killpg'):
            frame_headset_runtime.terminate_owned(proc, timeout=5)
        self.assertEqual(len(waits), 2)
        self.assertIsNotNone(waits[1])


if __name__ == '__main__':
    unittest.main()

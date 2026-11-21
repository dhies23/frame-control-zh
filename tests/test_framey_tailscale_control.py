"""Exact managed Tailscale Control 0.1.3 install, archive preservation and user runtime."""
import sandbox  # noqa: F401
import asyncio
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch
import zipfile

import framey_official
import test_frame_decky_store as transactions
import frame_decky_store

ROOT = Path(__file__).resolve().parents[1]
REPLACEMENTS = (
    (b'os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"\n',
     b'os.environ["XDG_RUNTIME_DIR"] = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"\n'),
    (b'user_dirs = ["/opt/tailscale", "/home/deck/.nix-profile/bin"]\n',
     b'user_dirs = [os.path.expanduser("~/.local/bin"), "/opt/tailscale", os.path.expanduser("~/.nix-profile/bin")]\n'),
    (b'return not subprocess.run(cmd_list, timeout=10, check=False)\n',
     b'return subprocess.run(cmd_list, timeout=10, check=False).returncode == 0\n'),
    (b'            subprocess.run(["tailscale", "down"], timeout=10, check=False)\n            return True\n',
     b'            return subprocess.run(["tailscale", "down"], timeout=10, check=False).returncode == 0\n'),
)
PREFIX = (b'from framey_decky_ipc import install as _framey_install_ipc_eof\n'
          b'_framey_install_ipc_eof()\ndel _framey_install_ipc_eof\n')


@unittest.skipIf(os.name == 'nt', 'remote plugin transaction requires POSIX')
class TailscaleControl(unittest.TestCase):
    def setUp(self):
        self.tx = transactions.StoreTransaction()
        self.tx.setUp()
        self.addCleanup(self.tx.doCleanups)
        self.pin = next(p for p in framey_official.PLUGINS if p['id'] == 43)
        self.data = framey_official.archive(self.pin)
        self.folder = self.tx.folder.parent / self.pin['folder']

    def install(self, **kwargs):
        return self.tx.apply('install', self.data, plugin_id=43, folder=self.pin['folder'],
                             version=self.pin['version'], **kwargs)

    def load_plugin(self, source_bytes, env, *, fake_run, strip_prefix=False):
        code = source_bytes[len(PREFIX):] if strip_prefix and source_bytes.startswith(PREFIX) else source_bytes
        fake_decky = types.SimpleNamespace(
            DECKY_HOME='/private/decky',
            DECKY_PLUGIN_SETTINGS_DIR='/private/settings',
            DECKY_PLUGIN_LOG_DIR='/private/logs',
            logger=logging.getLogger('test.tailscale'),
        )
        ns = {'__name__': 'tailscale_test'}
        with patch.dict(sys.modules, {'decky_plugin': fake_decky}), \
             patch.dict(os.environ, env, clear=True), \
             patch.object(os, 'getuid', return_value=1100), \
             patch.object(subprocess, 'run', side_effect=fake_run):
            exec(compile(code, 'tailscale-main.py', 'exec'), ns)
            recorded_env = dict(os.environ)
            plugin = ns['Plugin']()
            up_ok = asyncio.run(plugin.up(node_ip='100.64.0.20 (relay)'))
            down_fail = asyncio.run(plugin.down())
        return recorded_env, up_ok, down_fail

    def test_original_bugs_vs_managed_user_runtime_and_returncodes(self):
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            original = z.read(self.pin['folder'] + '/main.py')

        def fake_run(cmd, **_kwargs):
            rc = 0 if cmd[1] == 'up' else 1
            return subprocess.CompletedProcess(cmd, rc)

        base_env = {'HOME': '/private/user', 'PATH': '/usr/bin', 'XDG_RUNTIME_DIR': '/private/runtime'}
        orig_env, orig_up, orig_down = self.load_plugin(original, base_env, fake_run=fake_run)
        self.assertEqual(orig_env['XDG_RUNTIME_DIR'], '/run/user/1000')
        self.assertNotIn('/private/user/.local/bin', orig_env['PATH'].split(':'))
        self.assertFalse(orig_up)   # upstream `not CompletedProcess` bug
        self.assertTrue(orig_down)  # upstream unconditional `return True` bug

        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        managed = (self.folder / 'main.py').read_bytes()

        fixed_env, fixed_up, fixed_down = self.load_plugin(managed, base_env, fake_run=fake_run, strip_prefix=True)
        self.assertEqual(fixed_env['XDG_RUNTIME_DIR'], '/private/runtime')
        self.assertEqual(fixed_env['PATH'].split(':'), [
            '/usr/bin', '/private/user/.local/bin', '/opt/tailscale', '/private/user/.nix-profile/bin',
        ])
        self.assertTrue(fixed_up)
        self.assertFalse(fixed_down)

        fallback_env, _, _ = self.load_plugin(
            managed, {'HOME': '/private/user', 'PATH': '/usr/bin'}, fake_run=fake_run, strip_prefix=True,
        )
        self.assertEqual(fallback_env['XDG_RUNTIME_DIR'], '/run/user/1100')

    def test_only_main_repairs_and_ipc_adapter_change(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            for member in z.infolist():
                if member.is_dir():
                    continue
                relative = member.filename.split('/', 1)[1]
                expected = z.read(member)
                if relative == 'main.py':
                    for old, new in REPLACEMENTS:
                        expected = expected.replace(old, new)
                    expected = PREFIX + expected
                self.assertEqual((self.folder / relative).read_bytes(), expected, relative)
        self.assertEqual(
            (self.folder / 'py_modules/framey_decky_ipc.py').read_bytes(),
            (ROOT / 'ui/framey_decky_ipc.py').read_bytes(),
        )
        receipt = json.loads((self.folder / '.frame-control-store.json').read_text())
        self.assertEqual(receipt['compatShims'], ['tailscale-control-user-runtime', 'tailscale-control-ipc-eof'])

    def test_missing_adapter_fails_before_publishing(self):
        with patch.object(frame_decky_store, 'apply_script', return_value=frame_decky_store.APPLY):
            result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing Tailscale Control IPC helper', result.stderr)
        self.assertFalse(self.folder.exists())

    def test_other_identity_or_digest_gets_no_helper(self):
        self.assertEqual(frame_decky_store.apply_script(43, '0' * 64), frame_decky_store.APPLY)
        self.assertEqual(frame_decky_store.apply_script(44, self.pin['sha256']), frame_decky_store.APPLY)

    def test_wrong_folder_is_rejected(self):
        result = self.tx.apply('install', self.data, plugin_id=43, folder='OtherTailscale',
                               version=self.pin['version'])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.folder.exists())

    def test_old_receipt_requires_update_until_both_shims_present(self):
        import framey_compatibility
        for shims in ([], ['tailscale-control-user-runtime'], ['tailscale-control-ipc-eof']):
            old = framey_compatibility.lookup(43, self.pin['sha256'], self.pin['version'], installed_shims=shims)
            self.assertEqual(old['status'], 'partial', shims)
            self.assertIn('Update or reinstall', old['reason'])
        fixed = framey_compatibility.lookup(
            43, self.pin['sha256'], self.pin['version'],
            installed_shims=['tailscale-control-user-runtime', 'tailscale-control-ipc-eof'],
        )
        self.assertEqual(fixed['status'], 'partial')
        self.assertNotIn('Update or reinstall', fixed['reason'])


if __name__ == '__main__':
    unittest.main()

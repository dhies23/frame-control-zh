"""Exact managed Volume Boost install, archive preservation and user runtime."""
import sandbox  # noqa: F401
import ast
import io
import json
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import zipfile

import framey_official
import test_frame_decky_store as transactions
import frame_decky_store

ROOT = Path(__file__).resolve().parents[1]
OLD = b"os.environ['XDG_RUNTIME_DIR'] = '/run/user/1000'\r\n"
NEW = b"os.environ['XDG_RUNTIME_DIR'] = os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'\r\n"
PREFIX = (b'from framey_decky_ipc import install as _framey_install_ipc_eof\n'
          b'_framey_install_ipc_eof()\ndel _framey_install_ipc_eof\n')


@unittest.skipIf(os.name == 'nt', 'remote plugin transaction requires POSIX')
class VolumeBoost(unittest.TestCase):
    def setUp(self):
        self.tx = transactions.StoreTransaction()
        self.tx.setUp()
        self.addCleanup(self.tx.doCleanups)
        self.pin = next(p for p in framey_official.PLUGINS if p['id'] == 58)
        self.data = framey_official.archive(self.pin)
        self.folder = self.tx.folder.parent / self.pin['folder']

    def install(self, **kwargs):
        return self.tx.apply('install', self.data, plugin_id=58, folder=self.pin['folder'],
                             version=self.pin['version'], **kwargs)

    def run_top_level_env(self, source, initial_env, uid=1100):
        tree = ast.parse(source.decode('utf-8'))
        assign = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Subscript) for t in node.targets)
        )
        env = dict(initial_env)
        namespace = {'os': types.SimpleNamespace(environ=env, getuid=lambda: uid)}
        exec(compile(ast.Module(body=[assign], type_ignores=[]), 'volume-boost-env', 'exec'), namespace)
        return env

    def test_original_hardcodes_uid_1000_but_managed_install_preserves_user_runtime(self):
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            original = z.read(self.pin['folder'] + '/main.py')
        self.assertEqual(
            self.run_top_level_env(original, {'XDG_RUNTIME_DIR': '/private/runtime', 'UNCHANGED': 'yes'}),
            {'XDG_RUNTIME_DIR': '/run/user/1000', 'UNCHANGED': 'yes'},
        )
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        managed = (self.folder / 'main.py').read_bytes()
        self.assertEqual(
            self.run_top_level_env(managed, {'XDG_RUNTIME_DIR': '/private/runtime', 'UNCHANGED': 'yes'}, uid=1100),
            {'XDG_RUNTIME_DIR': '/private/runtime', 'UNCHANGED': 'yes'},
        )
        self.assertEqual(
            self.run_top_level_env(managed, {'XDG_RUNTIME_DIR': ''}, uid=1100),
            {'XDG_RUNTIME_DIR': '/run/user/1100'},
        )
        self.assertEqual(
            self.run_top_level_env(managed, {}, uid=1100),
            {'XDG_RUNTIME_DIR': '/run/user/1100'},
        )

    def test_only_environment_line_and_ipc_adapter_change(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            for member in z.infolist():
                if member.is_dir():
                    continue
                relative = member.filename.split('/', 1)[1]
                expected = z.read(member)
                if relative == 'main.py':
                    expected = PREFIX + expected.replace(OLD, NEW)
                self.assertEqual((self.folder / relative).read_bytes(), expected, relative)
        self.assertEqual(
            (self.folder / 'py_modules/framey_decky_ipc.py').read_bytes(),
            (ROOT / 'ui/framey_decky_ipc.py').read_bytes(),
        )
        receipt = json.loads((self.folder / '.frame-control-store.json').read_text())
        self.assertEqual(receipt['compatShims'], ['volume-boost-user-audio', 'volume-boost-ipc-eof'])

    def test_missing_adapter_fails_before_publishing(self):
        with patch.object(frame_decky_store, 'apply_script', return_value=frame_decky_store.APPLY):
            result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing Volume Boost IPC helper', result.stderr)
        self.assertFalse(self.folder.exists())

    def test_other_identity_or_digest_gets_no_helper(self):
        self.assertEqual(frame_decky_store.apply_script(58, '0' * 64), frame_decky_store.APPLY)
        self.assertEqual(frame_decky_store.apply_script(59, self.pin['sha256']), frame_decky_store.APPLY)

    def test_wrong_folder_is_rejected(self):
        result = self.tx.apply('install', self.data, plugin_id=58, folder='OtherVolumeBoost',
                               version=self.pin['version'])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.folder.exists())

    def test_old_receipt_does_not_inherit_the_user_path_and_eof_repairs(self):
        import framey_compatibility
        for shims in ([], ['volume-boost-user-audio'], ['volume-boost-ipc-eof']):
            old = framey_compatibility.lookup(58, self.pin['sha256'], self.pin['version'], installed_shims=shims)
            self.assertEqual(old['status'], 'partial', shims)
            self.assertIn('Update or reinstall', old['reason'])
        fixed = framey_compatibility.lookup(
            58, self.pin['sha256'], self.pin['version'],
            installed_shims=['volume-boost-user-audio', 'volume-boost-ipc-eof'],
        )
        self.assertEqual(fixed['status'], 'works')
        self.assertNotIn('Update or reinstall', fixed['reason'])


if __name__ == '__main__':
    unittest.main()

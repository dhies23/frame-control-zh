"""Exact managed KDE Connect 0.1.0-1 install, user runtime and clean unload."""
import sandbox  # noqa: F401
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import framey_official
import test_frame_decky_store as transactions
import frame_decky_store

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == 'nt', 'remote plugin transaction requires POSIX')
class KdeConnectRuntime(unittest.TestCase):
    def setUp(self):
        self.tx = transactions.StoreTransaction()
        self.tx.setUp()
        self.addCleanup(self.tx.doCleanups)
        self.pin = next(p for p in framey_official.PLUGINS if p['id'] == 74)
        self.data = framey_official.archive(self.pin)
        self.folder = self.tx.folder.parent / self.pin['folder']

    def install(self, **kwargs):
        return self.tx.apply('install', self.data, plugin_id=74, folder=self.pin['folder'],
                             version=self.pin['version'], **kwargs)

    def test_only_main_script_and_ipc_adapter_change(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            for member in z.infolist():
                if member.is_dir():
                    continue
                relative = member.filename.split('/', 1)[1]
                if relative in ('main.py', 'run_kde_connect.sh'):
                    continue
                self.assertEqual((self.folder / relative).read_bytes(), z.read(member), relative)

        main_text = (self.folder / 'main.py').read_text(encoding='utf-8')
        self.assertNotIn('from click import get_app_dir', main_text)
        self.assertNotIn('std_out_file', main_text)
        self.assertNotIn('std_err_file', main_text)
        self.assertNotIn('pkill', main_text)
        self.assertNotIn('"&"', main_text)
        self.assertIn('_framey_install_ipc_eof()', main_text)
        self.assertIn('async def _unload(self):', main_text)
        self.assertIn('def _stop_proc(cls):', main_text)

        script_path = self.folder / 'run_kde_connect.sh'
        script_text = script_path.read_text(encoding='utf-8')
        self.assertTrue(script_path.stat().st_mode & 0o100)
        self.assertNotIn('sudo -u deck', script_text)
        self.assertNotIn('/home/deck', script_text)
        self.assertNotIn('dbus-launch', script_text)
        self.assertIn('XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"', script_text)
        self.assertIn('exec "$kdeconnectd_bin" --replace', script_text)

        self.assertEqual(
            (self.folder / 'py_modules/framey_decky_ipc.py').read_bytes(),
            (ROOT / 'ui/framey_decky_ipc.py').read_bytes(),
        )
        receipt = json.loads((self.folder / '.frame-control-store.json').read_text())
        self.assertEqual(receipt['compatShims'], ['kde-connect-user-runtime', 'kde-connect-ipc-eof'])

    def test_missing_adapter_fails_before_publishing(self):
        with patch.object(frame_decky_store, 'apply_script', return_value=frame_decky_store.APPLY):
            result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing KDE Connect IPC helper', result.stderr)
        self.assertFalse(self.folder.exists())

    def test_other_identity_or_digest_gets_no_helper(self):
        self.assertEqual(frame_decky_store.apply_script(74, '0' * 64), frame_decky_store.APPLY)
        self.assertEqual(frame_decky_store.apply_script(75, self.pin['sha256']), frame_decky_store.APPLY)

    def test_wrong_folder_is_rejected(self):
        result = self.tx.apply('install', self.data, plugin_id=74, folder='OtherKdeConnect',
                               version=self.pin['version'])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.folder.exists())

    def test_old_receipt_requires_update_until_both_shims_present(self):
        import framey_compatibility
        for shims in ([], ['kde-connect-user-runtime'], ['kde-connect-ipc-eof']):
            old = framey_compatibility.lookup(74, self.pin['sha256'], self.pin['version'], installed_shims=shims)
            self.assertEqual(old['status'], 'partial', shims)
            self.assertIn('Update or reinstall', old['reason'])
        fixed = framey_compatibility.lookup(
            74, self.pin['sha256'], self.pin['version'],
            installed_shims=['kde-connect-user-runtime', 'kde-connect-ipc-eof'],
        )
        self.assertEqual(fixed['status'], 'partial')
        self.assertNotIn('Update or reinstall', fixed['reason'])


if __name__ == '__main__':
    unittest.main()

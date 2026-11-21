"""Exact managed Mixer 0.1.0 install, archive preservation and child IPC EOF repair."""
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
PREFIX = (b'from framey_decky_ipc import install as _framey_install_ipc_eof\n'
          b'_framey_install_ipc_eof()\ndel _framey_install_ipc_eof\n')


@unittest.skipIf(os.name == 'nt', 'remote plugin transaction requires POSIX')
class MixerIpc(unittest.TestCase):
    def setUp(self):
        self.tx = transactions.StoreTransaction()
        self.tx.setUp()
        self.addCleanup(self.tx.doCleanups)
        self.pin = next(p for p in framey_official.PLUGINS if p['id'] == 144)
        self.data = framey_official.archive(self.pin)
        self.folder = self.tx.folder.parent / self.pin['folder']

    def install(self, **kwargs):
        return self.tx.apply('install', self.data, plugin_id=144, folder=self.pin['folder'],
                             version=self.pin['version'], **kwargs)

    def test_only_main_prefix_and_ipc_adapter_change(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            for member in z.infolist():
                if member.is_dir():
                    continue
                relative = member.filename.split('/', 1)[1]
                expected = z.read(member)
                if relative == 'main.py':
                    expected = PREFIX + expected
                self.assertEqual((self.folder / relative).read_bytes(), expected, relative)
        self.assertEqual(
            (self.folder / 'py_modules/framey_decky_ipc.py').read_bytes(),
            (ROOT / 'ui/framey_decky_ipc.py').read_bytes(),
        )
        receipt = json.loads((self.folder / '.frame-control-store.json').read_text())
        self.assertEqual(receipt['compatShims'], ['mixer-ipc-eof'])

    def test_missing_adapter_fails_before_publishing(self):
        with patch.object(frame_decky_store, 'apply_script', return_value=frame_decky_store.APPLY):
            result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing Mixer child IPC EOF repair', result.stderr)
        self.assertFalse(self.folder.exists())

    def test_other_identity_or_digest_gets_no_helper(self):
        self.assertEqual(frame_decky_store.apply_script(144, '0' * 64), frame_decky_store.APPLY)
        self.assertEqual(frame_decky_store.apply_script(145, self.pin['sha256']), frame_decky_store.APPLY)

    def test_wrong_folder_is_rejected(self):
        result = self.tx.apply('install', self.data, plugin_id=144, folder='OtherMixer',
                               version=self.pin['version'])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.folder.exists())

    def test_old_receipt_does_not_inherit_ipc_repair(self):
        import framey_compatibility
        old = framey_compatibility.lookup(144, self.pin['sha256'], self.pin['version'], installed_shims=[])
        fixed = framey_compatibility.lookup(144, self.pin['sha256'], self.pin['version'],
                                            installed_shims=['mixer-ipc-eof'])
        self.assertEqual(old['status'], 'partial')
        self.assertIn('Update or reinstall', old['reason'])
        self.assertEqual(fixed['status'], 'works')
        self.assertNotIn('Update or reinstall', fixed['reason'])


if __name__ == '__main__':
    unittest.main()

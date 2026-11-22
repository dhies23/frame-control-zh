"""Exact managed Decky Proton Launch 0.9.0 install, safe profiles/logs and clean unload."""
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
MODIFIED_FILES = {
    'main.py',
    'py_modules/proton_launch/profile.py',
    'py_modules/proton_launch/plugin.py',
}


@unittest.skipIf(os.name == 'nt', 'remote plugin transaction requires POSIX')
class ProtonLaunchRuntime(unittest.TestCase):
    def setUp(self):
        self.tx = transactions.StoreTransaction()
        self.tx.setUp()
        self.addCleanup(self.tx.doCleanups)
        self.pin = next(p for p in framey_official.PLUGINS if p['id'] == 137)
        self.data = framey_official.archive(self.pin)
        self.folder = self.tx.folder.parent / self.pin['folder']

    def install(self, **kwargs):
        return self.tx.apply('install', self.data, plugin_id=137, folder=self.pin['folder'],
                             version=self.pin['version'], **kwargs)

    def test_only_targeted_python_files_and_ipc_adapter_change(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            for member in z.infolist():
                if member.is_dir():
                    continue
                relative = member.filename.split('/', 1)[1]
                if relative in MODIFIED_FILES:
                    continue
                self.assertEqual((self.folder / relative).read_bytes(), z.read(member), relative)

        main_text = (self.folder / 'main.py').read_text(encoding='utf-8')
        self.assertIn('_framey_install_ipc_eof()', main_text)

        profile_text = (self.folder / 'py_modules/proton_launch/profile.py').read_text(encoding='utf-8')
        self.assertIn('ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")', profile_text)
        self.assertIn('os.chmod(path, 0o600)', profile_text)
        self.assertIn('if path.is_symlink()', profile_text)

        plugin_text = (self.folder / 'py_modules/proton_launch/plugin.py').read_text(encoding='utf-8')
        self.assertNotIn('/tmp/proton-launch.log', plugin_text)
        self.assertIn('proton-launch.log', plugin_text)
        self.assertIn('os.chmod(sp, 0o700)', plugin_text)
        self.assertIn('Refusing to overwrite existing unowned proton-launch', plugin_text)
        self.assertIn('=~ ^[0-9]+$', plugin_text)

        self.assertEqual(
            (self.folder / 'py_modules/framey_decky_ipc.py').read_bytes(),
            (ROOT / 'ui/framey_decky_ipc.py').read_bytes(),
        )
        receipt = json.loads((self.folder / '.frame-control-store.json').read_text())
        self.assertEqual(receipt['compatShims'], ['proton-launch-safe-profiles', 'proton-launch-ipc-eof'])

    def test_missing_adapter_fails_before_publishing(self):
        with patch.object(frame_decky_store, 'apply_script', return_value=frame_decky_store.APPLY):
            result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing Decky Proton Launch IPC helper', result.stderr)
        self.assertFalse(self.folder.exists())

    def test_other_identity_or_digest_gets_no_helper(self):
        self.assertEqual(frame_decky_store.apply_script(137, '0' * 64), frame_decky_store.APPLY)
        self.assertEqual(frame_decky_store.apply_script(138, self.pin['sha256']), frame_decky_store.APPLY)

    def test_wrong_folder_is_rejected(self):
        result = self.tx.apply('install', self.data, plugin_id=137, folder='OtherProtonLaunch',
                               version=self.pin['version'])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.folder.exists())

    def test_old_receipt_requires_update_until_both_shims_present(self):
        import framey_compatibility
        for shims in ([], ['proton-launch-safe-profiles'], ['proton-launch-ipc-eof']):
            old = framey_compatibility.lookup(137, self.pin['sha256'], self.pin['version'], installed_shims=shims)
            self.assertEqual(old['status'], 'partial', shims)
            self.assertIn('Update or reinstall', old['reason'])
        fixed = framey_compatibility.lookup(
            137, self.pin['sha256'], self.pin['version'],
            installed_shims=['proton-launch-safe-profiles', 'proton-launch-ipc-eof'],
        )
        self.assertEqual(fixed['status'], 'partial')
        self.assertNotIn('Update or reinstall', fixed['reason'])


if __name__ == '__main__':
    unittest.main()

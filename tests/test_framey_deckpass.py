"""Exact managed DeckPass 1.1.1 install, user runtime, CLI safety and clean unload."""
import sandbox  # noqa: F401
import importlib.util
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
    'py_modules/utils.py',
    'py_modules/keypass_flatpak.py',
    'py_modules/keypass_cli.py',
    'py_modules/password_manager.py',
}


@unittest.skipIf(os.name == 'nt', 'remote plugin transaction requires POSIX')
class DeckPassRuntime(unittest.TestCase):
    def setUp(self):
        self.tx = transactions.StoreTransaction()
        self.tx.setUp()
        self.addCleanup(self.tx.doCleanups)
        self.pin = next(p for p in framey_official.PLUGINS if p['id'] == 121)
        self.data = framey_official.archive(self.pin)
        self.folder = self.tx.folder.parent / self.pin['folder']

    def install(self, **kwargs):
        return self.tx.apply('install', self.data, plugin_id=121, folder=self.pin['folder'],
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
        self.assertIn('await self.pm.aclose()', main_text)

        cli_text = (self.folder / 'py_modules/keypass_cli.py').read_text(encoding='utf-8')
        self.assertIn('process: asyncio.subprocess.Process | None = None', cli_text)
        self.assertIn('**os.environ,', cli_text)
        self.assertIn('stdout, _stderr = await result.communicate()', cli_text)
        self.assertIn('async def aclose(self):', cli_text)

        flatpak_text = (self.folder / 'py_modules/keypass_flatpak.py').read_text(encoding='utf-8')
        self.assertIn('env={ **os.environ, "LD_LIBRARY_PATH": "" }', flatpak_text)

        pm_text = (self.folder / 'py_modules/password_manager.py').read_text(encoding='utf-8')
        self.assertIn('async def aclose(self):', pm_text)
        self.assertIn('not self.security_token or not isinstance(security_token, str)', pm_text)

        # Load the patched utils module and verify escaping + newline/NUL rejection.
        spec = importlib.util.spec_from_file_location('deckpass_utils', self.folder / 'py_modules/utils.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.escape_cli_input('a\\b"c'), '"a\\\\b\\"c"')
        for bad in ('a\nrm x', 'a\rrm x', 'a\x00b'):
            with self.assertRaises(ValueError):
                mod.escape_cli_input(bad)

        self.assertEqual(
            (self.folder / 'py_modules/framey_decky_ipc.py').read_bytes(),
            (ROOT / 'ui/framey_decky_ipc.py').read_bytes(),
        )
        receipt = json.loads((self.folder / '.frame-control-store.json').read_text())
        self.assertEqual(receipt['compatShims'], ['deckpass-user-runtime', 'deckpass-ipc-eof'])

    def test_missing_adapter_fails_before_publishing(self):
        with patch.object(frame_decky_store, 'apply_script', return_value=frame_decky_store.APPLY):
            result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing DeckPass IPC helper', result.stderr)
        self.assertFalse(self.folder.exists())

    def test_other_identity_or_digest_gets_no_helper(self):
        self.assertEqual(frame_decky_store.apply_script(121, '0' * 64), frame_decky_store.APPLY)
        self.assertEqual(frame_decky_store.apply_script(122, self.pin['sha256']), frame_decky_store.APPLY)

    def test_wrong_folder_is_rejected(self):
        result = self.tx.apply('install', self.data, plugin_id=121, folder='OtherDeckPass',
                               version=self.pin['version'])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.folder.exists())

    def test_old_receipt_requires_update_until_both_shims_present(self):
        import framey_compatibility
        for shims in ([], ['deckpass-user-runtime'], ['deckpass-ipc-eof']):
            old = framey_compatibility.lookup(121, self.pin['sha256'], self.pin['version'], installed_shims=shims)
            self.assertEqual(old['status'], 'partial', shims)
            self.assertIn('Update or reinstall', old['reason'])
        fixed = framey_compatibility.lookup(
            121, self.pin['sha256'], self.pin['version'],
            installed_shims=['deckpass-user-runtime', 'deckpass-ipc-eof'],
        )
        self.assertEqual(fixed['status'], 'partial')
        self.assertNotIn('Update or reinstall', fixed['reason'])


if __name__ == '__main__':
    unittest.main()

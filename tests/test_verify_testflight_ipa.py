import sandbox  # noqa: F401
import importlib.util
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'ios' / 'scripts' / 'verify_testflight_ipa.py'
spec = importlib.util.spec_from_file_location('verify_testflight_ipa_test', SCRIPT)
ipa_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipa_mod)


def write_ipa(path, extra_entries=(), info_plist=None):
    info = info_plist if info_plist is not None else {
        'CFBundleIdentifier': 'com.example.app',
        'CFBundleShortVersionString': '1.2.3',
        'CFBundleVersion': '42',
    }
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('Payload/App.app/Info.plist', plistlib.dumps(info))
        zf.writestr('Payload/App.app/embedded.mobileprovision', b'cms-stub')
        for entry in extra_entries:
            if isinstance(entry, tuple) and isinstance(entry[0], zipfile.ZipInfo):
                zf.writestr(entry[0], entry[1])
            else:
                zf.writestr(entry[0], entry[1])


class VerifyTestFlightIpaTests(unittest.TestCase):
    def _run_main(self, ipa, team='TEAM1', bundle='com.example.app', version='1.2.3', build='42'):
        with mock.patch.object(sys, 'argv', ['verify_testflight_ipa.py', str(ipa), team, bundle, version, build]):
            ipa_mod.main()

    def test_rejects_zip_traversal_and_symlinks_before_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            traversal = root / 'traversal.ipa'
            write_ipa(traversal, extra_entries=[('../escape.txt', b'bad')])
            with mock.patch.object(ipa_mod.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'unsafe'):
                    self._run_main(traversal)
                run.assert_not_called()

            symlink_ipa = root / 'symlink.ipa'
            zi = zipfile.ZipInfo('Payload/App.app/link')
            zi.create_system = 3
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            write_ipa(symlink_ipa, extra_entries=[(zi, b'/etc/passwd')])
            with mock.patch.object(ipa_mod.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'symlink'):
                    self._run_main(symlink_ipa)
                run.assert_not_called()

    def test_rejects_prefix_team_match_and_non_list_profile_team(self):
        with tempfile.TemporaryDirectory() as tmp:
            ipa = Path(tmp) / 'ok.ipa'
            write_ipa(ipa)

            # 1. codesign TeamIdentifier=TEAM123456 must NOT match team='TEAM1'
            def fake_run_prefix(cmd, **_kwargs):
                if '--verify' in cmd:
                    return subprocess.CompletedProcess(cmd, 0, '', '')
                return subprocess.CompletedProcess(
                    cmd, 0, '',
                    'Authority=Apple Distribution: Example\nTeamIdentifier=TEAM123456\n',
                )

            good_profile = plistlib.dumps({
                'TeamIdentifier': ['TEAM1'],
                'Entitlements': {'application-identifier': 'TEAM1.com.example.app', 'get-task-allow': False},
            })
            with mock.patch.object(ipa_mod.subprocess, 'run', side_effect=fake_run_prefix), \
                 mock.patch.object(ipa_mod.subprocess, 'check_output', return_value=good_profile):
                with self.assertRaisesRegex(ValueError, 'signing team mismatch'):
                    self._run_main(ipa, team='TEAM1')

            # 2. provisioning profile TeamIdentifier as string 'TEAM123456' must NOT match team='TEAM1'
            def fake_run_exact(cmd, **_kwargs):
                if '--verify' in cmd:
                    return subprocess.CompletedProcess(cmd, 0, '', '')
                return subprocess.CompletedProcess(
                    cmd, 0, '',
                    'Authority=Apple Distribution: Example\nTeamIdentifier=TEAM1\n',
                )

            bad_profile = plistlib.dumps({
                'TeamIdentifier': 'TEAM123456',
                'Entitlements': {'application-identifier': 'TEAM1.com.example.app', 'get-task-allow': False},
            })
            with mock.patch.object(ipa_mod.subprocess, 'run', side_effect=fake_run_exact), \
                 mock.patch.object(ipa_mod.subprocess, 'check_output', return_value=bad_profile):
                with self.assertRaisesRegex(ValueError, 'profile team mismatch'):
                    self._run_main(ipa, team='TEAM1')

            # 3. Valid IPA passes cleanly
            with mock.patch.object(ipa_mod.subprocess, 'run', side_effect=fake_run_exact), \
                 mock.patch.object(ipa_mod.subprocess, 'check_output', return_value=good_profile):
                self._run_main(ipa, team='TEAM1')


if __name__ == '__main__':
    unittest.main()

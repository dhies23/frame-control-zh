#!/usr/bin/env python3
"""Verify the Xcode-exported IPA before submitting its source archive."""
import hashlib
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import zipfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    if len(sys.argv) != 6:
        raise SystemExit('usage: verify_testflight_ipa.py IPA TEAM_ID BUNDLE_ID VERSION BUILD')
    ipa, team, bundle, version, build = sys.argv[1:]
    ipa = Path(ipa)
    require(ipa.is_file(), 'IPA missing')
    with tempfile.TemporaryDirectory(prefix='frame-testflight-verify-') as temp:
        with zipfile.ZipFile(ipa) as archive:
            require(archive.testzip() is None, 'IPA ZIP checksum failed')
            for member in archive.infolist():
                name = member.filename
                parts = PurePosixPath(name).parts
                require(bool(parts) and not name.startswith('/') and '\\' not in name and '..' not in parts,
                        'IPA contains an unsafe archive path')
                mode = (member.external_attr >> 16) & 0o170000
                require(not stat.S_ISLNK(mode), 'IPA contains a symlink entry')
            app_plists = [name for name in archive.namelist()
                          if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', name)]
            require(len(app_plists) == 1, 'IPA must contain exactly one app')
            archive.extractall(temp)
        app = Path(temp) / app_plists[0].removesuffix('/Info.plist')
        info = plistlib.loads((app / 'Info.plist').read_bytes())
        require(isinstance(info, dict), 'IPA Info.plist must be a dictionary')
        require(info.get('CFBundleIdentifier') == bundle, 'IPA bundle ID mismatch')
        require(info.get('CFBundleShortVersionString') == version, 'IPA version mismatch')
        require(info.get('CFBundleVersion') == build, 'IPA build mismatch')

        result = subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)],
                                capture_output=True, text=True)
        require(result.returncode == 0, 'IPA code signature verification failed: ' + result.stderr[:200])
        result = subprocess.run(['codesign', '-dv', '--verbose=4', str(app)],
                                capture_output=True, text=True)
        require(result.returncode == 0, 'IPA signing details unavailable')
        detail_lines = result.stderr.splitlines()
        require(any(line.startswith('Authority=Apple Distribution:') for line in detail_lines),
                'IPA lacks an Apple Distribution signature')
        require(f'TeamIdentifier={team}' in detail_lines, 'IPA signing team mismatch')

        profile = app / 'embedded.mobileprovision'
        require(profile.is_file(), 'IPA Store provisioning profile missing')
        profile_data = plistlib.loads(subprocess.check_output(
            ['security', 'cms', '-D', '-i', str(profile)], stderr=subprocess.DEVNULL))
        require(isinstance(profile_data, dict), 'IPA provisioning profile must be a dictionary')
        entitlements = profile_data.get('Entitlements')
        require(isinstance(entitlements, dict), 'IPA profile entitlements missing')
        teams = profile_data.get('TeamIdentifier')
        require(isinstance(teams, list) and team in teams, 'IPA profile team mismatch')
        require(entitlements.get('application-identifier') == f'{team}.{bundle}',
                'IPA profile bundle mismatch')
        require(entitlements.get('get-task-allow') is False, 'IPA profile permits debugging')

    digest = hashlib.sha256()
    with ipa.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    print(f'Verified distribution IPA {bundle} {version} ({build}), team {team}, sha256 {digest.hexdigest()}')


if __name__ == '__main__':
    main()

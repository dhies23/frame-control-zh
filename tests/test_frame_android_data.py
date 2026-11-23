import sandbox  # noqa: F401  (first: keeps tests off real data and services)
import io
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ui'))
import frame_android as android
import frame_android_data as data

REMOTE = runpy.run_path(str(data.REMOTE))
PKG = 'org.example.game'
META = {'package': PKG, 'instance': 2800000001}


class ObbTests(unittest.TestCase):
    def test_invalid_files_never_contact_frame(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(android, 'ssh') as ssh:
            for name in ('game.obb', 'main.1.org.other.game.obb', 'main.x.' + PKG + '.obb'):
                path = Path(tmp) / name
                path.write_bytes(b'content')
                with self.assertRaises(android.FrameError):
                    data.install_obb(PKG, [path])
            with self.assertRaises(android.FrameError):
                data.install_obb('../game', [])
            with self.assertRaises(android.FrameError):
                data.install_obb(PKG, [])
            ssh.assert_not_called()

    def test_streams_to_correct_instance_and_checks_hash_before_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ('main.7.' + PKG + '.obb')
            path.write_bytes(b'expansion payload')
            calls = []
            def stream(command, src=None, dst=None):
                calls.append(command)
                self.assertEqual(src.read(), b'expansion payload')
            with patch.object(android, '_meta_or_fail', return_value=META), \
                    patch.object(android, 'ssh', return_value='lepton-steamlaunch-2800000001\n'), \
                    patch.object(data, '_stream', side_effect=stream):
                result = data.install_obb(PKG, [path])
            self.assertTrue(result['verified'])
            self.assertIn('podman exec -i lepton-steamlaunch-2800000001', calls[0])
            self.assertIn('/sdcard/Android/obb/' + PKG, calls[0])
            self.assertLess(calls[0].index('sha256sum'), calls[0].index('; mv'))
            self.assertIn(result['obb'][0]['sha256'], calls[0])

    def test_stopped_instance_and_failed_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ('patch.7.' + PKG + '.obb')
            path.write_bytes(b'patch')
            with patch.object(android, '_meta_or_fail', return_value=META), \
                    patch.object(android, 'ssh', return_value=''), patch.object(data, '_stream') as stream:
                with self.assertRaisesRegex(android.FrameError, 'start this app'):
                    data.install_obb(PKG, [path])
                stream.assert_not_called()
            with patch.object(data.frame_host, 'run_ssh', return_value=subprocess.CompletedProcess([], 1, b'', b'bad hash')):
                with self.assertRaisesRegex(android.FrameError, 'bad hash'):
                    data._stream('command')

    def test_non_string_package_and_ssh_link_failure_marking(self):
        for bad_pkg in (123, ['org.example.game']):
            with self.assertRaisesRegex(android.FrameError, 'invalid package name'):
                data.install_obb(bad_pkg, [])
            with self.assertRaisesRegex(android.FrameError, 'invalid package name'):
                data._meta(bad_pkg)
        err_proc = subprocess.CompletedProcess([], 255, b'', b'ssh: connect to host frame port 22: Connection timed out\n')
        with patch.object(data.frame_host, 'run_ssh', return_value=err_proc):
            with self.assertRaises(android.FrameError) as caught:
                data._stream('command')
        self.assertTrue(getattr(caught.exception, 'frame_link_failed', False))


    @unittest.skipUnless(os.name == 'posix' and shutil.which("sh") and shutil.which("shasum"),
                         "the OBB script runs on the Frame (Linux shell)")
    def test_android_shell_publish_and_hash_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / ('main.7.' + PKG + '.obb')
            source.write_bytes(b'good expansion')
            output = Path(tmp) / 'sdcard/Android/obb' / PKG / source.name
            tools_dir = Path(tmp) / 'bin'
            tools_dir.mkdir()
            checksum = tools_dir / 'sha256sum'
            checksum.write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
            checksum.chmod(0o700)
            corrupt = False
            def stream(command, src=None, dst=None):
                script = shlex.split(command)[-1].replace('/sdcard/', tmp + '/sdcard/')
                if corrupt:
                    source.write_bytes(b'corrupt expansion')
                result = subprocess.run(['sh', '-c', script], stdin=src, capture_output=True,
                                        env=dict(os.environ, PATH=str(tools_dir) + ':' + os.environ['PATH']))
                if result.returncode:
                    raise android.FrameError('checksum failed')
            with patch.object(android, '_meta_or_fail', return_value=META), \
                    patch.object(android, 'ssh', return_value='lepton-steamlaunch-2800000001'), \
                    patch.object(data, '_stream', side_effect=stream):
                data.install_obb(PKG, [source])
                self.assertEqual(output.read_bytes(), b'good expansion')
                corrupt = True
                with self.assertRaises(android.FrameError):
                    data.install_obb(PKG, [source])
                self.assertEqual(output.read_bytes(), b'good expansion')
                self.assertEqual(list(output.parent.glob('*.part')), [])


@unittest.skipUnless(os.name == 'posix', 'app-data backups run on the Frame (Linux ownership and modes)')
class BackupTests(unittest.TestCase):
    def test_roundtrip_and_retains_previous_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / PKG
            (source / 'files').mkdir(parents=True)
            (source / 'files/save').write_bytes(b'original save')
            archive = io.BytesIO()
            REMOTE['backup'](root, PKG, META['instance'], archive)
            (source / 'files/save').write_bytes(b'new save')
            archive.seek(0)
            # Current user's uid/gid in this local test; no elevated execution.
            result = REMOTE['restore'](root, PKG, META['instance'], archive)
            self.assertEqual((source / 'files/save').read_bytes(), b'original save')
            self.assertEqual((Path(result['previous']) / 'files/save').read_bytes(), b'new save')

    def test_symlinks_skipped_and_recorded_hardlinks_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / PKG
            (source / 'files').mkdir(parents=True)
            (source / 'files/save').write_bytes(b'save')
            os.link(str(source / 'files/save'), str(source / 'files/save-link'))
            os.symlink('/data/app/lib', str(source / 'lib'))
            os.symlink('save', str(source / 'files/alias'))
            archive = root / 'backup.tar.gz'
            with archive.open('wb') as output:
                REMOTE['backup'](root, PKG, META['instance'], output)
            result = REMOTE['inspect_archive'](archive, PKG, META['instance'])
            self.assertEqual(result['skipped_links'], 2)
            with tarfile.open(archive) as tar:
                manifest = json.load(tar.extractfile('manifest.json'))
                self.assertEqual(tar.extractfile('data/files/save-link').read(), b'save')
            self.assertEqual(sorted((l['path'], l['target']) for l in manifest['skipped_links']),
                             [('data/files/alias', 'save'), ('data/lib', '/data/app/lib')])
            with archive.open('rb') as src:
                REMOTE['restore'](root, PKG, META['instance'], src)
            self.assertFalse((source / 'lib').exists() or (source / 'lib').is_symlink())
            self.assertEqual((source / 'files/save-link').read_bytes(), b'save')

    @unittest.skipUnless(os.name == 'posix', 'restores run on the Frame (Linux flock)')
    def test_overlapping_restores_keep_a_recovery_copy(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PKG).mkdir()
            (root / PKG / 'save').write_bytes(b'backup')
            archive = io.BytesIO()
            REMOTE['backup'](root, PKG, META['instance'], archive)
            (root / PKG / 'save').write_bytes(b'current')
            REMOTE['restore'](root, PKG, META['instance'], io.BytesIO(archive.getvalue()))  # an old copy to clean up
            restore = REMOTE['restore']
            real_rmtree, inside, go = shutil.rmtree, threading.Event(), threading.Event()
            def rmtree(path, **kwargs):
                if threading.current_thread().name == 'first':
                    inside.set()  # swapped, now cleaning up; hold it here
                    go.wait(5)
                real_rmtree(path, **kwargs)
            results = {}
            def run():
                results[threading.current_thread().name] = restore(
                    root, PKG, META['instance'], io.BytesIO(archive.getvalue()))['previous']
            with patch.dict(restore.__globals__, {'shutil': type('S', (), {'rmtree': staticmethod(rmtree),
                                                                         'copyfileobj': shutil.copyfileobj})}):
                first = threading.Thread(target=run, name='first')
                first.start()
                self.assertTrue(inside.wait(5))
                second = threading.Thread(target=run, name='second')
                second.start()
                second.join(.5)
                self.assertTrue(second.is_alive())  # can't swap or clean up during the first's cleanup
                go.set()
                first.join(5)
                second.join(5)
            copies = sorted(root.glob('.' + PKG + '.before-restore-*'))
            self.assertEqual(copies, [Path(results['second'])])  # the second restore's recovery copy survives
            self.assertEqual((copies[0] / 'save').read_bytes(), b'backup')

    def test_restore_keeps_only_latest_previous_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PKG).mkdir()
            (root / PKG / 'save').write_bytes(b'one')
            other = root / '.org.example.gameplus.before-restore-1'  # another package's copy is left alone
            other.mkdir()
            archive = io.BytesIO()
            REMOTE['backup'](root, PKG, META['instance'], archive)
            previous = []
            for _ in range(3):
                archive.seek(0)
                previous.append(REMOTE['restore'](root, PKG, META['instance'], archive)['previous'])
            self.assertEqual(sorted(root.glob('.' + PKG + '.before-restore-*')), [Path(previous[-1])])
            self.assertTrue(other.exists())

    def make_archive(self, path, members, package=PKG):
        with tarfile.open(path, 'w:gz') as archive:
            payload = json.dumps({'format': 1, 'package': package, 'instance': META['instance']}).encode()
            member = tarfile.TarInfo('manifest.json')
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
            root = tarfile.TarInfo('data')
            root.type = tarfile.DIRTYPE
            archive.addfile(root)
            for name, kind in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = '/tmp/escape'
                archive.addfile(member)

    def test_rejects_wrong_package_traversal_links_devices_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.tar.gz'
            cases = [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                     ('data/link', tarfile.SYMTYPE), ('data/link', tarfile.LNKTYPE),
                     ('data/device', tarfile.CHRTYPE), ('data', tarfile.DIRTYPE),
                     ('other/file', tarfile.REGTYPE), ('data/../escape', tarfile.REGTYPE)]
            for member in cases:
                self.make_archive(path, [member])
                with self.assertRaises(ValueError, msg=str(member)):
                    REMOTE['inspect_archive'](path, PKG, META['instance'])
            self.make_archive(path, [], package='org.other.game')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                REMOTE['inspect_archive'](path, PKG, META['instance'])

    def test_failed_backup_leaves_no_archive_and_existing_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'backup.tar.gz'
            with patch.object(android, '_meta_or_fail', return_value=META), \
                    patch.object(data, '_stream', side_effect=android.FrameError('offline')):
                with self.assertRaises(android.FrameError):
                    data.backup_data(PKG, path)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            path.write_bytes(b'keep')
            with self.assertRaisesRegex(android.FrameError, 'already exists'):
                data.backup_data(PKG, path)
            self.assertEqual(path.read_bytes(), b'keep')

    def test_guard_does_not_hide_podman_failure(self):
        command = data._data_command('backup', META)
        self.assertIn('|| exit 1', command)
        self.assertIn('stop the app', command)
        self.assertIn('podman unshare python3', command)
        self.assertNotIn('|| true', command)

    def test_bad_restore_is_rejected_before_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.tar.gz'
            self.make_archive(path, [('../escape', tarfile.REGTYPE)])
            with patch.object(android, '_meta_or_fail', return_value=META), patch.object(data, '_stream') as stream:
                with self.assertRaises(android.FrameError):
                    data.restore_data(PKG, path)
                stream.assert_not_called()


    def test_successful_backup_is_private_and_inspectable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PKG).mkdir()
            (root / PKG / 'save').write_bytes(b'checkpoint')
            destination = root / 'backup.tar.gz'
            def stream(command, src=None, dst=None):
                REMOTE['backup'](root, PKG, META['instance'], dst)
            with patch.object(android, '_meta_or_fail', return_value=META), \
                    patch.object(data, '_stream', side_effect=stream):
                result = data.backup_data(PKG, destination)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result['sha256'], data._sha256(destination))
            self.assertEqual(result['files'], 2)

    def test_rejected_restore_keeps_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PKG).mkdir()
            (root / PKG / 'save').write_bytes(b'keep')
            archive = root / 'bad.tar.gz'
            self.make_archive(archive, [('data/link', tarfile.SYMTYPE)])
            with archive.open('rb') as source, self.assertRaises(ValueError):
                REMOTE['restore'](root, PKG, META['instance'], source)
            self.assertEqual((root / PKG / 'save').read_bytes(), b'keep')
            self.assertFalse(list(root.glob('.frame-restore-*')))
            self.assertFalse(list(root.glob('.*.before-restore-*')))

    def test_archive_root_must_be_a_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bad.tar.gz'
            with tarfile.open(archive, 'w:gz') as target:
                target.addfile(tarfile.TarInfo('data'))
            with self.assertRaisesRegex(ValueError, 'directory'):
                REMOTE['inspect_archive'](archive, PKG, META['instance'])

    def test_backup_creates_parent_handles_race_and_no_hardlink_fs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PKG).mkdir()
            (root / PKG / 'save').write_bytes(b'checkpoint')
            def stream(command, src=None, dst=None):
                REMOTE['backup'](root, PKG, META['instance'], dst)

            nested = root / 'nested' / 'dir' / 'backup.tar.gz'
            with patch.object(android, '_meta_or_fail', return_value=META), \
                    patch.object(data, '_stream', side_effect=stream):
                result = data.backup_data(PKG, nested)
            self.assertTrue(nested.is_file())
            self.assertEqual(result['sha256'], data._sha256(nested))

            raced = root / 'raced.tar.gz'
            with patch.object(android, '_meta_or_fail', return_value=META), \
                    patch.object(data, '_stream', side_effect=stream), \
                    patch.object(data.os, 'link', side_effect=FileExistsError('exists')):
                with self.assertRaisesRegex(android.FrameError, 'already exists'):
                    data.backup_data(PKG, raced)
            self.assertEqual(list(root.glob('.frame-backup-*')), [])

            fat_dest = root / 'fat.tar.gz'
            with patch.object(android, '_meta_or_fail', return_value=META), \
                    patch.object(data, '_stream', side_effect=stream), \
                    patch.object(data.os, 'link', side_effect=PermissionError('no hard links')):
                fat_result = data.backup_data(PKG, fat_dest)
            self.assertTrue(fat_dest.is_file())
            self.assertEqual(fat_dest.stat().st_mode & 0o777, 0o600)
            self.assertEqual(fat_result['sha256'], data._sha256(fat_dest))
            self.assertEqual(list(root.glob('.frame-backup-*')), [])

    def test_restore_rejects_non_dict_json_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PKG).mkdir()
            (root / PKG / 'save').write_bytes(b'checkpoint')
            archive = root / 'ok.tar.gz'
            with archive.open('wb') as out:
                REMOTE['backup'](root, PKG, META['instance'], out)
            for payload in (b'null', b'[]', b'"ok"'):
                with patch.object(android, '_meta_or_fail', return_value=META), \
                        patch.object(data, '_stream', return_value=payload):
                    with self.assertRaisesRegex(android.FrameError, 'could not read restore result'):
                        data.restore_data(PKG, archive)

    def test_remote_app_data_validates_package_instance_and_manifest_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PKG).mkdir()
            (root / PKG / 'save').write_bytes(b'data')

            for bad_pkg in ('../escape', 'org.example.*', 'com/evil', 'bad', 123):
                with self.assertRaisesRegex(ValueError, 'invalid package'):
                    REMOTE['backup'](root, bad_pkg, 1, io.BytesIO())
                with self.assertRaisesRegex(ValueError, 'invalid package'):
                    REMOTE['restore'](root, bad_pkg, 1, io.BytesIO())

            for bad_inst in (True, False, -1, '1', None):
                with self.assertRaisesRegex(ValueError, 'invalid instance'):
                    REMOTE['backup'](root, PKG, bad_inst, io.BytesIO())
                with self.assertRaisesRegex(ValueError, 'invalid instance'):
                    REMOTE['restore'](root, PKG, bad_inst, io.BytesIO())

            # Boolean format/instance in manifest must not match integer 1
            bool_archive = root / 'bool.tar.gz'
            with tarfile.open(bool_archive, 'w:gz') as archive:
                payload = json.dumps({'format': True, 'package': PKG, 'instance': 1}).encode()
                m = tarfile.TarInfo('manifest.json')
                m.size = len(payload)
                archive.addfile(m, io.BytesIO(payload))
                d = tarfile.TarInfo('data')
                d.type = tarfile.DIRTYPE
                archive.addfile(d)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                REMOTE['inspect_archive'](bool_archive, PKG, 1)

            bool_inst_archive = root / 'bool-inst.tar.gz'
            with tarfile.open(bool_inst_archive, 'w:gz') as archive:
                payload = json.dumps({'format': 1, 'package': PKG, 'instance': True}).encode()
                m = tarfile.TarInfo('manifest.json')
                m.size = len(payload)
                archive.addfile(m, io.BytesIO(payload))
                d = tarfile.TarInfo('data')
                d.type = tarfile.DIRTYPE
                archive.addfile(d)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                REMOTE['inspect_archive'](bool_inst_archive, PKG, 1)

            # main() rejects Unicode digits and traversal package before touching filesystem
            for bad_argv in (
                ['app-data.py', 'backup', '../escape', '1'],
                ['app-data.py', 'backup', PKG, '-1'],
                ['app-data.py', 'backup', PKG, '\u0661'],
            ):
                with patch.object(sys, 'argv', bad_argv):
                    with self.assertRaises(ValueError):
                        REMOTE['main']()

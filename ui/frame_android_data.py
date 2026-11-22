"""Expansion files and stopped-instance private-data backups. Python stdlib only."""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import shutil
import subprocess
import tempfile
import uuid

import frame_android as android
import frame_host

REMOTE = Path(android.ROOT) / 'frame/android/app-data.py'


def _stream(command, src=None, dst=None):
    try:
        result = frame_host.run_ssh(['ssh', *android.SSH_OPTS, android.FRAME, command],
                                    stdin=src if src else subprocess.DEVNULL,
                                    stdout=dst if dst else subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=1800)
    except subprocess.TimeoutExpired:
        raise android.FrameError('app-data transfer timed out')
    except OSError as error:
        raise android.FrameError('app-data transfer failed: ' + str(error))
    if result.returncode:
        stderr = (result.stderr or b'').decode(errors='replace')
        stdout = '' if dst else (result.stdout or b'').decode(errors='replace')
        err = android.FrameError(stderr.strip()[-600:] or 'app-data transfer failed')
        if frame_host.ssh_link_failed(result.returncode, stderr, stdout):
            frame_host.link_failure(err)
        raise err
    return result.stdout


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _meta(package):
    if not isinstance(package, str) or not android.PKG_RE.fullmatch(package):
        raise android.FrameError('invalid package name')
    meta = android._meta_or_fail(package)
    if meta['package'] != package:
        raise android.FrameError('installed app metadata has a different package')
    return meta


def install_obb(package, paths):
    if not isinstance(package, str) or not android.PKG_RE.fullmatch(package):
        raise android.FrameError('invalid package name')
    paths = [Path(p).resolve() for p in paths]
    if not paths:
        raise android.FrameError('select at least one OBB file')
    names = set()
    for path in paths:
        if (not re.fullmatch(r'(main|patch)\.[0-9]+\.' + re.escape(package) + r'\.obb', path.name)
                or not path.is_file() or path.stat().st_size == 0 or path.name in names):
            raise android.FrameError('OBB must be a nonempty main/patch.<version>.' + package + '.obb file')
        names.add(path.name)
    with android._install_lock:
        meta = _meta(package)
        container = 'lepton-steamlaunch-' + str(int(meta['instance']))
        # No implicit launch. Android resolves /sdcard, never an assumed host path.
        running = android.ssh('podman ps --format "{{.Names}}"').splitlines()
        if container not in running:
            raise android.FrameError('start this app instance before installing OBB data')
        dest = '/sdcard/Android/obb/' + package
        results = []
        for path in paths:
            digest = _sha256(path)
            part = dest + '/.frame-' + uuid.uuid4().hex + '.part'
            target = dest + '/' + path.name
            script = (f'set -eu; mkdir -p {dest}; umask 002; '
                      f'trap "rm -f {part}" EXIT; cat > {part}; '
                      f'test "$(sha256sum {part} | cut -d " " -f 1)" = {digest}; '
                      f'chmod 664 {part}; mv {part} {target}')
            command = shlex.join(['podman', 'exec', '-i', container, '/system/bin/sh', '-c', script])
            with path.open('rb') as src:
                _stream(command, src=src)
            results.append({'name': path.name, 'path': target, 'sha256': digest})
        return {'package': package, 'instance': meta['instance'], 'obb': results,
                'verified': True}


def _data_command(action, meta):
    container = 'lepton-steamlaunch-' + str(int(meta['instance']))
    # Fail closed if podman cannot enumerate containers; don't mistake errors for stopped.
    guard = ('running=$(podman ps --format "{{.Names}}") || exit 1; '
             f'if printf "%s\\n" "$running" | grep -Fxq {shlex.quote(container)}; then '
             'echo "stop the app before backup or restore" >&2; exit 1; fi; ')
    return guard + shlex.join(['podman', 'unshare', 'python3', '-c', REMOTE.read_text(encoding='utf-8'),
                              action, meta['package'], str(int(meta['instance']))])


def _publish_exclusive(temporary, destination):
    """Publish a verified backup without overwriting an existing destination."""
    try:
        os.link(temporary, str(destination))
    except FileExistsError:
        raise android.FrameError('backup destination already exists') from None
    except OSError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
        try:
            out_fd = os.open(str(destination), flags, 0o600)
        except FileExistsError:
            raise android.FrameError('backup destination already exists') from None
        try:
            with os.fdopen(out_fd, 'wb') as out, open(temporary, 'rb') as src:
                shutil.copyfileobj(src, out)
        except BaseException:
            try:
                os.unlink(str(destination))
            except OSError:
                pass
            raise


def backup_data(package, destination):
    destination = Path(destination).expanduser().absolute()
    if destination.exists():
        raise android.FrameError('backup destination already exists')
    with android._install_lock:
        meta = _meta(package)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.frame-backup-', dir=str(destination.parent))
        try:
            with os.fdopen(fd, 'wb') as dst:
                _stream(_data_command('backup', meta), dst=dst)
            result = _inspect(temporary, meta)
            # Exclusive publication: a concurrently created backup is never overwritten.
            _publish_exclusive(temporary, destination)
            return dict(result, path=str(destination), sha256=_sha256(destination))
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _inspect(path, meta):
    import tarfile
    try:
        return runpy.run_path(str(REMOTE))['inspect_archive'](path, meta['package'], int(meta['instance']))
    except (OSError, EOFError, ValueError, tarfile.TarError) as error:
        raise android.FrameError('invalid app-data backup: ' + str(error))


def restore_data(package, archive):
    with android._install_lock:
        meta = _meta(package)
        _inspect(archive, meta)
        try:
            with open(archive, 'rb') as src:
                result = _stream(_data_command('restore', meta), src=src)
        except OSError as error:
            raise android.FrameError('invalid app-data backup: ' + str(error))
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            parsed = None
        if not isinstance(parsed, dict):
            raise android.FrameError('could not read restore result; inspect app data before retrying')
        return parsed

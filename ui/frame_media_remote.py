"""Frame-side library and process ownership for Frame Control media.

Only the dedicated systemd user unit is controlled. No SteamVR settings change.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import frame_media
from frame_media_player import probe

ROOT = Path.home() / 'Videos' / 'FrameControl'
RUNTIME = Path.home() / '.local' / 'share' / 'frame-control' / 'media'
UNIT = 'frame-control-media.service'
STATUS = RUNTIME / 'status.json'


def media_path(identity):
    if not isinstance(identity, str) or '\\' in identity or '\x00' in identity:
        raise ValueError('Invalid media id')
    parts = Path(identity).parts
    if len(parts) != 2 or not re.fullmatch('[0-9a-f]{32}', parts[0]) or parts[1].startswith('.'):
        raise ValueError('Invalid media id')
    candidate = ROOT / identity
    if candidate.is_symlink() or candidate.parent.is_symlink():
        raise ValueError('Media links are not supported')
    path = candidate.resolve(strict=True)
    if not path.is_file() or ROOT.resolve() not in path.parents:
        raise ValueError('Media file is outside the library')
    return path


def active():
    try:
        return subprocess.run(['systemctl', '--user', 'is-active', '--quiet', UNIT],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def write_status(**values):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix('.tmp')
    tmp.write_text(json.dumps(values), encoding='utf-8')
    tmp.replace(STATUS)


def status():
    running = active()
    try:
        state = json.loads(STATUS.read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        state = {'state': 'idle'}
    if not isinstance(state, dict):
        state = {'state': 'idle'}
    if not running and state.get('state') in ('playing', 'starting', 'paused'):
        state = {'state': 'stopped', 'message': 'Player exited; check the media log if this was unexpected'}
    return dict(state, running=running)


def run(body):
    if not isinstance(body, dict):
        raise ValueError('Media request must be a JSON object')
    action = body.get('action')
    if action == 'list':
        files = []
        if ROOT.exists():
            for folder in sorted(ROOT.iterdir()):
                try:
                    if not re.fullmatch('[0-9a-f]{32}', folder.name) or not folder.is_dir() or folder.is_symlink():
                        continue
                    children = sorted(folder.iterdir())
                except OSError:
                    continue
                for path in children:
                    try:
                        if path.is_file() and not path.is_symlink() and not path.name.startswith('.'):
                            files.append({'id': folder.name+'/'+path.name, 'name': path.name, 'bytes': path.stat().st_size})
                    except OSError:
                        continue
        return {'files': files, 'player': status()}
    if action == 'status':
        return status()
    if action == 'stop':
        # --collect unloads the unit after it exits; systemctl then exits 5
        # ("not loaded", verified on the Frame). That's a finished player, not an error.
        try:
            stopped = subprocess.run(['systemctl', '--user', 'stop', UNIT], capture_output=True, text=True,
                                     encoding='utf-8', errors='replace', timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError('Could not stop the media player: ' + str(e)) from e
        if stopped.returncode not in (0, 5):
            raise RuntimeError('Could not stop the media player: ' + ((stopped.stderr or '').strip() or 'exit %s' % stopped.returncode))
        return {'message': 'Media player stopped', **status()}
    if action != 'play':
        raise ValueError('Media action must be list, status, play or stop')
    path = media_path(body.get('id'))
    if type(body.get('theatre', False)) is not bool:
        raise ValueError('theatre must be true or false')
    info = {} if path.suffix.lower() == '.splat' else probe(path)[0]
    layout = body.get('layout', 'auto')
    plan = frame_media.plan(path.name, layout, info.get('tags'))
    if active():
        raise ValueError('Stop the current media before starting another file')
    # systemd owns the process group and refuses a concurrent start of this name.
    # The runtime cap also cleans up if the controlling computer disconnects.
    try:
        subprocess.run(['systemctl', '--user', 'reset-failed', UNIT], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    write_status(state='starting', file=path.name)
    command = ['systemd-run', '--user', '--quiet', '--collect', '--unit='+UNIT,
               '--property=RuntimeMaxSec=14400', '--property=TimeoutStopSec=8',
               '--property=StandardOutput=append:'+str(RUNTIME/'player.log'),
               '--property=StandardError=append:'+str(RUNTIME/'player.log'),
               'python3', str(RUNTIME/'frame_media_player.py'), str(path),
               '--layout', layout, '--status', str(STATUS)]
    if body.get('theatre'):
        command.append('--theatre')
    try:
        started = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        msg = 'Could not start the media player: ' + str(e)
        write_status(state='error', file=path.name, error=msg)
        raise RuntimeError(msg)
    if started.returncode:
        msg = 'Could not start the media player: ' + ((started.stderr or '').strip() or 'systemd-run exited %s' % started.returncode)
        write_status(state='error', file=path.name, error=msg)
        raise RuntimeError(msg)
    return {'message': 'Starting Frame Control media', 'plan': plan}


def main():
    try:
        print(json.dumps(run(json.load(sys.stdin))))
    except Exception as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)


if __name__ == '__main__':
    main()

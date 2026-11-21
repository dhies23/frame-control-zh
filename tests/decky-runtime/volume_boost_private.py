"""Silent private Pulse null sinks for Volume Boost; no host audio, hardware, Bluetooth or UI."""
import hashlib
import io
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import zipfile
from deck_settings_private import identity, descendants

OLD_ENV = b"os.environ['XDG_RUNTIME_DIR'] = '/run/user/1000'\r\n"
NEW_ENV = b"os.environ['XDG_RUNTIME_DIR'] = os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'\r\n"
PREFIX = (b'from framey_decky_ipc import install as _framey_install_ipc_eof\n'
          b'_framey_install_ipc_eof()\ndel _framey_install_ipc_eof\n')
PRIMARY_SINK = 'framey.private-volume-boost'
UNRELATED_SINK = 'framey.private-unrelated'


def command(env, *args):
    return subprocess.check_output(args, env=dict(env, LC_ALL='C'), text=True,
                                   stderr=subprocess.DEVNULL, timeout=4)


def sink_volumes(env, sink):
    out = command(env, 'pactl', 'get-sink-volume', sink)
    left = re.search(r'front-left:.*?(\d+)%', out)
    right = re.search(r'front-right:.*?(\d+)%', out)
    assert left and right, out
    return int(left.group(1)), int(right.group(1))


def start(env, log, processes, until_ready):
    assert env['XDG_RUNTIME_DIR'] == '/run/user/1100' and 'PULSE_SERVER' not in env
    env['DBUS_SYSTEM_BUS_ADDRESS'] = env['DBUS_SESSION_BUS_ADDRESS']
    for args, ready in [
        (['dbus-daemon', '--session', '--nofork', '--address=' + env['DBUS_SESSION_BUS_ADDRESS']], 'bus'),
        (['pipewire'], 'pipewire-0'), (['wireplumber'], None), (['pipewire-pulse'], None),
    ]:
        child = subprocess.Popen(args, env=env, stdout=log, stderr=log, start_new_session=True)
        processes.append(child)
        if ready:
            until_ready(lambda r=ready: Path('/run/user/1100', r).is_socket(), 'private ' + ready)

    def available():
        try:
            return 'Server Name:' in command(env, 'pactl', 'info')
        except subprocess.CalledProcessError:
            return False

    until_ready(available, 'private Pulse server')
    primary_mod = int(command(env, 'pactl', 'load-module', 'module-null-sink',
                              'sink_name=' + PRIMARY_SINK, 'channels=2',
                              'channel_map=front-left,front-right').strip())
    unrelated_mod = int(command(env, 'pactl', 'load-module', 'module-null-sink',
                                'sink_name=' + UNRELATED_SINK, 'channels=2',
                                'channel_map=front-left,front-right').strip())
    command(env, 'pactl', 'set-default-sink', PRIMARY_SINK)
    with open('/dev/zero', 'rb') as zero:
        player = subprocess.Popen(
            ['pacat', '--playback', '--raw', '--rate=48000', '--channels=2',
             '--device=' + PRIMARY_SINK, '--client-name=FrameySilentBoost',
             '--stream-name=FrameySilentBoost'],
            env=env, stdin=zero, stdout=log, stderr=log, start_new_session=True,
        )
    processes.append(player)
    until_ready(lambda: 'State: RUNNING' in command(env, 'pactl', 'list', 'sinks')
                and sink_volumes(env, PRIMARY_SINK) == (100, 100)
                and sink_volumes(env, UNRELATED_SINK) == (100, 100),
                'running silent Pulse null sinks')
    return dict(
        primaryModule=primary_mod,
        unrelatedModule=unrelated_mod,
        privateRuntime=env['XDG_RUNTIME_DIR'],
        privateBus=env['DBUS_SYSTEM_BUS_ADDRESS'],
        pulseServerOverride=False,
        realFrameContacted=False,
        hostAudioExposed=False,
        bluetoothVerified=False,
        physicalAudioVerified=False,
        officialUiVerified=False,
    )


def stop(backend, log, disconnect, after_disconnect=None, *, expect_forced_kill=False):
    before = descendants(backend.pid)
    assert before
    peers = []
    if disconnect:
        paths = list(Path('/tmp').glob('plugin_socket_*'))
        assert len(paths) == 1, paths
        assert paths[0].is_socket() and paths[0].stat().st_uid == os.getuid()
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(.2)
            peer.connect(str(paths[0]))
            peer.shutdown(socket.SHUT_WR)
        peers = [str(paths[0])]
        time.sleep(.05)
        if after_disconnect is not None:
            after_disconnect()
    log.flush()
    previous = Path(log.name).read_text()
    started = time.monotonic()
    backend.send_signal(signal.SIGTERM)
    backend.wait(timeout=12)
    adopted = []
    deadline = time.monotonic() + 2
    while True:
        for pid, old in before.items():
            current = identity(pid)
            if current and current['start'] == old['start']:
                try:
                    done, status = os.waitpid(pid, os.WNOHANG)
                    if done:
                        adopted.append(dict(pid=done, returncode=os.waitstatus_to_exitcode(status)))
                except ChildProcessError:
                    pass
        remaining = {pid: state for pid, old in before.items()
                     if (state := identity(pid)) and state['start'] == old['start']}
        if not remaining or time.monotonic() >= deadline:
            break
        time.sleep(.01)
    log.flush()
    console = Path(log.name).read_text()[len(previous):]
    seconds = time.monotonic() - started
    forced = console.count('Sending SIGKILL!')
    assert backend.returncode == 0 and not remaining, (backend.returncode, remaining, console)
    if expect_forced_kill:
        assert forced == 1 and seconds >= 5, (forced, seconds, console)
    else:
        assert forced == 0 and seconds < 5, (forced, seconds, console)
        assert all(v['returncode'] == 0 for v in adopted), adopted
        assert 'Error during shutdown:' not in console, console
        assert 'Plugin Volume Boost (v0.0.7) has been stopped in ' in console, console
    return dict(
        parentOnlySignal=True,
        returncode=backend.returncode,
        seconds=seconds,
        forcedPluginKills=forced,
        descendantsBefore=before,
        exactDescendantsRemaining=remaining,
        adoptedChildExits=adopted,
        closedUnixPeers=peers,
        console=console,
        consoleSha256=hashlib.sha256(console.encode()).hexdigest(),
        cleanShutdownVerified=not expect_forced_kill,
    )


def probe(backend, loader, env, log, processes, legacy, rpc, until_ready, fixture, plugin,
          discovered, archive_bytes, *, original=False, unload_regression=False):
    evidence = dict(fixture, readbacks=[], stops=[])
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = member.filename.split('/', 1)[1]
            expected = archive.read(member)
            if relative == 'main.py':
                if unload_regression:
                    expected = expected.replace(OLD_ENV, NEW_ENV)
                elif not original:
                    expected = PREFIX + expected.replace(OLD_ENV, NEW_ENV)
            assert (plugin / relative).read_bytes() == expected, relative
    ipc_helper = plugin / 'py_modules/framey_decky_ipc.py'
    if original or unload_regression:
        assert not ipc_helper.exists()
    else:
        assert ipc_helper.is_file()

    marker = Path.home() / 'unrelated-volume-boost-marker'
    marker.write_bytes(b'private unrelated file')
    before = dict(
        inode=marker.stat().st_ino,
        sha256=hashlib.sha256(marker.read_bytes()).hexdigest(),
        mtimeNs=marker.stat().st_mtime_ns,
    )
    sibling = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(100)'], start_new_session=True)
    processes.append(sibling)
    sibling_before = identity(sibling.pid)

    if original:
        left = legacy('get_volume_state', channel='left')
        right = legacy('get_volume_state', channel='right')
        write_ok = legacy('set_volume_state', value=125, channel='both')
        assert left == 0 and right == 0 and write_ok is False, (left, right, write_ok)
        assert sink_volumes(env, PRIMARY_SINK) == (100, 100)
        assert sink_volumes(env, UNRELATED_SINK) == (100, 100)
        evidence['expectedRegression'] = dict(
            left=left,
            right=right,
            setVolumeReturned=write_ok,
            primarySinkUnchanged=True,
            unrelatedSinkUnchanged=True,
            qualifiedCompatibilityPass=False,
        )
        return evidence

    if unload_regression:
        assert legacy('get_volume_state', channel='left') == 100
        assert legacy('set_volume_state', value=125, channel='both') is True
        until_ready(lambda: sink_volumes(env, PRIMARY_SINK) == (125, 125), 'unload regression sink volume 125')
        assert sink_volumes(env, UNRELATED_SINK) == (100, 100)
        evidence['stops'].append(stop(backend, log, True, expect_forced_kill=True))
        evidence['expectedRegression'] = dict(
            forcedPluginKills=evidence['stops'][0]['forcedPluginKills'],
            seconds=evidence['stops'][0]['seconds'],
            qualifiedCompatibilityPass=False,
        )
        return evidence

    assert legacy('get_volume_state', channel='left') == 100
    assert legacy('get_volume_state', channel='right') == 100
    for channel, value, expected_pair in [
        ('both', 125, (125, 125)),
        ('both', 150, (150, 150)),
        ('left', 85, (85, 150)),
        ('right', 135, (85, 135)),
        ('both', 100, (100, 100)),
    ]:
        assert legacy('set_volume_state', value=value, channel=channel) is True
        until_ready(lambda want=expected_pair: sink_volumes(env, PRIMARY_SINK) == want,
                    f'sink volume {expected_pair}')
        actual_pair = sink_volumes(env, PRIMARY_SINK)
        assert actual_pair == expected_pair, (channel, value, actual_pair, expected_pair)
        assert sink_volumes(env, UNRELATED_SINK) == (100, 100)
        rpc_pair = (
            legacy('get_volume_state', channel='left'),
            legacy('get_volume_state', channel='right'),
        )
        assert rpc_pair == expected_pair, (rpc_pair, expected_pair)
        evidence['readbacks'].append(dict(
            channel=channel,
            requested=value,
            pactl=list(actual_pair),
            rpc=list(rpc_pair),
        ))

    post_eof_rpc = {}

    def verify_post_eof():
        post_eof_rpc['left'] = legacy('get_volume_state', channel='left')
        post_eof_rpc['right'] = legacy('get_volume_state', channel='right')
        assert (post_eof_rpc['left'], post_eof_rpc['right']) == (100, 100), post_eof_rpc

    for cycle in range(2):
        evidence['stops'].append(stop(backend, log, cycle == 0,
                                      after_disconnect=verify_post_eof if cycle == 0 else None))
        assert sink_volumes(env, UNRELATED_SINK) == (100, 100)
        state = identity(sibling.pid)
        assert state and state['start'] == sibling_before['start'] and state['parent'] == sibling_before['parent']
        assert dict(
            inode=marker.stat().st_ino,
            sha256=hashlib.sha256(marker.read_bytes()).hexdigest(),
            mtimeNs=marker.stat().st_mtime_ns,
        ) == before
        if cycle == 0:
            backend = subprocess.Popen([str(loader)], env=env, stdout=log, stderr=log, start_new_session=True)
            processes.append(backend)
            found = until_ready(lambda: rpc('loader/get_plugins'), 'restarted Volume Boost discovery')
            assert found == discovered
            assert (legacy('get_volume_state', channel='left'),
                    legacy('get_volume_state', channel='right')) == (100, 100)
            assert legacy('set_volume_state', value=115, channel='both') is True
            until_ready(lambda: sink_volumes(env, PRIMARY_SINK) == (115, 115), 'restarted sink volume 115')
            assert sink_volumes(env, PRIMARY_SINK) == (115, 115)
            assert (legacy('get_volume_state', channel='left'),
                    legacy('get_volume_state', channel='right')) == (115, 115)
            evidence['restartedReadback'] = [115, 115]

    evidence['postEofReadback'] = post_eof_rpc
    evidence['unrelatedSinkSiblingAndMarkerPreserved'] = True
    evidence['installedHashes'] = {
        p: hashlib.sha256((plugin / p).read_bytes()).hexdigest()
        for p in ['main.py', 'py_modules/framey_decky_ipc.py']
    }
    return evidence

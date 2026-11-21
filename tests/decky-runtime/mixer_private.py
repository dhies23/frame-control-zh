"""Silent private PipeWire/WirePlumber streams for Mixer 0.1.0; no host audio, Bluetooth or UI."""
import hashlib
import io
import json
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

PREFIX = (b'from framey_decky_ipc import install as _framey_install_ipc_eof\n'
          b'_framey_install_ipc_eof()\ndel _framey_install_ipc_eof\n')
NULL_SINK = 'framey.private-mixer'


def command(env, *args):
    return subprocess.check_output(args, env=dict(env, LC_ALL='C'), text=True,
                                   stderr=subprocess.DEVNULL, timeout=4)


def wpctl_volume(env, node_id):
    out = command(env, 'wpctl', 'get-volume', str(node_id))
    match = re.search(r'Volume:\s*([0-9.]+)', out)
    assert match, out
    return float(match.group(1))


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
    module = int(command(env, 'pactl', 'load-module', 'module-null-sink',
                         'sink_name=' + NULL_SINK, 'channels=2',
                         'channel_map=front-left,front-right').strip())
    command(env, 'pactl', 'set-default-sink', NULL_SINK)
    players = []
    for name in ['FrameySelected', 'FrameyUnrelated']:
        with open('/dev/zero', 'rb') as zero:
            player = subprocess.Popen(
                ['pacat', '--playback', '--raw', '--rate=48000', '--channels=2',
                 '--device=' + NULL_SINK, '--client-name=' + name, '--stream-name=' + name],
                env=env, stdin=zero, stdout=log, stderr=log, start_new_session=True,
            )
        players.append(player)
        processes.append(player)

    def streams_in_pw_dump():
        try:
            nodes = json.loads(command(env, 'pw-dump'))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return False
        names = set()
        for node in nodes:
            if node.get('type') != 'PipeWire:Interface:Node':
                continue
            info = node.get('info', {})
            props = info.get('props', {})
            if props.get('media.class') != 'Stream/Output/Audio':
                continue
            params = info.get('params', {}).get('Props', [])
            if not params or not params[0].get('channelVolumes'):
                continue
            label = (props.get('node.description') or props.get('node.nick')
                     or props.get('application.name') or props.get('node.name'))
            if label:
                names.add(label)
        return {'FrameySelected', 'FrameyUnrelated'}.issubset(names)

    until_ready(streams_in_pw_dump, 'two silent PipeWire playback streams in pw-dump')
    return dict(
        players=players,
        nullModule=module,
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
        assert 'Plugin Mixer (v0.1.0) has been stopped in ' in console, console
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


def probe(backend, loader, env, log, processes, current, rpc, until_ready, fixture, plugin,
          discovered, archive_bytes, *, regression=False):
    players = fixture.pop('players')
    evidence = dict(fixture, readbacks=[], stops=[])
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = member.filename.split('/', 1)[1]
            expected = archive.read(member)
            if relative == 'main.py' and not regression:
                expected = PREFIX + expected
            assert (plugin / relative).read_bytes() == expected, relative
    ipc_helper = plugin / 'py_modules/framey_decky_ipc.py'
    if regression:
        assert not ipc_helper.exists()
    else:
        assert ipc_helper.is_file()

    marker = Path.home() / 'unrelated-mixer-marker'
    marker.write_bytes(b'private unrelated file')
    before = dict(
        inode=marker.stat().st_ino,
        sha256=hashlib.sha256(marker.read_bytes()).hexdigest(),
        mtimeNs=marker.stat().st_mtime_ns,
    )
    sibling = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(100)'], start_new_session=True)
    processes.append(sibling)
    sibling_before = identity(sibling.pid)

    assert current('get_settings') == {'filters': [], 'boostLimit': 0}
    playbacks = current('get_playbacks')
    by_name = {p['name']: p for p in playbacks}
    assert set(by_name) == {'FrameySelected', 'FrameyUnrelated'}, playbacks
    selected_id = by_name['FrameySelected']['id']
    unrelated_id = by_name['FrameyUnrelated']['id']
    assert by_name['FrameySelected']['volume'] == 100
    assert by_name['FrameyUnrelated']['volume'] == 100

    assert current('set_volume', selected_id, 50) is True
    until_ready(lambda: abs(wpctl_volume(env, selected_id) - 0.50) < 0.02, 'wpctl volume 0.50')
    assert abs(wpctl_volume(env, unrelated_id) - 1.00) < 0.02

    if regression:
        evidence['stops'].append(stop(backend, log, True, expect_forced_kill=True))
        evidence['expectedRegression'] = dict(
            forcedPluginKills=evidence['stops'][0]['forcedPluginKills'],
            seconds=evidence['stops'][0]['seconds'],
            qualifiedCompatibilityPass=False,
        )
        return evidence

    observed = {p['name']: p for p in current('get_playbacks')}
    assert observed['FrameySelected']['volume'] == 50, observed
    assert observed['FrameyUnrelated']['volume'] == 100, observed
    evidence['readbacks'].append(dict(step='set_50', wpctlSelected=wpctl_volume(env, selected_id),
                                      wpctlUnrelated=wpctl_volume(env, unrelated_id), rpc=observed))

    # Reject invalid filter regex without altering saved settings.
    assert current('save_settings', {'filters': ['['], 'boostLimit': 50}) is False
    assert current('get_settings') == {'filters': [], 'boostLimit': 0}
    evidence['invalidRegexRejected'] = True

    # Apply filter and boostLimit, verify unrelated stream is hidden and boost applies.
    desired_settings = {'filters': ['^FrameyUnrelated$'], 'boostLimit': 25}
    assert current('save_settings', desired_settings) is True
    assert current('get_settings') == desired_settings
    filtered = current('get_playbacks')
    assert [p['name'] for p in filtered] == ['FrameySelected'], filtered

    assert current('set_volume', selected_id, 125) is True
    until_ready(lambda: abs(wpctl_volume(env, selected_id) - 1.25) < 0.02, 'wpctl boosted volume 1.25')
    boosted = current('get_playbacks')
    assert len(boosted) == 1 and boosted[0]['volume'] == 125, boosted
    evidence['readbacks'].append(dict(step='boost_125', wpctlSelected=wpctl_volume(env, selected_id),
                                      rpc=boosted))

    # Set unrelated stream to 0.60 independently, then confirm reset_volumes only resets unfiltered streams.
    command(env, 'wpctl', 'set-volume', str(unrelated_id), '0.60')
    until_ready(lambda: abs(wpctl_volume(env, unrelated_id) - 0.60) < 0.02, 'unrelated stream 0.60')
    assert current('reset_volumes') is None
    until_ready(lambda: abs(wpctl_volume(env, selected_id) - 1.00) < 0.02, 'reset selected to 1.00')
    assert abs(wpctl_volume(env, unrelated_id) - 0.60) < 0.02
    after_reset = current('get_playbacks')
    assert len(after_reset) == 1 and after_reset[0]['volume'] == 100, after_reset
    evidence['resetVerified'] = dict(wpctlSelected=wpctl_volume(env, selected_id),
                                     wpctlUnrelated=wpctl_volume(env, unrelated_id), rpc=after_reset)

    post_eof = {}

    def verify_post_eof():
        post_eof['settings'] = current('get_settings')
        post_eof['playbacks'] = current('get_playbacks')
        assert post_eof['settings'] == desired_settings, post_eof
        assert len(post_eof['playbacks']) == 1 and post_eof['playbacks'][0]['volume'] == 100, post_eof

    for cycle in range(2):
        evidence['stops'].append(stop(backend, log, cycle == 0,
                                      after_disconnect=verify_post_eof if cycle == 0 else None))
        assert all(p.poll() is None for p in players)
        assert abs(wpctl_volume(env, unrelated_id) - 0.60) < 0.02
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
            found = until_ready(lambda: rpc('loader/get_plugins'), 'restarted Mixer discovery')
            assert found == discovered
            assert current('get_settings') == desired_settings
            restarted_playbacks = current('get_playbacks')
            assert [p['name'] for p in restarted_playbacks] == ['FrameySelected'], restarted_playbacks
            assert current('set_volume', selected_id, 80) is True
            until_ready(lambda: abs(wpctl_volume(env, selected_id) - 0.80) < 0.02, 'restarted wpctl 0.80')
            after_restart_write = current('get_playbacks')
            assert len(after_restart_write) == 1 and after_restart_write[0]['volume'] == 80, after_restart_write
            evidence['restartedPersistence'] = dict(
                settings=desired_settings,
                wpctlSelected=wpctl_volume(env, selected_id),
                rpc=after_restart_write,
            )

    evidence['postEofReadback'] = post_eof
    evidence['unrelatedStreamSiblingAndMarkerPreserved'] = True
    evidence['installedHashes'] = {
        p: hashlib.sha256((plugin / p).read_bytes()).hexdigest()
        for p in ['main.py', 'py_modules/framey_decky_ipc.py']
    }
    return evidence

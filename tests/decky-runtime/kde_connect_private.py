"""Private stub xprop/kdeconnectd probe for KDE Connect 0.1.0-1; no real network, X11, or kdeconnectd."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
import zipfile
from deck_settings_private import identity, descendants

XPROP_STUB = """#!/usr/bin/env python3
import sys
from pathlib import Path

focus_path = Path.home() / 'private-kde-connect-focus.txt'
if not focus_path.exists():
    sys.exit(0)
value = focus_path.read_text(encoding='utf-8').strip()
if value:
    print(f'GAMESCOPE_FOCUSED_APP(CARDINAL) = {value}')
"""

KDECONNECTD_STUB = """#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path

events_path = Path.home() / 'private-kde-connect-events.jsonl'
running = True

def record(payload):
    with events_path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, sort_keys=True) + '\\n')
        f.flush()

def handle_term(signum, _frame):
    global running
    record({'event': 'stopped', 'pid': os.getpid(), 'signal': 'SIGTERM'})
    running = False

signal.signal(signal.SIGTERM, handle_term)
record({
    'event': 'started',
    'pid': os.getpid(),
    'args': sys.argv[1:],
    'display': os.environ.get('DISPLAY'),
    'home': os.environ.get('HOME'),
    'xdgRuntimeDir': os.environ.get('XDG_RUNTIME_DIR'),
    'dbus': os.environ.get('DBUS_SESSION_BUS_ADDRESS'),
    'uid': os.getuid(),
})
while running:
    time.sleep(0.05)
"""


def _read_events(path):
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def start(home, env):
    assert env['XDG_RUNTIME_DIR'] == '/run/user/1100'
    local_bin = home / '.local/bin'
    local_bin.mkdir(parents=True, exist_ok=True)
    env['PATH'] = str(local_bin) + ':' + env['PATH']
    env['DBUS_SESSION_BUS_ADDRESS'] = f"unix:path={env['XDG_RUNTIME_DIR']}/bus"
    xprop = local_bin / 'xprop'
    xprop.write_text(XPROP_STUB, encoding='utf-8')
    xprop.chmod(0o700)
    daemon = local_bin / 'kdeconnectd'
    daemon.write_text(KDECONNECTD_STUB, encoding='utf-8')
    daemon.chmod(0o700)
    focus_path = home / 'private-kde-connect-focus.txt'
    focus_path.write_text('769\n', encoding='utf-8')
    events_path = home / 'private-kde-connect-events.jsonl'
    if events_path.exists():
        events_path.unlink()
    assert shutil.which('xprop', path=env['PATH']) == str(xprop)
    assert shutil.which('kdeconnectd', path=env['PATH']) == str(daemon)
    return dict(
        xpropStub=str(xprop),
        kdeconnectdStub=str(daemon),
        focusPath=str(focus_path),
        eventsPath=str(events_path),
        privateHome=str(home),
        privateRuntime=env['XDG_RUNTIME_DIR'],
        privateDbus=env['DBUS_SESSION_BUS_ADDRESS'],
        realKdeConnectContacted=False,
        realX11Contacted=False,
        realFrameContacted=False,
        networkDevice=False,
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
    if expect_forced_kill:
        assert backend.returncode == 0 and forced == 1 and seconds >= 5, (backend.returncode, forced, seconds, console)
        orphaned = dict(remaining)
        for pid in list(remaining):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        return dict(
            parentOnlySignal=True,
            returncode=backend.returncode,
            seconds=seconds,
            forcedPluginKills=forced,
            descendantsBefore=before,
            orphanedDaemonsAfterSigkill=orphaned,
            adoptedChildExits=adopted,
            ipcPeersDisconnected=peers,
        )
    else:
        assert backend.returncode == 0 and not remaining, (backend.returncode, remaining, console)
        assert forced == 0 and seconds < 5, (forced, seconds, console)
        assert all(v['returncode'] == 0 for v in adopted), adopted
        assert 'Error during shutdown:' not in console, console
        assert 'KDE Connect unloaded' in console, console
        assert 'Plugin KDE Connect (v0.1.0-1) has been stopped in ' in console, console
    return dict(
        parentOnlySignal=True,
        returncode=backend.returncode,
        seconds=seconds,
        forcedPluginKills=forced,
        descendantsBefore=before,
        exactDescendantsRemaining=remaining,
        adoptedChildExits=adopted,
        ipcPeersDisconnected=peers,
    )


def probe_original_runtime_failure(backend, log, legacy, until_ready, fixture):
    events_path = Path(fixture['eventsPath'])
    focus_path = Path(fixture['focusPath'])
    focus_path.write_text('769\n', encoding='utf-8')
    assert legacy('is_enabled') is False
    legacy('set_enabled', enabled=True)
    assert legacy('is_enabled') is True

    def saw_permission_error():
        log.flush()
        text = Path(log.name).read_text(encoding='utf-8', errors='replace')
        return 'PermissionError: [Errno 13] Permission denied' in text and 'run_kde_connect.sh' in text

    until_ready(saw_permission_error, 'original KDE Connect non-executable run_kde_connect.sh failure', timeout=20)
    assert _read_events(events_path) == [], _read_events(events_path)
    log.flush()
    previous = Path(log.name).read_text(encoding='utf-8', errors='replace')
    backend.send_signal(signal.SIGTERM)
    backend.wait(timeout=12)
    log.flush()
    shutdown_log = Path(log.name).read_text(encoding='utf-8', errors='replace')[len(previous):]
    assert backend.returncode == 0, (backend.returncode, shutdown_log)
    assert 'Could not find "_unload" in KDE Connect (v0.1.0-1)\'s main.py' in shutdown_log, shutdown_log
    return dict(
        **fixture,
        reproducedOriginalRuntimeFailure=True,
        matchedLaunchError='PermissionError: [Errno 13] Permission denied',
        missingUnloadHook=True,
        daemonEvents=_read_events(events_path),
        backendReturncode=backend.returncode,
    )


def probe(backend, loader, env, log, processes, legacy, rpc, until_ready,
          fixture, plugin, discovered, archive_bytes, *, ipc_regression=False):
    unchanged = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as original:
        for member in original.infolist():
            if member.is_dir():
                continue
            rel = member.filename.split('/', 1)[1]
            if rel in ('main.py', 'run_kde_connect.sh'):
                continue
            expected = original.read(member)
            actual = (plugin / rel).read_bytes()
            assert actual == expected, rel
            unchanged[rel] = hashlib.sha256(actual).hexdigest()

    script_path = plugin / 'run_kde_connect.sh'
    assert script_path.stat().st_mode & 0o100, oct(script_path.stat().st_mode)
    adapter = plugin / 'py_modules/framey_decky_ipc.py'
    if ipc_regression:
        assert not adapter.exists()
    else:
        assert adapter.is_file()

    focus_path = Path(fixture['focusPath'])
    events_path = Path(fixture['eventsPath'])

    initial_enabled = legacy('is_enabled')
    assert initial_enabled is False, initial_enabled
    assert _read_events(events_path) == [], _read_events(events_path)

    # Enable while Steam UI (769) is focused -> DISPLAY=:0 with active user's runtime env.
    focus_path.write_text('769\n', encoding='utf-8')
    legacy('set_enabled', enabled=True)
    assert legacy('is_enabled') is True

    first_started = until_ready(
        lambda: next((e for e in _read_events(events_path) if e['event'] == 'started'), None),
        'first stub kdeconnectd start on DISPLAY=:0',
        timeout=10,
    )
    assert first_started['args'] == ['--replace'], first_started
    assert first_started['display'] == ':0', first_started
    assert first_started['home'] == fixture['privateHome'], first_started
    assert first_started['xdgRuntimeDir'] == fixture['privateRuntime'], first_started
    assert first_started['dbus'] == fixture['privateDbus'], first_started
    assert first_started['uid'] == os.getuid(), first_started

    # Switch focus to a game (12345) -> stops :0 daemon cleanly and starts :1 daemon.
    focus_path.write_text('12345\n', encoding='utf-8')
    second_started = until_ready(
        lambda: next(
            (e for e in _read_events(events_path)
             if e['event'] == 'started' and e['pid'] != first_started['pid']),
            None,
        ),
        'second stub kdeconnectd start on DISPLAY=:1',
        timeout=10,
    )
    first_stopped = until_ready(
        lambda: next(
            (e for e in _read_events(events_path)
             if e['event'] == 'stopped' and e['pid'] == first_started['pid']),
            None,
        ),
        'first stub kdeconnectd SIGTERM stop',
        timeout=5,
    )
    assert second_started['display'] == ':1', second_started
    assert second_started['args'] == ['--replace'], second_started
    assert first_stopped['signal'] == 'SIGTERM', first_stopped

    # Disable -> terminates second daemon cleanly.
    legacy('set_enabled', enabled=False)
    assert legacy('is_enabled') is False
    second_stopped = until_ready(
        lambda: next(
            (e for e in _read_events(events_path)
             if e['event'] == 'stopped' and e['pid'] == second_started['pid']),
            None,
        ),
        'second stub kdeconnectd SIGTERM stop on disable',
        timeout=5,
    )
    assert second_stopped['signal'] == 'SIGTERM', second_stopped

    # Re-enable so a live third daemon is active when Decky stops -> verifies _unload stops it.
    legacy('set_enabled', enabled=True)
    assert legacy('is_enabled') is True
    third_started = until_ready(
        lambda: next(
            (e for e in _read_events(events_path)
             if e['event'] == 'started' and e['pid'] not in (first_started['pid'], second_started['pid'])),
            None,
        ),
        'third stub kdeconnectd start before unload',
        timeout=10,
    )
    assert third_started['display'] == ':1', third_started

    if ipc_regression:
        forced_stop = stop(backend, log, True, expect_forced_kill=True)
        assert third_started['pid'] in forced_stop['orphanedDaemonsAfterSigkill'], forced_stop
        return dict(
            **fixture,
            reproducedIpcRegression=True,
            initialEnabled=initial_enabled,
            firstStarted=first_started,
            secondStarted=second_started,
            thirdStarted=third_started,
            forcedStop=forced_stop,
            unchangedPublishedFiles=unchanged,
        )

    post_eof_enabled = []
    first_stop = stop(
        backend, log, True,
        after_disconnect=lambda: post_eof_enabled.append(legacy('is_enabled')),
    )
    assert post_eof_enabled == [True], post_eof_enabled
    third_stopped = until_ready(
        lambda: next(
            (e for e in _read_events(events_path)
             if e['event'] == 'stopped' and e['pid'] == third_started['pid']),
            None,
        ),
        'third stub kdeconnectd SIGTERM stop during unload',
        timeout=5,
    )
    assert third_stopped['signal'] == 'SIGTERM', third_stopped

    restarted = subprocess.Popen([str(loader)], env=env, stdout=log, stderr=log, start_new_session=True)
    processes.append(restarted)
    again = until_ready(lambda: rpc('loader/get_plugins'), 'restarted published Decky plugin discovery', timeout=20)
    assert again == discovered, again
    restarted_enabled = legacy('is_enabled')
    assert restarted_enabled is False, restarted_enabled
    second_stop = stop(restarted, log, False)

    return dict(
        **fixture,
        initialEnabled=initial_enabled,
        firstStarted=first_started,
        firstStopped=first_stopped,
        secondStarted=second_started,
        secondStopped=second_stopped,
        thirdStarted=third_started,
        thirdStopped=third_stopped,
        postEofEnabled=post_eof_enabled[0],
        restartedEnabled=restarted_enabled,
        firstStop=first_stop,
        secondStop=second_stop,
        ipcAdapterSha256=hashlib.sha256(adapter.read_bytes()).hexdigest(),
        unchangedPublishedFiles=unchanged,
    )

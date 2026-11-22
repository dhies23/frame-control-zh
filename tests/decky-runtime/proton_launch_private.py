"""Private wrapper/profile/log probe for Decky Proton Launch 0.9.0; no real Steam or Proton launches."""
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import time
import zipfile
from deck_settings_private import identity, descendants

MODIFIED_FILES = {
    'main.py',
    'py_modules/proton_launch/profile.py',
    'py_modules/proton_launch/plugin.py',
}


def start(home, env):
    assert env['XDG_RUNTIME_DIR'] == '/run/user/1100'
    shared_log = Path('/tmp/proton-launch.log')
    if shared_log.exists() or shared_log.is_symlink():
        shared_log.unlink()
    wrapper = home / 'proton-launch'
    if wrapper.exists() or wrapper.is_symlink():
        wrapper.unlink()
    return dict(
        privateHome=str(home),
        wrapperPath=str(wrapper),
        privateLogPath=str(home / '.config/decky-proton-launch/proton-launch.log'),
        sharedTmpLog=str(shared_log),
        realSteamContacted=False,
        realProtonLaunched=False,
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
    assert backend.returncode == 0 and not remaining, (backend.returncode, remaining, console)
    if expect_forced_kill:
        assert forced == 1 and seconds >= 5, (forced, seconds, console)
    else:
        assert forced == 0 and seconds < 5, (forced, seconds, console)
        assert all(v['returncode'] == 0 for v in adopted), adopted
        assert 'Error during shutdown:' not in console, console
        assert 'decky-proton-launch unloaded' in console, console
        assert 'Plugin Decky Proton Launch (v0.9.0) has been stopped in ' in console, console
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


def probe_original_safety_failure(backend, log, current, env, fixture):
    wrapper = Path(fixture['wrapperPath'])
    shared_log = Path(fixture['sharedTmpLog'])
    wrapper.write_text('#!/bin/sh\necho custom-unowned\n', encoding='utf-8')
    # Unmodified install_script() overwrites unowned ~/proton-launch and writes LOG=/tmp/proton-launch.log.
    assert current('install_script') is True
    assert '# decky-proton-launch' in wrapper.read_text(encoding='utf-8')
    # Unmodified write_profile() accepts shell-injecting variable keys.
    assert current('set_game_profile', 440, {'BAD=1; echo INJECTED #': 'x'}, 'TF2') is True
    run = subprocess.run(
        [str(wrapper), sys.executable, '-c', 'print("ok")'],
        env={**env, 'SteamAppId': '440'},
        capture_output=True, text=True, timeout=10,
    )
    assert run.returncode == 0, run.stderr
    assert 'INJECTED' in run.stdout, run.stdout
    assert shared_log.is_file(), 'expected unmodified wrapper to write /tmp/proton-launch.log'
    shared_log.unlink(missing_ok=True)
    backend.send_signal(signal.SIGTERM)
    backend.wait(timeout=12)
    assert backend.returncode == 0, backend.returncode
    return dict(
        **fixture,
        reproducedOriginalSafetyFailure=True,
        overwroteUnownedScript=True,
        injectedCommandExecuted=True,
        wroteSharedTmpLog=True,
        backendReturncode=backend.returncode,
    )


def probe(backend, loader, env, log, processes, current, rpc, until_ready,
          fixture, plugin, discovered, archive_bytes, *, ipc_regression=False):
    unchanged = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as original:
        for member in original.infolist():
            if member.is_dir():
                continue
            rel = member.filename.split('/', 1)[1]
            if rel in MODIFIED_FILES:
                continue
            expected = original.read(member)
            actual = (plugin / rel).read_bytes()
            assert actual == expected, rel
            unchanged[rel] = hashlib.sha256(actual).hexdigest()

    adapter = plugin / 'py_modules/framey_decky_ipc.py'
    if ipc_regression:
        assert not adapter.exists()
    else:
        assert adapter.is_file()

    assert current('ping') == 'pong'

    wrapper = Path(fixture['wrapperPath'])
    private_log = Path(fixture['privateLogPath'])
    shared_log = Path(fixture['sharedTmpLog'])

    # 1. Existing unowned ~/proton-launch must never be overwritten.
    unowned_bytes = b'#!/bin/sh\necho keep-my-script\n'
    wrapper.write_bytes(unowned_bytes)
    assert current('install_script') is False
    assert wrapper.read_bytes() == unowned_bytes
    wrapper.unlink()

    # 2. Managed install_script() installs owner-only (0o700) wrapper using private log.
    assert current('install_script') is True
    assert current('is_script_installed') == 'current'
    assert stat.S_IMODE(wrapper.stat().st_mode) == 0o700
    assert '/tmp/proton-launch.log' not in wrapper.read_text(encoding='utf-8')

    # 3. Shell-injection keys and multi-line values are rejected; valid profiles persist with 0o600.
    assert current('set_game_profile', 440, {'BAD=1; echo INJECTED #': 'x'}, 'TF2') is False
    assert current('set_game_profile', 440, {'GOOD_KEY': 'line1\nline2'}, 'TF2') is False
    assert current('set_game_profile', 440, {
        'PROTON_FSR4_UPGRADE': '1',
        'MANGOHUD_CONFIG': "fps_limit=60,a'b",
    }, 'TF2') is True
    profile_file = Path(fixture['privateHome']) / '.config/decky-proton-launch/profiles/440.env'
    assert stat.S_IMODE(profile_file.stat().st_mode) == 0o600
    read_back = current('get_game_profile', 440)
    assert read_back == {'PROTON_FSR4_UPGRADE': '1', 'MANGOHUD_CONFIG': "fps_limit=60,a'b"}, read_back

    # 4. Executing ~/proton-launch applies the profile, logs privately, and never touches /tmp/proton-launch.log.
    probe_cmd = (
        'import json, os; print(json.dumps({'
        '"PROTON_FSR4_UPGRADE": os.environ.get("PROTON_FSR4_UPGRADE"), '
        '"MANGOHUD_CONFIG": os.environ.get("MANGOHUD_CONFIG")}))'
    )
    launch = subprocess.run(
        [str(wrapper), sys.executable, '-c', probe_cmd],
        env={**env, 'SteamAppId': '440'},
        capture_output=True, text=True, timeout=10,
    )
    assert launch.returncode == 0, launch.stderr
    exported = json.loads(launch.stdout)
    assert exported == {'PROTON_FSR4_UPGRADE': '1', 'MANGOHUD_CONFIG': "fps_limit=60,a'b"}, exported
    assert not shared_log.exists(), 'managed wrapper must not write /tmp/proton-launch.log'
    assert private_log.is_file()
    assert current('get_last_launched_appid') == 440
    log_text = current('get_launch_log', 440)
    assert 'APPLYING profile for appid=440' in log_text, log_text

    # 5. Non-numeric SteamAppId traversal is ignored by ~/proton-launch.
    traversal = subprocess.run(
        [str(wrapper), sys.executable, '-c', probe_cmd],
        env={**env, 'SteamAppId': '../profiles/440'},
        capture_output=True, text=True, timeout=10,
    )
    assert traversal.returncode == 0, traversal.stderr
    assert json.loads(traversal.stdout) == {'PROTON_FSR4_UPGRADE': None, 'MANGOHUD_CONFIG': None}

    if ipc_regression:
        forced_stop = stop(backend, log, True, expect_forced_kill=True)
        return dict(
            **fixture,
            reproducedIpcRegression=True,
            forcedStop=forced_stop,
            unchangedPublishedFiles=unchanged,
        )

    post_eof_ping = []
    first_stop = stop(
        backend, log, True,
        after_disconnect=lambda: post_eof_ping.append(current('ping')),
    )
    assert post_eof_ping == ['pong'], post_eof_ping

    restarted = subprocess.Popen([str(loader)], env=env, stdout=log, stderr=log, start_new_session=True)
    processes.append(restarted)
    again = until_ready(lambda: rpc('loader/get_plugins'), 'restarted published Decky plugin discovery', timeout=20)
    assert again == discovered, again
    assert current('get_game_profile', 440) == {'PROTON_FSR4_UPGRADE': '1', 'MANGOHUD_CONFIG': "fps_limit=60,a'b"}
    assert current('get_last_launched_appid') == 440
    second_stop = stop(restarted, log, False)

    return dict(
        **fixture,
        exportedEnv=exported,
        lastLaunchedAppId=440,
        postEofPing=post_eof_ping[0],
        firstStop=first_stop,
        secondStop=second_stop,
        ipcAdapterSha256=hashlib.sha256(adapter.read_bytes()).hexdigest(),
        unchangedPublishedFiles=unchanged,
    )

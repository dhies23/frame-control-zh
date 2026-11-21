"""Private stub ~/.local/bin/tailscale probe for Tailscale Control 0.1.3; no real network or tailscaled."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import zipfile
from deck_settings_private import identity, descendants

REPLACEMENTS = (
    (b'os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"\n',
     b'os.environ["XDG_RUNTIME_DIR"] = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"\n'),
    (b'user_dirs = ["/opt/tailscale", "/home/deck/.nix-profile/bin"]\n',
     b'user_dirs = [os.path.expanduser("~/.local/bin"), "/opt/tailscale", os.path.expanduser("~/.nix-profile/bin")]\n'),
    (b'return not subprocess.run(cmd_list, timeout=10, check=False)\n',
     b'return subprocess.run(cmd_list, timeout=10, check=False).returncode == 0\n'),
    (b'            subprocess.run(["tailscale", "down"], timeout=10, check=False)\n            return True\n',
     b'            return subprocess.run(["tailscale", "down"], timeout=10, check=False).returncode == 0\n'),
)
PREFIX = (b'from framey_decky_ipc import install as _framey_install_ipc_eof\n'
          b'_framey_install_ipc_eof()\ndel _framey_install_ipc_eof\n')

STUB_SCRIPT = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

state_path = Path.home() / 'private-tailscale-state.json'
log_path = Path.home() / 'private-tailscale-invocations.jsonl'
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with log_path.open('a', encoding='utf-8') as f:
    f.write(json.dumps({
        'args': args,
        'xdgRuntimeDir': os.environ.get('XDG_RUNTIME_DIR'),
    }) + '\\n')

cmd = args[0] if args else ''
if cmd == 'status':
    if not state.get('connected', False):
        print('Tailscale is stopped.')
        sys.exit(1)
    print('100.64.0.10  frame-private  user@  linux    active; offers exit node')
    print('100.64.0.20  relay-private  user@  linux    idle; offers exit node')
    print('100.64.0.30  phone-private  user@  android  offline')
    sys.exit(0)
if cmd == 'up':
    if state.get('failNextUp'):
        state['failNextUp'] = False
        state_path.write_text(json.dumps(state))
        sys.exit(1)
    state['connected'] = True
    state['lastUpArgs'] = args
    state_path.write_text(json.dumps(state))
    sys.exit(0)
if cmd == 'down':
    if state.get('failNextDown'):
        state['failNextDown'] = False
        state_path.write_text(json.dumps(state))
        sys.exit(1)
    state['connected'] = False
    state_path.write_text(json.dumps(state))
    sys.exit(0)
sys.exit(2)
"""


def start(home, env):
    assert env['XDG_RUNTIME_DIR'] == '/run/user/1100'
    local_bin = home / '.local/bin'
    local_bin.mkdir(parents=True, exist_ok=True)
    # Confirm ~/.local/bin is NOT in the incoming PATH so ~/.local/bin discovery
    # is genuinely exercised by Tailscale Control's managed PATH setup.
    assert str(local_bin) not in env['PATH'].split(':'), env['PATH']
    assert shutil.which('tailscale', path=env['PATH']) is None
    stub = local_bin / 'tailscale'
    stub.write_text(STUB_SCRIPT, encoding='utf-8')
    stub.chmod(0o700)
    state_path = home / 'private-tailscale-state.json'
    state_path.write_text(json.dumps({'connected': True, 'failNextUp': False, 'failNextDown': False}),
                          encoding='utf-8')
    log_path = home / 'private-tailscale-invocations.jsonl'
    if log_path.exists():
        log_path.unlink()
    return dict(
        stub=str(stub),
        statePath=str(state_path),
        logPath=str(log_path),
        privateRuntime=env['XDG_RUNTIME_DIR'],
        localBinPreconfiguredInPath=False,
        realTailscaleContacted=False,
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
        assert 'Plugin Tailscale Control (v0.1.3) has been stopped in ' in console, console
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


def apply_runtime_patch(source):
    body = source
    for old, new in REPLACEMENTS:
        assert body.count(old) == 1
        body = body.replace(old, new)
    return body


def probe(backend, loader, env, log, processes, legacy, rpc, until_ready, fixture, plugin,
          discovered, archive_bytes, *, original=False, unload_regression=False):
    evidence = dict(fixture, stops=[])
    state_path = Path(fixture['statePath'])
    log_path = Path(fixture['logPath'])

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = member.filename.split('/', 1)[1]
            expected = archive.read(member)
            if relative == 'main.py':
                if unload_regression:
                    expected = apply_runtime_patch(expected)
                elif not original:
                    expected = PREFIX + apply_runtime_patch(expected)
            assert (plugin / relative).read_bytes() == expected, relative
    ipc_helper = plugin / 'py_modules/framey_decky_ipc.py'
    if original or unload_regression:
        assert not ipc_helper.exists()
    else:
        assert ipc_helper.is_file()

    marker = Path.home() / 'unrelated-tailscale-marker'
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
        state_resp = legacy('get_tailscale_state')
        up_resp = legacy('up')
        assert state_resp is None and up_resp is None, (state_resp, up_resp)
        assert not log_path.exists()
        evidence['expectedRegression'] = dict(
            stateResult=state_resp,
            upResult=up_resp,
            localBinStubReached=False,
            qualifiedCompatibilityPass=False,
        )
        return evidence

    if unload_regression:
        assert legacy('get_tailscale_state') is True
        evidence['stops'].append(stop(backend, log, True, expect_forced_kill=True))
        evidence['expectedRegression'] = dict(
            forcedPluginKills=evidence['stops'][0]['forcedPluginKills'],
            seconds=evidence['stops'][0]['seconds'],
            qualifiedCompatibilityPass=False,
        )
        return evidence

    assert legacy('get_tailscale_state') is True
    exit_nodes = legacy('get_tailscale_exit_node_ip_list')
    assert exit_nodes == ['100.64.0.10 (frame-private)', '100.64.0.20 (relay-private)'], exit_nodes
    devices = legacy('get_tailscale_device_status')
    assert devices == {
        'name': ['frame-private', 'relay-private', 'phone-private'],
        'status': ['active', 'idle', 'offline'],
    }, devices

    # Exercise down (success + failure) and up (success with flags + failure).
    assert legacy('down') is True
    assert legacy('get_tailscale_state') is False

    state = json.loads(state_path.read_text())
    state['failNextDown'] = True
    state_path.write_text(json.dumps(state))
    assert legacy('down') is False

    assert legacy(
        'up',
        node_ip='100.64.0.20 (relay-private)',
        allow_lan_access=True,
        custom_flags='--accept-routes',
        login_server='https://headscale.private.invalid',
    ) is True
    assert legacy('get_tailscale_state') is True
    state = json.loads(state_path.read_text())
    assert state['lastUpArgs'] == [
        'up',
        '--exit-node=100.64.0.20',
        '--exit-node-allow-lan-access=true',
        '--accept-routes',
        '--login-server=https://headscale.private.invalid',
        '--reset',
    ], state

    state['failNextUp'] = True
    state_path.write_text(json.dumps(state))
    assert legacy('up') is False

    invocations = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(invocations) >= 7
    assert all(entry['xdgRuntimeDir'] == '/run/user/1100' for entry in invocations), invocations
    evidence['cliReadbacks'] = dict(
        exitNodes=exit_nodes,
        devices=devices,
        lastUpArgs=state['lastUpArgs'],
        upSuccessReturnedTrue=True,
        upFailureReturnedFalse=True,
        downSuccessReturnedTrue=True,
        downFailureReturnedFalse=True,
        invocationCount=len(invocations),
        allInvocationsPreservedUserRuntimeDir=True,
    )

    post_eof = {}

    def verify_post_eof():
        post_eof['state'] = legacy('get_tailscale_state')
        post_eof['exitNodes'] = legacy('get_tailscale_exit_node_ip_list')
        assert post_eof['state'] is True and post_eof['exitNodes'] == exit_nodes, post_eof

    for cycle in range(2):
        evidence['stops'].append(stop(backend, log, cycle == 0,
                                      after_disconnect=verify_post_eof if cycle == 0 else None))
        s = identity(sibling.pid)
        assert s and s['start'] == sibling_before['start'] and s['parent'] == sibling_before['parent']
        assert dict(
            inode=marker.stat().st_ino,
            sha256=hashlib.sha256(marker.read_bytes()).hexdigest(),
            mtimeNs=marker.stat().st_mtime_ns,
        ) == before
        if cycle == 0:
            backend = subprocess.Popen([str(loader)], env=env, stdout=log, stderr=log, start_new_session=True)
            processes.append(backend)
            found = until_ready(lambda: rpc('loader/get_plugins'), 'restarted Tailscale Control discovery')
            assert found == discovered
            assert legacy('get_tailscale_state') is True
            assert legacy('get_tailscale_exit_node_ip_list') == exit_nodes
            evidence['restartedStateVerified'] = True

    evidence['postEofReadback'] = post_eof
    evidence['siblingAndMarkerPreserved'] = True
    evidence['installedHashes'] = {
        p: hashlib.sha256((plugin / p).read_bytes()).hexdigest()
        for p in ['main.py', 'py_modules/framey_decky_ipc.py']
    }
    return evidence

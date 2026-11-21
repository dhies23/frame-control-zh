"""Private stub flatpak/keepassxc-cli probe for DeckPass 1.1.1; no real Flatpak, KeePassXC, or secrets."""
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

MODIFIED_FILES = {
    'main.py',
    'py_modules/utils.py',
    'py_modules/keypass_flatpak.py',
    'py_modules/keypass_cli.py',
    'py_modules/password_manager.py',
}

FLATPAK_STUB = """#!/usr/bin/env python3
import json, os, shlex, signal, sys
from pathlib import Path

home = os.environ.get('HOME', '')
xdg_runtime = os.environ.get('XDG_RUNTIME_DIR', '')
ld_lib = os.environ.get('LD_LIBRARY_PATH')
if not home or not xdg_runtime:
    sys.stderr.write('missing user HOME or XDG_RUNTIME_DIR in flatpak environment\\n')
    sys.exit(2)

events_path = Path(home) / 'private-deckpass-events.jsonl'
def record(payload):
    with events_path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, sort_keys=True) + '\\n')
        f.flush()

args = sys.argv[1:]
if args[:3] != ['run', '--command=keepassxc-cli', 'org.keepassxc.KeePassXC']:
    sys.stderr.write(f'unexpected flatpak args: {args}\\n')
    sys.exit(2)
cli_args = args[3:]

if cli_args == ['-h']:
    record({'event': 'setup_check', 'home': home, 'xdgRuntimeDir': xdg_runtime, 'ldLibraryPath': ld_lib})
    sys.stdout.write('Usage: keepassxc-cli [options] command\\n')
    sys.exit(0)

if len(cli_args) == 3 and cli_args[0] == 'db-create' and cli_args[2] == '-p':
    db_path = Path(cli_args[1])
    sys.stdout.write('Enter password to encrypt database: ')
    sys.stdout.flush()
    p1 = sys.stdin.readline().rstrip('\\n')
    sys.stdout.write('Repeat password: ')
    sys.stdout.flush()
    p2 = sys.stdin.readline().rstrip('\\n')
    if db_path.name == 'fail.kdbx' or p1 != p2:
        sys.stdout.write('Error: refusing stub vault creation\\n')
        sys.stdout.flush()
        sys.exit(1)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text(json.dumps({'password': p1, 'entries': {}}), encoding='utf-8')
    record({'event': 'db_created', 'dbPath': str(db_path), 'home': home, 'xdgRuntimeDir': xdg_runtime})
    sys.exit(0)

if len(cli_args) == 2 and cli_args[0] == 'open':
    db_path = Path(cli_args[1])
    def handle_term(_signum, _frame):
        record({'event': 'stopped', 'pid': os.getpid(), 'signal': 'SIGTERM'})
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_term)
    record({
        'event': 'started',
        'pid': os.getpid(),
        'dbPath': str(db_path),
        'home': home,
        'xdgRuntimeDir': xdg_runtime,
        'ldLibraryPath': ld_lib,
        'uid': os.getuid(),
    })
    sys.stdout.write(f'Enter password to unlock {db_path}: ')
    sys.stdout.flush()
    pw = sys.stdin.readline().rstrip('\\n')
    vault = json.loads(db_path.read_text(encoding='utf-8'))
    if pw != vault['password']:
        sys.stdout.write('Error: Invalid credentials\\n')
        sys.stdout.flush()
        sys.exit(1)
    def prompt():
        sys.stdout.write(f'{db_path.name}> ')
        sys.stdout.flush()
    prompt()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            prompt()
            continue
        parts = shlex.split(line)
        cmd = parts[0]
        if cmd == 'ls':
            sys.stdout.write(line + '\\n')
            for name in sorted(vault['entries']):
                sys.stdout.write(name + '\\n')
            prompt()
        elif cmd == 'show':
            name = parts[1]
            entry = vault['entries'][name]
            sys.stdout.write(line + '\\n')
            sys.stdout.write(entry['username'] + '\\n')
            sys.stdout.write(entry['password'] + '\\n')
            prompt()
        elif cmd == 'generate':
            sys.stdout.write(line + '\\n')
            sys.stdout.write('StubGenPass_1234567890abcdef!@#\\n')
            prompt()
        elif cmd == 'add':
            name = parts[1]
            u_idx = parts.index('-u')
            username = parts[u_idx + 1]
            sys.stdout.write('Enter password for new entry: ')
            sys.stdout.flush()
            entry_pw = sys.stdin.readline().rstrip('\\n')
            vault['entries'][name] = {'username': username, 'password': entry_pw}
            db_path.write_text(json.dumps(vault), encoding='utf-8')
            sys.stdout.write('Successfully added entry.\\n')
            prompt()
        elif cmd == 'edit':
            name = parts[1]
            new_name = parts[parts.index('-t') + 1]
            username = parts[parts.index('-u') + 1]
            sys.stdout.write('Enter new password for entry: ')
            sys.stdout.flush()
            entry_pw = sys.stdin.readline().rstrip('\\n')
            vault['entries'].pop(name, None)
            vault['entries'][new_name] = {'username': username, 'password': entry_pw}
            db_path.write_text(json.dumps(vault), encoding='utf-8')
            sys.stdout.write('Successfully edited entry.\\n')
            prompt()
        elif cmd == 'rm':
            name = parts[1]
            vault['entries'].pop(name, None)
            db_path.write_text(json.dumps(vault), encoding='utf-8')
            sys.stdout.write(line + '\\n')
            sys.stdout.write('Successfully removed entry.\\n')
            prompt()
        else:
            sys.stdout.write(f'Unknown command: {cmd}\\n')
            prompt()
    sys.exit(0)

sys.stderr.write(f'unsupported keepassxc-cli args: {cli_args}\\n')
sys.exit(2)
"""


def _read_events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def start(home, env):
    assert env['XDG_RUNTIME_DIR'] == '/run/user/1100'
    local_bin = home / '.local/bin'
    local_bin.mkdir(parents=True, exist_ok=True)
    env['PATH'] = str(local_bin) + ':' + env['PATH']
    stub = local_bin / 'flatpak'
    stub.write_text(FLATPAK_STUB, encoding='utf-8')
    stub.chmod(0o700)
    events_path = home / 'private-deckpass-events.jsonl'
    if events_path.exists():
        events_path.unlink()
    db_path = home / 'private-vault.kdbx'
    if db_path.exists():
        db_path.unlink()
    assert shutil.which('flatpak', path=env['PATH']) == str(stub)
    return dict(
        flatpakStub=str(stub),
        eventsPath=str(events_path),
        dbPath=str(db_path),
        privateHome=str(home),
        privateRuntime=env['XDG_RUNTIME_DIR'],
        realFlatpakContacted=False,
        realKeePassContacted=False,
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
        assert backend.returncode == 0 and not remaining and forced == 1 and seconds >= 5, (
            backend.returncode, remaining, forced, seconds, console)
        assert 'Unloaded DeckPass' not in console, console
        return dict(
            parentOnlySignal=True,
            returncode=backend.returncode,
            seconds=seconds,
            forcedPluginKills=forced,
            descendantsBefore=before,
            exactDescendantsRemaining=remaining,
            unloadedHookRan=False,
            adoptedChildExits=adopted,
            ipcPeersDisconnected=peers,
        )
    assert backend.returncode == 0 and not remaining, (backend.returncode, remaining, console)
    assert forced == 0 and seconds < 5, (forced, seconds, console)
    assert all(v['returncode'] == 0 for v in adopted), adopted
    assert 'Error during shutdown:' not in console, console
    assert 'Failed to close password manager!' not in console, console
    assert 'Unloaded DeckPass' in console, console
    assert 'Plugin DeckPass (v1.1.1) has been stopped in ' in console, console
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


def _expect_rpc_error(fn):
    try:
        fn()
    except Exception as error:
        return str(error)
    raise AssertionError('expected DeckPass RPC error')


def probe_original_runtime_failure(backend, log, current, fixture):
    # Unmodified _get_env_variables() wipes PATH and HOME, breaking ~/.local/bin/flatpak discovery.
    setup_error = _expect_rpc_error(lambda: current('check_setup_state'))
    # Unmodified close_password_manager() while idle hits AttributeError on missing KeypassCli.process.
    current('close_password_manager')
    log.flush()
    console = Path(log.name).read_text(encoding='utf-8', errors='replace')
    assert 'Failed to close password manager!' in console, console
    assert _read_events(Path(fixture['eventsPath'])) == []
    backend.send_signal(signal.SIGTERM)
    backend.wait(timeout=12)
    assert backend.returncode == 0, backend.returncode
    return dict(
        **fixture,
        reproducedOriginalRuntimeFailure=True,
        setupCheckError=setup_error,
        idleCloseLoggedError=True,
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

    events_path = Path(fixture['eventsPath'])
    db_path = Path(fixture['dbPath'])

    # 1. Setup state finds private ~/.local/bin/flatpak with preserved HOME/XDG_RUNTIME_DIR and empty LD_LIBRARY_PATH.
    setup_state = current('check_setup_state')
    assert setup_state == [True, None, fixture['privateHome']], setup_state

    # 2. Empty security token is rejected before opening any vault.
    empty_token_error = _expect_rpc_error(lambda: current('get_entries', ''))
    assert 'Failed to get entries!' in empty_token_error, empty_token_error

    # 3. create_database() succeeds for a private vault and handles errors without tuple.decode() crash.
    fail_db_error = _expect_rpc_error(
        lambda: current('create_database', str(Path(fixture['privateHome']) / 'fail.kdbx'), 'fixture-vault-pass'))
    assert 'Failed to create database!' in fail_db_error, fail_db_error
    current('create_database', str(db_path), 'fixture-vault-pass')
    assert db_path.is_file()
    assert current('does_file_exist', str(db_path)) is True
    current('set_setting', 'databasePath', str(db_path))
    assert current('get_setting', 'databasePath') == str(db_path)
    assert current('check_setup_state') == [True, str(db_path), fixture['privateHome']]

    # 4. Open vault, exercise entry CRUD + password generation + newline injection rejection.
    token = current('open_password_manager', 'fixture-vault-pass')
    assert isinstance(token, str) and len(token) >= 20, token
    started_event = until_ready(
        lambda: next((e for e in _read_events(events_path) if e['event'] == 'started'), None),
        'stub keepassxc-cli open event',
        timeout=5,
    )
    assert started_event['home'] == fixture['privateHome'], started_event
    assert started_event['xdgRuntimeDir'] == fixture['privateRuntime'], started_event
    assert started_event['ldLibraryPath'] == '', started_event
    assert started_event['uid'] == os.getuid(), started_event

    assert current('get_entries', token) == []
    current('create_entry', token, 'Frame Test', 'alice', 'pw-one')
    assert current('get_entries', token) == ['Frame Test']
    assert current('get_entry_details', token, 'Frame Test') == ['alice', 'pw-one']
    generated = current('generate_random_password', token)
    assert generated == 'StubGenPass_1234567890abcdef!@#', generated
    current('edit_entry', token, 'Frame Test', 'Frame Renamed', 'bob', 'pw-two')
    assert current('get_entries', token) == ['Frame Renamed']
    assert current('get_entry_details', token, 'Frame Renamed') == ['bob', 'pw-two']

    # Newline injection must fail and close the active session cleanly.
    injection_error = _expect_rpc_error(lambda: current('get_entry_details', token, 'Frame Renamed\nrm x'))
    assert 'Failed to get entry details!' in injection_error, injection_error
    first_stopped = until_ready(
        lambda: next((e for e in _read_events(events_path)
                      if e['event'] == 'stopped' and e['pid'] == started_event['pid']), None),
        'first stub keepassxc-cli SIGTERM stop after rejected injection',
        timeout=5,
    )
    assert first_stopped['signal'] == 'SIGTERM', first_stopped

    # Re-open vault, verify remove_entry, and leave the unlocked vault open across shutdown.
    token2 = current('open_password_manager', 'fixture-vault-pass')
    assert isinstance(token2, str) and token2 != token
    second_started = until_ready(
        lambda: next((e for e in _read_events(events_path)
                      if e['event'] == 'started' and e['pid'] != started_event['pid']), None),
        'second stub keepassxc-cli open event',
        timeout=5,
    )
    current('remove_entry', token2, 'Frame Renamed')
    assert current('get_entries', token2) == []

    if ipc_regression:
        forced_stop = stop(backend, log, True, expect_forced_kill=True)
        vault_sigterm_events = [
            e for e in _read_events(events_path)
            if e['event'] == 'stopped' and e['pid'] == second_started['pid']
        ]
        assert vault_sigterm_events == [], vault_sigterm_events
        return dict(
            **fixture,
            reproducedIpcRegression=True,
            secondStarted=second_started,
            vaultReceivedUnloadSigterm=False,
            forcedStop=forced_stop,
            unchangedPublishedFiles=unchanged,
        )

    post_eof_setup = []
    first_stop = stop(
        backend, log, True,
        after_disconnect=lambda: post_eof_setup.append(current('check_setup_state')),
    )
    assert post_eof_setup == [[True, str(db_path), fixture['privateHome']]], post_eof_setup
    second_stopped = until_ready(
        lambda: next((e for e in _read_events(events_path)
                      if e['event'] == 'stopped' and e['pid'] == second_started['pid']), None),
        'second stub keepassxc-cli SIGTERM stop during _unload',
        timeout=5,
    )
    assert second_stopped['signal'] == 'SIGTERM', second_stopped

    # Restart Decky, confirm databasePath setting persisted, and verify idle unload without errors.
    restarted = subprocess.Popen([str(loader)], env=env, stdout=log, stderr=log, start_new_session=True)
    processes.append(restarted)
    again = until_ready(lambda: rpc('loader/get_plugins'), 'restarted published Decky plugin discovery', timeout=20)
    assert again == discovered, again
    restarted_setup = current('check_setup_state')
    assert restarted_setup == [True, str(db_path), fixture['privateHome']], restarted_setup
    second_stop = stop(restarted, log, False)

    return dict(
        **fixture,
        setupState=setup_state,
        emptyTokenError=empty_token_error,
        failDbError=fail_db_error,
        injectionError=injection_error,
        firstStarted=started_event,
        firstStopped=first_stopped,
        secondStarted=second_started,
        secondStopped=second_stopped,
        postEofSetup=post_eof_setup[0],
        restartedSetup=restarted_setup,
        firstStop=first_stop,
        secondStop=second_stop,
        ipcAdapterSha256=hashlib.sha256(adapter.read_bytes()).hexdigest(),
        unchangedPublishedFiles=unchanged,
    )

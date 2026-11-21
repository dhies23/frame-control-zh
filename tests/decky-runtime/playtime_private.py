"""Synthetic PlayTime sessions and child IPC EOF lifecycle through the published loader."""
import base64
from contextlib import closing
import hashlib
import io
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile

PREFIX = (b'from framey_decky_ipc import install as _framey_install_ipc_eof\n'
          b'_framey_install_ipc_eof()\ndel _framey_install_ipc_eof\n')


def exercise(call, plugin, archive, record, base, backend, loader, env, log,
             processes, until_ready, rpc, *, regression=False):
    # Preserve every official archive member; only main.py gets the install prefix.
    hashes = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as original:
        for item in original.infolist():
            if item.is_dir():
                continue
            relative = item.filename.split('/', 1)[1]
            data = (plugin/relative).read_bytes()
            expected = original.read(item)
            if relative == 'main.py' and not regression:
                assert data == PREFIX+expected, relative
            else:
                assert data == expected, relative
            if relative.endswith('.py'):
                hashes[relative] = hashlib.sha256(data).hexdigest()
    ipc_helper = plugin/'py_modules/framey_decky_ipc.py'
    if regression:
        assert not ipc_helper.exists()
        ipc_sha = None
    else:
        ipc_sha = hashlib.sha256(ipc_helper.read_bytes()).hexdigest()

    runtime = base/'data'/record['folder']
    users = ['76561198000000001', '76561198000000002']
    game = '424242'
    paths = {user: runtime/'users'/user/'storage.db' for user in users}
    snapshots, stops = [], []
    post_eof_readback = False

    def rows(user):
        with closing(sqlite3.connect(paths[user].as_uri()+'?mode=ro', uri=True)) as db:
            return {table: db.execute(query).fetchall() for table, query in {
                'games': 'SELECT game_id,name FROM game_dict ORDER BY game_id',
                'sessions': 'SELECT game_id,duration FROM play_time ORDER BY rowid',
                'totals': 'SELECT game_id,duration FROM overall_time ORDER BY game_id',
                'tracking': 'SELECT game_id,status FROM game_tracking_status ORDER BY game_id',
            }.items()}

    def observe(user, duration, name, paused=False):
        actual = rows(user)
        assert actual == {'games': [(game, name)], 'sessions': [(game, duration)],
                          'totals': [(game, duration)],
                          'tracking': [(game, 'pause')] if paused else []}, actual
        snapshots.append({'user': user, 'rows': actual})

    def stop(process, *, disconnect=False, after_disconnect=None):
        # Signal only this parent, then require its descendants to leave without
        # the fixture's group cleanup hiding an unload stall.
        tree = {}
        for entry in Path('/proc').glob('[0-9]*'):
            try:
                fields = (entry/'stat').read_text().rsplit(')', 1)[1].split()
                tree[int(entry.name)] = (int(fields[1]), fields[19])
            except (FileNotFoundError, ProcessLookupError):
                continue
        owned = {process.pid}
        while True:
            children = {pid for pid, (parent, _) in tree.items() if parent in owned}
            if children <= owned:
                break
            owned.update(children)
        assert len(owned) >= 2
        log.flush()
        before = Path(log.name).read_text()
        closed_peers = []
        if disconnect:
            for path in Path('/tmp').glob('plugin_socket_*'):
                assert path.is_socket() and path.stat().st_uid == os.getuid()
                with socket.socket(socket.AF_UNIX) as peer:
                    peer.settimeout(.2)
                    try:
                        peer.connect(str(path))
                    except ConnectionRefusedError:
                        continue
                    peer.shutdown(socket.SHUT_WR)
                    closed_peers.append(str(path))
            assert len(closed_peers) == 1, closed_peers
            time.sleep(.05)
            if after_disconnect:
                after_disconnect()
        started = time.monotonic()
        os.kill(process.pid, signal.SIGTERM)
        assert process.wait(timeout=8) == 0
        elapsed = time.monotonic()-started
        adopted_exits = []
        def reaped():
            for pid in owned:
                # This fixture is the container's PID 1. Collect only adopted
                # descendants of this loader, after it has exited; a zombie
                # is not a running helper and needs waitpid rather than a kill.
                if pid != process.pid:
                    try:
                        child, status = os.waitpid(pid, os.WNOHANG)
                        if child:
                            adopted_exits.append({'pid': child,
                                'returncode': os.waitstatus_to_exitcode(status)})
                    except ChildProcessError:
                        pass
                try:
                    current = Path('/proc')/str(pid)/'stat'
                    if current.read_text().rsplit(')', 1)[1].split()[19] == tree[pid][1]:
                        return False
                except (FileNotFoundError, ProcessLookupError):
                    pass
            return True
        until_ready(reaped, 'owned PlayTime loader descendants reaped')
        log.flush()
        stop_console = Path(log.name).read_text()[len(before):]
        forced = stop_console.count('still alive 5 seconds after stop request! Sending SIGKILL!')
        errors = [marker for marker in ('Error during shutdown:', 'Task was destroyed but it is pending',
                  'asyncio.exceptions.CancelledError', 'Bad file descriptor') if marker in stop_console]
        unloaded = stop_console.count('Goodnight, World!')
        clean = (process.returncode == 0 and elapsed < 5 and not forced and not errors
                 and all(c['returncode'] == 0 for c in adopted_exits)
                 and stop_console.count('[main][INFO]: Shutdown finished.') == 1
                 and 'Plugin PlayTime (v3.2.5) has been stopped in ' in stop_console
                 and unloaded == 1)
        observation = {'returncode': 0, 'elapsedSeconds': elapsed,
                       'parentOnlySignal': True, 'ownedProcessCount': len(owned),
                       'adoptedChildExits': adopted_exits,
                       'ownedProcessIdentitiesReaped': True,
                       'fixtureClosedUnixPeers': closed_peers,
                       'forcedPluginKills': forced,
                       'pluginUnloadCompleted': unloaded == 1,
                       'errors': errors,
                       'consoleSha256': hashlib.sha256(stop_console.encode()).hexdigest(),
                       'cleanShutdownVerified': clean}
        stops.append(observation)
        return observation

    sibling = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                               start_new_session=True)
    processes.append(sibling)
    try:
        assert call('has_min_required_python_version') is True
        assert call('get_current_user') is None
        assert call('set_current_user', users[0]) is None
        assert call('get_current_user') == users[0]
        assert call('fetch_playtime_information') == []
        assert call('add_time', dict(started_at=1706788800, ended_at=1706789100,
                                    game_id=game, game_name='Private Alice game')) is None
        alice = call('get_game', game)
        assert alice['game'] == {'id': game, 'name': 'Private Alice game'}
        assert alice['totalTime'] == 300
        observe(users[0], 300, 'Private Alice game')
        assert call('set_game_tracking_status', {'game_id': game, 'status': 'pause'}) is True
        assert call('add_time', dict(started_at=1706789200, ended_at=1706789800,
                                    game_id=game, game_name='Private Alice game')) is None
        observe(users[0], 300, 'Private Alice game', paused=True)
        assert call('remove_game_tracking_status', game) is True
        assert call('set_current_user', users[1]) is None
        assert call('fetch_playtime_information') == []
        assert call('get_game', game) is None
        assert call('add_time', dict(started_at=1706788800, ended_at=1706789400,
                                    game_id=game, game_name='Private Bob game')) is None
        bob = call('get_game', game)
        assert bob['game'] == {'id': game, 'name': 'Private Bob game'}
        assert bob['totalTime'] == 600
        observe(users[1], 600, 'Private Bob game')
        observe(users[0], 300, 'Private Alice game')
        assert call('set_current_user', users[0]) is None
        assert call('get_game', game) == alice

        def verify_after_eof():
            nonlocal post_eof_readback
            assert call('get_game', game) == alice
            observe(users[0], 300, 'Private Alice game')
            post_eof_readback = True

        first = stop(backend, disconnect=True,
                     after_disconnect=None if regression else verify_after_eof)
        assert sibling.poll() is None, 'unrelated sibling must survive loader shutdown'
        if regression:
            assert first['forcedPluginKills'] == 1, first
            assert not first['cleanShutdownVerified'] and not first['pluginUnloadCompleted'], first
            assert 5 <= first['elapsedSeconds'] < 8 and first['ownedProcessIdentitiesReaped'], first
            observe(users[0], 300, 'Private Alice game')
            observe(users[1], 600, 'Private Bob game')
        else:
            assert first['cleanShutdownVerified'] and post_eof_readback, first
            restarted = subprocess.Popen([str(loader)], env=env, stdout=log, stderr=log,
                                         start_new_session=True)
            processes.append(restarted)
            found = until_ready(lambda: rpc('loader/get_plugins'), 'restarted PlayTime discovery')
            assert len(found) == 1 and found[0]['name'] == record['name'] and not found[0]['disabled']
            assert call('get_current_user') is None
            for user, expected, name, duration in [(users[0], alice, 'Private Alice game', 300),
                                                    (users[1], bob, 'Private Bob game', 600)]:
                assert call('set_current_user', user) is None
                assert call('get_game', game) == expected
                observe(user, duration, name)
            second = stop(restarted, disconnect=False)
            assert sibling.poll() is None, 'unrelated sibling must survive second loader shutdown'
            assert second['cleanShutdownVerified'], second
        databases = {}
        for user, path in paths.items():
            # Read a self-contained committed snapshot, including any WAL pages.
            # The read-only observer does not checkpoint or change plugin data.
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
                payload = db.serialize()
            databases[user] = {'sha256': hashlib.sha256(payload).hexdigest(),
                               'base64': base64.b64encode(payload).decode(),
                               'kind': 'read-only SQLite serialized snapshot'}
        assert not (runtime/'storage.db').exists(), 'the fixture must not create a legacy database'
        log.flush()
        log.seek(0)
        console = log.read()
        expected_starts = 1 if regression else 2
        assert console.count('Starting Decky version v3.3.0') == expected_starts
        assert console.count('[main][INFO]: Shutdown finished.') == expected_starts
        if not regression:
            assert console.count('Goodnight, World!') == 2
            for marker in ['Error during shutdown:', 'Task was destroyed but it is pending',
                           'asyncio.exceptions.CancelledError', 'Bad file descriptor']:
                assert marker not in console, marker
        return {'publishedArchiveFilesPreserved': True, 'backendSourceSha256': hashes,
                'ipcAdapterSha256': ipc_sha, 'expectedRegression': regression,
                'postEofSessionReadback': post_eof_readback,
                'unrelatedSiblingPreserved': sibling.poll() is None,
                'sessionReadbacks': snapshots, 'finalDatabases': databases, 'ownedLoaderStops': stops,
                'loaderConsole': console, 'loaderConsoleSha256': hashlib.sha256(console.encode()).hexdigest(),
                'privateSessionRpcVerified': True, 'perUserIsolationVerified': True,
                'pauseSkipsSessionVerified': True, 'restartPersistenceVerified': not regression,
                'cleanShutdownVerified': not regression,
                'automaticRecordingVerified': False, 'realSteamLibraryVerified': False,
                'browserVerified': False, 'realFrameContacted': False,
                'physicalControllerVerified': False}
    finally:
        if sibling.poll() is None:
            sibling.terminate()
        sibling.wait(timeout=5)

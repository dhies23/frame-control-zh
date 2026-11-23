"""PC adapters: grants, input cleanup, launcher reuse and native codec contract.
Native tests require the bundled libraries; CI sets REQUIRE_NATIVE so a missing
bundle is a failure, never a silent skip. No desktop consent or GPU is faked as
successful real-host capture.

The encoding tests need x264, which only CI's own builds bundle (it's GPL, so
the installer leaves it out). FRAME_PC_REQUIRE_X264=1 makes its absence a
failure; FRAME_PC_INSTALLER_BUNDLE=1 checks the installer's bundle instead.
"""
import ctypes
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'ui'))
import frame_pc_agent as agent
import frame_pc_capture as capture
import frame_pcview
import frame_macview
from frame_stream_stats import Stats, distribution
from test_macview import WS


class GrantsTests(unittest.TestCase):
    def test_source_bound_retry_ack_and_stop(self):
        g = agent.Grants('master')
        ticket = g.ticket('window:1')
        self.assertIsNone(g.redeem('window:2', {'t': ticket}))
        key = g.redeem('window:1', {'t': ticket})
        self.assertEqual(key, g.redeem('window:1', {'t': ticket}))
        g.ack(key)
        self.assertIsNone(g.redeem('window:1', {'t': ticket}))
        self.assertEqual(key, g.redeem('window:1', {'r': key}))
        self.assertIsNone(g.redeem('window:2', {'r': key}))
        g.revoke('window:1')
        self.assertIsNone(g.redeem('window:1', {'r': key}))

    def test_expiry_and_stop_before_redeem(self):
        g = agent.Grants('master')
        with mock.patch.object(agent.time, 'monotonic', return_value=1):
            ticket = g.ticket('test')
        with mock.patch.object(agent.time, 'monotonic', return_value=62):
            self.assertIsNone(g.redeem('test', {'t': ticket}))
        ticket = g.ticket('test')
        g.revoke()
        self.assertIsNone(g.redeem('test', {'t': ticket}))


class GatingTests(unittest.TestCase):
    """Experimental, Windows only, and off until the person turns it on."""

    def view(self):
        return frame_pcview.PCView(['ssh'], mock.Mock(), 'frame')

    def test_only_windows_gets_the_pc_host(self):
        for platform, cls in (('win32', frame_pcview.PCView), ('linux', frame_macview.MacView),
                              ('darwin', frame_macview.MacView)):
            with mock.patch.object(frame_pcview.sys, 'platform', platform):
                view = frame_pcview.host_view(['ssh'], mock.Mock(), 'frame')
            self.assertIs(type(view), cls, platform)

    def test_off_by_default_starts_nothing(self):
        view = self.view()
        with mock.patch.object(frame_pcview.sys, 'platform', 'win32'), \
                mock.patch.object(frame_macview.subprocess, 'Popen', side_effect=AssertionError('started while off')):
            state = view.state()
            self.assertEqual(state, {'available': False, 'reason': frame_pcview.OFF, 'host': view.host,
                                     'experimental': True, 'enabled': False})
            with self.assertRaises(frame_macview.MacViewError):
                view.show('window:12')
        view.run.assert_not_called()

    def test_never_offered_off_windows_even_when_turned_on(self):
        view = self.view()
        view.enabled = True
        with mock.patch.object(frame_pcview.sys, 'platform', 'linux'):
            self.assertIn('only available on Windows', view.unavailable())

    def test_turning_on_checks_for_the_bundled_libraries(self):
        view = self.view()
        missing = mock.Mock(is_file=mock.Mock(return_value=False))
        with mock.patch.object(frame_pcview.sys, 'platform', 'win32'), \
                mock.patch.object(frame_pcview, 'LIBRARY', missing):
            state = view.set_enabled(True)
        self.assertTrue(state['enabled'])
        self.assertFalse(state['available'])
        self.assertIn('libraries are missing', state['reason'])

    def test_turning_off_stops_streams_and_the_helper(self):
        view = self.view()
        view.enabled = True
        agent = mock.Mock()
        agent.poll.return_value = None
        view.agent, view.tunnel = agent, mock.Mock()
        tunnel = view.tunnel
        tunnel.poll.return_value = None
        view.stop = mock.Mock()
        with mock.patch.object(frame_pcview.sys, 'platform', 'win32'):
            state = view.set_enabled(False)
        view.stop.assert_called_once_with()
        agent.stdin.close.assert_called_once()
        agent.wait.assert_called()
        tunnel.terminate.assert_called_once()
        self.assertIsNone(view.agent)
        self.assertEqual((state['enabled'], state['reason']), (False, frame_pcview.OFF))

    def test_server_enable_action(self):
        import sandbox  # noqa: F401  (keeps the server off real data and services)
        import server
        with mock.patch.object(server, 'macview', frame_macview.MacView(['ssh'], mock.Mock(), 'frame')):
            with self.assertRaises(server.Failure) as e:
                server.macview_action({'action': 'enable', 'on': True})
            self.assertIn('only available on Windows', str(e.exception))
        view = self.view()
        view.set_enabled = mock.Mock(return_value={'enabled': True})
        with mock.patch.object(server, 'macview', view):
            self.assertEqual(server.macview_action({'action': 'enable', 'on': True}), {'enabled': True})
            server.macview_action({'action': 'enable', 'on': 'yes'})  # anything but true is off
        self.assertEqual([c.args for c in view.set_enabled.call_args_list], [(True,), (False,)])


class PageTests(unittest.TestCase):
    page = (ROOT / 'ui' / 'index.html').read_text(encoding='utf-8')

    def test_tool_link_is_windows_only_and_marked_experimental(self):
        self.assertIn('<a href="#macview" id="pcToolLink" hidden><span>PC in the headset</span>'
                      '<small>Experimental:', self.page)
        self.assertIn('$("pcToolLink").hidden = h.mobile || h.os !== "Windows";', self.page)
        self.assertIn('$("macToolLink").hidden = h.mobile || h.os !== "macOS";', self.page)

    def test_card_says_experimental_and_what_is_unverified(self):
        self.assertIn('<span class="tag" id="mvTag" hidden>Experimental</span>', self.page)
        self.assertIn('<input type="checkbox" id="mvPcOn">', self.page)
        self.assertIn('Tested in a Windows VM; physical PCs, hardware encoding and laser clicks remain unverified.', self.page)
        self.assertIn('docs/pc-in-headset.md', self.page)

    def test_opt_in_is_remembered_with_the_client_preferences(self):
        self.assertIn("const keys = ['mvSeparate', 'padUsed', 'pcView']", self.page)
        prefs = (ROOT / 'app' / 'preferences.js').read_text(encoding='utf-8')
        self.assertIn('const KEYS = ["mvSeparate", "padUsed", "pcView"];', prefs)


class UpgradeCleanupTests(unittest.TestCase):
    def request(self, host, output, *, path='/stream?src=test&k=master', headers=None):
        # Exercise the production HTTP header flush, Session, controller
        # and WebSocket lifecycle with private in-memory transport only.
        handler = object.__new__(agent.Handler)
        handler.server = mock.Mock(agent=host)
        handler.connection = mock.Mock()
        handler.rfile, handler.wfile = io.BytesIO(), output
        handler.request_version, handler.command = 'HTTP/1.1', 'GET'
        handler.path = path
        handler.requestline = 'GET '+path+' HTTP/1.1'
        handler.headers = {'Upgrade': 'websocket', 'Sec-WebSocket-Version': '13',
                           'Sec-WebSocket-Key': 'MDEyMzQ1Njc4OWFiY2RlZg=='}
        handler.headers.update(headers or {})
        handler.close_connection = False
        handler.dispatch('GET')
        return handler

    def test_failed_upgrade_releases_each_session_and_allows_a_fresh_viewer(self):
        native = mock.Mock()
        native.lib.fc_new.side_effect = range(1, 10)
        with mock.patch.object(agent.sys, 'platform', 'linux'):
            host = agent.Agent(native, 'master', ROOT/'ui/mac-view.html')

        class DisconnectedViewer(io.BytesIO):
            def write(self, data):
                raise BrokenPipeError('viewer left during the HTTP upgrade')

        # Eight failed upgrades used to retain all eight slots and controllers.
        # No source capture or desktop input is permitted by this test.
        with mock.patch.object(agent.Session, 'produce', side_effect=AssertionError('unexpected capture')):
            for attempt in range(8):
                with self.subTest(attempt=attempt):
                    handler = self.request(host, DisconnectedViewer())
                    self.assertTrue(handler.close_connection)
                    self.assertEqual(host.sessions, {})
                    self.assertEqual(native.lib.fc_free.call_args_list,
                                     [mock.call(i) for i in range(1, attempt+2)])
                    handler.connection.shutdown.assert_called()

        # The next upgrade is admitted, then the private viewer immediately
        # reaches EOF. Only its producer is a no-op; normal cleanup still runs.
        output = io.BytesIO()
        with mock.patch.object(agent.Session, 'produce') as produce:
            handler = self.request(host, output)
        self.assertTrue(output.getvalue().startswith(b'HTTP/1.1 101 '))
        produce.assert_called_once_with()
        self.assertTrue(handler.close_connection)
        self.assertEqual(host.sessions, {})
        self.assertEqual(native.lib.fc_free.call_args_list, [mock.call(i) for i in range(1, 10)])

    def test_validation_errors_preserve_an_existing_session_and_http_status(self):
        native, existing = mock.Mock(), mock.Mock()
        with mock.patch.object(agent.sys, 'platform', 'linux'):
            host = agent.Agent(native, 'master', ROOT/'ui/mac-view.html')
        host.sessions[99] = existing
        for path, headers, status in [
                ('/stream?src=test&k=wrong', {}, 403),
                ('/stream?src=test&k=master', {'Sec-WebSocket-Version': '12'}, 400),
                ('/stream?src=test&k=master', {'Sec-WebSocket-Key': 'eA=='}, 400)]:
            with self.subTest(status=status, headers=headers):
                output = io.BytesIO()
                handler = self.request(host, output, path=path, headers=headers)
                self.assertTrue(output.getvalue().startswith(('HTTP/1.1 %s ' % status).encode()))
                self.assertTrue(handler.close_connection)
                self.assertEqual(host.sessions, {99: existing})
                existing.end.assert_not_called()
                native.lib.fc_new.assert_not_called()
                native.lib.fc_free.assert_not_called()

    def test_producer_startup_failure_releases_session_and_allows_retry(self):
        native = mock.Mock()
        native.lib.fc_new.side_effect = range(1, 4)
        with mock.patch.object(agent.sys, 'platform', 'linux'):
            host = agent.Agent(native, 'master', ROOT/'ui/mac-view.html')
        for count, phase in enumerate(('construct', 'start'), 1):
            with self.subTest(phase=phase):
                failure = (mock.patch.object(agent.threading, 'Thread',
                                             side_effect=RuntimeError('producer unavailable'))
                           if phase == 'construct' else
                           mock.patch.object(agent.threading.Thread, 'start',
                                             side_effect=RuntimeError('producer unavailable')))
                with failure, mock.patch.object(agent.Session, 'produce') as produce:
                    handler = self.request(host, io.BytesIO())
                produce.assert_not_called()
                self.assertTrue(handler.close_connection)
                handler.connection.shutdown.assert_called()
                self.assertEqual(host.sessions, {})
                self.assertEqual(native.lib.fc_free.call_args_list,
                                 [mock.call(i) for i in range(1, count+1)])
        output = io.BytesIO()
        with mock.patch.object(agent.Session, 'produce') as produce:
            handler = self.request(host, output)
        produce.assert_called_once_with()
        self.assertTrue(output.getvalue().startswith(b'HTTP/1.1 101 '))
        self.assertTrue(handler.close_connection)
        self.assertEqual(host.sessions, {})
        self.assertEqual(native.lib.fc_free.call_args_list, [mock.call(i) for i in range(1, 4)])

    def test_portal_select_failures_reset_selecting_and_close_partial_portal(self):
        native = mock.Mock()
        with mock.patch.object(agent.sys, 'platform', 'linux'):
            host = agent.Agent(native, 'master', ROOT/'ui/mac-view.html')

        # 1. Thread construction and start failures must not leave host.selecting stuck True.
        for phase in ('construct', 'start'):
            with self.subTest(phase=phase):
                failure = (mock.patch.object(agent.threading, 'Thread',
                                             side_effect=RuntimeError('picker thread unavailable'))
                           if phase == 'construct' else
                           mock.patch.object(agent.threading.Thread, 'start',
                                             side_effect=RuntimeError('picker thread unavailable')))
                with failure:
                    with self.assertRaises(RuntimeError):
                        host.select()
                self.assertFalse(host.selecting)
                self.assertEqual(host.sources, {})

        # Helper to run the spawned Thread synchronously via a real Thread + join.
        spawned = []
        orig_thread = agent.threading.Thread
        def capture_thread(*args, **kwargs):
            t = orig_thread(*args, **kwargs)
            spawned.append(t)
            return t

        # 2. fc_portal_select raising must reset selecting and record selection_error.
        native.lib.fc_portal_select.side_effect = RuntimeError('portal dbus unavailable')
        with mock.patch.object(agent.threading, 'Thread', side_effect=capture_thread):
            host.select()
        spawned.pop().join(5)
        self.assertFalse(host.selecting)
        self.assertIn('portal dbus unavailable', host.selection_error)
        self.assertEqual(host.sources, {})
        native.lib.fc_portal_close.assert_not_called()

        # 3. fc_portal_value raising after portal open must close the leaked portal handle.
        native.lib.fc_portal_select.side_effect = None
        native.lib.fc_portal_select.return_value = 99
        native.lib.fc_portal_value.side_effect = RuntimeError('bad portal metadata')
        with mock.patch.object(agent.threading, 'Thread', side_effect=capture_thread):
            host.select()
        spawned.pop().join(5)
        self.assertFalse(host.selecting)
        self.assertIn('bad portal metadata', host.selection_error)
        self.assertEqual(host.sources, {})
        native.lib.fc_portal_close.assert_called_once_with(99)

        # 4. Subsequent clean selection succeeds and clears selection_error.
        native.lib.fc_portal_select.return_value = 100
        native.lib.fc_portal_value.side_effect = [12, 34, 1920, 1080, 3]
        with mock.patch.object(agent.threading, 'Thread', side_effect=capture_thread):
            host.select()
        spawned.pop().join(5)
        self.assertFalse(host.selecting)
        self.assertEqual(host.selection_error, '')
        self.assertEqual(len(host.sources), 1)
        source = next(iter(host.sources.values()))
        self.assertEqual((source['fd'], source['node'], source['w'], source['h'], source['portal'], source['devices']),
                         (12, 34, 1920, 1080, 100, 3))


class AdapterTests(unittest.TestCase):
    def test_dxgi_only_bundle_captures_displays_without_optional_wgc_properties(self):
        p = capture.pipeline(dict(src='display:65537', capture_api='dxgi'), 'win32', 'mfh264enc', 640, 360, 30, 1000000)
        self.assertIn('monitor-handle=65537', p)
        self.assertNotIn('capture-api=', p)
        self.assertNotIn('show-border=', p)
        self.assertIn('mfh264enc', p)
        for source in (dict(src='window:42', capture_api='dxgi'),
                       dict(src='display:42', capture_api='dxgi ! fakesink')):
            with self.assertRaises(ValueError):
                capture.pipeline(source, 'win32', 'mfh264enc', 640, 360, 30, 1000000)

    def test_windows_sources_follow_actual_wgc_capabilities(self):
        for wgc in (False, True):
            with self.subTest(wgc=wgc):
                native, windows = mock.Mock(), mock.Mock()
                native.has_property.return_value = wgc
                windows.sources.side_effect = lambda: ([dict(src='window:12', w=800, h=600)],
                                                       [dict(src='display:4', w=1920, h=1080)])
                with mock.patch.object(agent.sys, 'platform', 'win32'), \
                        mock.patch.object(agent, 'Windows', return_value=windows):
                    host = agent.Agent(native, 'test', ROOT/'ui/mac-view.html')
                wins, displays = host.lists()
                self.assertEqual(len(wins), int(wgc))
                self.assertEqual(displays[0]['capture_api'], 'wgc' if wgc else 'dxgi')
                self.assertEqual(host.source('display:4')['capture_api'], displays[0]['capture_api'])
                if wgc:
                    self.assertEqual(host.source('window:12')['capture_api'], 'wgc')
                    self.assertEqual(host.window_capture_reason, '')
                else:
                    with self.assertRaisesRegex(ValueError, 'Individual windows are unavailable'):
                        host.source('window:12')
                    self.assertIn('whole screen', host.window_capture_reason)
                # Synthetic encoder/protocol checks remain available without WGC.
                self.assertEqual(host.source('test')['src'], 'test')

    def test_pc_card_receives_window_capture_limitation(self):
        view = frame_pcview.PCView([], lambda *a: None, 'frame')
        view.enabled = True
        view.call = mock.Mock(side_effect=lambda path: {
            '/status': dict(screen=True, accessibility=True, windowCaptureReason=agent.WINDOW_CAPTURE_UNAVAILABLE),
            '/windows': dict(windows=[]), '/displays': dict(displays=[dict(src='display:4')])}[path])
        with mock.patch.object(frame_pcview.sys, 'platform', 'win32'), \
                mock.patch.object(frame_pcview, 'LIBRARY', mock.Mock(is_file=lambda: True)):
            state = view.state()
        self.assertTrue(state['available'])
        self.assertEqual(state['windowCaptureReason'], agent.WINDOW_CAPTURE_UNAVAILABLE)
        self.assertEqual(state['windows'], [])
        self.assertEqual(state['displays'], [dict(src='display:4')])

    def test_windows_uses_wgc_and_media_foundation(self):
        for kind in ('window', 'display'):
            pipeline = capture.pipeline(dict(src=kind+':42'), 'win32', 'mfh264enc', 640, 360, 30, 1000000)
            self.assertIn('capture-api=wgc', pipeline)
            self.assertIn('window-handle=42' if kind == 'window' else 'monitor-handle=42', pipeline)
            self.assertIn('mfh264enc name=enc low-latency=true bframes=0', pipeline)
            self.assertNotIn('drop=true', pipeline)
        with self.assertRaises(ValueError):
            capture.pipeline(dict(src='window:42 ! fakesink'), 'win32', 'mfh264enc', 640, 360, 30, 1000000)

    def test_linux_uses_only_portal_fd_and_node(self):
        p = capture.pipeline(dict(src='window:consented', fd=19, node=47), 'linux', 'x264enc', 640, 360, 30, 1000000)
        self.assertIn('pipewiresrc fd=19 path=47', p)
        self.assertIn('tune=zerolatency', p)
        self.assertEqual(capture.dimensions(1920, 1080, 1280), (1280, 720))

    def test_portal_input_clamps_and_releases_held_state(self):
        native = mock.Mock()
        native.lib.fc_portal_input.return_value = 1
        inp = capture.PortalInput(native, dict(portal=123, w=100, h=50))
        inp.handle(dict(t='m', e='down', b=0, x=2, y=-1))
        inp.handle(dict(t='k', e='down', key='Control'))
        inp.release()
        calls = native.lib.fc_portal_input.call_args_list
        self.assertEqual(calls[0].args, (123, 0, 99, 0, 0, 0))
        self.assertIn(mock.call(123, 1, 0, 0, 0x110, 0), calls)
        self.assertIn(mock.call(123, 3, 0, 0, 0xffe3, 0), calls)
        self.assertFalse(inp.buttons or inp.keys)

    def test_windows_release_and_negative_monitor_origin(self):
        host = mock.Mock()
        host.user.GetSystemMetrics.side_effect = [-1920, 0, 3840, 1080]
        inp = capture.WindowsInput(host, dict(src='display:4', x=-1920, y=0, w=1920, h=1080))
        inp.handle(dict(t='m', e='down', b=2, x=0, y=0))
        self.assertEqual(host.send.call_args_list[0], mock.call(mouse=(0, 0, 0, 0xc001)))
        inp.handle(dict(t='k', e='down', code='ControlLeft', key='Control'))
        inp.release()
        self.assertIn(mock.call(mouse=(0, 0, 0, 0x10)), host.send.call_args_list)
        self.assertIn(mock.call(key=(162, 0, 2)), host.send.call_args_list)

    def test_refused_windows_focus_never_clicks_the_covering_app(self):
        host = mock.Mock()
        host.rect.return_value = (0, 0, 800, 600)
        host.user.GetForegroundWindow.return_value = 99
        host.user.SetForegroundWindow.return_value = False
        inp = capture.WindowsInput(host, dict(src='window:12', w=800, h=600))
        with self.assertRaisesRegex(RuntimeError, 'could not focus'):
            inp.handle(dict(t='m', e='down', b=0, x=.5, y=.5))
        host.send.assert_not_called()

    def test_pc_reuses_launcher_and_namespaces_panel_ids(self):
        view = frame_pcview.PCView(['ssh'], mock.Mock(return_value='panel created'), 'frame')
        view.call = mock.Mock(side_effect=lambda path, **kw: {'screen': True, 'ticket': 'one-use', 'streams': []})
        view.ensure_tunnel = mock.Mock()
        view.remote_port = 47900
        view.show('window:12')
        self.assertEqual(view.run.call_args.kwargs['stdin'], frame_macview.LAUNCH)
        self.assertNotEqual(frame_macview.panel_id('window:12'), frame_macview.panel_id('window:12', 'windows'))
        self.assertFalse(view.prefer_usb)
        with self.assertRaises(frame_macview.MacViewError):
            view.show('separate:12')

    def test_session_end_releases_native_input_only_once(self):
        session = object.__new__(agent.Session)
        session.lock, session.input_lock = threading.RLock(), threading.RLock()
        session.stop_event = threading.Event()
        session.ws, session.input = mock.Mock(), mock.Mock()
        session.released = False
        session.end()
        session.end()
        session.input.release.assert_called_once()
        self.assertTrue(session.stop_event.is_set())

    def test_idle_source_is_not_a_stall(self):
        # PipeWire/WGC deliver frames only on damage: a static desktop sends
        # its first frame and then nothing. That must not end the session.
        session = object.__new__(agent.Session)
        session.lock, session.pending = threading.RLock(), {}
        session.stats = Stats(lambda: 0)
        session.stats.captured = 1
        self.assertIsNone(session.stalled(60000000, 0, 0))
        # A frame that entered the encoder and never came out is a fault.
        session.pending[5] = dict(cap=0, arr=0, e0=1000000, tier=0, br=0)
        self.assertIn('encoder', session.stalled(12000000, 0, 0))
        self.assertIsNone(session.stalled(10000000, 0, 0))
        # No initial frame at all after the pipeline opened is a fault.
        session.pending.clear()
        self.assertIn('capture', session.stalled(12000000, 0, 1))
        self.assertIsNone(session.stalled(9000000, 0, 1))

    def test_controller_is_inert_after_close(self):
        lib = mock.Mock()
        lib.fc_new.return_value = 1234
        lib.fc_value.return_value = 100
        controller = capture.Controller(lib, 60, 5000000)
        controller.close()
        lib.fc_free.assert_called_once_with(1234)
        lib.reset_mock()
        state = controller.state()
        self.assertEqual((state['fps'], state['target'], state['scale']), (60, 0, 1))
        self.assertEqual(controller.call('gate', 1, 1), 0)
        self.assertEqual(controller.update(1), 0)
        controller.close()
        self.assertEqual(lib.method_calls, [])  # nothing touched the freed pointer

    def test_library_path_prepend_adds_no_empty_entry(self):
        env = {}
        frame_pcview.prepend(env, 'LD_LIBRARY_PATH', '/opt/fc/lib')
        self.assertEqual(env['LD_LIBRARY_PATH'], '/opt/fc/lib')
        frame_pcview.prepend(env, 'LD_LIBRARY_PATH', '/x')
        self.assertEqual(env['LD_LIBRARY_PATH'], '/x' + os.pathsep + '/opt/fc/lib')
        with mock.patch.dict(os.environ, clear=True):
            env = frame_pcview.PCView([], lambda *a: None, 'frame').agent_environment()
        for name in ('PATH', 'LD_LIBRARY_PATH'):
            parts = env.get(name, 'x').split(os.pathsep)
            self.assertNotIn('', parts, name)

    def test_malformed_viewer_input_is_dropped_not_fatal(self):
        session = object.__new__(agent.Session)
        session.lock, session.input_lock = threading.RLock(), threading.RLock()
        session.stop_event, session.key_event = threading.Event(), threading.Event()
        session.src, session.codec, session.key, session.w, session.h = 'display:1', 'h264', 'r', 100, 100
        session.source = dict(src='display:1', w=100, h=100, portal=1)
        session.input_enabled, session.released = True, False
        session.native = mock.Mock()
        session.native.now.return_value = 0
        session.controller, session.agent = mock.Mock(), mock.Mock()
        session.stats = Stats(lambda: 0)
        session.produce = lambda: None
        lib = mock.Mock()
        lib.fc_portal_input.return_value = 1
        session.input = capture.PortalInput(mock.Mock(lib=lib), session.source)
        session.ws = mock.Mock()
        session.ws.receive.side_effect = [
            dict(t='m', x='left', y=0), dict(t='wheel', dx=[1]), dict(t='k', e='down', key=None, i='x'),
            dict(t='fd', drop='many'), dict(t='rx', s=[1]), dict(t='m', x=.5, y=.5), ConnectionError('closed')]
        with self.assertRaises(ConnectionError):
            session.start()
        # Every malformed message was skipped; the good move after them still ran.
        moves = [c.args for c in lib.fc_portal_input.call_args_list if c.args[1] == 0]
        self.assertAlmostEqual(moves[-1][2], .5*99)
        self.assertEqual(session.ws.receive.call_count, 7)

    def test_stats_keep_capture_time_and_bound_records(self):
        stats = Stats(lambda: 10000000)
        stats.input(dict(t='m', i=1, tv=9999990))
        f = stats.add(cap=10000001, arr=10000002, e0=10000003, e1=10000004)
        self.assertEqual(f['echo'], 1)
        stats.report(dict(t='rx', s=f['s'], r=10000010))
        stats.report(dict(t='fd', f=[[f['s'], 10000011, 10000012, 10000013]]))
        self.assertEqual(stats.snapshot(settle=0)['inputs'][0]['frame'], f['s'])
        for _ in range(4100):
            stats.add(cap=10000001)
        self.assertEqual(len(stats.frames), 4096)

    def test_stats_ignore_malformed_nonfinite_and_bool_reports(self):
        stats = Stats(lambda: 10000000)
        f1 = stats.add(cap=10000001)
        f2 = stats.add(cap=10000002)
        stats.report(None)
        stats.report({})
        stats.report(dict(t='rx', s=True, r=999))
        stats.report(dict(t='rx', s=[1], r=999))
        stats.report(dict(t='rx', s=f1['s'], r=True))
        stats.report(dict(t='rx', s=f1['s'], r=float('nan')))
        stats.report(dict(t='rx', s=f1['s'], r=float('inf')))
        self.assertEqual(f1['rx'], 0)
        stats.report(dict(t='rx', s=f1['s'], r=10000010))
        self.assertEqual(f1['rx'], 10000010)

        stats.report(dict(t='fd', f=None, drop=2))
        self.assertEqual(stats.dropped, 2)
        stats.report(dict(t='fd', f=[
            [True, 11, 12, 13],
            [[1], 14, 15, 16],
            [f1['s'], float('nan'), float('inf'), True],
            [f2['s'], 21, 22, 23],
        ], drop=3))
        self.assertEqual((f1['dec'], f1['drw'], f1['vs']), (0, 0, 0))
        self.assertEqual((f2['dec'], f2['drw'], f2['vs']), (21, 22, 23))
        self.assertEqual(stats.dropped, 5)
        for bad_drop in (True, False, -4, 'many', None, float('nan'), float('inf')):
            stats.report(dict(t='fd', f=[], drop=bad_drop))
        self.assertEqual(stats.dropped, 5)

        for bad_rtt in (float('nan'), float('inf'), float('-inf'), -1, True, 'bad', None):
            stats.report(dict(t='clock', rtt=bad_rtt, dec='bad'))
        self.assertEqual((stats.rtt, stats.decoder, stats.synced), (0, '', False))
        stats.report(dict(t='clock', rtt=4.25, dec=None))
        self.assertEqual((stats.rtt, stats.decoder, stats.synced), (4.25, '', True))
        self.assertEqual(distribution([1.0, float('nan'), float('inf'), True, None, 3.0]), {'p50': 3.0, 'p95': 3.0})

    def test_stats_input_bounds_and_sequence_wrap_skip_zero(self):
        now = [10000000]
        stats = Stats(lambda: now[0])
        for bad in (None, dict(t='m', i=True), dict(t='m', i=0), dict(t='m', i=-1), dict(t='m', i=1 << 32)):
            stats.input(bad)
        self.assertEqual(stats.inputs, {})

        stats.input(dict(t='m', i=7, tv=float('nan')))
        self.assertEqual(stats.inputs[7]['tv'], 0)

        stats.sequence = 0xffffffff
        wrapped = stats.add(cap=10000001)
        self.assertEqual(wrapped['s'], 1)
        self.assertEqual(wrapped['echo'], 7)
        self.assertEqual(stats.inputs[7]['frame'], 1)
        later = stats.add(cap=10000002)
        self.assertEqual(later['s'], 2)
        self.assertEqual(later['echo'], 0)
        now[0] = 10000010
        self.assertEqual([f['s'] for f in stats.snapshot(since=0, settle=0)['frames']], [1, 2])

    def test_rx_bool_sequence_does_not_ack_controller(self):
        session = agent.Session.__new__(agent.Session)
        session.lock, session.input_lock = threading.RLock(), threading.RLock()
        session.stop_event, session.key_event = threading.Event(), threading.Event()
        session.src, session.codec, session.key, session.w, session.h = 'display:1', 'h264', 'r', 100, 100
        session.source = dict(src='display:1', w=100, h=100, portal=1)
        session.input_enabled, session.released = True, False
        session.native = mock.Mock()
        session.native.now.return_value = 0
        session.controller, session.agent = mock.Mock(), mock.Mock()
        session.stats = Stats(lambda: 0)
        session.produce = lambda: None
        session.input = None
        session.ws = mock.Mock()
        session.ws.receive.side_effect = [dict(t='rx', s=True, r=100), ConnectionError('closed')]
        with self.assertRaises(ConnectionError):
            session.start()
        session.controller.call.assert_not_called()


def x264_bundled():
    return any((capture.NATIVE / 'lib' / 'gstreamer-1.0').glob('*gstx264*'))


def needs_x264(test):
    def run(self):
        if not x264_bundled():
            if os.environ.get('FRAME_PC_REQUIRE_X264') == '1':
                raise AssertionError('CI required x264 in the native PC bundle (desktop/build.py --with-x264)')
            raise unittest.SkipTest('x264 is only in CI test bundles, not the installer')
        return test(self)
    run.__name__, run.__doc__ = test.__name__, test.__doc__
    return run


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not capture.LIBRARY.exists():
            if os.environ.get('FRAME_PC_REQUIRE_NATIVE') == '1':
                raise AssertionError('CI required native PC libraries but they were not built')
            raise unittest.SkipTest('native PC libraries not built on this host')
        cls.token = 'test-' + os.urandom(8).hex()
        env = frame_pcview.PCView([], lambda *a: None, 'frame').agent_environment()
        env['FRAME_MAC_VIEW_TOKEN'] = cls.token
        cls.proc = subprocess.Popen([sys.executable, str(ROOT/'ui/frame_pc_agent.py'), 'serve', '--port', '0',
            '--page', str(ROOT/'ui/mac-view.html'), '--exit-on-eof'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env)
        line = cls.proc.stdout.readline()
        if 'listening' not in line:
            raise AssertionError('Native agent failed: ' + cls.proc.stderr.read())
        cls.port = int(line.rsplit(':', 1)[1])

    @classmethod
    def tearDownClass(cls):
        cls.proc.stdin.close()
        try:
            rc = cls.proc.wait(10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            raise AssertionError('Native agent did not stop after EOF')
        error = cls.proc.stderr.read()
        cls.proc.stdout.close()
        cls.proc.stderr.close()
        if rc:
            raise AssertionError('Native agent exited %s: %s' % (rc, error))

    def request(self, path, method='GET'):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def tearDown(self):
        self.request('/close?k='+self.token, 'POST')

    def test_native_property_probe_checks_the_instantiated_element(self):
        code = """
from frame_pc_capture import Native
n = Native()
assert n.has_property('fakesink', 'sync')
assert not n.has_property('fakesink', 'not-a-gstreamer-property')
assert not n.has_property('not-a-gstreamer-element', 'sync')
"""
        env = frame_pcview.PCView([], lambda *a: None, 'frame').agent_environment()
        env['PYTHONPATH'] = str(ROOT/'ui')
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux PipeWire client libraries')
    def test_bundled_pipewire_context_needs_no_system_modules(self):
        # No portal request, remote connection or desktop capture: create and
        # destroy a private client context to test the dynamically loaded SPA
        # and protocol modules that ldd cannot discover.
        code = """
import ctypes as C, os
lib = C.CDLL(os.path.join(os.environ['FRAME_PC_NATIVE'], 'lib', 'libpipewire-0.3.so.0'))
for name, ret, args in [
    ('pw_init', None, [C.c_void_p, C.c_void_p]),
    ('pw_main_loop_new', C.c_void_p, [C.c_void_p]),
    ('pw_main_loop_get_loop', C.c_void_p, [C.c_void_p]),
    ('pw_context_new', C.c_void_p, [C.c_void_p, C.c_void_p, C.c_size_t]),
    ('pw_context_find_factory', C.c_void_p, [C.c_void_p, C.c_char_p]),
    ('pw_context_find_spa_lib', C.c_char_p, [C.c_void_p, C.c_char_p]),
    ('pw_context_destroy', None, [C.c_void_p]),
    ('pw_main_loop_destroy', None, [C.c_void_p])]:
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = ret, args
lib.pw_init(None, None)
loop = lib.pw_main_loop_new(None)
assert loop, 'bundled SPA loop support did not load'
context = lib.pw_context_new(lib.pw_main_loop_get_loop(loop), None, 0)
assert context, 'bundled PipeWire client modules did not load'
assert lib.pw_context_find_factory(context, b'adapter'), 'video adapter factory is missing'
# The exported factory is video.adapt, which needs a live follower node to
# instantiate. Check its configured library and exported factory without
# pretending a headless context is a consented video stream.
plugin_name = lib.pw_context_find_spa_lib(context, b'video.adapt')
assert plugin_name, 'video.adapt has no bundled library mapping'
plugin = C.CDLL(os.path.join(os.environ['SPA_PLUGIN_DIR'], plugin_name.decode() + '.so'))
class Factory(C.Structure):
    _fields_ = [('version', C.c_uint32), ('name', C.c_char_p)]
plugin.spa_handle_factory_enum.argtypes = [C.POINTER(C.POINTER(Factory)), C.POINTER(C.c_uint32)]
plugin.spa_handle_factory_enum.restype = C.c_int
factory, index, names = C.POINTER(Factory)(), C.c_uint32(), []
while plugin.spa_handle_factory_enum(C.byref(factory), C.byref(index)) > 0:
    names.append(factory.contents.name)
assert b'video.adapt' in names, 'bundled SPA video adapter factory is missing'
lib.pw_context_destroy(context)
lib.pw_main_loop_destroy(loop)
"""
        env = frame_pcview.PCView([], lambda *a: None, 'frame').agent_environment()
        env['FRAME_PC_NATIVE'] = str(capture.NATIVE)
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_agent_reports_its_host_and_encoder(self):
        self.assertEqual(self.request('/status')[0], 403)
        status, body = self.request('/status?k='+self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        if sys.platform == 'win32':
            self.assertEqual((data['host'], data['encoder']), ('windows', 'mfh264enc'))
        self.assertEqual(data['streams'], [])

    @unittest.skipUnless(os.environ.get('FRAME_PC_INSTALLER_BUNDLE') == '1', 'checks the installer\'s bundle')
    def test_installer_bundle_leaves_out_gpl_x264(self):
        self.assertFalse(x264_bundled())
        self.assertFalse(list((capture.NATIVE / 'bin').glob('libx264*')))
        plugins = [p.name for p in (capture.NATIVE / 'lib' / 'gstreamer-1.0').iterdir()]
        for name in ('mediafoundation', 'd3d11', 'jpeg'):
            self.assertTrue(any(p.endswith('gst%s.dll' % name) for p in plugins), (name, plugins))

    @needs_x264
    def test_last_raw_picture_survives_a_closed_gate(self):
        # A source sends ONE changed frame, then becomes idle. It must reach
        # the encoder after congestion clears, without needing another update.
        code = """
import ctypes as C, sys, time
from frame_pc_capture import Native, GATE, Encoded, pipeline
native = Native()
start = None
closing = False
phases = []
def gate(stage, pts, capture, arrived):
    global start
    phases.append(stage)
    if closing: return -1
    if start is None: start = time.monotonic()
    return int(stage == 1 or time.monotonic()-start >= .15)
callback = GATE(gate)
p = pipeline(dict(src='test'), sys.platform, 'x264enc', 320, 180, 30, 300000)
p = p.replace('is-live=true', 'is-live=true num-buffers=1')
error = C.create_string_buffer(1024)
handle = native.lib.fc_capture_open(p.encode(), callback, error, len(error))
assert handle, error.value
try:
    frame = Encoded()
    result = 0
    deadline = time.monotonic()+5
    while not result and time.monotonic() < deadline:
        result = native.lib.fc_capture_pull(handle, C.byref(frame))
    assert result == 1, native.lib.fc_capture_error(handle)
    assert frame.key and frame.size > 17
    assert phases.count(0) == 1 and 2 in phases, phases
finally:
    closing = True
    native.lib.fc_capture_close(handle)
"""
        env = frame_pcview.PCView([], lambda *a: None, 'frame').agent_environment()
        env['PYTHONPATH'] = str(ROOT / 'ui')
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    @needs_x264
    def test_auth_and_real_h264_stats(self):
        self.assertEqual(self.request('/status')[0], 403)
        status, body = self.request('/ticket?src=test&k='+self.token, 'POST')
        self.assertEqual(status, 200)
        ticket = json.loads(body)['ticket']
        ws = WS(self.port, '/stream?src=test&fps=30&max=640&t='+ticket)
        try:
            self.assertEqual(ws.status, 101)
            key, got, echo = None, 0, False
            ws.send_text(json.dumps(dict(t='m', e='down', i=7, tv=1, x=.2, y=.3)))
            deadline = time.monotonic()+10
            while time.monotonic() < deadline and (got < 5 or not echo):
                op, data = ws.recv()
                if op == 1:
                    m = json.loads(data)
                    self.assertNotEqual(m.get('t'), 'error', m)
                    if m.get('t') == 'hello':
                        key = m['r']
                        ws.send_text(json.dumps(dict(t='ack')))
                elif op == 2:
                    got += 1
                    seq, echoed = struct.unpack('!II', data[9:17])
                    now = struct.unpack('!Q', data[1:9])[0]
                    echo |= echoed == 7
                    self.assertIn(b'\x00\x00\x00\x01', data[17:])
                    ws.send_text(json.dumps(dict(t='rx', s=seq, r=now+100)))
                    ws.send_text(json.dumps(dict(t='fd', f=[[seq, now+200, now+300, now+400]])))
            self.assertGreaterEqual(got, 5)
            self.assertTrue(echo)
            time.sleep(.1)
            stats = json.loads(self.request('/stats?settle=0&k='+self.token)[1])['streams'][0]
            self.assertGreater(stats['frames'][0]['e1'], stats['frames'][0]['e0'])
            self.assertIn('controller', stats)
            used = WS(self.port, '/stream?src=test&t='+ticket)
            self.assertEqual(used.status, 403)
            used.close()
            self.request('/close?src=test&k='+self.token, 'POST')
            revoked = WS(self.port, '/stream?src=test&r='+key)
            self.assertEqual(revoked.status, 403)
            revoked.close()
        finally:
            ws.close()


class HardeningTests(unittest.TestCase):
    def test_dimensions_rejects_non_positive_or_bool_values(self):
        for args in ((0, 0, 1280), (-10, 100, 1280), (100, 0, 1280), (100, 100, 0), (True, 100, 1280)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                capture.dimensions(*args)

    def test_pipeline_rejects_malformed_windows_src_and_unknown_encoder(self):
        for bad_src in ('no-colon', 'window:1:2', 123, None):
            with self.subTest(src=bad_src), self.assertRaisesRegex(ValueError, 'Invalid Windows capture source'):
                capture.pipeline({'src': bad_src}, 'win32', 'mfh264enc', 640, 360, 30, 1000000)
        with self.assertRaisesRegex(ValueError, 'Unsupported encoder'):
            capture.pipeline({'src': 'test'}, 'linux', 'bogus_enc', 640, 360, 30, 1000000)

    def test_portal_and_windows_input_reject_non_finite_and_bool_or_unhashable_buttons(self):
        native = mock.Mock()
        native.lib.fc_portal_input.return_value = 1
        portal = capture.PortalInput(native, dict(portal=123, w=100, h=50))
        for bad in (
            dict(t='m', x='nan', y=0.5),
            dict(t='m', x=0.5, y='inf'),
            dict(t='m', x=True, y=0.5),
            dict(t='wheel', x=0.5, y=0.5, dx='nan', dy=0),
            dict(t='wheel', x=0.5, y=0.5, dx=0, dy='-inf'),
        ):
            with self.subTest(portal_bad=bad), self.assertRaises(ValueError):
                portal.handle(bad)
        native.lib.fc_portal_input.reset_mock()
        portal.handle(dict(t='m', e='down', b=True, x=0.5, y=0.5))
        portal.handle(dict(t='m', e='down', b=['bad'], x=0.5, y=0.5))
        # Pointer moves emitted, but neither bool nor unhashable button emitted a click.
        self.assertFalse(any(c.args[1] == 1 for c in native.lib.fc_portal_input.call_args_list))
        self.assertEqual(portal.buttons, set())

        host = mock.Mock()
        host.user.GetSystemMetrics.side_effect = lambda i: {76: 0, 77: 0, 78: 1920, 79: 1080}[i]
        win = capture.WindowsInput(host, dict(src='display:4', x=0, y=0, w=1920, h=1080))
        for bad in (
            dict(t='m', x='nan', y=0.5),
            dict(t='wheel', x=0.5, y=0.5, dy='nan'),
            dict(t='wheel', x=0.5, y=0.5, dx='inf'),
        ):
            with self.subTest(win_bad=bad), self.assertRaises(ValueError):
                win.handle(bad)
        host.send.reset_mock()
        win.handle(dict(t='m', e='down', b=True, x=0.5, y=0.5))
        win.handle(dict(t='m', e='down', b=['bad'], x=0.5, y=0.5))
        # Only absolute pointer move calls (flags == 0xc001), never button clicks.
        self.assertEqual(len(host.send.call_args_list), 2)
        self.assertTrue(all(c.kwargs['mouse'][3] == 0xc001 for c in host.send.call_args_list))
        self.assertEqual(win.buttons, set())

    def test_grants_redeem_and_agent_source_guard_unhashable_inputs(self):
        grants = agent.Grants('master')
        self.assertIsNone(grants.redeem('test', {'t': ['bad'], 'r': {'bad': 1}}))
        native = mock.Mock()
        with mock.patch.object(agent.sys, 'platform', 'linux'):
            host = agent.Agent(native, 'master', ROOT / 'ui/mac-view.html')
        for bad in (['bad'], None, 42):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                host.source(bad)

    def test_bench_rejects_non_finite_parameters(self):
        native = mock.Mock()
        with mock.patch.object(agent.sys, 'platform', 'linux'):
            host = agent.Agent(native, 'master', ROOT / 'ui/mac-view.html')
        session = mock.Mock(src='test')
        host.sessions[1] = session
        for query in ('x=nan', 'y=inf', 'interval=-inf'):
            with self.subTest(query=query):
                output = io.BytesIO()
                handler = agent.Handler.__new__(agent.Handler)
                handler.server = mock.Mock(agent=host)
                handler.path = f'/bench?src=test&k=master&{query}'
                handler.headers = {}
                handler.wfile = output
                handler.request_version = 'HTTP/1.1'
                handler.requestline = f'POST {handler.path} HTTP/1.1'
                handler.command = 'POST'
                handler.close_connection = False
                handler.dispatch('POST')
                self.assertTrue(output.getvalue().startswith(b'HTTP/1.1 400 '), output.getvalue())
                session.ws.send.assert_not_called()

    def test_stream_closes_new_controller_if_replacement_fails_before_registration(self):
        native = mock.Mock()
        native.lib.fc_new.return_value = 77
        with mock.patch.object(agent.sys, 'platform', 'linux'):
            host = agent.Agent(native, 'master', ROOT / 'ui/mac-view.html')
        existing = mock.Mock(key='old-key', src='test', writer=None)
        existing.end.side_effect = RuntimeError('stop failed')
        host.sessions[1] = existing
        output = io.BytesIO()
        handler = agent.Handler.__new__(agent.Handler)
        handler.server = mock.Mock(agent=host)
        handler.path = '/stream?src=test&k=master'
        handler.headers = {
            'Upgrade': 'websocket',
            'Sec-WebSocket-Version': '13',
            'Sec-WebSocket-Key': agent.base64.b64encode(b'0123456789abcdef').decode(),
        }
        handler.connection = mock.Mock()
        handler.rfile = io.BytesIO()
        handler.wfile = output
        handler.request_version = 'HTTP/1.1'
        handler.requestline = f'GET {handler.path} HTTP/1.1'
        handler.command = 'GET'
        handler.close_connection = False
        handler.dispatch('GET')
        self.assertTrue(output.getvalue().startswith(b'HTTP/1.1 400 '), output.getvalue())
        native.lib.fc_free.assert_called_once_with(77)


if __name__ == '__main__':
    unittest.main()

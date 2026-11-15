"""Exercise the VM browser's actual startup loop without a browser or headset."""
import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class BrowserReadiness(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).parent/'decky-runtime/syncthing_browser.py'
        tree = ast.parse(source.read_text())
        exercise = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'exercise')
        block = next(n for n in ast.walk(exercise) if isinstance(n, ast.With)
                     and any(isinstance(s, ast.While) for s in n.body))
        start = next(i for i, n in enumerate(block.body) if isinstance(n, ast.Assign)
                     and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'deadline')
        end = next(i for i, n in enumerate(block.body[start:], start) if isinstance(n, ast.Assert))
        wrapper = ast.parse('def wait(browser, profile, report):\n page = None\n pending_pages = []\n return page').body[0]
        wrapper.body[2:2] = block.body[start:end+1]
        self.now = 0
        self.on_sleep = lambda: None
        def sleep(seconds):
            self.now += seconds
            self.on_sleep()
        self.backend = SimpleNamespace(Fail=RuntimeError, Page=Mock())
        scope = dict(time=SimpleNamespace(monotonic=lambda: self.now, sleep=sleep),
                     frame_steam=self.backend, bridge_started=0, clock={})
        helpers = [n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == 'ready_page']
        exec(compile(ast.fix_missing_locations(ast.Module(body=helpers+[wrapper], type_ignores=[])),
                     str(source), 'exec'), scope)
        self.wait = scope['wait']
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name)
        self.port = self.profile/'DevToolsActivePort'
        self.browser = Mock(poll=lambda: None)
        self.report = {'startupClock': scope['clock']}

    def ready_page(self):
        page = Mock()
        page.eval.return_value = dict(ready=True)
        self.backend.Page.return_value = page
        return page

    def test_empty_port_file_is_retried_until_chromium_publishes_port(self):
        self.port.touch()
        page = self.ready_page()
        self.on_sleep = lambda: self.port.write_text('12345\nfixture')
        self.assertIs(self.wait(self.browser, self.profile, self.report), page)
        self.assertGreater(self.now, 0)
        self.assertEqual(self.report['startupClock']['readinessDeadlineAfterSeconds'], 60)
        self.assertTrue(self.report['startupClock']['readinessPassed'])
        self.assertEqual(self.backend.CDP, 'http://127.0.0.1:12345/json')
        self.backend.Page.assert_called_once()

    def test_file_disappearing_during_read_is_retried(self):
        self.port.write_text('12345\nfixture')
        page = self.ready_page()
        with patch.object(Path, 'read_text', side_effect=[FileNotFoundError('port moved'), '12345\nfixture']):
            self.assertIs(self.wait(self.browser, self.profile, self.report), page)
        self.assertEqual(self.report['lastReadinessError'], 'port moved')
        self.assertGreater(self.now, 0)

    def test_permanently_empty_file_keeps_bounded_readiness_failure(self):
        self.port.touch()
        with self.assertRaisesRegex(AssertionError, 'Browser CDP/panel readiness unavailable'):
            self.wait(self.browser, self.profile, self.report)
        self.assertGreaterEqual(self.now, 60)
        self.assertLess(self.now, 61)
        self.assertEqual(self.report['startupClock']['readinessDeadlineAfterSeconds'], 60)
        self.assertFalse(self.report['startupClock']['readinessPassed'])
        self.assertGreaterEqual(self.report['startupClock']['readinessFinishedAfterSeconds'], 60)
        self.backend.Page.assert_not_called()

    def test_exited_browser_keeps_distinct_failure(self):
        self.browser.poll = lambda: 7
        self.browser.returncode = 7
        with self.assertRaisesRegex(RuntimeError, 'Sandboxed browser exited: 7'):
            self.wait(self.browser, self.profile, self.report)
        self.assertEqual(self.now, 0)

    def test_ready_probe_finishing_after_deadline_is_closed_and_rejected(self):
        self.port.write_text('12345\nfixture')
        page = self.ready_page()
        def finish_late(_expression):
            self.now = 60.5
            return dict(ready=True)
        page.eval.side_effect = finish_late
        with self.assertRaisesRegex(AssertionError, 'Browser CDP/panel readiness unavailable'):
            self.wait(self.browser, self.profile, self.report)
        page.sock.close.assert_called_once()
        self.backend.Page.assert_called_once()
        self.assertFalse(self.report['startupClock']['readinessPassed'])
        self.assertEqual(self.report['startupClock']['readinessFinishedAfterSeconds'], 60.5)
        self.assertTrue(self.report['startupClock']['lateReadyPageRejected'])

    def test_ready_probe_finishing_at_deadline_is_accepted(self):
        self.port.write_text('12345\nfixture')
        page = self.ready_page()
        def finish_at_deadline(_expression):
            self.now = 60
            return dict(ready=True)
        page.eval.side_effect = finish_at_deadline
        self.assertIs(self.wait(self.browser, self.profile, self.report), page)
        page.sock.close.assert_not_called()
        self.assertTrue(self.report['startupClock']['readinessPassed'])

    def test_ready_probe_finishing_before_deadline_is_accepted(self):
        self.port.write_text('12345\nfixture')
        page = self.ready_page()
        def finish_before_deadline(_expression):
            self.now = 59.9
            return dict(ready=True)
        page.eval.side_effect = finish_before_deadline
        self.assertIs(self.wait(self.browser, self.profile, self.report), page)
        page.sock.close.assert_not_called()
        self.assertTrue(self.report['startupClock']['readinessPassed'])

    def test_incomplete_page_is_closed_before_ready_page_is_accepted(self):
        self.port.write_text('12345\nfixture')
        pending, ready = Mock(), Mock()
        pending.eval.side_effect = [dict(ready=False, errors=['loading'], state=None,
                                               documentState='loading', iframeAssigned=False),
                                    OSError('owned CDP connection closed')]
        ready.eval.return_value = dict(ready=True)
        self.backend.Page.side_effect = [RuntimeError('page not listed yet'), pending, ready]
        self.assertIs(self.wait(self.browser, self.profile, self.report), ready)
        pending.sock.close.assert_called_once()
        self.assertEqual(self.backend.Page.call_count, 3)
        ready.sock.close.assert_not_called()
        self.assertEqual(self.report['startupErrors'], ['loading'])
        self.assertEqual(self.report['startupState'], dict(state=None,
                         documentState='loading', iframeAssigned=False))
        attempts = self.report['readinessAttempts']
        self.assertEqual(attempts[0]['error'], 'page not listed yet')
        self.assertEqual(attempts[1]['outcome'], 'not-ready')
        self.assertNotIn('error', attempts[1])
        self.assertEqual(self.report['lastReadinessAttempt']['outcome'], 'ready')
        self.assertNotIn('error', self.report['lastReadinessAttempt'])

    def test_pending_connection_is_reused_until_its_page_becomes_ready(self):
        self.port.write_text('12345\nfixture')
        page = self.ready_page()
        page.eval.side_effect = [dict(ready=False, errors=[], state=None,
            documentState='loading', iframeAssigned=False), dict(ready=True)]
        self.assertIs(self.wait(self.browser, self.profile, self.report), page)
        self.backend.Page.assert_called_once()
        page.sock.close.assert_not_called()
        # The first pending snapshot and the accepted snapshot each use one
        # round trip; diagnostics must not add another startup evaluation.
        self.assertEqual(page.eval.call_count, 2)
        self.assertEqual([a['connectionReused'] for a in self.report['readinessAttempts']],
                         [False, True])

    def test_pending_connection_is_closed_once_when_readiness_expires(self):
        self.port.write_text('12345\nfixture')
        page = self.ready_page()
        page.eval.return_value = dict(ready=False,
            errors=[], state=None, documentState='loading', iframeAssigned=False)
        with self.assertRaisesRegex(AssertionError, 'Browser CDP/panel readiness unavailable'):
            self.wait(self.browser, self.profile, self.report)
        self.backend.Page.assert_called_once()
        page.sock.close.assert_called_once()
        self.assertFalse(self.report['startupClock']['readinessPassed'])

    def test_reused_connection_cannot_accept_readiness_after_deadline(self):
        self.port.write_text('12345\nfixture')
        page = self.ready_page()
        probes = 0
        def evaluate(expression):
            nonlocal probes
            probes += 1
            if probes == 1:
                return dict(ready=False, errors=[], state=None,
                            documentState='loading', iframeAssigned=False)
            self.now = 60.5
            return dict(ready=True)
        page.eval.side_effect = evaluate
        with self.assertRaisesRegex(AssertionError, 'Browser CDP/panel readiness unavailable'):
            self.wait(self.browser, self.profile, self.report)
        self.backend.Page.assert_called_once()
        page.sock.close.assert_called_once()
        self.assertFalse(self.report['startupClock']['readinessPassed'])
        self.assertTrue(self.report['startupClock']['lateReadyPageRejected'])
        self.assertTrue(self.report['lastReadinessAttempt']['connectionReused'])


if __name__ == '__main__':
    unittest.main()

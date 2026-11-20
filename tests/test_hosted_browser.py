from base64 import b64encode
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch
import os, sys, time, unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ai_agent.tools import hosted_browser
from ai_agent.tools.base import CURRENT_EXECUTION, ToolExecutionContext, ToolError, ToolResult
from ai_agent.tools.browser import BrowserFailure, PlaywrightBrowserRunner
from ai_agent.tools.hosted_browser import HostedBrowserBackend, _NetworklessBrowser
from ai_agent.tools.hosted_http import _HTTPBroker


class BrowserHostContracts(unittest.TestCase):
    def test_private_profile_never_starts_a_process(self):
        with TemporaryDirectory() as folder:
            runner = PlaywrightBrowserRunner(Path(folder), allow_private_networks=True)
            with patch.object(hosted_browser, '_execute_pair') as execute:
                with self.assertRaises(BrowserFailure): HostedBrowserBackend(runner, timeout_seconds=3)(url='http://127.0.0.1')
            execute.assert_not_called()

    def test_failed_and_cancelled_calls_preserve_existing_screenshot(self):
        with TemporaryDirectory() as folder:
            runner = PlaywrightBrowserRunner(Path(folder))
            target = Path(folder) / 'old.png'; target.write_bytes(b'original')
            backend = HostedBrowserBackend(runner, timeout_seconds=3)
            failure = ToolResult('failed', error=ToolError('timeout', 'Synthetic timeout'))
            with patch.object(hosted_browser, '_linux_browser_executable', return_value=Path(sys.executable)), patch.object(hosted_browser, '_execute_pair', return_value=failure):
                with self.assertRaises(BrowserFailure) as caught:
                    backend(url='https://example.com', actions=[{'type': 'click'}], screenshot=True, screenshot_path='old.png')
            self.assertTrue(caught.exception.details['outcome_unknown'])
            self.assertEqual(target.read_bytes(), b'original')
            cancel = Event(); token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
            def finish(*args):
                cancel.set()
                return {'result': {'screenshot': None}, 'screenshot_png': b64encode(b'\x89PNG\r\n\x1a\n').decode()}
            try:
                with patch.object(hosted_browser, '_linux_browser_executable', return_value=Path(sys.executable)), patch.object(hosted_browser, '_execute_pair', side_effect=finish):
                    with self.assertRaises(BrowserFailure): backend(url='https://example.com', screenshot=True, screenshot_path='old.png')
                self.assertEqual(target.read_bytes(), b'original')
            finally: CURRENT_EXECUTION.reset(token)

    def test_timed_out_interaction_keeps_unknown_outcome(self):
        with TemporaryDirectory() as folder:
            runner = PlaywrightBrowserRunner(Path(folder))
            frame = {'failure': {'code': 'timeout', 'message': 'Synthetic timeout', 'retryable': True, 'details': None}}
            with patch.object(hosted_browser, '_linux_browser_executable', return_value=Path(sys.executable)), patch.object(hosted_browser, '_execute_pair', return_value=frame):
                with self.assertRaises(BrowserFailure) as caught:
                    HostedBrowserBackend(runner, timeout_seconds=3)(url='https://example.com', actions=[{'type': 'click'}])
            self.assertTrue(caught.exception.details['outcome_unknown']); self.assertFalse(caught.exception.retryable)

    def test_invalid_screenshot_path_is_rejected_before_execution(self):
        with TemporaryDirectory() as folder:
            runner = PlaywrightBrowserRunner(Path(folder))
            with patch.object(hosted_browser, '_execute_pair') as execute:
                with self.assertRaises(BrowserFailure):
                    HostedBrowserBackend(runner, timeout_seconds=3)(url='https://example.com', screenshot=True, screenshot_path='../bad.png')
            execute.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and Path('/usr/bin/bwrap').exists(), 'requires Linux bubblewrap')
class BrowserHostLinux(unittest.TestCase):
    def setUp(self):
        self.events = []; self.ready = Event()
        events, ready = self.events, self.ready
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                events.append(self.path)
                if self.path == '/redirect-private':
                    self.send_response(302)
                    self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/sentinel')
                    self.end_headers()
                    return
                if self.path == '/child-ready': ready.set()
                body = ('<title>Isolated browser fixture</title><main>' + 'Verified public content. ' * 20 + '</main>'
                    '<button id="go" onclick="document.querySelector(\'main\').textContent=\'Clicked verified content. \'.repeat(20)">Continue</button>'
                    '<script>setInterval(()=>fetch(\'/heartbeat\'),100)</script>').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/html'); self.send_header('Content-Length', str(len(body))); self.end_headers()
                try: self.wfile.write(body)
                except OSError: pass
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.serving = Thread(target=self.server.serve_forever, daemon=True); self.serving.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.serving.join(2)

    def profiles(self, **extra):
        port = self.server.server_address[1]
        def proxy(spec, *, timeout_seconds):
            return _HTTPBroker(replace(spec, entrypoint='browser_host_fixture:proxy', code_root=ROOT / 'tests',
                configuration={**spec.configuration, 'fixture_port': port, **extra}), timeout_seconds=timeout_seconds)
        def browser(spec, *, timeout_seconds):
            return _NetworklessBrowser(replace(spec, entrypoint='browser_host_fixture:browser', code_root=ROOT / 'tests',
                configuration={**spec.configuration, 'fixture_port': port, **extra}), timeout_seconds=timeout_seconds)
        return patch.object(hosted_browser, '_HTTPBroker', side_effect=proxy), patch.object(hosted_browser, '_NetworklessBrowser', side_effect=browser)

    def runner(self, folder, timeout=10):
        runner = PlaywrightBrowserRunner(Path(folder), timeout_seconds=5)
        runner.process_backend = HostedBrowserBackend(runner, timeout_seconds=timeout)
        return runner

    def test_real_dom_click_screenshot_hash_and_private_redirect_policy(self):
        first_patch, second_patch = self.profiles()
        with TemporaryDirectory() as folder, first_patch, second_patch:
            runner = self.runner(folder)
            first = runner.read(url='http://fixture.test/', screenshot=True, screenshot_path='capture.png')
            self.assertEqual(first['title'], 'Isolated browser fixture')
            self.assertTrue((Path(folder) / 'capture.png').read_bytes().startswith(b'\x89PNG'))
            clicked = runner.interact(url='http://fixture.test/', actions=[{'type': 'click', 'selector': '#go'}], screenshot=False, expected_dom_hash=first['dom_hash'])
            self.assertIn('Clicked verified content', clicked['main_text'])
            with self.assertRaises(BrowserFailure) as caught:
                runner.interact(url='http://fixture.test/', actions=[{'type': 'click', 'selector': '#go'}], screenshot=False, expected_dom_hash='wrong')
            self.assertEqual(caught.exception.code, 'dom_changed')
            with self.assertRaises(BrowserFailure): runner.read(url='http://169.254.169.254/latest/meta-data/', screenshot=False)
            from ai_agent.tools.browser import browser_read_result
            blocked = browser_read_result(runner, {'url': 'http://fixture.test/redirect-private', 'screenshot': False})
            self.assertFalse(blocked.ok, blocked)
            self.assertNotIn('/sentinel', self.events)

    def test_browser_has_no_direct_network_or_host_state(self):
        first_patch, second_patch = self.profiles()
        with TemporaryDirectory() as folder, first_patch, second_patch, patch.dict(os.environ, {'AVELIN_TEST_SECRET': 'synthetic'}):
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright: executable = Path(playwright.chromium.executable_path)
            frame = hosted_browser._execute_pair({'url': 'http://fixture.test/', 'screenshot': False},
                {'browser_root': str(executable.parent), 'browser_name': executable.name, 'timeout_seconds': 5,
                 'allowed_hosts': [], 'max_text_chars': 20000, 'max_elements': 100}, 10)
            self.assertEqual(frame['boundary'], {'env_secret': None, 'database': False, 'provider_keys': False,
                'workspace_files': [], 'direct_public': False, 'host_loopback': False})
            self.assertEqual(frame['result']['status'], 200)

    def test_cancel_and_deadline_drain_browser_proxy_and_detached_descendants(self):
        for cancel_early in (True, False):
            with self.subTest(cancel=cancel_early), TemporaryDirectory() as folder:
                self.ready.clear(); self.events.clear(); box = {}; cancel = Event()
                first_patch, second_patch = self.profiles(stall_browser=True, delay=9)
                runner = self.runner(folder, timeout=8 if cancel_early else 5)
                target = Path(folder) / 'capture.png'; target.write_bytes(b'original')
                def read():
                    token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
                    try: runner.read(url='http://fixture.test/', screenshot=True, screenshot_path='capture.png')
                    except BrowserFailure as exc: box['error'] = exc
                    finally: CURRENT_EXECUTION.reset(token)
                with first_patch, second_patch:
                    worker = Thread(target=read); worker.start()
                    try:
                        self.assertTrue(self.ready.wait(4), 'Browser descendant not ready')
                        if cancel_early: cancel.set()
                        worker.join(8); self.assertFalse(worker.is_alive())
                        self.assertEqual(box['error'].code, 'cancelled' if cancel_early else 'timeout')
                        self.assertEqual(target.read_bytes(), b'original')
                        count = len(self.events); time.sleep(9.2 if cancel_early else 5.2)
                        self.assertEqual(len(self.events), count, 'Network activity after terminal status')
                        self.assertNotIn('/late', self.events)
                    finally: cancel.set(); worker.join(10)

    def test_research_fallback_uses_the_registered_browser_backend(self):
        from tests.test_runtime_audit import isolated_agent
        first_patch, second_patch = self.profiles()
        with isolated_agent([]) as agent, first_patch, second_patch:
            self.assertIsInstance(agent.browser.process_backend, HostedBrowserBackend)
            result = agent.tools.invoke('research_read', {'url': 'http://fixture.test/', 'force_browser': True})
            self.assertTrue(result.ok, result.text())
            self.assertEqual(result.output['extraction_method'], 'browser')
            self.assertTrue(result.output['evidence'])

    def test_proxy_startup_cancel_and_timeout_kill_detached_network_child(self):
        import socketserver
        for cancel_early in (True, False):
            with self.subTest(cancel=cancel_early), TemporaryDirectory() as folder:
                ready = Event(); messages = []; cancel = Event(); box = {}
                class Receiver(socketserver.BaseRequestHandler):
                    def handle(self):
                        data = self.request.recv(100); messages.append(data)
                        if data == b'CHILD_READY': ready.set()
                with socketserver.TCPServer(('127.0.0.1', 0), Receiver) as server:
                    serving = Thread(target=server.serve_forever, daemon=True); serving.start()
                    first_patch, second_patch = self.profiles(stall_proxy=True,
                        signal_port=server.server_address[1], delay=4)
                    runner = self.runner(folder, timeout=6 if cancel_early else 2)
                    def read():
                        token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
                        try: runner.read(url='http://fixture.test/', screenshot=False)
                        except BrowserFailure as exc: box['error'] = exc
                        finally: CURRENT_EXECUTION.reset(token)
                    with first_patch, second_patch as browser:
                        worker = Thread(target=read); worker.start()
                        try:
                            self.assertTrue(ready.wait(1.7), 'Proxy descendant did not start')
                            if cancel_early: cancel.set()
                            worker.join(4); self.assertFalse(worker.is_alive())
                            self.assertEqual(box['error'].code, 'cancelled' if cancel_early else 'timeout')
                            browser.assert_not_called()
                            time.sleep(4.2 if cancel_early else 2.2)
                            self.assertNotIn(b'LATE', messages)
                        finally:
                            cancel.set(); worker.join(5); server.shutdown(); serving.join(2)

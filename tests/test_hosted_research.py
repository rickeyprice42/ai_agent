from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch
import socketserver
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ai_agent.tools.base import CURRENT_EXECUTION, ToolExecutionContext, ToolError, ToolResult
from ai_agent.tools.hosted_http import _HTTPBroker
from ai_agent.tools.hosted_research import HostedResearchHTTPStage, call_research_http
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.research import BrowserResearchReader, ResearchFailure
from tests.test_browser_research import FakeBrowser, response, PUBLIC_FIXTURES


def make_reader(browser=None, timeout=5):
    reader = BrowserResearchReader(HttpSandbox(timeout_seconds=2), browser or FakeBrowser())
    reader.http_stage = HostedResearchHTTPStage(reader, timeout_seconds=timeout)
    return reader


def fixture_broker(mode, **extra):
    def construct(spec, *, timeout_seconds):
        return _HTTPBroker(replace(spec, entrypoint='research_host_fixture:run',
            code_root=ROOT / 'tests', configuration={**spec.configuration,
            'fixture': mode, **extra}), timeout_seconds=timeout_seconds)
    return construct


class ResearchHostContracts(unittest.TestCase):
    def test_http_parser_contract_preserves_all_public_fixture_content(self):
        for name, body in PUBLIC_FIXTURES:
            with self.subTest(name=name), patch.object(HttpSandbox, 'fetch', return_value=response(body)):
                local = BrowserResearchReader(HttpSandbox(), FakeBrowser()).read(url='https://example.com/page')
                remote = call_research_http({'url': 'https://example.com/page', 'locale': 'ru-RU',
                    'region': 'auto', 'catalog': 'auto', 'adapter': 'generic'},
                    {'socket_timeout': 2, 'max_response_chars': 2_000_000, 'max_fragments': 8})['result']
                for key in ('fields', 'evidence', 'safety', 'confidence', 'adapter', 'extraction_method'):
                    self.assertEqual(local[key], remote[key])

    def test_failure_does_not_launch_browser_or_cache_partial_result(self):
        for code in ('timeout', 'cancelled', 'host_output_limit', 'host_protocol'):
            with self.subTest(code=code):
                reader = make_reader()
                failure = ToolResult('failed', error=ToolError(code, 'Synthetic host failure'))
                with patch('ai_agent.tools.hosted_research._HTTPBroker') as broker:
                    broker.return_value.return_value = failure
                    with self.assertRaises(ResearchFailure) as caught:
                        reader.read(url='https://example.com/page')
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(reader.browser.calls, 0)
                self.assertFalse(reader._cache)

    def test_cancel_between_http_result_and_browser_never_starts_browser(self):
        reader = make_reader()
        cancel = Event()
        token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
        def finish(*args, **kwargs):
            cancel.set()
            return {'http_text_chars': 0, 'offer_failure': None, 'transport_failure': None}
        try:
            with patch.object(HostedResearchHTTPStage, '__call__', side_effect=finish):
                with self.assertRaises(ResearchFailure) as caught:
                    reader.read(url='https://example.com/page')
            self.assertEqual(caught.exception.code, 'cancelled')
            self.assertEqual(reader.browser.calls, 0)
            self.assertFalse(reader._cache)
        finally:
            CURRENT_EXECUTION.reset(token)

    def test_forced_browser_skips_http_process(self):
        browser = FakeBrowser({'url': 'https://example.com/page', 'main_text': 'Rendered evidence. ' * 20})
        reader = make_reader(browser)
        with patch('ai_agent.tools.hosted_research._HTTPBroker') as broker:
            result = reader.read(url='https://example.com/page', force_browser=True)
        broker.assert_not_called()
        self.assertEqual(result['extraction_method'], 'browser')

    def test_private_profile_fails_closed_before_process(self):
        reader = make_reader()
        reader.http.allow_private_networks = True
        with patch('ai_agent.tools.hosted_research._HTTPBroker') as broker:
            with self.assertRaises(ResearchFailure): reader.read(url='https://example.com/page')
        broker.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and Path('/usr/bin/bwrap').exists(), 'requires Linux bubblewrap')
class ResearchHostLinux(unittest.TestCase):
    def test_builtin_registration_routes_research_through_fixed_process(self):
        from tests.test_runtime_audit import isolated_agent
        with isolated_agent([]) as agent:
            self.assertIsInstance(agent.research.http_stage, HostedResearchHTTPStage)
            with patch('ai_agent.tools.hosted_research._HTTPBroker', side_effect=fixture_broker('article')):
                result = agent.tools.invoke('research_read', {'url': 'https://example.com/page'})
            self.assertTrue(result.ok, result.text())
            self.assertEqual(result.output['extraction_method'], 'http')
            self.assertTrue(result.output['evidence'])

    def test_fresh_process_preserves_results_parent_cache_and_dynamic_tls_fallback(self):
        for mode in ('article', 'dynamic', 'tls', 'antibot'):
            with self.subTest(mode=mode):
                browser = FakeBrowser({'url': 'https://example.com/page', 'main_text': 'Rendered evidence. ' * 20})
                reader = make_reader(browser)
                with patch('ai_agent.tools.hosted_research._HTTPBroker', side_effect=fixture_broker(mode)) as broker:
                    if mode == 'antibot':
                        with self.assertRaises(ResearchFailure) as caught: reader.read(url='https://example.com/page')
                        self.assertEqual(caught.exception.code, 'anti_bot')
                        self.assertEqual(browser.calls, 0)
                        continue
                    first = reader.read(url='https://example.com/page')
                    second = reader.read(url='https://example.com/page')
                    self.assertTrue(second['cache_hit'])
                    self.assertEqual(broker.call_count, 1)
                    self.assertEqual(first['extraction_method'], 'http' if mode == 'article' else 'browser')
                    self.assertEqual(browser.calls, 0 if mode == 'article' else 1)

    def test_cancel_and_deadline_kill_parser_descendants_without_browser_fallback(self):
        for cancel_early in (True, False):
            with self.subTest(cancel=cancel_early):
                ready = Event(); messages = []; box = {}; cancel = Event()
                class Receiver(socketserver.BaseRequestHandler):
                    def handle(self):
                        data = self.request.recv(100); messages.append(data)
                        if data == b'CHILD_READY': ready.set()
                with socketserver.TCPServer(('127.0.0.1', 0), Receiver) as server:
                    serving = Thread(target=server.serve_forever, daemon=True); serving.start()
                    reader = make_reader(timeout=5 if cancel_early else 1.2)
                    def read():
                        token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
                        try: reader.read(url='https://example.com/page')
                        except ResearchFailure as exc: box['failure'] = exc
                        finally: CURRENT_EXECUTION.reset(token)
                    with patch('ai_agent.tools.hosted_research._HTTPBroker', side_effect=fixture_broker(
                            'slow_parser', port=server.server_address[1])):
                        worker = Thread(target=read); worker.start()
                        try:
                            self.assertTrue(ready.wait(1), 'Parser descendant did not start')
                            if cancel_early: cancel.set()
                            worker.join(3)
                            self.assertFalse(worker.is_alive())
                            self.assertEqual(box['failure'].code, 'cancelled' if cancel_early else 'timeout')
                            self.assertEqual(reader.browser.calls, 0)
                            self.assertFalse(reader._cache)
                            time.sleep(2.2)
                            self.assertNotIn(b'LATE', messages)
                        finally:
                            cancel.set(); worker.join(5); server.shutdown(); serving.join(2)

    def test_fixed_profile_rejects_private_target(self):
        reader = make_reader()
        with self.assertRaises(ResearchFailure) as caught:
            reader.read(url='http://169.254.169.254/latest/meta-data/')
        self.assertEqual(caught.exception.code, 'private_network_blocked')

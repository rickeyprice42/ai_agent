from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch
import os
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ai_agent.tools.base import CURRENT_EXECUTION, ToolExecutionContext, ToolError, ToolResult
from ai_agent.tools.hosted_research import HostedResearchBrowserResult, call_research_browser_result
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.process_host import HostedHandler, ProcessHandlerSpec
from ai_agent.tools.research import BrowserResearchReader, ResearchFailure
from tests.test_browser_research import FakeBrowser

URL = 'https://shop.example/item'
METADATA = {'url': URL, 'locale': 'ru-RU', 'region': 'auto', 'catalog': 'auto',
            'adapter': 'generic', 'offer_failure': None, 'transport_failure_code': 'tls_error'}
SNAPSHOT = {'url': URL, 'status': 200, 'title': 'Verified shoes',
    'main_text': 'Verified public product description. ' * 20,
    'json_ld': [{'@type': 'Product', 'name': 'Verified shoes', 'offers':
                 {'@type': 'Offer', 'price': '1957.50', 'priceCurrency': 'RUB', 'url': URL}}],
    'images': [{'url': '/photo.jpg', 'alt': 'Shoes'}]}


def reader_with_stage(snapshot=None, timeout=3):
    reader = BrowserResearchReader(HttpSandbox(), FakeBrowser(snapshot or SNAPSHOT))
    reader.browser_result_stage = HostedResearchBrowserResult(reader, timeout_seconds=timeout)
    return reader


class EvidenceContracts(unittest.TestCase):
    def test_preserves_verified_product_evidence_and_untrusted_markers(self):
        frame = call_research_browser_result({'snapshot': SNAPSHOT, 'metadata': METADATA}, {'max_fragments': 8})
        result = frame['result']
        self.assertEqual(result['extraction_method'], 'browser')
        self.assertEqual(result['fields']['merchant_offer']['price'], '1957.50')
        self.assertEqual(result['fields']['merchant_offer']['currency'], 'RUB')
        self.assertEqual(result['fields']['images'][0]['url'], 'https://shop.example/photo.jpg')
        self.assertEqual(result['fields']['http_fallback_reason'], 'tls_error')
        self.assertTrue(result['safety']['untrusted_external_data'])
        self.assertTrue(result['evidence'])

    def test_refusals_remain_typed_failures(self):
        for status, text, code in [(401, 'Sign in', 'requires_login'),
                (402, 'Subscribe to continue', 'paywall'), (403, 'Verify you are human', 'anti_bot')]:
            with self.subTest(status=status):
                frame = call_research_browser_result({'snapshot': {**SNAPSHOT, 'status': status,
                    'main_text': text}, 'metadata': METADATA}, {'max_fragments': 8})
                self.assertEqual(frame['failure']['code'], code)

    def test_host_failure_never_caches_or_repeats_browser(self):
        for code in ('timeout', 'cancelled', 'host_input_limit', 'host_output_limit', 'host_protocol'):
            reader = reader_with_stage()
            with self.subTest(code=code), patch('ai_agent.tools.hosted_research.HostedHandler') as host:
                host.return_value.return_value = ToolResult('failed', error=ToolError(code, 'Synthetic failure'))
                with self.assertRaises(ResearchFailure) as caught: reader.read(url=URL, force_browser=True)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(reader.browser.calls, 1)
            self.assertFalse(reader._cache)
            self.assertEqual(reader.trace()[-1]['method'], 'failed')

    def test_cancel_at_handoff_does_not_start_evidence_process(self):
        reader = reader_with_stage(); cancel = Event()
        original = reader.browser.read
        def finish(**kwargs): cancel.set(); return original(**kwargs)
        reader.browser.read = finish
        token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
        try:
            with patch('ai_agent.tools.hosted_research.HostedHandler') as host:
                with self.assertRaises(ResearchFailure) as caught: reader.read(url=URL, force_browser=True)
            self.assertEqual(caught.exception.code, 'cancelled'); host.assert_not_called()
            self.assertFalse(reader._cache)
        finally: CURRENT_EXECUTION.reset(token)

    def test_cancel_before_cache_commit_discards_finished_result(self):
        reader = reader_with_stage(); cancel = Event()
        def finish(*args, **kwargs):
            cancel.set()
            return {'url': URL, 'extraction_method': 'browser', 'evidence': []}
        reader.browser_result_stage = finish
        token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
        try:
            with self.assertRaises(ResearchFailure): reader.read(url=URL, force_browser=True)
            self.assertFalse(reader._cache)
        finally: CURRENT_EXECUTION.reset(token)

    def test_cancel_during_cache_copy_is_checked_before_publication(self):
        reader = reader_with_stage(); cancel = Event()
        reader.browser_result_stage = lambda *args, **kwargs: {'url': URL, 'extraction_method': 'browser'}
        def copy(value): cancel.set(); return dict(value)
        token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
        try:
            with patch('ai_agent.tools.research._deepcopy', side_effect=copy):
                with self.assertRaises(ResearchFailure): reader.read(url=URL, force_browser=True)
            self.assertFalse(reader._cache)
            self.assertEqual(reader.trace()[-1]['code'], 'cancelled')
        finally: CURRENT_EXECUTION.reset(token)


@unittest.skipUnless(sys.platform == 'linux' and Path('/usr/bin/bwrap').exists(), 'requires Linux bubblewrap')
class EvidenceLinux(unittest.TestCase):
    def test_registered_pipeline_preserves_product_and_cache(self):
        from tests.test_runtime_audit import isolated_agent
        with isolated_agent([]) as agent:
            self.assertIsInstance(agent.research.browser_result_stage, HostedResearchBrowserResult)
            agent.research.browser = FakeBrowser(SNAPSHOT)
            first = agent.tools.invoke('research_read', {'url': URL, 'force_browser': True})
            self.assertTrue(first.ok, first.text())
            second = agent.research.read(url=URL, force_browser=True)
            self.assertTrue(second['cache_hit'])
            self.assertEqual(agent.research.browser.calls, 1)
            self.assertEqual(second['fields']['merchant_offer']['price'], '1957.50')

    def test_fresh_worker_has_no_network_keys_database_or_environment(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {'AVELIN_TEST_SECRET': 'synthetic'}):
            spec = ProcessHandlerSpec('research_host_fixture:finish', Path(folder),
                code_root=ROOT / 'tests', configuration={'max_fragments': 8})
            frame = HostedHandler(spec, timeout_seconds=3)({'snapshot': SNAPSHOT, 'metadata': METADATA})
            self.assertEqual(frame['boundary'], {'secret': None, 'database': False,
                                                'provider_keys': False, 'direct_network': False})
            self.assertTrue(frame['result']['evidence'])

    def test_cancel_and_deadline_kill_noncooperative_postprocessor_and_descendant(self):
        for cancel_early in (True, False):
            with self.subTest(cancel=cancel_early), TemporaryDirectory() as folder:
                root = Path(folder); cancel = Event(); box = {}
                reader = reader_with_stage(timeout=5 if cancel_early else 1.5)
                def host(spec, *, timeout_seconds):
                    return HostedHandler(replace(spec, entrypoint='research_host_fixture:finish',
                        code_root=ROOT / 'tests', workspace=root, read_only_workspace=False,
                        configuration={**spec.configuration, 'stall': True}), timeout_seconds=timeout_seconds)
                def read():
                    token = CURRENT_EXECUTION.set(ToolExecutionContext(cancel))
                    try: reader.read(url=URL, force_browser=True)
                    except ResearchFailure as exc: box['error'] = exc
                    finally: CURRENT_EXECUTION.reset(token)
                with patch('ai_agent.tools.hosted_research.HostedHandler', side_effect=host):
                    thread = Thread(target=read); thread.start()
                    try:
                        deadline = time.monotonic() + 1.2
                        while not (root / 'ready').exists() and time.monotonic() < deadline: time.sleep(.02)
                        self.assertTrue((root / 'ready').exists(), 'Postprocessor descendant did not start')
                        if cancel_early: cancel.set()
                        thread.join(3); self.assertFalse(thread.is_alive())
                        self.assertEqual(box['error'].code, 'cancelled' if cancel_early else 'timeout')
                        self.assertFalse(reader._cache); self.assertEqual(reader.browser.calls, 1)
                        time.sleep(3.2 if cancel_early else 1.8)
                        self.assertFalse((root / 'late').exists())
                    finally: cancel.set(); thread.join(5)

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import request
from unittest.mock import patch
import json,socket,sys,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_agent.tools.hosted_web_search import HostedWebSearchHandler,call_web_search
from ai_agent.tools.hosted_search import HostedSearch,validate_search_endpoint,_open_official
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.hosted_http import _HTTPBroker
from ai_agent.tools.process_host import ProcessHandlerSpec
from ai_agent.tools.base import ToolError,ToolResult
from ai_agent.tools.public_transport import AddressBlocked


class SearchHostContracts(unittest.TestCase):
    def test_parent_preserves_cooldown_without_exposing_provider_key(self):
        http=HttpSandbox();http.search_fallback=HostedSearch('synthetic-search-key')
        from time import monotonic
        frame={'text':'Qwen discovery sources','cooldowns':{'duckduckgo_lite':monotonic()+200},'fallback_tokens':321}
        with patch.object(_HTTPBroker,'__call__',return_value=frame) as execute:
            result=HostedWebSearchHandler(http,timeout_seconds=3)({'query':'Qwen'})
        self.assertEqual(result,'Qwen discovery sources')
        self.assertIn('duckduckgo_lite',http._search_cooldowns)
        configuration=execute.call_args[0][0]
        self.assertNotIn('api_key',configuration)

    def test_unknown_billable_timeout_stops_retries_and_model_followup(self):
        http=HttpSandbox();http.search_fallback=HostedSearch('synthetic-search-key')
        result=ToolResult('failed',error=ToolError('timeout','timeout',retryable=True))
        with patch.object(_HTTPBroker,'__call__',return_value=result):
            answer=HostedWebSearchHandler(http,timeout_seconds=3)({'query':'Qwen'})
        self.assertTrue(answer.error.details['outcome_unknown'])
        self.assertTrue(answer.error.details['billable_usage_unknown'])
        self.assertFalse(answer.error.retryable)

    def test_unknown_paid_search_stops_following_tools_and_model_in_actual_agent(self):
        from tests.test_runtime_audit import isolated_agent
        from ai_agent.types import ModelResponse,ToolCall
        for streaming in (False,True):
            with self.subTest(streaming=streaming),isolated_agent([
                ModelResponse(tool_calls=[ToolCall('web_search',{'query':'Qwen tool calling'}),
                    ToolCall('write_file',{'path':'should-not-exist.txt','content':'not allowed'})]),
                ModelResponse(text='Must never be requested')]) as agent:
                agent.http.search_fallback=HostedSearch('synthetic-search-key')
                agent.tools.get('web_search').handler=HostedWebSearchHandler(agent.http,timeout_seconds=3)
                timeout=ToolResult('failed',error=ToolError('timeout','timeout'))
                with patch.object(_HTTPBroker,'__call__',return_value=timeout):
                    if streaming:list(agent.respond_stream('Найди Qwen tool calling.'))
                    else:agent.respond('Найди Qwen tool calling.')
                self.assertEqual(len(agent.provider.inputs),1)
                self.assertFalse((agent.files.workspace_dir/'should-not-exist.txt').exists())

    def test_unreconstructable_callback_and_private_profile_fail_before_process(self):
        for private,fallback in [(True,None),(False,lambda *args:[])]:
            http=HttpSandbox(allow_private_networks=private);http.search_fallback=fallback
            with patch.object(_HTTPBroker,'__call__') as execute:
                result=HostedWebSearchHandler(http,timeout_seconds=3)({'query':'Qwen'})
                self.assertFalse(result.ok);execute.assert_not_called()

    def test_registry_deadline_keeps_billable_uncertainty_after_handler_drain(self):
        from ai_agent.tools.base import Tool,ToolRegistry,ToolPermission,RetryPolicy
        import time
        http=HttpSandbox();http.search_fallback=HostedSearch('synthetic-search-key')
        handler=HostedWebSearchHandler(http,timeout_seconds=3)
        registry=ToolRegistry();registry.register(Tool('billable_probe','Fixture',{'type':'object'},handler,
            permissions=(ToolPermission('probe.read','read'),),timeout_seconds=.04,idempotent=True,
            retry_policy=RetryPolicy(max_attempts=3)))
        def drain(*args,**kwargs):
            context=args[1] if len(args)>1 else kwargs.get('context')
            if context is None:
                from ai_agent.tools.base import CURRENT_EXECUTION
                context=CURRENT_EXECUTION.get()
            while not context.cancelled:time.sleep(.005)
            return ToolResult('cancelled',error=ToolError('cancelled','cancelled'))
        with patch.object(_HTTPBroker,'__call__',side_effect=drain) as execute:
            result=registry.invoke('billable_probe',{})
        self.assertEqual(execute.call_count,1)
        self.assertTrue(result.error.details['billable_usage_unknown'])
        self.assertFalse(result.error.retryable)

    def test_provider_key_is_never_sent_to_redirect_or_private_dns_target(self):
        for url in ['https://api.openai.com.evil.test/v1/responses','https://user@api.openai.com/v1/responses',
                'https://api.openai.com/v1/responses?redirect=elsewhere','http://api.openai.com/v1/responses']:
            with self.assertRaises(ValueError):validate_search_endpoint(url)
        with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]),patch('socket.socket') as connect:
            with self.assertRaises(AddressBlocked):_open_official(request.Request('https://api.openai.com/v1/responses'),3)
            connect.assert_not_called()
        from ai_agent.tools.hosted_search import _NoSearchRedirect
        self.assertIsNone(_NoSearchRedirect().redirect_request(None,None,302,'',{},'http://127.0.0.1'))

    def test_primary_search_connection_revalidates_and_pins_dns(self):
        def resolve(host,port,*args,**kwargs):
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',
                ('93.184.216.34' if port is None else '127.0.0.1',port or 0))]
        with patch('socket.getaddrinfo',side_effect=resolve),patch('socket.socket') as connect:
            with self.assertRaises(AddressBlocked):
                HttpSandbox()._open_public(request.Request('http://rebind.test/'))
            connect.assert_not_called()


@unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').exists(),'requires Linux bubblewrap')
class SearchHostLinux(unittest.TestCase):
    def test_registered_search_round_trip_keeps_state_and_never_returns_secret(self):
        from dataclasses import replace
        from ai_agent.tools.base import Tool,ToolRegistry
        http=HttpSandbox();http.search_fallback=HostedSearch('synthetic-search-key')
        registry=ToolRegistry();registry.register(Tool('web_search','Fixture',
            {'type':'object','properties':{'query':{'type':'string'}},'required':['query']},
            HostedWebSearchHandler(http,timeout_seconds=5)))
        original=_HTTPBroker
        def fixture_broker(spec,*,timeout_seconds):
            return original(replace(spec,entrypoint='search_host_fixture:run',code_root=ROOT/'tests',
                configuration={**spec.configuration,'fixture':'fallback'}),timeout_seconds=timeout_seconds)
        with patch('ai_agent.tools.hosted_web_search._HTTPBroker',side_effect=fixture_broker):
            first=registry.invoke('web_search',{'query':'Qwen tool calling benchmarks'})
            second=registry.invoke('web_search',{'query':'Qwen tool calling report'})
        self.assertTrue(first.ok,first.text());self.assertTrue(second.ok,second.text())
        self.assertIn('challenge cooldown',second.text())
        self.assertIn('https://example.org/qwen',second.text())
        self.assertNotIn('synthetic-search-key',first.text()+second.text())
    def run_fixture(self,configuration):
        with TemporaryDirectory() as folder:
            spec=ProcessHandlerSpec('search_host_fixture:run',Path(folder),configuration=configuration,code_root=ROOT/'tests')
            return _HTTPBroker(spec,timeout_seconds=5)({'query':'Qwen tool calling benchmarks','entrypoint':'os:system'})

    def test_primary_search_and_antibot_pause_survive_fresh_process(self):
        config={'socket_timeout':2,'max_response_chars':20000,'cooldowns':{}}
        first=self.run_fixture(config)
        self.assertIn('bing_rss',first['text']);self.assertEqual(len(first['fixture_opens']),2)
        second=self.run_fixture({**config,'cooldowns':first['cooldowns']})
        self.assertEqual(len(second['fixture_opens']),1)
        self.assertIn('challenge cooldown',second['text'])
        self.assertIn('https://example.org/qwen',second['text'])

    def test_paid_fallback_contract_uses_only_synthetic_credential_and_preserves_usage(self):
        result=self.run_fixture({'socket_timeout':2,'max_response_chars':20000,'cooldowns':{},'fixture':'fallback',
            'fallback':{'api_key':'synthetic-search-key','model':'gpt-6-luna','base_url':'https://api.openai.com/v1','timeout':2}})
        self.assertIn('openai_web_search',result['text']);self.assertIn('discovery snippets only',result['text'])
        self.assertEqual(result['fallback_tokens'],321);self.assertTrue(result['fallback_attempted'])
        self.assertNotIn('synthetic-search-key',json.dumps(result))


if __name__=='__main__':unittest.main()

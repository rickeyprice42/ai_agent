from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_agent.tools.hosted_search import HostedSearch
from ai_agent.tools.http import HttpSandbox
from ai_agent.automations import automation_name


class HostedSearchTests(unittest.TestCase):
    def test_requires_real_search_and_returns_only_cited_sources(self):
        payload={'status':'completed','output':[
            {'type':'web_search_call','status':'completed','action':{'sources':[{'url':'https://example.org/report','title':'Qwen tool calling report'}]}},
            {'type':'message','content':[{'type':'output_text','text':'A Qwen tool calling report.',
                'annotations':[{'type':'url_citation','url':'https://example.org/report','title':'Qwen report'}]}]}]}
        with patch('ai_agent.tools.hosted_search.request.urlopen',return_value=BytesIO(json.dumps(payload).encode())) as call:
            result=HostedSearch('synthetic-key')('Qwen tool calling',5)
        self.assertEqual(len(result),1)
        sent=json.loads(call.call_args.args[0].data)
        self.assertEqual(sent['tool_choice'],'required')
        self.assertFalse(sent['store'])
        payload['output']=payload['output'][1:]
        with patch('ai_agent.tools.hosted_search.request.urlopen',return_value=BytesIO(json.dumps(payload).encode())):
            with self.assertRaisesRegex(ValueError,'verified search'):HostedSearch('synthetic-key')('Qwen',5)

    def test_custom_endpoint_never_receives_openai_credential(self):
        with patch('ai_agent.tools.hosted_search.request.urlopen') as send:
            with self.assertRaises(ValueError):HostedSearch('synthetic-key',base_url='https://other.example/v1')('query',5)
            send.assert_not_called()

    def test_provider_challenge_uses_cited_fallback_and_keeps_quality_filter(self):
        http=HttpSandbox()
        fallback=lambda query,limit,country:[{'url':'https://example.org/qwen','title':'Qwen tool calling report','snippet':'Qwen tool calling benchmarks'}]
        http.search_fallback=fallback
        def challenge(*args,**kwargs):
            response=BytesIO(b'<form id="challenge-form">captcha</form>')
            response.headers={'Content-Type':'text/html'}
            return response
        with patch('ai_agent.tools.http.request.urlopen',side_effect=challenge):
            result=http.search('Qwen tool calling benchmarks')
        self.assertIn('openai_web_search',result)
        self.assertIn('https://example.org/qwen',result)

    def test_automation_titles_are_specific_without_schedule_boilerplate(self):
        prompts=['Раз в неделю проводи для меня занятие по VBA для Excel и Word 2007.',
                 'Каждую пятницу находи новые исследования открытых LLM для Avelin Health.',
                 'Каждую пятницу готовь для меня обзор новых научных исследований в приборостроении.']
        names=[automation_name(p) for p in prompts]
        self.assertEqual(len(set(names)),3)
        self.assertIn('VBA',names[0])
        self.assertFalse(any('Еженедельный черновик' in n for n in names))

"""Search context, product delivery and screenshot regressions from real usage."""
import json
from unittest.mock import MagicMock
import unittest
from tests.test_runtime_audit import isolated_agent, ScriptedProvider
from ai_agent.agent import CHAT_DISPLAY_GUIDANCE,ToolOutcome,_turn_phase
from ai_agent.types import Message,ModelResponse,ToolCall
from ai_agent.llm.base import ProviderError
from ai_agent.tools.base import ToolResult
from ai_agent.security import mark_untrusted_tool_output
from ai_agent.tools.browser import browser_read_result
from ai_agent.tools.http import _plan_search,_rank_search_results
from ai_agent.context import ModelTokenEstimator
from ai_agent.tool_payloads import compact_tool_output


def product_page(index=0):
    url=f'https://shop.example/product/{index}'
    return {'url':url,'status':200,'retrieved_at':'2026-11-14T15:00:00Z','fields':{
        'title':'Synthetic black trainers','main_text':'Catalog description. '*3500,
        'merchant_offer':{'title':'Synthetic black trainers','url':url,'price':'7990','currency':'RUB','checked_at':'2026-11-14T15:00:00Z'}}}


class ShoppingContinuityTests(unittest.TestCase):
    def test_large_old_page_is_not_promoted_to_system_memory_and_turn_fits(self):
        with isolated_agent([]) as agent:
            agent.memory.append_message(Message('user','Найди чёрные кеды. Не превышай 8000 рублей.'))
            agent.memory.append_message(Message('tool',mark_untrusted_tool_output('research_read',json.dumps(product_page())),name='research_read'))
            agent.memory.append_message(Message('assistant','Уточните город и размер.'))
            agent.memory.append_message(Message('user','Иркутск, 42 RU, до 8000 рублей.'))
            tools=agent._turn_tools(_turn_phase(ToolOutcome(),0),False)
            context=agent.context.build(agent.settings.system_prompt+CHAT_DISPLAY_GUIDANCE,model=agent.settings.model_name,tools=tools,include_task_queue=False)
            self.assertNotIn('chat_tool',context.system_prompt)
            self.assertNotIn('Catalog description. '*10,context.system_prompt)
            self.assertIn('Не превышай 8000',context.system_prompt)
            turns=[]
            for index in range(7):
                turns.extend([Message('assistant','',tool_calls=[ToolCall('research_read',{'url':product_page(index)['url']},str(index))]),
                    Message('tool',mark_untrusted_tool_output('research_read',json.dumps(product_page(index))),name='research_read',tool_call_id=str(index))])
            packed=agent._provider_messages(context,turns)
            estimator=ModelTokenEstimator(agent.settings.model_name)
            cost=estimator.count_text(context.system_prompt)+sum(estimator.count_message(m)+estimator.count_text(json.dumps([c.arguments for c in m.tool_calls])) for m in packed)
            self.assertLess(cost,context.trace.input_budget_tokens-context.trace.tool_schema_tokens)
            self.assertTrue(any('7990' in m.content for m in packed))

    def test_broad_browser_snapshot_obeys_real_compaction_budget(self):
        snapshot={'url':'https://shop.example/p','main_text':'Synthetic public product facts. '*2000,
                  'title':'Trainers','blocked_requests':['https://tracker.example/'+str(i) for i in range(500)],
                  'elements':[{'name':'label '+('x'*1000),'ref':str(i)} for i in range(1000)],
                  'json_ld':[{'@type':'Product','name':'Trainers','offers':{'price':7990,'priceCurrency':'RUB'}}]}
        text=mark_untrusted_tool_output('browser_read',json.dumps(snapshot))
        compact=compact_tool_output(text,1500)
        self.assertLessEqual(len(compact),1500)
        self.assertIn('shop.example/p',compact)

    def test_browser_read_does_not_capture_by_default_and_internal_image_is_not_delivered(self):
        runner=MagicMock()
        runner.read.return_value={'main_text':'Page','status':200,'url':'https://shop.example','screenshot':None}
        browser_read_result(runner,{'url':'https://shop.example'})
        self.assertFalse(runner.read.call_args.kwargs['screenshot'])
        with isolated_agent([]) as agent:
            output={'screenshot':{'path':'outputs/internal.png','size_bytes':100}}
            self.assertIsNone(agent._artifact_from_tool('browser_read',{},'completed',output,user_text='Найди кеды по скриншоту.'))
            self.assertEqual(agent._artifact_from_tool('browser_read',{},'completed',output,user_text='Сделай скриншот страницы.')['path'],'outputs/internal.png')

    def test_verified_product_survives_later_provider_failure_without_fake_file_claim(self):
        for streaming in (False,True):
            with self.subTest(streaming=streaming), isolated_agent([]) as agent:
                class Provider:
                    def generate(self,**kwargs):
                        raise ProviderError('Synthetic capacity stop','context_overflow')
                    def stream(self,**kwargs):
                        raise ProviderError('Synthetic capacity stop','context_overflow')
                        yield
                # The first model call returns a real tool invocation; the next fails.
                first=ScriptedProvider([ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://shop.example/product/0'},'p')])])
                class Sequence:
                    calls=0
                    def generate(self,**kwargs):
                        self.calls+=1
                        return first.generate(**kwargs) if self.calls==1 else Provider().generate(**kwargs)
                    def stream(self,**kwargs):
                        yield type('Chunk',(),{'delta':'','response':self.generate(**kwargs)})()
                agent.provider=Sequence()
                tool=agent.tools.get('research_read')
                tool.handler=lambda args:ToolResult('completed',output=product_page())
                tool.output_schema={'type':'object'}
                if streaming:list(agent.respond_stream('Найди кеды до 8000 рублей.'))
                else:agent.respond('Найди кеды до 8000 рублей.')
                final=agent.memory.database.list_messages(agent.memory.thread_id)[-1]
                self.assertEqual(final['artifacts'][0]['_verified_product']['price'],'7990')
                self.assertIn('карточки товаров',final['content'])
                self.assertNotIn('Созданные файлы',final['content'])

    def test_russian_shopping_rejects_obvious_foreign_storefront(self):
        plan=_plan_search('Vans Old Skool купить 42 Россия до 8000 рублей',country='ru')
        results,rejected=_rank_search_results(plan,[
            {'url':'https://vans.example.ae/old-skool','title':'Vans Old Skool UAE 42 sale','snippet':'Vans Old Skool AED 100'},
            {'url':'https://shop.example.ru/vans-old-skool','title':'Vans Old Skool купить Россия 42','snippet':'Vans Old Skool 7990 рублей'}],5)
        self.assertEqual(len(results),1)
        self.assertEqual(rejected['foreign_market'],1)

    def test_browser_product_uses_page_offer_without_delivering_screenshot(self):
        with isolated_agent([]) as agent:
            browser=agent.tools.get('browser_read')
            browser.availability=lambda:(True,None)
            browser.output_schema={'type':'object'}
            url='https://shop.example/product/0'
            browser.handler=lambda args:ToolResult('completed',output={'url':url,'status':200,
                'screenshot':{'path':'outputs/internal.png','size_bytes':123},
                'json_ld':[{'@type':'Product','name':'Trainers','url':url,
                    'offers':{'@type':'Offer','price':7990,'priceCurrency':'RUB','url':url}}]})
            result=agent._execute_tool_calls([ToolCall('browser_read',{'url':url,'screenshot':True})],'Найди кеды до 8000 рублей.')
            self.assertEqual(result.products[url]['price'],'7990')
            self.assertEqual(result.artifacts,[])

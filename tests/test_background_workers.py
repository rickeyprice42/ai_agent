import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))
from ai_agent.chat_titles import ChatTitleService
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from ai_agent.memory import MemoryStore
from ai_agent.types import Message, ModelResponse
from backend.main import _title_worker, _push_worker
from backend.service import AgentService


class BackgroundWorkersTests(unittest.TestCase):
    def test_title_worker_uses_bounded_provider_and_does_not_need_chat_to_remain_open(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            service=AgentService.__new__(AgentService)
            service.root_dir=ROOT
            service.settings=replace(Settings.load(ROOT),model_provider='mock',model_name='mock-local',
                database_file=root/'db.sqlite',memory_file=root/'memory.json',tool_workspace_dir=root/'workspace')
            service.database=AvelinDatabase(service.settings.database_file,default_model_provider='mock',default_model_name='mock-local')
            service._agents={}
            memory=MemoryStore(service.settings.database_file,user_id='alice')
            memory.append_message(Message(role='user',content='Помоги выбрать монитор для работы.'))
            factory_settings=[]
            class Provider:
                def generate(self,system,messages,tools):
                    self_test.assertEqual(tools,[])
                    return ModelResponse(text='Монитор для комфортной работы')
            self_test=self
            def factory(settings):factory_settings.append(settings);return Provider()
            original=ChatTitleService.process_queue
            async def one_cycle():
                stop=asyncio.Event()
                def process(instance,*args,**kwargs):
                    result=original(instance,*args,**kwargs);stop.set();return result
                with patch('backend.main.agent_service',service),patch('backend.main.build_provider',factory),patch.object(ChatTitleService,'process_queue',process):
                    await _title_worker(stop)
            asyncio.run(one_cycle())
            self.assertEqual(service.database.get_chat_thread('alice',memory.thread_id)['title'],'Монитор для комфортной работы')
            self.assertEqual(factory_settings[0].llm_timeout_seconds,12)
            self.assertEqual(factory_settings[0].llm_max_retries,0)
            self.assertFalse(factory_settings[0].reasoning_fallback_enabled)

    def test_push_worker_drains_server_queue_without_a_browser_poll(self):
        calls=[]
        async def one_cycle():
            stop=asyncio.Event()
            class Store:
                def deliver_pending(self):calls.append('delivered');stop.set()
            with patch('backend.main.push_service_for',return_value=Store()):
                await _push_worker(stop)
        asyncio.run(one_cycle())
        self.assertEqual(calls,['delivered'])


if __name__=='__main__':unittest.main()

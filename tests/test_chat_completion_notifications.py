from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from ai_agent.llm.base import ProviderError
from ai_agent.tools.base import Tool, ToolPermission
from ai_agent.types import ModelResponse, ModelStreamChunk, ToolCall
from ai_agent.web_push import _notification_payload
from backend.service import AgentService


class Provider:
    def __init__(self,responses):self.responses=iter(responses)
    def generate(self,**_):
        result=next(self.responses)
        if isinstance(result,Exception):raise result
        return result
    def stream(self,**kwargs):yield ModelStreamChunk(response=self.generate(**kwargs))


class ChatCompletionNotificationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();root=Path(self.temp.name)
        self.service=AgentService.__new__(AgentService)
        self.service.root_dir=root
        self.service.settings=replace(Settings.load(ROOT),model_provider='mock',model_name='mock-local',
            database_file=root/'db.sqlite',memory_file=root/'memory.json',tool_workspace_dir=root/'workspace')
        self.service.database=AvelinDatabase(self.service.settings.database_file,default_model_provider='mock',default_model_name='mock-local')
        self.service._agents={};self.service._connector_factories=[]
        self.a=self.service.database.create_user('a@complete.test','complete-a','hash','A')['id']
        self.b=self.service.database.create_user('b@complete.test','complete-b','hash','B')['id']
        self.agent=self.service.agent_for_user(self.a);self.thread=self.agent.memory.thread_id
        self.agent.tools.register(Tool(name='external_fixture',description='Approval fixture',
            schema={'type':'object','properties':{}},permissions=(ToolPermission('external.fixture','external'),),handler=lambda _:'Verified effect'))

    def tearDown(self):self.temp.cleanup()

    def completed(self,user=None):
        return [row for row in self.service.database.list_in_app_notifications(user or self.a) if row['kind']=='chat_completed']

    def stream(self,prompt='Объясни результат'):
        _,events=self.service.chat_stream(prompt,self.a,self.thread)
        return list(events)

    def pending(self):
        self.agent.provider=Provider([ModelResponse(tool_calls=[ToolCall('external_fixture',{})])])
        return next(event['approval'] for event in self.stream('Выполни внешнее действие и объясни результат') if event['type']=='approval')

    def test_sync_completion_is_durable_deduplicated_exact_message_and_private(self):
        self.agent.provider=Provider([ModelResponse(text='Объяснение слова error без ошибки выполнения.')])
        self.service.chat('PRIVATE USER PROMPT',self.a,self.thread)
        message_id=self.agent.last_turn_message_id
        self.assertEqual(self.agent.last_turn_outcome,'completed')
        notification=self.completed()[0]
        with self.service.database.connect() as db:
            key=db.execute('SELECT deduplication_key FROM in_app_notifications WHERE id=?',(notification['id'],)).fetchone()[0]
        self.assertEqual(key,'chat-completed:'+message_id)
        self.assertEqual(notification['entity_id'],self.thread)
        self.assertNotIn('PRIVATE',str(notification))
        self.service._notify_chat_completion(self.a,self.thread,message_id,'completed')
        self.service._notify_chat_completion(self.b,self.thread,message_id,'completed')
        self.assertEqual(len(self.completed()),1)
        self.assertEqual(self.completed(self.b),[])
        self.service._agents.clear()
        self.assertEqual(len(self.completed()),1)
        payload=_notification_payload(notification)
        self.assertEqual(payload['data']['url'],'/?view=chat&thread='+self.thread)
        self.assertNotIn('PRIVATE',str(payload))

    def test_stream_notification_is_saved_before_done_is_delivered_and_close_preserves_completion(self):
        self.agent.provider=Provider([ModelResponse(text='Результат подтверждён.')])
        _,events=self.service.chat_stream('Поясни результат',self.a,self.thread)
        for event in events:
            if event['type']=='done':
                self.assertEqual(event['outcome'],'completed')
                self.assertEqual(len(self.completed()),1)
                break
        events.close()
        self.assertEqual(self.agent.last_turn_outcome,'completed')
        self.assertEqual(len(self.completed()),1)

    def test_cancellation_has_no_success_notification(self):
        class PartialProvider:
            def stream(self,**_):
                yield ModelStreamChunk(delta='Частичный ответ')
                yield ModelStreamChunk(response=ModelResponse(text='Законченный ответ'))
        self.agent.provider=PartialProvider()
        _,events=self.service.chat_stream('Объясни результат',self.a,self.thread)
        self.assertEqual(next(events)['type'],'delta')
        events.close()
        self.assertEqual(self.agent.last_turn_outcome,'cancelled')
        self.assertEqual(self.completed(),[])
        self.assertEqual(self.service.database.list_messages(self.thread)[-1]['status'],'cancelled')

    def test_provider_failure_in_sync_and_stream_never_notifies_success(self):
        self.agent.provider=Provider([ProviderError('Safe failure','upstream_unavailable')])
        with self.assertRaises(ProviderError):self.service.chat('Объясни результат',self.a,self.thread)
        self.assertEqual(self.agent.last_turn_outcome,'failed')
        self.agent.provider=Provider([ProviderError('Safe failure','upstream_unavailable')])
        self.assertEqual(self.stream()[-1]['type'],'error')
        self.assertEqual(self.completed(),[])

    def test_waiting_approval_deny_and_resume_only_notifies_real_completion(self):
        approval=self.pending()
        self.assertEqual(self.agent.last_turn_outcome,'waiting_approval')
        self.assertEqual(self.completed(),[])
        self.agent.provider=Provider([ModelResponse(text='Подтвержденный результат готов.')])
        _,events=self.service.decide_chat_approval(self.a,self.thread,approval['id'],'approved')
        for event in events:
            if event['type']=='done':
                self.assertEqual(event['outcome'],'completed')
                break
        events.close()
        self.assertEqual(self.agent.pending_chat_actions.get(self.a,self.thread,approval['id'])['status'],'completed')
        self.assertEqual(len(self.completed()),1)
        second=self.pending()
        _,events=self.service.decide_chat_approval(self.a,self.thread,second['id'],'denied')
        list(events)
        self.assertEqual(len(self.completed()),1)

    def test_sync_approval_wait_has_no_completion_notification(self):
        self.agent.provider=Provider([ModelResponse(tool_calls=[ToolCall('external_fixture',{})])])
        self.service.chat('Выполни внешнее действие и объясни результат',self.a,self.thread)
        self.assertEqual(self.agent.last_turn_outcome,'waiting_approval')
        self.assertEqual(self.completed(),[])

    def test_failure_after_saved_artifact_is_partial_and_not_success_even_with_complete_row(self):
        for streaming in (True,False):
            with self.subTest(streaming=streaming):
                self.agent.provider=Provider([ModelResponse(tool_calls=[ToolCall('create_docx',{
                    'path':f'report-{streaming}.docx','title':'Report','paragraphs':['Verified draft']})]),
                    ProviderError('Safe provider failure','upstream_unavailable')])
                if streaming:self.assertEqual(self.stream('Подготовь файл и поясни содержимое')[-1]['outcome'],'partial')
                else:self.service.chat('Подготовь файл и поясни содержимое',self.a,self.thread)
                self.assertEqual(self.agent.last_turn_outcome,'partial')
                row=self.service.database.list_messages(self.thread)[-1]
                self.assertEqual(row['status'],'complete')
                self.assertTrue(row['artifacts'])
                self.assertEqual(self.completed(),[])

    def test_shared_mention_required_and_off_do_not_notify(self):
        project=self.service.database.create_project(self.a,title='Shared',shared=True)
        thread=self.service.database.create_chat_thread(self.a,project_id=project['id'])['id']
        for mode in ('mention_only','off'):
            self.service.database.update_project(self.a,project['id'],agent_mode=mode)
            _,events=self.service.chat_stream('Командное сообщение',self.a,thread)
            self.assertEqual(list(events)[0]['status'],'mention_required')
            self.assertEqual(self.service.chat('Командное сообщение',self.a,thread)['reply'],'')
        self.assertEqual(self.completed(),[])

    def test_step_limit_is_not_completed(self):
        self.agent.provider=Provider([ModelResponse(tool_calls=[ToolCall('get_time',{})])])
        with patch('ai_agent.agent.MAX_TOOL_ROUNDS',1):
            self.assertNotEqual(self.stream()[-1]['outcome'],'completed')
        self.assertEqual(self.completed(),[])


if __name__=='__main__':unittest.main()

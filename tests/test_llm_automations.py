from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from threading import Event, Thread

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))
from ai_agent.agent import Agent
from ai_agent.automation_runner import LLMAutomationRunner
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from ai_agent.llm.base import ProviderError
from ai_agent.tasks import TaskManager
from ai_agent.tools.base import ToolResult, ToolPermission
from ai_agent.types import ModelResponse,ModelStreamChunk,ToolCall
from backend.service import AgentService


class Provider:
    def __init__(self,responses):self.responses=iter(responses);self.inputs=[]
    def stream(self,**kwargs):
        self.inputs.append(kwargs)
        response=next(self.responses)
        if isinstance(response,Exception):raise response
        yield ModelStreamChunk(response=response)


class LLMAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();root=Path(self.temp.name)
        self.service=AgentService.__new__(AgentService);self.service.root_dir=ROOT
        self.service.settings=replace(Settings.load(ROOT),model_provider='mock',model_name='mock-local',
            database_file=root/'db.sqlite',memory_file=root/'memory.json',tool_workspace_dir=root/'workspace')
        self.service.database=AvelinDatabase(self.service.settings.database_file,default_model_provider='mock',default_model_name='mock-local')
        self.service._agents={};self.service._connector_factories=[]
        self.user=self.service.database.create_user('llm@test.invalid','llm-auto','hash','Alice')['id']
        self.provider=Provider([]);self.effects=[]
        self.handlers={
            'web_search':lambda args:ToolResult('completed',output='Study: https://example.org/study'),
            'research_read':lambda args:ToolResult('completed',output={'url':args['url'],'retrieved_at':'2026-11-04T00:00:00Z','locale':'en-US','region':'us',
                'extraction_method':'fixture','adapter':'generic','confidence':.9,'fields':{'title':'Study','text':'Verified fixture evidence'},
                'evidence':[{'text':'Verified fixture evidence'}],'safety':{},'support':'supported','cache_hit':False}),
            'http_request':lambda args:self.effects.append(args) or ToolResult('completed',output='Verified external effect')}
        def factory(settings,user_id,thread_id):
            agent=Agent(settings,user_id=user_id,thread_id=thread_id,task_queue_context=False)
            agent.provider=self.provider
            for name,handler in self.handlers.items():agent.tools.get(name).handler=handler
            agent.tools.get('http_request').permissions=(ToolPermission('external.fixture','external'),)
            return agent
        self.service._automation_agent_factory=factory

    def tearDown(self):self.temp.cleanup()

    def occurrence(self,budget=None,prompt=None):
        automation=self.service.create_automation(self.user,{'name':'Research fixture','prompt':prompt or 'Найди новые исследования и составь проверяемый итог со ссылками.',
            'schedule_type':'weekly','timezone':'UTC','weekday':4,'time_of_day':'10:00','budget':budget or {}})
        occurrence=self.service.run_automation_now(self.user,automation['id'])
        return automation,occurrence

    def result(self,automation,occurrence):
        return next(row for row in self.service.automation_runs(self.user,automation['id']) if row['task_run_id']==occurrence['task_run_id'])

    def test_multistep_research_persists_final_links_tool_checkpoints_and_scoped_artifact(self):
        self.provider=Provider([
            ModelResponse(tool_calls=[ToolCall('web_search',{'query':'new LLM research'})]),
            ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://example.org/study'})]),
            ModelResponse(tool_calls=[ToolCall('create_docx',{'path':'report.docx','title':'Research','paragraphs':['Study: https://example.org/study']})]),
            ModelResponse(text='Исследование проверено: https://example.org/study')])
        automation,occurrence=self.occurrence(prompt='Найди исследования и создай документ report.docx с текстом: проверенный итог со ссылками.');self.service.process_automation_queue()
        result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'completed');self.assertEqual(result['outcome'],'completed')
        self.assertIn('https://example.org/study',result['result']);self.assertTrue(result['thread_id'])
        self.assertIn('Verified fixture evidence',' '.join(message.content for message in self.provider.inputs[2]['messages']))
        artifact=result['artifacts'][0];self.assertTrue(artifact['path'].startswith(f"automations/{automation['id']}/{occurrence['task_run_id']}/"))
        self.assertTrue(self.service.workspace_file_path(self.user,artifact['path']).is_file())
        checkpoints=self.service.database.list_task_checkpoints(self.user,occurrence['task_run_id'])
        self.assertTrue(any(cp['tool_name']=='research_read' and cp['evidence']['verified'] for cp in checkpoints))
        self.assertGreater(result['usage']['tokens'],0)
        self.assertEqual(self.service.process_automation_queue(),[])
        self.service._agents.clear();self.assertEqual(self.result(automation,occurrence)['result'],result['result'])

    def test_token_budget_rejects_before_provider_call(self):
        automation,occurrence=self.occurrence({'max_tokens':1})
        self.service.process_automation_queue()
        self.assertEqual(self.result(automation,occurrence)['state'],'failed')
        self.assertEqual(self.provider.inputs,[])

    def test_office_2007_lesson_is_not_a_docx_delivery_request(self):
        self.provider=Provider([ModelResponse(text='Практический урок VBA: массивы Excel и таблицы Word 2007. Пример и упражнение.')])
        automation,occurrence=self.occurrence(prompt='Раз в неделю проводи для меня короткое практическое занятие по VBA для Excel и Word 2007. Используй реальные типы задач: извлечение таблиц из больших документов Word в Excel, обработка отчётных данных, ускорение макросов и сохранение форматирования. Давай небольшой пример кода, практическое задание и разбор решения. Учитывай ограничения Office 2007 и слабого рабочего ПК.')
        self.service.process_automation_queue()
        self.assertEqual(self.result(automation,occurrence)['state'],'completed')

    def test_large_site_write_can_be_read_by_its_relative_path_without_replaying_code(self):
        content='<html>'+('synthetic-site-code '*3200)+'</html>'
        self.provider=Provider([
            ModelResponse(tool_calls=[ToolCall('write_file',{'path':'outputs/site/index.html','content':content})]),
            ModelResponse(tool_calls=[ToolCall('read_file_lines',{'path':'outputs/site/index.html','start_line':1,'end_line':1})]),
            ModelResponse(text='HTML создан, прочитан. Браузерная проверка не выполнялась.')])
        automation,occurrence=self.occurrence(prompt='Создай локальный HTML сайт и проверь, что сохранённый файл читается.')
        self.service.process_automation_queue()
        result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'completed',result)
        artifact=next(a for a in result['artifacts'] if a['path'].endswith('index.html'))
        self.assertEqual(self.service.workspace_file_path(self.user,artifact['path']).read_text('utf-8'),content)
        second=self.provider.inputs[1]['messages']
        self.assertFalse(any('synthetic-site-code '*10 in str(call.arguments) for m in second for call in m.tool_calls))
        checkpoints=self.service.database.list_task_checkpoints(self.user,occurrence['task_run_id'])
        self.assertTrue(any(cp['tool_name']=='read_file_lines' and cp['status']=='completed' for cp in checkpoints))

    def test_step_budget_stops_before_next_tool_effect(self):
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('web_search',{'query':'fixture'})]),
            ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://example.org/study'})])])
        reads=[];self.handlers['research_read']=lambda args:reads.append(args) or ToolResult('completed',output='must not run')
        automation,occurrence=self.occurrence({'max_steps':1})
        self.service.process_automation_queue();result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'failed');self.assertEqual(reads,[])
        self.assertIn('бюджет действий',result['failure_reason'])

    def test_step_budget_allows_a_final_synthesis_without_more_tools(self):
        self.provider=Provider([
            ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://example.org/study'})]),
            ModelResponse(text='Проверенный результат: https://example.org/study')])
        automation,occurrence=self.occurrence({'max_steps':1})
        self.service.process_automation_queue()
        self.assertEqual(self.result(automation,occurrence)['state'],'completed')
        self.assertEqual(self.provider.inputs[-1]['tools'],[])
        self.assertIn('без инструментов',self.provider.inputs[-1]['system_prompt'])

    def test_previous_material_is_scoped_to_the_same_automation(self):
        self.provider=Provider([ModelResponse(text='Занятие номер один: массивы VBA.')])
        auto,first=self.occurrence(prompt='Проводи занятие по VBA, не повторяй старые темы.')
        self.service.process_automation_queue()
        self.provider=Provider([ModelResponse(text='Занятие номер два: обработка ошибок.')])
        second=self.service.run_automation_now(self.user,auto['id'])
        self.service.process_automation_queue()
        prompt=' '.join(m.content for m in self.provider.inputs[0]['messages'])
        self.assertIn('массивы VBA',prompt)
        self.assertIn('Расписание сохранено сервером',prompt)

    def test_provider_failure_after_file_is_failed_with_preserved_artifact(self):
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('create_docx',{'path':'partial.docx','paragraphs':['Draft']})]),ProviderError('Safe failure','upstream_unavailable')])
        automation,occurrence=self.occurrence();self.service.process_automation_queue()
        result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'failed');self.assertEqual(result['outcome'],'partial')
        self.assertTrue(result['artifacts']);self.assertTrue(self.service.workspace_file_path(self.user,result['artifacts'][0]['path']).is_file())

    def test_waiting_approval_survives_agent_restart_then_continues_exact_action(self):
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('http_request',{'url':'https://example.org/action','method':'POST'})])])
        automation,occurrence=self.occurrence({'max_seconds':30},prompt='Выполни предусмотренное действие и объясни результат.');self.service.process_automation_queue()
        result=self.result(automation,occurrence);self.assertEqual(result['state'],'waiting_approval');self.assertEqual(self.effects,[])
        self.service._agents.clear()
        with self.service.database.connect() as db:db.execute('UPDATE task_runs SET started_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(hours=8)).isoformat(),occurrence['task_run_id']))
        agent=self.service.agent_for_user(self.user,result['thread_id'])
        agent.provider=Provider([ModelResponse(text='Подтверждённое действие выполнено.')])
        agent.tools.get('http_request').handler=self.handlers['http_request']
        agent.tools.get('http_request').permissions=(ToolPermission('external.fixture','external'),)
        _,events=self.service.decide_chat_approval(self.user,result['thread_id'],result['approval_ids'][0],'approved')
        self.assertEqual(list(events)[-1]['outcome'],'completed')
        self.assertEqual(len(self.effects),1)
        self.assertEqual(self.result(automation,occurrence)['state'],'completed')
        self.assertEqual(self.service.process_automation_queue(),[])

    def test_denied_approval_cancels_without_effect(self):
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('http_request',{'url':'https://example.org/action','method':'POST'})])])
        automation,occurrence=self.occurrence();self.service.process_automation_queue();result=self.result(automation,occurrence)
        _,events=self.service.decide_chat_approval(self.user,result['thread_id'],result['approval_ids'][0],'denied');list(events)
        self.assertEqual(self.result(automation,occurrence)['state'],'cancelled');self.assertEqual(self.effects,[])

    def test_expired_running_turn_fails_without_replaying_unknown_effects(self):
        automation,occurrence=self.occurrence();runner=LLMAutomationRunner(self.service)
        thread=self.service.database.create_chat_thread(self.user,title='Interrupted fixture')
        with self.service.database.connect() as db:
            db.execute("UPDATE task_runs SET state='running',worker_id='dead',lease_expires_at='2000-01-01' WHERE id=?",(occurrence['task_run_id'],))
            db.execute("INSERT INTO automation_llm_runs(run_id,user_id,thread_id,outcome) VALUES(?,?,?,'running')",(occurrence['task_run_id'],self.user,thread['id']))
        self.service.process_automation_queue();result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'running');self.assertEqual(self.provider.inputs,[])
        # Legacy metadata cannot establish that a crashed parent's children
        # have stopped. Keep the result unresolved instead of replaying it.
        from ai_agent.task_recovery import TaskRecovery
        self.assertFalse(TaskRecovery(self.service.database,self.user).status(occurrence['task_run_id'])['can_recover'])

    def test_cancel_during_first_tool_prevents_later_actions(self):
        automation,occurrence=self.occurrence()
        def cancel(args):
            TaskManager(self.service.database,self.user).cancel_task(occurrence['task_id'])
            self.effects.append('one read');return ToolResult('completed',output={'sources':[]})
        self.handlers['web_search']=cancel
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('web_search',{'query':'fixture'})]),ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://example.org/study'})])])
        self.service.process_automation_queue()
        self.assertEqual(self.result(automation,occurrence)['state'],'cancelled');self.assertEqual(len(self.provider.inputs),1)

    def test_remote_stop_keeps_ownership_until_uncooperative_handler_drains(self):
        started, release = Event(), Event()
        errors = []
        def slow_read(args):
            started.set()
            if not release.wait(10):
                raise RuntimeError('test handler was not released')
            return ToolResult('completed', output='Already performed read')
        self.handlers['web_search'] = slow_read
        self.provider = Provider([
            ModelResponse(tool_calls=[ToolCall('web_search', {'query':'fixture'})]),
            ModelResponse(text='Must not request another model turn')])
        automation, occurrence = self.occurrence()
        def work():
            try:self.service.process_automation_queue()
            except Exception as exc:errors.append(exc)
        worker = Thread(target=work)
        worker.start()
        try:
            self.assertTrue(started.wait(10))
            # Independent connection represents an HTTP process controlling
            # a worker that has no shared in-memory cancellation event.
            remote = AvelinDatabase(self.service.settings.database_file)
            remote.request_task_stop(self.user, occurrence['task_run_id'], 'paused')
            stopping = remote.get_task_run(self.user, occurrence['task_run_id'])
            self.assertEqual(stopping['state'], 'running')
            self.assertEqual(stopping['control_requested'], 'paused')
            self.assertTrue(stopping['worker_id'])
            self.assertEqual(self.result(automation, occurrence)['control_requested'], 'paused')
            self.assertIsNone(remote.acquire_task_run(self.user, 'second-worker', 180, run_id=stopping['id']))
            self.assertIsNone(remote.request_task_stop('other-user', stopping['id'], 'cancelled'))
            # Escalation from pause to cancellation cannot be downgraded.
            remote.request_task_stop(self.user, stopping['id'], 'cancelled')
            remote.request_task_stop(self.user, stopping['id'], 'paused')
            self.assertEqual(remote.get_task_run(self.user, stopping['id'])['control_requested'], 'cancelled')
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join(15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        result = self.result(automation, occurrence)
        self.assertEqual(result['state'], 'cancelled')
        self.assertIsNone(result['control_requested'])
        self.assertEqual(len(self.provider.inputs), 1)

    def test_remote_stop_reaches_cooperative_handler(self):
        started, stopped = Event(), Event()
        def read(args, context):
            started.set()
            from time import monotonic, sleep
            deadline = monotonic() + 5
            while not context.cancelled and monotonic() < deadline:sleep(.01)
            if context.cancelled:stopped.set()
            return ToolResult('cancelled' if context.cancelled else 'failed', output='Stopped')
        self.handlers['web_search'] = read
        self.provider = Provider([ModelResponse(tool_calls=[ToolCall('web_search', {'query':'fixture'})])])
        automation, occurrence = self.occurrence()
        worker = Thread(target=self.service.process_automation_queue)
        worker.start()
        try:
            self.assertTrue(started.wait(10))
            remote = AvelinDatabase(self.service.settings.database_file)
            remote.request_task_stop(self.user, occurrence['task_run_id'], 'cancelled')
            self.assertTrue(stopped.wait(3))
        finally:worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.result(automation, occurrence)['state'], 'cancelled')

    def test_expired_pending_stop_is_not_reclaimed_or_replayed(self):
        _, occurrence = self.occurrence()
        run_id = occurrence['task_run_id']
        with self.service.database.connect() as db:
            db.execute("UPDATE task_runs SET state='running',worker_id='dead',lease_expires_at='2000-01-01',control_requested='cancelled' WHERE id=?", (run_id,))
        self.assertIsNone(self.service.database.acquire_task_run(self.user, 'new-worker', 180, run_id=run_id))
        run = self.service.database.get_task_run(self.user, run_id)
        self.assertEqual((run['state'], run['worker_id'], run['control_requested']), ('running', 'dead', 'cancelled'))

    def test_expired_wall_budget_prevents_model_request(self):
        automation,occurrence=self.occurrence({'max_seconds':30})
        LLMAutomationRunner(self.service)
        thread=self.service.database.create_chat_thread(self.user,title='Budget fixture')
        with self.service.database.connect() as db:
            db.execute("INSERT INTO automation_llm_runs(run_id,user_id,thread_id,outcome,active_seconds) VALUES(?,?,?,'step_completed',60)",
                (occurrence['task_run_id'],self.user,thread['id']))
        self.service.process_automation_queue()
        self.assertEqual(self.result(automation,occurrence)['state'],'failed');self.assertEqual(self.provider.inputs,[])

    def test_research_claim_without_a_source_read_is_not_completed(self):
        self.provider=Provider([ModelResponse(text='Нашёл исследование: https://example.org/invented')])
        automation,occurrence=self.occurrence();self.service.process_automation_queue()
        result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'failed');self.assertEqual(result['outcome'],'partial')
        self.assertIn('не подтверждено',result['failure_reason'])

    def test_lost_lease_stops_before_effect_and_never_overwrites_new_worker(self):
        automation,occurrence=self.occurrence();effects=[]
        self.handlers['get_time']=lambda _:effects.append('effect') or 'time'
        outer=self
        class StealingProvider:
            def stream(self,**_):
                with outer.service.database.connect() as db:db.execute("UPDATE task_runs SET worker_id='replacement' WHERE id=?",(occurrence['task_run_id'],))
                yield ModelStreamChunk(response=ModelResponse(tool_calls=[ToolCall('get_time',{})]))
        self.provider=StealingProvider()
        self.service.process_automation_queue()
        current=self.service.database.get_task_run(self.user,occurrence['task_run_id'])
        self.assertEqual(current['state'],'running');self.assertEqual(current['worker_id'],'replacement');self.assertEqual(effects,[])

    def test_multiple_plan_steps_requeue_then_finish_without_replaying_prior_tools(self):
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://example.org/study'})]),
            ModelResponse(text='Первый шаг: https://example.org/study'),ModelResponse(text='Итог второго шага: https://example.org/study')])
        automation,occurrence=self.occurrence()
        with self.service.database.connect() as db:db.execute("INSERT INTO task_steps(id,task_id,position,description,status) VALUES('next-step',?,2,'Составь итог второго шага со ссылками','pending')",(occurrence['task_id'],))
        self.service.process_automation_queue()
        self.assertEqual(self.result(automation,occurrence)['state'],'queued')
        first=self.service.database.get_task(occurrence['task_id'])
        self.assertEqual([step['status'] for step in first['steps']],['completed','pending'])
        self.service.process_automation_queue()
        self.assertEqual(self.result(automation,occurrence)['state'],'completed')
        self.assertEqual([step['status'] for step in self.service.database.get_task(occurrence['task_id'])['steps']],['completed','completed'])
        self.assertEqual(len(self.provider.inputs),3)

    def test_unattended_text_research_advertises_dom_readers_without_cloud_image_analysis(self):
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://example.org/study'})]),
            ModelResponse(text='Проверенный источник: https://example.org/study')])
        automation,occurrence=self.occurrence();self.service.process_automation_queue()
        self.assertEqual(self.result(automation,occurrence)['state'],'completed')
        names={tool['name'] for tool in self.provider.inputs[0]['tools']}
        self.assertIn('browser_read',names);self.assertIn('research_read',names)
        self.assertNotIn('analyze_image',names)
        self.assertIn('screenshot=false',self.provider.inputs[0]['messages'][-1].content)

    def test_png_artifact_cannot_fulfill_a_word_document_request(self):
        from PIL import Image
        def png(args):
            path=self.service.user_workspace(self.user)/args['path']
            path.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(2,2),'white').save(path)
            return ToolResult('completed',output='Создано изображение: '+args['path'])
        self.handlers['write_file']=png
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://example.org/study'})]),
            ModelResponse(tool_calls=[ToolCall('write_file',{'path':'only.png','content':'fixture'})]),
            ModelResponse(text='Источник: https://example.org/study')])
        automation,occurrence=self.occurrence(prompt='Найди исследование и подготовь Word документ с источниками.')
        self.service.process_automation_queue();result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'failed');self.assertEqual(result['outcome'],'partial')
        self.assertTrue(any(item['path'].endswith('.png') for item in result['artifacts']))
        self.assertIn('DOCX',result['failure_reason'])

    def test_default_budget_can_finish_after_observed_real_research_usage(self):
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('research_read',{'url':'https://example.org/study'})]),
            ModelResponse(text='Проверенный итог: https://example.org/study')])
        automation,occurrence=self.occurrence()
        self.assertEqual(automation['budget']['max_tokens'],180000)
        self.assertEqual(automation['budget']['max_failures'],5)
        self.assertEqual(automation['budget']['max_steps'],20)
        self.assertEqual(automation['budget']['max_seconds'],1800)
        self.service.database.update_task_run_usage(self.user,occurrence['task_run_id'],tokens=85980,failures=4)
        self.service.process_automation_queue()
        result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'completed')
        self.assertGreater(result['usage']['tokens'],85980)
        self.assertEqual(result['usage']['failures'],4)

    def test_cancel_or_pause_after_final_checks_cannot_be_overwritten_by_completion(self):
        for operation in ('cancel','pause'):
            with self.subTest(operation=operation):
                self.provider=Provider([ModelResponse(tool_calls=[ToolCall('create_docx',{'path':operation+'.docx','paragraphs':['Draft']})]),
                    ModelResponse(text='Документ подготовлен.')])
                automation,occurrence=self.occurrence(prompt='Подготовь Word документ и объясни результат.')
                original=self.service.database.finalize_automation_agent_run
                def interleave(*args,**kwargs):
                    manager=TaskManager(self.service.database,self.user)
                    if operation=='cancel':manager.cancel_task(occurrence['task_id'])
                    else:manager.pause_task(occurrence['task_id'])
                    return original(*args,**kwargs)
                self.service.database.finalize_automation_agent_run=interleave
                try:self.service.process_automation_queue()
                finally:self.service.database.finalize_automation_agent_run=original
                expected='cancelled' if operation=='cancel' else 'paused'
                result=self.result(automation,occurrence)
                self.assertEqual(result['state'],expected)
                self.assertEqual(result['outcome'],expected)
                self.assertEqual(self.service.database.get_task(occurrence['task_id'])['status'],expected)
                self.assertTrue(result['artifacts'])
                self.assertFalse(any(item['title']=='Задача: completed' for item in self.service.database.list_in_app_notifications(self.user)))

    def test_replacement_owner_at_final_commit_preserves_entire_new_worker_state(self):
        self.provider=Provider([ModelResponse(tool_calls=[ToolCall('create_docx',{'path':'race.docx','paragraphs':['Draft']})]),ModelResponse(text='Готово.')])
        automation,occurrence=self.occurrence(prompt='Подготовь Word документ и объясни результат.')
        original=self.service.database.finalize_automation_agent_run
        def interleave(*args,**kwargs):
            with self.service.database.connect() as db:
                db.execute("UPDATE task_runs SET worker_id='replacement' WHERE id=?",(occurrence['task_run_id'],))
                db.execute("UPDATE tasks SET result='REPLACEMENT RESULT' WHERE id=?",(occurrence['task_id'],))
                db.execute("UPDATE automation_llm_runs SET result='REPLACEMENT RESULT' WHERE run_id=?",(occurrence['task_run_id'],))
            return original(*args,**kwargs)
        self.service.database.finalize_automation_agent_run=interleave
        try:self.service.process_automation_queue()
        finally:self.service.database.finalize_automation_agent_run=original
        run=self.service.database.get_task_run(self.user,occurrence['task_run_id'])
        self.assertEqual(run['state'],'running');self.assertEqual(run['worker_id'],'replacement')
        self.assertEqual(self.service.database.get_task(occurrence['task_id'])['result'],'REPLACEMENT RESULT')
        link=LLMAutomationRunner(self.service).get(self.user,run_id=run['id'])
        self.assertEqual(link['outcome'],'running');self.assertEqual(link['result'],'REPLACEMENT RESULT')
        self.assertFalse(any(cp['tool_name']=='file_metadata' for cp in self.service.database.list_task_checkpoints(self.user,run['id'])))

    def test_monetary_limit_fails_before_provider_call_with_a_terminal_run(self):
        automation,occurrence=self.occurrence({'max_cost_micros':1})
        self.service.process_automation_queue();result=self.result(automation,occurrence)
        self.assertEqual(result['state'],'failed');self.assertEqual(self.provider.inputs,[])
        self.assertIn('стоимости',result['failure_reason'])


if __name__=='__main__':unittest.main()

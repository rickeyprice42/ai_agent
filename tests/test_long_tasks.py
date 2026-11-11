from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_agent.long_tasks import LongTaskPlans, LongTaskRunner, PlanConflict, daily_long_task_reports
from ai_agent.llm.base import ProviderError
from ai_agent.types import ModelResponse, ToolCall
from tests import test_llm_automations as fixtures
from backend.auth import issue_session_token
from backend.main import app
from fastapi.testclient import TestClient
from unittest.mock import patch
Provider = fixtures.Provider


class LongTaskTests(unittest.TestCase):
    setUp = fixtures.LLMAutomationTests.setUp
    tearDown = fixtures.LLMAutomationTests.tearDown

    def draft(self, **changes):
        thread = self.service.database.create_chat_thread(self.user,title='Game project')
        data = dict(title='Мини-игра: первая бета',goal='Создай маленькую игру от концепта до проверенной браузерной бета-версии.',
            steps=['Сохрани концепт игры в concept.md и проверь файл.', 'Создай самостоятельную браузерную игру index.html и проверь файл.', 'Проверь готовые файлы и подготовь beta-notes.md.'],
            acceptance_checks=['Игра запускается без сети и имеет понятные правила.','Файлы существуют и проверены инструментами.'])
        data.update(changes)
        return LongTaskPlans(self.service.database,self.user).propose(thread['id'],**data)

    def approve(self,plan):
        return LongTaskPlans(self.service.database,self.user).decide(plan['id'],'approved',plan['revision'],plan['digest'],'Asia/Irkutsk')

    def test_draft_does_not_create_task_or_execute_model_and_approval_is_idempotent(self):
        plan = self.draft()
        self.assertEqual(self.service.process_long_task_queue(),[])
        self.assertEqual(self.provider.inputs,[])
        with self.service.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks WHERE user_id=?',(self.user,)).fetchone()[0],0)
        approved = self.approve(plan)
        again = self.approve(plan)
        self.assertEqual(approved['run_id'],again['run_id'])
        self.assertEqual(approved['run']['state'],'queued')
        with self.service.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks WHERE user_id=?',(self.user,)).fetchone()[0],1)

    def test_regular_execute_next_cannot_steal_an_approved_background_plan(self):
        plan = self.approve(self.draft())
        result = self.service.execute_next_step(self.user)
        self.assertIn('нет задач',result.lower())
        self.assertEqual(LongTaskPlans(self.service.database,self.user).get(plan['id'])['run']['state'],'queued')

    def test_model_can_only_propose_the_plan_without_starting_execution(self):
        thread = self.service.database.create_chat_thread(self.user,title='Plan proposal')
        agent = self.service.agent_for_user(self.user,thread['id'])
        agent.provider = Provider([ModelResponse(tool_calls=[ToolCall('propose_long_task',{
            'title':'Проверяемая мини-игра','goal':'Создай офлайн браузерную игру до первой тестовой беты.',
            'steps':['Подготовь концепт в отдельном файле.','Создай и проверь работающую игру.'],
            'acceptance_checks':['Игра работает без сети и показывает счёт.']})]),ModelResponse(text='План подготовлен для утверждения.')])
        list(agent.respond_stream('Сначала предложи план длительной работы над игрой, не выполняй его до утверждения.'))
        plans = LongTaskPlans(self.service.database,self.user).for_thread(thread['id'])
        self.assertEqual(len(plans),1)
        self.assertEqual(plans[0]['status'],'draft')
        self.assertEqual(plans[0]['request_text'],'Сначала предложи план длительной работы над игрой, не выполняй его до утверждения.')
        self.assertIsNone(plans[0]['run'])
        self.assertEqual(self.service.process_long_task_queue(),[])

    def test_real_code_check_requires_approval_and_continues_after_agent_restart(self):
        plan = self.approve(self.draft())
        prefix = f"outputs/tasks/{plan['id']}"
        self.provider = Provider([ModelResponse(tool_calls=[ToolCall('write_file',{'path':'probe.py','content':'VALUE = 42\n'})]),
            ModelResponse(tool_calls=[ToolCall('run_code_check',{'runner':'python_compile','target':prefix+'/probe.py'})])])
        self.service.process_long_task_queue()
        current = LongTaskPlans(self.service.database,self.user).get(plan['id'])
        self.assertEqual(current['run']['state'],'waiting_approval')
        path = self.service.workspace_file_path(self.user,prefix+'/probe.py')
        self.assertFalse((path.parent/'__pycache__').exists())
        self.service._agents.clear()
        agent = self.service.agent_for_user(self.user,plan['work_thread_id'])
        agent.provider = Provider([ModelResponse(text='Файл проверен компилятором, этап завершён.')])
        approval = agent.pending_chat_actions.list(self.user,plan['work_thread_id'])[0]
        _,events = self.service.decide_chat_approval(self.user,plan['work_thread_id'],approval['id'],'approved')
        list(events)
        self.assertTrue((path.parent/'__pycache__').is_dir())
        current = LongTaskPlans(self.service.database,self.user).get(plan['id'])
        self.assertEqual(current['run']['state'],'queued')
        self.assertEqual(current['task']['steps'][0]['status'],'completed')

    def test_changed_plan_requires_current_digest_and_other_user_cannot_approve(self):
        plan = self.draft()
        store = LongTaskPlans(self.service.database,self.user)
        current = store.propose(plan['origin_thread_id'],title=plan['title'],goal=plan['goal'],steps=plan['steps']+['Дополнительно проверь клавиатурное управление.'],acceptance_checks=plan['acceptance_checks'])
        self.assertEqual(current['revision'],2)
        with self.assertRaises(PlanConflict):self.approve(plan)
        other = self.service.database.create_user('other-long@test.invalid','other-long','hash','Other')['id']
        with self.assertRaises(ValueError):LongTaskPlans(self.service.database,other).decide(plan['id'],'approved',2,current['digest'])
        self.assertEqual(store.get(plan['id'])['status'],'draft')

    def test_real_api_requires_owner_and_exact_plan_revision(self):
        plan = self.draft()
        token = issue_session_token()
        self.service.database.create_session(self.user,token.token_hash,token.expires_at)
        headers = {'Authorization':'Bearer '+token.raw}
        other = self.service.database.create_user('api-other@test.invalid','api-other','hash','Other')['id']
        other_token = issue_session_token()
        self.service.database.create_session(other,other_token.token_hash,other_token.expires_at)
        with patch('backend.routes.agent_service',self.service):
            client = TestClient(app)
            url = f"/api/chats/{plan['origin_thread_id']}/plans"
            self.assertEqual(client.get(url,headers=headers).status_code,200)
            self.assertEqual(client.get(url,headers={'Authorization':'Bearer '+other_token.raw}).status_code,404)
            decision = {'decision':'approved','revision':1,'digest':'0'*64,'timezone':'Asia/Irkutsk'}
            endpoint = f"/api/long-tasks/{plan['id']}/decision"
            self.assertEqual(client.post(endpoint,headers=headers,json=decision).status_code,409)
            decision['digest'] = plan['digest']
            result = client.post(endpoint,headers=headers,json=decision)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['run_id'],client.post(endpoint,headers=headers,json=decision).json()['run_id'])

    def test_three_phase_file_work_survives_restart_and_reuses_verified_context(self):
        plan = self.approve(self.draft())
        contents = [('concept.md','# Concept\nAvoid the moving blocks.'),('index.html','<!doctype html><title>Beta</title><button>Play</button>'),('beta-notes.md','# Beta\nThree verified files.')]
        for index,(name,content) in enumerate(contents):
            self.provider = Provider([ModelResponse(tool_calls=[ToolCall('write_file',{'path':name,'content':content})]),ModelResponse(text=f'Этап {index+1} выполнен; файл сохранён.')])
            self.service._agents.clear()
            runs = self.service.process_long_task_queue()
            self.assertEqual(len(runs),1)
            current = LongTaskPlans(self.service.database,self.user).get(plan['id'])
            self.assertEqual(current['run']['state'],'completed' if index==2 else 'queued')
            self.assertEqual(sum(s['status']=='completed' for s in current['task']['steps']),index+1)
            self.assertTrue(self.service.workspace_file_path(self.user,f"outputs/tasks/{plan['id']}/{name}").is_file())
            if index:
                text = self.provider.inputs[0]['messages'][-1].content
                self.assertIn('Завершённые этапы',text)
                self.assertIn('concept.md',text)
                self.assertFalse(any('Avoid the moving blocks.' in message.content for message in self.provider.inputs[0]['messages'] if message.role=='tool'))
        self.assertEqual(self.service.process_long_task_queue(),[])

    def test_next_phase_uses_verified_handoff_without_replaying_large_old_tool_outputs(self):
        plan = self.approve(self.draft())
        large = 'OLD_PHASE_CONTENT_' * 1000
        self.provider = Provider([ModelResponse(tool_calls=[ToolCall('write_file',{'path':'concept.md','content':large})]),ModelResponse(text='Концепт сохранён в concept.md.')])
        self.service.process_long_task_queue()
        old_rows = self.service.database.list_messages(plan['work_thread_id'])
        self.provider = Provider([ModelResponse(tool_calls=[ToolCall('write_file',{'path':'index.html','content':'<!doctype html><title>Beta</title>'})]),ModelResponse(text='HTML сохранён.')])
        self.service._agents.clear()
        self.service.process_long_task_queue()
        request = self.provider.inputs[0]
        self.assertNotIn('OLD_PHASE_CONTENT_', '\n'.join(m.content for m in request['messages']))
        self.assertIn('concept.md',request['messages'][-1].content)
        current_rows = self.service.database.list_messages(plan['work_thread_id'])
        self.assertTrue(all(any(new['id']==old['id'] and new['content']==old['content'] for new in current_rows) for old in old_rows))

    def test_relative_stage_notes_can_be_read_without_granting_another_plan_access(self):
        plan = self.approve(self.draft())
        runner = LongTaskRunner(self.service)
        agent = runner._agent(self.user,runner.get(self.user,run_id=plan['run_id']),plan['run'])
        path = agent.automation_output_prefix+'/outputs/tasks/stage-2.md'
        agent.files.write_file(path,'Verified stage notes')
        result = agent.tools.get('read_file_lines').handler({'path':'outputs/tasks/stage-2.md'})
        self.assertIn('Verified stage notes',str(result))
        with self.assertRaises(ValueError):
            agent.tools.get('read_file_lines').handler({'path':'outputs/tasks/00000000-0000-0000-0000-000000000000/private.md'})

    def test_plain_model_done_without_tool_evidence_is_not_completed(self):
        plan = self.approve(self.draft())
        self.provider = Provider([ModelResponse(text='Всё готово!')])
        self.service.process_long_task_queue()
        current = LongTaskPlans(self.service.database,self.user).get(plan['id'])
        self.assertEqual(current['run']['state'],'failed')
        self.assertIn('не подтверждён',current['run']['failure_reason'])

    def test_long_task_provider_timeout_follows_configuration_and_remaining_budget(self):
        plan = self.approve(self.draft())
        runner = LongTaskRunner(self.service)
        link = runner.get(self.user, run_id=plan['run_id'])
        settings = runner._runtime_settings(self.user, link, plan['run'])
        self.assertEqual(settings.llm_timeout_seconds, min(120, self.service.settings.llm_timeout_seconds))
        link['active_seconds'] = plan['run']['budget']['max_seconds'] - 11
        settings = runner._runtime_settings(self.user, link, plan['run'])
        self.assertEqual(settings.llm_timeout_seconds, min(11, self.service.settings.llm_timeout_seconds))

    def test_provider_failure_after_saved_file_preserves_file_and_specific_failure_reason(self):
        plan = self.approve(self.draft())
        self.provider = Provider([
            ModelResponse(tool_calls=[ToolCall('write_file', {'path':'concept.md','content':'# Concept'})]),
            ProviderError('Провайдер превысил время ожидания.', 'timeout'),
        ])
        self.service.process_long_task_queue()
        result = LongTaskPlans(self.service.database, self.user).get(plan['id'])
        self.assertEqual(result['run']['state'], 'failed')
        self.assertIn('Провайдер превысил время ожидания.', result['run']['failure_reason'])
        self.assertTrue(self.service.workspace_file_path(self.user, result['artifacts'][0]['path']).is_file())

    def test_initialization_failure_finalizes_the_current_phase_without_waiting_for_lease(self):
        plan = self.approve(self.draft())
        with patch.object(LongTaskRunner, '_agent', side_effect=ValueError('invalid setup')):
            self.service.process_long_task_queue()
        result = LongTaskPlans(self.service.database, self.user).get(plan['id'])
        self.assertEqual(result['run']['state'], 'failed')
        self.assertIn('этап 1', result['run']['failure_reason'])
        self.assertEqual(result['task']['steps'][0]['status'], 'failed')
        self.assertEqual(self.provider.inputs, [])

    def test_crash_does_not_replay_unknown_action(self):
        plan = self.approve(self.draft())
        with self.service.database.connect() as connection:
            connection.execute("UPDATE task_runs SET state='running',worker_id='dead',lease_expires_at='2000-01-01' WHERE id=?",(plan['run_id'],))
            connection.execute("UPDATE automation_llm_runs SET outcome='running' WHERE run_id=?",(plan['run_id'],))
        self.service.process_long_task_queue()
        self.assertEqual(self.provider.inputs,[])
        self.assertEqual(LongTaskPlans(self.service.database,self.user).get(plan['id'])['run']['state'],'failed')

    def test_daily_reports_use_local_day_checkpoints_and_do_not_collide_between_goals(self):
        plans = [self.approve(self.draft()),self.approve(self.draft(title='Вторая независимая бета'))]
        for plan in plans:
            for hour in [15,17]:
                cp = self.service.database.create_task_checkpoint(self.user,run_id=plan['run_id'],task_id=plan['task_id'],step_id=None,
                    plan_version=plan['revision'],status='completed',risk='read',tool_name='file_metadata',arguments_digest='x',
                    deduplication_key=f'cp-{hour}',tool_call={'name':'file_metadata'},observation=f'file {hour}',evidence={'verified':True})
                with self.service.database.connect() as connection:
                    connection.execute('UPDATE task_checkpoints SET created_at=?,updated_at=? WHERE id=?',(f'2026-11-04T{hour}:30:00+00:00',f'2026-11-04T{hour}:30:00+00:00',cp['id']))
            with self.service.database.connect() as connection:connection.execute("UPDATE long_task_plans SET report_cursor='2026-11-04' WHERE id=?",(plan['id'],))
        reports = daily_long_task_reports(self.service.database,datetime(2026,11,4,18,tzinfo=timezone.utc))
        self.assertEqual(len(reports),2)
        self.assertNotEqual(reports[0]['id'],reports[1]['id'])
        self.assertTrue(all(len(r['payload']['completed_actions'])==1 for r in reports))
        self.assertTrue(all(r['payload']['completed_actions'][0]['observation']=='file 15' for r in reports))
        self.assertEqual(daily_long_task_reports(self.service.database,datetime(2026,11,4,18,tzinfo=timezone.utc)),[])


if __name__=='__main__':unittest.main()

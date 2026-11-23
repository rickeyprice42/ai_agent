import asyncio
import json
from threading import Event, Thread
import unittest
from unittest.mock import patch

from tests import test_llm_automations as fixtures
from ai_agent.tasks import TaskManager
from ai_agent.task_recovery import TaskRecovery, RecoveryConflict
from ai_agent.worker_identity import current_worker_identity, worker_liveness, worker_quiescence
from ai_agent.worker_lifecycle import task_worker_stop
from ai_agent.types import ModelResponse, ToolCall
from ai_agent.tools.base import ToolResult
from backend.main import _drain_durable_workers, app
from backend.auth import issue_session_token
from fastapi.testclient import TestClient


class TaskRecoveryTests(unittest.TestCase):
    setUp = fixtures.LLMAutomationTests.setUp
    tearDown = fixtures.LLMAutomationTests.tearDown

    def test_cancelled_task_updates_leave_task_and_steps_intact(self):
        manager = TaskManager(self.service.database, self.user)
        task = manager.create_task("Mutation fixture", steps=["Original"])
        before = self.service.database.get_task(task.id)

        def cancel():
            raise InterruptedError("Cancelled before commit")

        for action, args in (
            (manager.add_step, (task.id, "Extra")),
            (manager.update_task, (task.id, "completed", "Result")),
            (manager.update_step, (task.steps[0].id, "completed", "Result")),
        ):
            with self.subTest(action=action.__name__):
                with self.assertRaises(InterruptedError):
                    action(*args, check_cancelled=cancel)
                self.assertEqual(self.service.database.get_task(task.id), before)

        updated = manager.add_step(task.id, "Extra")
        self.assertEqual(updated.status, "planned")
        self.assertEqual([step.description for step in updated.steps], ["Original", "Extra"])

    def test_plan_tool_rolls_back_task_steps_and_run_on_cancel(self):
        from types import SimpleNamespace
        from ai_agent.tools import builtin

        database = self.service.database
        manager = TaskManager(database, self.user)
        planner = SimpleNamespace(create_plan=lambda **kwargs: SimpleNamespace(
            goal="Cancellation fixture", priority=3, steps=["First step"],
        ))

        def counts():
            with database.connect() as connection:
                return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                             for table in ("tasks", "task_steps", "task_runs"))

        before = counts()
        with patch.object(builtin, "_check_task_write_cancelled", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                builtin._plan_task(planner, manager, {"goal": "Cancellation fixture"})
        self.assertEqual(counts(), before)
        builtin._plan_task(planner, manager, {"goal": "Cancellation fixture"})
        task = manager.list_tasks()[0]
        self.assertEqual(task.status, "planned")
        self.assertEqual([step.description for step in task.steps], ["First step"])
        self.assertEqual(counts(), tuple(count + 1 for count in before))

    def orphan(self):
        task = TaskManager(self.service.database,self.user).create_task('Recovery fixture',steps=['External fixture'])
        run = self.service.database.acquire_task_run(self.user,'old-worker')
        cp = self.service.database.create_task_checkpoint(self.user,run_id=run['id'],task_id=task.id,
            step_id=task.steps[0].id,plan_version=1,status='started',risk='external',tool_name='http_request',
            arguments_digest='fixture-digest',deduplication_key='fixture-key',tool_call={'name':'http_request'},
            evidence={'verified':False},observation='Request started')
        with self.service.database.connect() as db:
            db.execute("UPDATE task_runs SET lease_expires_at='2000-01-01',control_requested='cancelled' WHERE id=?",(run['id'],))
        return task,run,cp

    def test_expired_lease_of_live_process_does_not_authorize_reclaim_or_recovery(self):
        task,run,_ = self.orphan()
        self.assertEqual(worker_liveness(current_worker_identity()),'alive')
        recovery = TaskRecovery(self.service.database,self.user)
        self.assertFalse(recovery.status(run['id'])['can_recover'])
        with self.service.database.connect() as db:db.execute('UPDATE task_runs SET control_requested=NULL WHERE id=?',(run['id'],))
        self.assertIsNone(self.service.database.acquire_task_run(self.user,'new-worker',run_id=run['id']))
        self.assertEqual(self.service.database.get_task_run(self.user,run['id'])['worker_id'],'old-worker')

    def test_close_records_unknown_effect_without_replay_and_is_not_repeatable(self):
        task,run,cp = self.orphan()
        store = TaskRecovery(self.service.database,self.user)
        with patch('ai_agent.task_recovery.worker_quiescence',return_value=True):
            status = store.status(run['id'])
            result = store.close(run['id'],status['token'],'Проверил файлы и журнал запроса, результат внешнего действия неизвестен.',True)
            self.assertEqual(result['state'],'cancelled')
            self.assertIsNone(result['worker_id'])
            self.assertIsNone(result['control_requested'])
            with self.assertRaises(RecoveryConflict):store.close(run['id'],status['token'],'Повторная попытка закрытия.',True)
        checkpoint = self.service.database.list_task_checkpoints(self.user,run['id'])[0]
        self.assertEqual(checkpoint['status'],'interrupted')
        self.assertFalse(checkpoint['evidence']['verified'])
        self.assertTrue(checkpoint['evidence']['outcome_unknown'])
        self.assertEqual(checkpoint['observation'],'Request started')
        self.assertEqual(TaskManager(self.service.database,self.user).get_task(task.id).status,'cancelled')
        self.assertEqual(self.provider.inputs,[])
        with self.service.database.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM task_run_recoveries').fetchone()[0],1)

    def test_token_changes_and_explicit_acknowledgement_are_required(self):
        _,run,_ = self.orphan()
        store = TaskRecovery(self.service.database,self.user)
        with patch('ai_agent.task_recovery.worker_quiescence',return_value=True):
            status = store.status(run['id'])
            with self.assertRaises(ValueError):store.close(run['id'],status['token'],'Проверил результат задачи.',False)
            with self.service.database.connect() as db:db.execute("UPDATE task_runs SET worker_id='replacement' WHERE id=?",(run['id'],))
            with self.assertRaises(RecoveryConflict):store.close(run['id'],status['token'],'Проверил результат задачи.',True)
        self.assertEqual(self.service.database.get_task_run(self.user,run['id'])['state'],'running')

    def test_recovery_revokes_only_pending_approvals_bound_to_that_run(self):
        from ai_agent.automation_runner import LLMAutomationRunner
        LLMAutomationRunner(self.service)
        task,run,_ = self.orphan()
        db = self.service.database
        approvals = [db.create_approval_request(self.user,tool_name='http_request',arguments_digest=key,
            arguments={'url':'https://example.invalid'},scope={},risk='external',expires_at='2099-01-01',
            task_id=task.id) for key in ('bound','unrelated')]
        thread = db.create_chat_thread(self.user,title='Recovery fixture')
        with db.connect() as connection:
            connection.execute("INSERT INTO automation_llm_runs(run_id,user_id,thread_id,outcome,approvals_json) VALUES(?,?,?,'running',?)",
                (run['id'],self.user,thread['id'],json.dumps([approvals[0]['id']])))
        store = TaskRecovery(db,self.user)
        with patch('ai_agent.task_recovery.worker_quiescence',return_value=True):
            store.close(run['id'],store.status(run['id'])['token'],'Проверил разрешения и результаты запуска.',True)
        self.assertEqual(db.get_approval_request(self.user,approvals[0]['id'])['status'],'revoked')
        self.assertEqual(db.get_approval_request(self.user,approvals[1]['id'])['status'],'pending')

    def test_dead_parent_without_containment_proof_stays_blocked(self):
        identity = current_worker_identity()
        identity['birth'] = 'different-process-start'
        self.assertEqual(worker_liveness(identity),'dead')
        identity.pop('service',None)
        self.assertFalse(worker_quiescence(identity))
        self.assertEqual(worker_liveness('{bad-json'),'unknown')

    def test_recovery_http_enforces_account_isolation_and_conflict_response(self):
        _,run,_ = self.orphan()
        other = self.service.database.create_user('other@fixture.invalid','recovery-other','hash','Other')['id']
        tokens = {}
        for user in (self.user,other):
            token = issue_session_token()
            self.service.database.create_session(user,token.token_hash,token.expires_at)
            tokens[user] = token.raw
        from backend import routes
        with patch.object(routes,'agent_service',self.service):
            client = TestClient(app)
            def headers(user):return {'Authorization':'Bearer '+tokens[user]}
            url = '/api/task-runs/'+run['id']
            self.assertEqual(client.get(url+'/recovery',headers=headers(other)).status_code,404)
            status = client.get(url+'/recovery',headers=headers(self.user)).json()
            self.assertNotIn('worker_identity',status)
            payload = {'token':status['token'],'note':'Проверил результат задачи.','acknowledge_unknown_effects':True}
            self.assertEqual(client.post(url+'/recover-stop',json=payload,headers=headers(other)).status_code,404)
            self.assertEqual(client.post(url+'/recover-stop',json=payload,headers=headers(self.user)).status_code,409)
            with patch('ai_agent.task_recovery.worker_quiescence',return_value=True):
                self.assertEqual(client.post(url+'/recover-stop',json=payload,headers=headers(self.user)).status_code,200)

    def test_shutdown_pauses_active_tool_and_does_not_launch_queued_work(self):
        started,stopped = Event(),Event()
        def slow(args,context):
            started.set()
            if context.cancellation.wait(5):stopped.set()
            return ToolResult('cancelled',display_text='Stopped fixture')
        self.handlers['web_search'] = slow
        self.provider = fixtures.Provider([ModelResponse(tool_calls=[ToolCall('web_search',{'query':'fixture'})])])
        _,occurrence = fixtures.LLMAutomationTests.occurrence(self)
        worker = Thread(target=self.service.process_automation_queue)
        worker.start()
        try:
            self.assertTrue(started.wait(10))
            task_worker_stop(self.service).set()
            self.assertTrue(stopped.wait(3))
        finally:worker.join(10)
        self.assertFalse(worker.is_alive())
        run = self.service.database.get_task_run(self.user,occurrence['task_run_id'])
        self.assertEqual(run['state'],'paused')
        self.assertIsNone(run['worker_id'])
        with self.assertRaises(ValueError):TaskManager(self.service.database,self.user).resume_task(occurrence['task_id'])
        self.assertEqual(self.service.process_automation_queue(),[])
        self.assertEqual(len(self.provider.inputs),1)

    def test_unresolved_old_automation_does_not_starve_new_queued_work(self):
        _,old = fixtures.LLMAutomationTests.occurrence(self,prompt='Старая задача с неизвестным результатом.')
        with self.service.database.connect() as db:
            db.execute("UPDATE task_runs SET state='running',worker_id='unknown',lease_expires_at='2000-01-01' WHERE id=?",(old['task_run_id'],))
        self.provider = fixtures.Provider([ModelResponse(text='Новый материал подготовлен.')])
        _,fresh = fixtures.LLMAutomationTests.occurrence(self,prompt='Подготовь новый учебный материал.')
        self.service.process_automation_queue(limit=1)
        self.assertEqual(self.service.database.get_task_run(self.user,fresh['task_run_id'])['state'],'completed')
        self.assertEqual(self.service.database.get_task_run(self.user,old['task_run_id'])['state'],'running')

    def test_full_api_lifespan_shutdown_drains_automation_and_preserves_pause(self):
        from backend import main, routes
        started,stopped = Event(),Event()
        def slow(args,context):
            started.set()
            if context.cancellation.wait(10):stopped.set()
            return ToolResult('cancelled',display_text='Stopped on service shutdown')
        self.handlers['web_search'] = slow
        self.provider = fixtures.Provider([ModelResponse(tool_calls=[ToolCall('web_search',{'query':'fixture'})])])
        _,occurrence = fixtures.LLMAutomationTests.occurrence(self)
        # Both background workers and HTTP routes use the isolated service.
        # No real account/database is touched by the lifespan fixture.
        with patch.object(main,'agent_service',self.service),patch.object(routes,'agent_service',self.service):
            with TestClient(app):
                self.assertTrue(started.wait(10))
                self.assertEqual(self.service.database.get_task_run(self.user,occurrence['task_run_id'])['state'],'running')
        self.assertTrue(stopped.is_set())
        run = self.service.database.get_task_run(self.user,occurrence['task_run_id'])
        self.assertEqual(run['state'],'paused')
        self.assertIsNone(run['worker_id'])
        self.assertIsNone(run['control_requested'])

    def test_drain_waits_for_real_thread_even_if_cleanup_is_cancelled(self):
        started,release,finished = Event(),Event(),Event()
        def handler():
            started.set()
            release.wait(5)
            finished.set()
        async def check():
            worker = asyncio.create_task(asyncio.to_thread(handler))
            await asyncio.to_thread(started.wait,2)
            draining = asyncio.create_task(_drain_durable_workers([worker]))
            await asyncio.sleep(.01)
            draining.cancel()
            await asyncio.sleep(.02)
            self.assertFalse(draining.done())
            self.assertFalse(finished.is_set())
            release.set()
            await asyncio.wait_for(draining,3)
            self.assertTrue(finished.is_set())
        try:asyncio.run(check())
        finally:release.set()


if __name__ == '__main__':unittest.main()

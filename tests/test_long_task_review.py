"""Independent control/scope regressions for the durable long-task runner."""
from datetime import datetime, timezone
import unittest

from tests import test_llm_automations as automation_fixture
from ai_agent.long_tasks import LongTaskPlans, LongTaskRunner, daily_long_task_reports
from ai_agent.types import ModelResponse, ToolCall


class LongTaskIndependentReview(unittest.TestCase):
    def setUp(self):
        self.fixture=automation_fixture.LLMAutomationTests()
        self.fixture.setUp()
        self.service=self.fixture.service
        self.user=self.fixture.user
        self.thread=self.service.database.create_chat_thread(self.user,title='Independent plan review')
        self.plans=LongTaskPlans(self.service.database,self.user)
        proposed=self.plans.propose(self.thread['id'],title='Мини-игра',goal='Создать маленькую проверенную тестовую игру.',
            steps=['Создать концепцию и правила игры.','Написать код и проверить первую версию.'],
            acceptance_checks=['Код первой версии проходит предопределённую проверку.'])
        self.plan=self.plans.decide(proposed['id'],'approved',proposed['revision'],proposed['digest'])

    def tearDown(self):
        self.fixture.tearDown()

    def test_pausing_a_pending_approval_can_resume_to_the_same_approval_without_execution(self):
        run_id=self.plan['run_id']
        with self.service.database.connect() as db:
            db.execute("UPDATE task_runs SET state='waiting_approval' WHERE id=?",(run_id,))
            db.execute("UPDATE automation_llm_runs SET outcome='waiting_approval',approvals_json='[\"same-approval\"]' WHERE run_id=?",(run_id,))
        self.plans.control(self.plan['id'],'pause')
        resumed=self.plans.control(self.plan['id'],'resume')
        self.assertEqual(resumed['run']['state'],'waiting_approval')
        link=LongTaskRunner(self.service).get(self.user,run_id=run_id)
        self.assertEqual(link['outcome'],'waiting_approval')
        self.assertEqual(link['approval_ids'],['same-approval'])
        self.assertEqual(self.fixture.provider.inputs,[])

    def test_plan_approval_does_not_authorize_editing_unrelated_preexisting_user_files(self):
        runner=LongTaskRunner(self.service)
        run=self.service.database.get_task_run(self.user,self.plan['run_id'])
        agent=runner._agent(self.user,runner.get(self.user,run_id=run['id']),run)
        agent.files.write_file('personal.txt','ORIGINAL')
        metadata=agent.files.metadata('personal.txt')
        arguments={'path':'personal.txt','expected_sha256':metadata['sha256'],'old_text':'ORIGINAL','new_text':'CHANGED'}
        tool=agent.tools.get('edit_file_exact')
        # A simple plan click cannot grant a write outside its output namespace.
        decision=agent.policy.authorize(tool,arguments,step_id='chat:'+agent.memory.thread_id)
        if decision.outcome=='allow':
            try: result=tool.handler(arguments)
            except (ValueError,PermissionError): pass
            else:
                if hasattr(result,'ok'):self.assertFalse(result.ok)
        self.assertEqual(agent.files.resolve_download_path('personal.txt').read_text(encoding='utf-8'),'ORIGINAL')

    def test_completed_plan_does_not_send_empty_daily_reports_forever(self):
        run_id=self.plan['run_id']
        with self.service.database.connect() as db:
            db.execute("UPDATE task_runs SET state='completed',finished_at='2026-11-01 12:00:00' WHERE id=?",(run_id,))
            db.execute("UPDATE tasks SET status='completed' WHERE id=?",(self.plan['task_id'],))
            db.execute("UPDATE automation_llm_runs SET outcome='completed' WHERE run_id=?",(run_id,))
            db.execute("UPDATE long_task_plans SET report_cursor='2026-11-01' WHERE id=?",(self.plan['id'],))
        reports=daily_long_task_reports(self.service.database,now=datetime(2026,11,5,tzinfo=timezone.utc))
        self.assertLessEqual(len(reports),1)
        self.assertEqual(daily_long_task_reports(self.service.database,now=datetime(2026,11,6,tzinfo=timezone.utc)),[])

    def test_concept_code_and_real_checks_require_separate_approval_then_finish_first_version(self):
        # A deliberately tiny playable CLI prototype, not a claim of a full game.
        self.fixture.provider=automation_fixture.Provider([
            ModelResponse(tool_calls=[ToolCall('write_file',{'path':'concept.md','content':'Guess a number from 1 to 10. Feedback says lower, higher or correct.'})]),
            ModelResponse(text='Концепция сохранена в concept.md.')])
        self.service.process_long_task_queue()
        self.assertEqual(self.plans.get(self.plan['id'])['run']['state'],'queued')
        game=('def feedback(secret, guess):\n'
              '    return "correct" if guess == secret else "higher" if guess < secret else "lower"\n'
              '\nif __name__ == "__main__":\n'
              '    import random\n    secret=random.randint(1,10)\n'
              '    while True:\n        result=feedback(secret,int(input("Guess 1..10: ")))\n'
              '        print(result)\n        if result == "correct": break\n')
        tests=('import unittest\nfrom game import feedback\n\nclass GameChecks(unittest.TestCase):\n'
               '    def test_correct(self): self.assertEqual(feedback(5,5),"correct")\n'
               '    def test_higher(self): self.assertEqual(feedback(5,2),"higher")\n'
               '    def test_lower(self): self.assertEqual(feedback(5,8),"lower")\n')
        self.fixture.provider=automation_fixture.Provider([
            ModelResponse(tool_calls=[ToolCall('write_file',{'path':'game.py','content':game}),
                                      ToolCall('write_file',{'path':'tests/test_game.py','content':tests})]),
            ModelResponse(tool_calls=[ToolCall('run_code_check',{'runner':'python_unittest','target':'tests','timeout_seconds':10})]),
            ModelResponse(text='Первая CLI-версия готова; три проверки игровой обратной связи прошли.')])
        self.service._agents.clear()
        self.service.process_long_task_queue()
        current=self.plans.get(self.plan['id'])
        self.assertEqual(current['run']['state'],'waiting_approval')
        runner=LongTaskRunner(self.service)
        link=runner.get(self.user,run_id=self.plan['run_id'])
        approvals=self.service.agent_for_user(self.user,link['thread_id']).pending_chat_actions.list(self.user,link['thread_id'])
        self.assertEqual(len(approvals),1)
        self.assertEqual(approvals[0]['tool_name'],'run_code_check')
        _,events=self.service.decide_chat_approval(self.user,link['thread_id'],approvals[0]['id'],'approved')
        list(events)
        current=self.plans.get(self.plan['id'])
        self.assertEqual(current['run']['state'],'completed')
        checks=self.service.database.list_task_checkpoints(self.user,self.plan['run_id'])
        real_check=next(cp for cp in checks if cp['tool_name']=='run_code_check' and cp['status']=='completed')
        self.assertTrue(real_check['evidence']['verified'])
        self.assertIn('Ran 3 tests',real_check['observation'])
        self.assertIn('OK',real_check['observation'])


if __name__=='__main__':unittest.main()

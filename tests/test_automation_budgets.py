from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))
from ai_agent.automations import AutomationService,DEFAULT_AUTOMATION_BUDGET,LEGACY_UI_AUTOMATION_BUDGET,plan_stock_research_budgets
from ai_agent.database import AvelinDatabase


class AutomationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.db=AvelinDatabase(Path(self.temp.name)/'db.sqlite')
        self.db.ensure_user_defaults('alice');self.service=AutomationService(self.db,'alice')

    def tearDown(self):self.temp.cleanup()

    def make(self,prompt='Найди новые исследования LLM',budget=None,state='failed'):
        item=self.service.create(name='Fixture',prompt=prompt,schedule_type='weekly',timezone_name='UTC',weekday=1,time_of_day='12:00',budget=budget)
        run=self.service.run_now(item['id'])
        self.db.set_task_run_state('alice',run['task_run_id'],state,failure_reason='Fixture failure' if state=='failed' else None)
        return item,run

    def plan(self):
        with self.db.connect() as connection:return plan_stock_research_budgets(connection)

    def test_new_default_is_calibrated_without_unbounding_steps_or_time(self):
        item,run=self.make()
        self.assertEqual(item['budget'],DEFAULT_AUTOMATION_BUDGET)
        stored=self.db.get_task_run('alice',run['task_run_id'])['budget']
        self.assertEqual(stored['max_tokens'],180000);self.assertEqual(stored['max_failures'],5)
        self.assertEqual(stored['max_steps'],20);self.assertEqual(stored['max_seconds'],1800)

    def test_explicit_custom_limits_survive_creation_and_unrelated_edit(self):
        custom={**LEGACY_UI_AUTOMATION_BUDGET,'max_tokens':12000,'max_steps':7,'max_failures':1}
        item,run=self.make(budget=custom)
        updated=self.service.update(item['id'],{'name':'Renamed'})
        self.assertEqual(updated['budget'],custom)
        self.assertEqual(self.db.get_task_run('alice',run['task_run_id'])['budget'],custom)

    def test_targeted_repair_excludes_custom_nonresearch_paused_and_successful_jobs(self):
        eligible,run=self.make(budget=LEGACY_UI_AUTOMATION_BUDGET)
        self.make(budget={**LEGACY_UI_AUTOMATION_BUDGET,'max_tokens':12000})
        self.make(prompt='Создай локальный черновик',budget=LEGACY_UI_AUTOMATION_BUDGET)
        paused,_=self.make(budget=LEGACY_UI_AUTOMATION_BUDGET);self.service.pause(paused['id'])
        self.make(budget=LEGACY_UI_AUTOMATION_BUDGET,state='completed')
        plan=self.plan();self.assertEqual([item['automation_id'] for item in plan],[eligible['id']])
        result=self.service.apply_stock_budget_plan(plan)
        self.assertEqual(result['applied'],[eligible['id']])
        self.assertEqual(self.db.get_automation('alice',eligible['id'])['budget'],DEFAULT_AUTOMATION_BUDGET)
        self.assertEqual(self.db.get_task_run('alice',run['task_run_id'])['budget'],LEGACY_UI_AUTOMATION_BUDGET)

    def test_user_edit_after_plan_is_preserved(self):
        item,_=self.make(budget=LEGACY_UI_AUTOMATION_BUDGET);plan=self.plan()
        custom={**LEGACY_UI_AUTOMATION_BUDGET,'max_tokens':13000}
        self.service.update(item['id'],{'budget':custom})
        self.assertEqual(self.service.apply_stock_budget_plan(plan)['applied'],[])
        self.assertEqual(self.db.get_automation('alice',item['id'])['budget'],custom)


if __name__=='__main__':unittest.main()

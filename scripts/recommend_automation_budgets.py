"""Read-only recommendations by default; explicit --apply backs up first."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_agent.automations import AutomationService,plan_stock_research_budgets
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=None)
    parser.add_argument('--plan-file',type=Path,default=None)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();source=(args.database or Settings.load(ROOT).database_file).resolve()
    folder=ROOT/'.local/automation-budget-repair';folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        plan=json.loads(args.plan_file.read_text(encoding='utf-8')) if args.apply and args.plan_file else plan_stock_research_budgets(connection)
        destination=folder/f'plan-private-{stamp}.json'
        destination.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        if not args.apply:
            print(json.dumps({'mode':'read-only','eligible':len(plan),'plan_file':str(destination)}));return
        backup=folder/f'before-budget-repair-{stamp}.sqlite3'
        with sqlite3.connect(backup) as target:connection.backup(target)
    database=AvelinDatabase(source);applied=[];skipped=[]
    for user in {item['user_id'] for item in plan}:
        result=AutomationService(database,user).apply_stock_budget_plan([item for item in plan if item['user_id']==user])
        applied.extend(result['applied']);skipped.extend(result['skipped'])
    print(json.dumps({'mode':'applied','applied':len(applied),'skipped':len(skipped),'backup':str(backup),'plan_file':str(destination)}))


if __name__=='__main__':main()

"""Plan legacy auto-title repair; explicit --apply backs up before writing.

Only exact matches to the historical automatic formula and placeholders are
eligible. Manual-title markers and later renames are preserved.
"""
from datetime import datetime
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from ai_agent.chat_titles import ChatTitleService, plan_legacy_titles


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=None)
    parser.add_argument('--plan-file',type=Path,default=None)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    source=(args.database or Settings.load(ROOT).database_file).resolve()
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    folder=ROOT/'.local/followup-2026-11-04'
    folder.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as read:
        read.row_factory=sqlite3.Row
        plan=(json.loads(args.plan_file.read_text(encoding='utf-8'))
            if args.apply and args.plan_file else plan_legacy_titles(read))
        destination=args.plan_file if args.plan_file and not args.apply else folder/f'title-plan-private-{stamp}.json'
        destination.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        if not args.apply:
            print(json.dumps({'mode':'read-only','eligible':len(plan),'plan_file':str(destination)}))
            return
        backup=folder/f'before-title-repair-{stamp}.sqlite3'
        with sqlite3.connect(backup) as target:read.backup(target)
    result=ChatTitleService(AvelinDatabase(source)).apply_legacy_plan(plan)
    print(json.dumps({'mode':'applied','applied':len(result['applied']),'skipped':len(result['skipped']),
        'backup':str(backup),'plan_file':str(destination)}))


if __name__=='__main__':main()

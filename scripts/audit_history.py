"""Read-only historical QA inventory. Private evidence stays under .local."""
from pathlib import Path
from collections import Counter
import json
import sqlite3
import sys
import argparse
from zipfile import ZipFile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.local' / 'audit'

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-doc', type=Path, help='Optional local DOCX test plan to extract; remains private under .local.')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f'file:{ROOT / "data/avelin.sqlite3"}?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    backup = sqlite3.connect(OUT / 'history-snapshot.sqlite3')
    source.backup(backup)
    backup.close()
    users = source.execute("select id,username from users where username in ('rickey','iphonetest')").fetchall()
    evidence = []
    summary = []
    for user in users:
        threads = source.execute('select id,title from chat_threads where user_id=? order by updated_at', (user['id'],)).fetchall()
        all_messages = []
        for thread in threads:
            messages = [dict(row) for row in source.execute('select id,role,name,content,status,error,provider,model,created_at from messages where thread_id=? order by created_at,rowid', (thread['id'],))]
            evidence.append({'account':user['username'],'thread':dict(thread),'messages':messages})
            all_messages.extend(messages)
        actions = [dict(row) for row in source.execute('select tool_name,status,result,created_at from action_logs where user_id=? order by created_at', (user['id'],))]
        summary.append({'account':user['username'],'threads':len(threads),'messages':len(all_messages),'message_statuses':dict(Counter(m['status'] for m in all_messages)), 'models':dict(Counter(m['model'] for m in all_messages if m['role']=='assistant')), 'action_statuses':dict(Counter(a['status'] for a in actions)), 'action_errors':[a for a in actions if a['status'] not in ('completed','success')], 'assistant_errors':[{'id':m['id'],'error':m['error'],'content':m['content'][:600]} for m in all_messages if m['status']=='failed']})
    (OUT/'historical-conversations-private.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'history-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.source_doc:
        with ZipFile(args.source_doc) as z:
            tree=ET.fromstring(z.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            paragraphs=[''.join(p.itertext()) for p in tree.findall('.//w:p',ns)]
        (OUT/'source-qa-plan.txt').write_text('\n'.join(paragraphs),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__': main()

"""Run each suite in its own process so Windows UIA/COM state is isolated."""
from pathlib import Path
from datetime import datetime,timezone
import json
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.local/audit/suites'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    results=[]
    for path in sorted((ROOT/'tests').glob('test_*.py')):
        module='tests.'+path.stem
        try:
            run=subprocess.run([sys.executable,'-X','utf8','-m','unittest',module,'-v'],cwd=ROOT,capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            log=(run.stdout+run.stderr).decode('utf8',errors='replace')
            count=re.search(r'Ran (\d+) tests?',log)
            row={'module':module,'status':'PASS' if run.returncode==0 else 'FAIL','tests':int(count.group(1)) if count else 0,'exit_code':run.returncode}
        except subprocess.TimeoutExpired:
            log='Suite timed out after 180 seconds.'
            row={'module':module,'status':'FAIL','tests':0,'exit_code':'timeout'}
        (OUT/(path.stem+'.log')).write_text(log,encoding='utf8')
        results.append(row)
        (OUT/'results.json').write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat(),'results':results},indent=2),encoding='utf8')
        print(json.dumps(row),flush=True)
    return int(any(r['status']!='PASS' for r in results))

if __name__=='__main__':raise SystemExit(main())

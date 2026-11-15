"""Isolated Linux/systemd acceptance fixture. Never uses production data/APIs.

Run 'service' twice in the same temporary systemd unit with KillMode=control-group.
The first invocation starts a detached child; the second verifies containment and
closes the orphan without replay. 'inspect' must report blocked while it is alive.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_agent.database import AvelinDatabase, DEFAULT_USER_ID
from ai_agent.tasks import TaskManager
from ai_agent.task_recovery import TaskRecovery, RecoveryConflict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',choices=['service','inspect'])
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    db = AvelinDatabase(output/'fixture.sqlite3')
    store = TaskRecovery(db,DEFAULT_USER_ID)
    ready = output/'ready.json'
    if args.mode=='inspect':
        run_id = json.loads(ready.read_text())['run_id']
        status = store.status(run_id)
        assert status['needs_attention'] and not status['can_recover'],status
        print('LIVE_EXECUTION_RECOVERY=blocked',flush=True)
        return
    if not ready.exists():
        manager = TaskManager(db,DEFAULT_USER_ID)
        task = manager.create_task('Isolated service crash fixture',steps=['Wait for stop'])
        run = manager.acquire_durable_run('crash-fixture-worker')
        manager.ensure_task_running(task.id)
        db.create_task_checkpoint(DEFAULT_USER_ID,run_id=run['id'],task_id=task.id,step_id=task.steps[0].id,
            plan_version=1,status='started',risk='high',tool_name='fixture_process',arguments_digest='fixture',
            deduplication_key='fixture-effect',tool_call={'name':'fixture_process'},observation='Unknown synthetic effect',evidence={'verified':False})
        child = subprocess.Popen([sys.executable,'-c',
            'import time; from pathlib import Path; time.sleep(5); Path("late-effect.txt").write_text("BAD")'],
            cwd=output,start_new_session=True)
        db.request_task_stop(DEFAULT_USER_ID,run['id'],'cancelled')
        with db.connect() as connection:connection.execute("UPDATE task_runs SET lease_expires_at='2000-01-01' WHERE id=?",(run['id'],))
        ready.write_text(json.dumps({'run_id':run['id'],'child_pid':child.pid}))
        time.sleep(60)
        raise AssertionError('Fixture service was not stopped')
    run_id = json.loads(ready.read_text())['run_id']
    status = store.status(run_id)
    assert status['can_recover'],status
    result = store.close(run_id,status['token'],'Проверен тестовый service cgroup после рестарта; неизвестное действие не повторяем.',True)
    assert result['state']=='cancelled' and not result['worker_id']
    try:store.close(run_id,status['token'],'Повторная попытка закрытия.',True)
    except RecoveryConflict:pass
    else:raise AssertionError('Recovery must not be repeatable')
    checkpoints = db.list_task_checkpoints(DEFAULT_USER_ID,run_id)
    assert checkpoints[0]['status']=='interrupted' and checkpoints[0]['evidence']['outcome_unknown']
    time.sleep(6)
    assert not (output/'late-effect.txt').exists(),'Detached child performed a late write'
    report = {'status':'PASS','worker_and_descendants_stopped':True,'unknown_effect_preserved':True,
              'no_replay':True,'late_effect':False,'model_api_calls':0,'production_data_used':False}
    (output/'result.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()

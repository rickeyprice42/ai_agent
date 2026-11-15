"""Explicitly close orphaned durable runs after proving execution has stopped."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from ai_agent.worker_identity import worker_quiescence


SCHEMA = """CREATE TABLE IF NOT EXISTS task_run_recoveries (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    worker_id TEXT,
    evidence_json TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);"""


class RecoveryConflict(ValueError):
    pass


class TaskRecovery:
    def __init__(self, database, user_id):
        self.database, self.user_id = database, user_id
        with database.connect() as connection:connection.executescript(SCHEMA)

    def _snapshot(self, connection, run_id):
        row = connection.execute('SELECT * FROM task_runs WHERE id=? AND user_id=?', (run_id,self.user_id)).fetchone()
        if row is None:raise LookupError('Запуск не найден.')
        checkpoints = connection.execute("SELECT id,tool_name,status,arguments_digest,updated_at FROM task_checkpoints WHERE run_id=? AND status IN ('started','cancelled','interrupted') ORDER BY sequence", (run_id,)).fetchall()
        snapshot = {key:row[key] for key in ('id','state','worker_id','worker_identity_json','lease_expires_at','control_requested','plan_version','updated_at')}
        snapshot['checkpoints'] = [dict(cp) for cp in checkpoints]
        token = sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return row, checkpoints, token

    @staticmethod
    def _status(row, checkpoints, token):
        expired = False
        if row['lease_expires_at']:
            try:
                expires = datetime.fromisoformat(row['lease_expires_at'].replace('Z','+00:00'))
                if expires.tzinfo is None:expires = expires.replace(tzinfo=timezone.utc)
                expired = expires <= datetime.now(timezone.utc)
            except ValueError:pass
        attention = row['state']=='running' and expired
        eligible = attention and worker_quiescence(row['worker_identity_json'])
        if eligible:reason = 'Исполнение остановлено. Можно закрыть прерванный запуск без повторения действий.'
        elif attention:reason = 'Остановка исполнителя и всех его действий пока не подтверждена. Закрытие запуска заблокировано.'
        else:reason = ''
        return {'needs_attention':attention,'can_recover':eligible,'token':token,'reason':reason,
                'uncertain_actions':[{'id':cp['id'],'tool':cp['tool_name']} for cp in checkpoints]}

    def status(self, run_id):
        with self.database.connect() as connection:
            row, checkpoints, token = self._snapshot(connection,run_id)
        return self._status(row,checkpoints,token)

    def close(self, run_id, token, note, acknowledge_unknown_effects):
        if acknowledge_unknown_effects is not True:
            raise ValueError('Подтвердите, что уже выполненные действия не будут отменены или повторены.')
        note = note.strip()
        if not 10 <= len(note) <= 2000:raise ValueError('Опишите проверку результата: от 10 до 2000 символов.')
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row, checkpoints, current_token = self._snapshot(connection,run_id)
            if token != current_token:raise RecoveryConflict('Состояние запуска изменилось. Обновите данные и проверьте их снова.')
            status = self._status(row,checkpoints,current_token)
            if not status['can_recover']:raise RecoveryConflict(status['reason'] or 'Запуск не требует восстановления.')
            reason = 'Прерванный запуск закрыт владельцем после подтверждённой остановки исполнителя. Неизвестные действия не повторялись; выполненные действия не откатываются.'
            for cp in checkpoints:
                previous = connection.execute('SELECT evidence_json FROM task_checkpoints WHERE id=?',(cp['id'],)).fetchone()
                evidence = json.loads(previous['evidence_json'] or '{}')
                evidence.update(verified=False, outcome_unknown=True, recovery='owner_closed_without_replay')
                connection.execute("UPDATE task_checkpoints SET status='interrupted',evidence_json=?,next_action='review_result',updated_at=CURRENT_TIMESTAMP WHERE id=?",(json.dumps(evidence,ensure_ascii=False),cp['id']))
            connection.execute("UPDATE task_runs SET state='cancelled',control_requested=NULL,worker_id=NULL,lease_expires_at=NULL,failure_reason=?,finished_at=COALESCE(finished_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",(reason,run_id,self.user_id))
            connection.execute("UPDATE task_steps SET status='failed',result=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND status IN ('running','blocked')",(reason,row['task_id']))
            connection.execute("UPDATE tasks SET status='cancelled',result=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",(reason,row['task_id'],self.user_id))
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_llm_runs'").fetchone():
                link = connection.execute('SELECT approvals_json FROM automation_llm_runs WHERE run_id=? AND user_id=?',(run_id,self.user_id)).fetchone()
                if link:
                    for approval_id in json.loads(link['approvals_json'] or '[]'):
                        connection.execute("UPDATE approval_requests SET status='revoked',decided_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND status='pending'",(approval_id,self.user_id))
                    connection.execute("UPDATE automation_llm_runs SET outcome='interrupted',updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND user_id=?",(run_id,self.user_id))
            connection.execute('INSERT INTO task_run_recoveries(id,run_id,user_id,worker_id,evidence_json,note) VALUES(?,?,?,?,?,?)',
                (str(uuid4()),run_id,self.user_id,row['worker_id'],json.dumps({'worker_identity':json.loads(row['worker_identity_json']), 'execution_domain_stopped':True,'unknown_checkpoints':[cp['id'] for cp in checkpoints]},ensure_ascii=False),note))
        return self.database.get_task_run(self.user_id,run_id)

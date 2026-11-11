"""Server-owned chat producers with durable, replayable subscriber events.

HTTP subscribers own only a cursor. Closing one never closes the Agent stream.
An expired producer is marked interrupted, never replayed after a restart.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from threading import Event, Lock, Thread, local
from uuid import uuid4

from backend.service import ChatBusyError
from ai_agent.chat_output import split_message_artifacts

ACTIVE = {"queued", "running"}
TERMINAL = {"completed", "waiting_approval", "waiting_clarification", "failed", "partial", "cancelled", "interrupted"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_generation_runs(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),thread_id TEXT NOT NULL REFERENCES chat_threads(id),
 request_id TEXT NOT NULL,payload_digest TEXT NOT NULL,kind TEXT NOT NULL,input_json TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued',outcome TEXT,text TEXT NOT NULL DEFAULT '',latest_seq INTEGER NOT NULL DEFAULT 0,
 message_id TEXT,user_message_id TEXT,baseline_rowid INTEGER NOT NULL DEFAULT 0,
 artifacts_json TEXT NOT NULL DEFAULT '[]',approvals_json TEXT NOT NULL DEFAULT '[]',error TEXT,
 cancel_requested INTEGER NOT NULL DEFAULT 0,completion_claimed INTEGER NOT NULL DEFAULT 0,
 owner_id TEXT NOT NULL,lease_expires_at TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(user_id,request_id));
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_chat_producer ON chat_generation_runs(thread_id) WHERE state IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_chat_generation_thread ON chat_generation_runs(user_id,thread_id,created_at);
CREATE TABLE IF NOT EXISTS chat_generation_events(
 run_id TEXT NOT NULL REFERENCES chat_generation_runs(id) ON DELETE CASCADE,seq INTEGER NOT NULL,
 payload_json TEXT NOT NULL,PRIMARY KEY(run_id,seq));
"""
_FACTORY_LOCK = Lock()


def _now():
    return datetime.now(timezone.utc)


class ChatRunService:
    def __init__(self, service):
        self.service, self.database = service, service.database
        self.owner_id = uuid4().hex
        self._stop = Event()
        self._workers = {}
        self._lock = Lock()
        self._context = getattr(service,'_chat_run_context',None) or local()
        service._chat_run_context = self._context
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)
        self.recover_expired()
        self._heartbeat = Thread(target=self._heartbeat_loop, name='avelin-chat-heartbeat', daemon=True)
        self._heartbeat.start()

    @staticmethod
    def _request_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', value):
            raise ValueError('Некорректный идентификатор запроса.')

    def _public(self, row):
        from ai_agent.chat_approvals import PendingChatActions
        from ai_agent.chat_questions import PendingQuestions
        data = dict(row)
        payload = json.loads(data['input_json'])
        actions=PendingChatActions(self.database)
        pending=actions.list(data['user_id'],data['thread_id'])
        history=actions.history(data['user_id'],data['thread_id'])
        artifacts, products = split_message_artifacts(json.loads(data['artifacts_json']))
        return {key: data[key] for key in ('request_id','thread_id','kind','state','outcome','text','latest_seq',
            'message_id','user_message_id','error','created_at','updated_at')} | {
            'run_id':data['id'], 'cancel_requested':bool(data['cancel_requested']),
            'user_text':payload.get('message',''), 'attachments':payload.get('attachments',[]),
            'artifacts':artifacts, 'products':products, 'approvals':pending,'approval_history':history,
            'questions':PendingQuestions(self.database).list(data['user_id'],data['thread_id'])}

    def _accessible(self, user_id, row):
        if row is None or row['user_id'] != user_id:
            raise ValueError('Запрос не найден.')
        thread = self.database.get_chat_thread(user_id, row['thread_id'])
        if thread is None or thread.get('deleted_at'):
            raise ValueError('Чат не найден или удалён.')
        return row

    def get(self, user_id, run_id):
        self.recover_expired()
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM chat_generation_runs WHERE id=? AND user_id=?',(run_id,user_id)).fetchone()
        return self._public(self._accessible(user_id,row))

    def by_request(self, user_id, request_id):
        with self.database.connect() as connection:
            row = connection.execute('SELECT id FROM chat_generation_runs WHERE user_id=? AND request_id=?',(user_id,request_id)).fetchone()
        return self.get(user_id,row['id']) if row else None

    def latest(self, user_id, thread_id):
        thread = self.database.get_chat_thread(user_id,thread_id)
        if thread is None or thread.get('deleted_at'):
            raise ValueError('Чат не найден или удалён.')
        self.recover_expired()
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM chat_generation_runs WHERE user_id=? AND thread_id=? ORDER BY rowid DESC LIMIT 1',(user_id,thread_id)).fetchone()
        return self._public(row) if row else None

    def start(self,user_id,request_id,message,thread_id=None,attachments=None):
        return self._start(user_id,request_id,{'message':message,'thread_id':thread_id,'attachments':attachments or []},'chat')

    def approve(self,user_id,request_id,thread_id,approval_id,decision):
        return self._start(user_id,request_id,{'thread_id':thread_id,'approval_id':approval_id,'decision':decision},'approval')

    def question(self,user_id,request_id,thread_id,question_id,option_id=None,text=None):
        from ai_agent.chat_questions import PendingQuestions
        self.service.resolve_thread(user_id,thread_id)
        answer=PendingQuestions(self.database).resolve_answer(user_id,thread_id,question_id,option_id,text)
        return self._start(user_id,request_id,{'thread_id':thread_id,'question_id':question_id,'option_id':option_id,'text':text,'message':answer},'question')

    def _start(self,user_id,request_id,payload,kind):
        self._request_id(request_id)
        digest = sha256(json.dumps({'kind':kind,**payload},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.database.connect() as connection:
            existing = connection.execute('SELECT * FROM chat_generation_runs WHERE user_id=? AND request_id=?',(user_id,request_id)).fetchone()
        if existing:
            if existing['payload_digest'] != digest:
                raise ChatBusyError('Этот идентификатор уже использован для другого запроса.')
            return self.get(user_id,existing['id'])
        if self._stop.is_set():
            raise ChatBusyError('Сервер завершает работу. Повтори запрос после подключения.')
        thread = self.service.resolve_thread(user_id,payload.get('thread_id'))
        self.recover_expired()
        stamp = _now()
        run_id = uuid4().hex
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('SELECT * FROM chat_generation_runs WHERE user_id=? AND request_id=?',(user_id,request_id)).fetchone()
            if existing:
                if existing['payload_digest'] != digest:
                    raise ChatBusyError('Этот идентификатор уже использован для другого запроса.')
                connection.commit()
                return self.get(user_id,existing['id'])
            if connection.execute("SELECT 1 FROM chat_generation_runs WHERE thread_id=? AND state IN ('queued','running')",(thread['id'],)).fetchone():
                raise ChatBusyError('В этом чате уже выполняется запрос. Дождись ответа или останови его.')
            if connection.execute("SELECT COUNT(*) FROM chat_generation_runs WHERE state IN ('queued','running')").fetchone()[0] >= 8:
                raise ChatBusyError('Сервер уже выполняет несколько запросов. Повтори позже.')
            baseline = connection.execute('SELECT COALESCE(MAX(rowid),0) FROM messages WHERE thread_id=?',(thread['id'],)).fetchone()[0]
            connection.execute('''INSERT INTO chat_generation_runs(id,user_id,thread_id,request_id,payload_digest,kind,input_json,
                baseline_rowid,owner_id,lease_expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (run_id,user_id,thread['id'],request_id,digest,kind,json.dumps(payload,ensure_ascii=False),baseline,
                 self.owner_id,(stamp+timedelta(seconds=30)).isoformat(),stamp.isoformat(),stamp.isoformat()))
        try:
            previous = getattr(self._context,'run_id',None)
            self._context.run_id = run_id
            if kind == 'chat':
                message = self.service.message_with_attachments(user_id,payload['message'],payload['attachments'])
                _,events = self.service.chat_stream(message,user_id,thread['id'],attachment_paths=payload['attachments'],display_message=payload['message'])
            elif kind=='approval':
                _,events = self.service.decide_chat_approval(user_id,thread['id'],payload['approval_id'],payload['decision'])
            else:
                message=payload['message']
                _,events=self.service.answer_chat_question(user_id,thread['id'],payload['question_id'],request_id,payload.get('option_id'),payload.get('text'))
            agent = self.service.agent_for_user(user_id,thread['id'])
            accepted_user = {'content':message,'artifacts':list(getattr(agent,'incoming_attachments',[])),
                'name':agent._shared_author_name()} if kind in {'chat','question'} else None
        except BaseException:
            self._finish(run_id,'failed','failed','Не удалось начать запрос.',None)
            raise
        finally:
            self._context.run_id = previous
        worker = Thread(target=self._produce,args=(run_id,user_id,events,agent,payload,kind,accepted_user),name='avelin-chat-'+run_id[:8],daemon=True)
        with self._lock:
            self._workers[run_id] = worker
        worker.start()
        return self.get(user_id,run_id)

    def _message_snapshot(self, connection, row):
        messages = connection.execute('SELECT id,role,content,status,error,artifacts_json FROM messages WHERE thread_id=? AND rowid>? ORDER BY rowid DESC',
            (row['thread_id'],row['baseline_rowid'])).fetchall()
        assistant = next((item for item in messages if item['role']=='assistant'),None)
        user = next((item for item in messages if item['role']=='user'),None)
        return assistant,user

    def _event(self, run_id, event):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM chat_generation_runs WHERE id=? AND owner_id=?',(run_id,self.owner_id)).fetchone()
            if row is None or row['state'] not in ACTIVE:
                return False
            seq = row['latest_seq']+1
            if seq > 20000:
                raise ValueError('Слишком большой поток событий.')
            text = row['text'] + str(event.get('text') or '') if event.get('type')=='delta' else row['text']
            assistant,user = self._message_snapshot(connection,row)
            if assistant:
                text = assistant['content']
            if len(text)>2000000:
                raise ValueError('Слишком большой ответ.')
            approvals = json.loads(row['approvals_json'])
            if event.get('type')=='approval' and event.get('approval'):
                approvals.append(event['approval'])
            connection.execute('INSERT INTO chat_generation_events(run_id,seq,payload_json) VALUES(?,?,?)',
                (run_id,seq,json.dumps({**event,'seq':seq,'run_id':run_id,'thread_id':row['thread_id']},ensure_ascii=False)))
            connection.execute('''UPDATE chat_generation_runs SET latest_seq=?,text=?,message_id=?,user_message_id=?,artifacts_json=?,approvals_json=?,updated_at=? WHERE id=?''',
                (seq,text,assistant['id'] if assistant else row['message_id'],user['id'] if user else row['user_message_id'],
                 assistant['artifacts_json'] if assistant else row['artifacts_json'],json.dumps(approvals,ensure_ascii=False),_now().isoformat(),run_id))
        return True

    def _produce(self,run_id,user_id,events,agent,payload,kind,accepted_user=None):
        self._context.run_id = run_id
        done = None
        provider_error = None
        state,outcome,error = 'interrupted','interrupted','Ответ прерван. Неизвестные действия не повторяются.'
        with self.database.connect() as connection:
            connection.execute("UPDATE chat_generation_runs SET state='running' WHERE id=? AND state='queued' AND owner_id=?",(run_id,self.owner_id))
        try:
            while True:
                with self.database.connect() as connection:
                    row = connection.execute('SELECT state,cancel_requested FROM chat_generation_runs WHERE id=? AND owner_id=?',(run_id,self.owner_id)).fetchone()
                if not row or row['state'] not in ACTIVE or self._stop.is_set():
                    break
                if row['cancel_requested']:
                    state,outcome,error='cancelled','cancelled','Генерация остановлена пользователем.'
                    break
                try:
                    event = next(events)
                except StopIteration:
                    break
                with self.database.connect() as connection:
                    current = connection.execute('SELECT cancel_requested FROM chat_generation_runs WHERE id=?',(run_id,)).fetchone()
                if current and current['cancel_requested']:
                    state,outcome,error='cancelled','cancelled','Генерация остановлена пользователем.'
                    break
                if event.get('type')=='done':
                    done=event
                    break
                if event.get('type') == 'error':
                    provider_error = str(event.get('text') or 'Провайдер не завершил ответ.')[:800]
                if not self._event(run_id,event):break
            if done:
                outcome = done.get('outcome')
                if kind=='approval' and payload.get('decision')=='denied':outcome='cancelled'
                if not outcome:
                    outcome = 'failed' if provider_error else 'not_invoked' if done.get('text')=='' else 'failed'
                state = outcome if outcome in TERMINAL else 'completed' if outcome=='not_invoked' else 'failed'
                if state in {'completed', 'waiting_approval', 'waiting_clarification'}:
                    error = None
                elif state == 'cancelled':
                    error = 'Генерация остановлена пользователем.'
                else:
                    error = str(done.get('error') or provider_error or 'Ответ не завершён полностью. Частичный результат сохранён.')[:800]
            elif provider_error and state != 'cancelled':
                state, outcome, error = 'failed', 'failed', provider_error
        except Exception:
            state,outcome,error='failed','failed','Не удалось завершить ответ. Частичный результат сохранён.'
        finally:
            try:
                close=getattr(events,'close',None)
                if close:close()
            finally:
                try:self._finish(run_id,state,outcome,error,agent,done,accepted_user)
                finally:
                    with self._lock:self._workers.pop(run_id,None)
                    self._context.run_id = None

    def _finish(self,run_id,state,outcome,error,agent,done=None,accepted_user=None):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM chat_generation_runs WHERE id=? AND owner_id=?',(run_id,self.owner_id)).fetchone()
            if row is None:return
            if row['state'] not in ACTIVE:
                if row['state']=='interrupted' and row['message_id']:
                    connection.execute("UPDATE messages SET status='failed',error=? WHERE id=? AND thread_id=? AND status IN ('pending','cancelled','complete')",
                        (row['error'],row['message_id'],row['thread_id']))
                return
            if row['cancel_requested'] and not row['completion_claimed']:
                state,outcome,error='cancelled','cancelled','Генерация остановлена пользователем.'
            assistant,user = self._message_snapshot(connection,row)
            if state=='cancelled' and row['kind'] in {'chat','question'} and user is None and accepted_user is not None:
                # Agent.respond_stream is lazy. An accepted request cancelled
                # before its first next() still belongs in history; persist it
                # atomically with terminal state, without advancing the Agent.
                user={'id':str(uuid4())}
                connection.execute('''INSERT INTO messages(id,thread_id,role,content,name,status,artifacts_json,author_user_id)
                    VALUES(?,?,'user',?,?,'complete',?,?)''',
                    (user['id'],row['thread_id'],accepted_user['content'],accepted_user.get('name'),
                     json.dumps(accepted_user.get('artifacts',[]),ensure_ascii=False),row['user_id']))
                connection.execute('UPDATE chat_threads SET updated_at=CURRENT_TIMESTAMP WHERE id=?',(row['thread_id'],))
            text = assistant['content'] if assistant else row['text']
            artifacts = assistant['artifacts_json'] if assistant else row['artifacts_json']
            if assistant and state in {'interrupted','cancelled','failed'} and assistant['status'] in {'pending','cancelled'}:
                connection.execute('UPDATE messages SET status=?,error=? WHERE id=? AND thread_id=?',
                    ('cancelled' if state=='cancelled' else 'failed',error,assistant['id'],row['thread_id']))
            seq = row['latest_seq']+1
            event = {**(done or {}),'type':'done','state':state,'outcome':outcome,'seq':seq,'run_id':run_id,'thread_id':row['thread_id']}
            connection.execute('INSERT INTO chat_generation_events(run_id,seq,payload_json) VALUES(?,?,?)',(run_id,seq,json.dumps(event,ensure_ascii=False)))
            connection.execute('''UPDATE chat_generation_runs SET state=?,outcome=?,error=?,text=?,message_id=?,user_message_id=?,artifacts_json=?,latest_seq=?,updated_at=?,finished_at=? WHERE id=?''',
                (state,outcome,error,text,assistant['id'] if assistant else row['message_id'],user['id'] if user else row['user_message_id'],
                 artifacts,seq,_now().isoformat(),_now().isoformat(),run_id))

    def claim_completion(self,user_id,thread_id):
        run_id = getattr(self._context,'run_id',None)
        if not run_id:return True
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            changed = connection.execute("UPDATE chat_generation_runs SET completion_claimed=1 WHERE id=? AND user_id=? AND thread_id=? AND owner_id=? AND state IN ('queued','running') AND cancel_requested=0",
                (run_id,user_id,thread_id,self.owner_id)).rowcount
        return changed==1

    def assert_available(self,user_id,thread_id):
        with self.database.connect() as connection:
            row=connection.execute("SELECT id FROM chat_generation_runs WHERE thread_id=? AND state IN ('queued','running')",(thread_id,)).fetchone()
        if row and row['id']!=getattr(self._context,'run_id',None):
            raise ChatBusyError('В этом чате уже выполняется запрос. Дождись ответа или останови его.')

    def cancel(self,user_id,run_id):
        self.get(user_id,run_id)
        with self.database.connect() as connection:
            connection.execute("UPDATE chat_generation_runs SET cancel_requested=1,updated_at=? WHERE id=? AND user_id=? AND state IN ('queued','running') AND completion_claimed=0",(_now().isoformat(),run_id,user_id))
        return self.get(user_id,run_id)

    def events_after(self,user_id,run_id,after=0):
        self.get(user_id,run_id)
        with self.database.connect() as connection:
            connection.execute('BEGIN')
            row=connection.execute('SELECT * FROM chat_generation_runs WHERE id=? AND user_id=?',(run_id,user_id)).fetchone()
            events=connection.execute('SELECT payload_json FROM chat_generation_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 200',(run_id,max(0,int(after)))).fetchall()
        return self._public(row),[json.loads(event['payload_json']) for event in events]

    def recover_expired(self):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows=connection.execute("SELECT * FROM chat_generation_runs WHERE state IN ('queued','running') AND lease_expires_at<=?",(_now().isoformat(),)).fetchall()
            for row in rows:
                error='Сервер прервал ответ. Неизвестные действия не повторяются; отправьте новое сообщение для продолжения.'
                seq=row['latest_seq']+1
                event={'type':'done','state':'interrupted','outcome':'interrupted','seq':seq,'run_id':row['id'],'thread_id':row['thread_id']}
                connection.execute('INSERT INTO chat_generation_events(run_id,seq,payload_json) VALUES(?,?,?)',(row['id'],seq,json.dumps(event)))
                connection.execute("UPDATE chat_generation_runs SET state='interrupted',outcome='interrupted',error=?,latest_seq=?,finished_at=?,updated_at=? WHERE id=?",(error,seq,_now().isoformat(),_now().isoformat(),row['id']))
                if row['message_id']:
                    connection.execute("UPDATE messages SET status='failed',error=? WHERE id=? AND thread_id=? AND status='pending'",(error,row['message_id'],row['thread_id']))

    def _heartbeat_loop(self):
        while not self._stop.wait(5):
            try:
                with self._lock:
                    alive=[key for key,worker in self._workers.items() if worker.is_alive()]
                if alive:
                    placeholders=','.join('?' for _ in alive)
                    with self.database.connect() as connection:
                        connection.execute(f"UPDATE chat_generation_runs SET lease_expires_at=? WHERE owner_id=? AND state IN ('queued','running') AND id IN ({placeholders})",((_now()+timedelta(seconds=30)).isoformat(),self.owner_id,*alive))
                self.recover_expired()
            except Exception:pass

    def close(self):
        self._stop.set()
        self._heartbeat.join(timeout=1)
        with self.database.connect() as connection:
            connection.execute("UPDATE chat_generation_runs SET lease_expires_at=? WHERE owner_id=? AND state IN ('queued','running')",(_now().isoformat(),self.owner_id))
        self.recover_expired()


def chat_runs_for(service):
    with _FACTORY_LOCK:
        existing=getattr(service,'_chat_run_service',None)
        if existing is None or existing._stop.is_set():
            existing=ChatRunService(service)
            service._chat_run_service=existing
    return existing

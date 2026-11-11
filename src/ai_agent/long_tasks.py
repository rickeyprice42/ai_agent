"""User-approved plans backed by the existing durable Agent worker and checkpoints."""
from datetime import datetime, time, timedelta, timezone
from dataclasses import replace
from pathlib import PurePosixPath, PureWindowsPath
from hashlib import sha256
import json
import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_agent.automation_runner import ALLOWED_TOOLS, LLMAutomationRunner, SCHEMA as RUN_SCHEMA
from ai_agent.memory import user_authored_text
from ai_agent.task_reports import _report_payload
from ai_agent.tasks import TaskManager
from ai_agent.tools.base import Tool, ToolRegistry
from ai_agent.tools.files import FileSandbox
from ai_agent.tools.code_tools import CodeSandbox
from ai_agent.types import Message

DEFAULT_BUDGET = {'max_steps': 80, 'max_failures': 8, 'max_seconds': 7200,
                  'max_tokens': 800000, 'max_cost_micros': 0}
SCHEMA = """CREATE TABLE IF NOT EXISTS long_task_plans(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
 origin_thread_id TEXT NOT NULL REFERENCES chat_threads(id),project_id TEXT,
 title TEXT NOT NULL,goal TEXT NOT NULL,steps_json TEXT NOT NULL,checks_json TEXT NOT NULL,
 request_text TEXT NOT NULL DEFAULT '',source_message_id TEXT,
 budget_json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,digest TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft',timezone TEXT NOT NULL DEFAULT 'UTC',
 task_id TEXT,run_id TEXT,work_thread_id TEXT,approved_at TEXT,report_cursor TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
 CREATE INDEX IF NOT EXISTS idx_long_task_origin ON long_task_plans(user_id,origin_thread_id);"""


class PlanConflict(ValueError):
    pass


class LongTaskPlans:
    def __init__(self, database, user_id):
        self.database, self.user_id = database, user_id
        with database.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(long_task_plans)')}
            if 'request_text' not in columns:connection.execute("ALTER TABLE long_task_plans ADD COLUMN request_text TEXT NOT NULL DEFAULT ''")
            if 'source_message_id' not in columns:connection.execute('ALTER TABLE long_task_plans ADD COLUMN source_message_id TEXT')

    def propose(self, thread_id, *, title, goal, steps, acceptance_checks, budget=None):
        thread = self.database.get_chat_thread(self.user_id, thread_id)
        if thread is None or thread.get('status') != 'active':
            raise ValueError('Активный диалог не найден.')
        if thread.get('project_id'):
            project = self.database.get_project(self.user_id, thread['project_id'])
            if project is None or project.get('shared') or project.get('status') != 'active':
                raise ValueError('Длительные задачи пока доступны только в личных диалогах и проектах.')
        title, goal = str(title).strip(), str(goal).strip()
        steps = _strings(steps, 2, 16, 2000)
        checks = _strings(acceptance_checks, 1, 12, 1000)
        if not 3 <= len(title) <= 120 or not 10 <= len(goal) <= 6000:
            raise ValueError('Нужны название и конкретная цель задачи.')
        limits = _budget(budget or {})
        source = next((row for row in reversed(self.database.list_messages(thread_id)) if row['role']=='user'),None)
        authored = user_authored_text(Message('user',source['content'],artifacts=source.get('artifacts',[]))) if source else ''
        payload = dict(title=title, goal=goal, steps=steps, acceptance_checks=checks, budget=limits,request_text=authored)
        digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('SELECT * FROM long_task_plans WHERE user_id=? AND origin_thread_id=? AND goal=? ORDER BY rowid DESC LIMIT 1', (self.user_id, thread_id, goal)).fetchone()
            if existing and (existing['digest'] == digest or existing['status'] == 'approved'):
                plan_id = existing['id']
            elif existing and existing['status'] == 'draft':
                plan_id = existing['id']
                connection.execute("UPDATE long_task_plans SET title=?,steps_json=?,checks_json=?,budget_json=?,request_text=?,source_message_id=?,digest=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='draft'",
                    (title, json.dumps(steps, ensure_ascii=False), json.dumps(checks, ensure_ascii=False), json.dumps(limits), authored, source['id'] if source else None,digest, plan_id))
            else:
                plan_id = str(uuid4())
                connection.execute('INSERT INTO long_task_plans(id,user_id,origin_thread_id,project_id,title,goal,steps_json,checks_json,budget_json,digest,request_text,source_message_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (plan_id,self.user_id,thread_id,thread.get('project_id'),title,goal,json.dumps(steps,ensure_ascii=False),json.dumps(checks,ensure_ascii=False),json.dumps(limits),digest,authored,source['id'] if source else None))
        return self.get(plan_id)

    def get(self, plan_id):
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM long_task_plans WHERE id=? AND user_id=?', (plan_id,self.user_id)).fetchone()
        if row is None:
            raise ValueError('План не найден.')
        item = dict(row)
        item['steps'] = json.loads(item.pop('steps_json'))
        item['acceptance_checks'] = json.loads(item.pop('checks_json'))
        item['budget'] = json.loads(item.pop('budget_json'))
        item['run'] = self.database.get_task_run(self.user_id,item['run_id']) if item['run_id'] else None
        item['task'] = self.database.get_task(item['task_id']) if item['task_id'] else None
        if item['run_id']:
            with self.database.connect() as connection:
                link = connection.execute('SELECT result,artifacts_json FROM automation_llm_runs WHERE run_id=? AND user_id=?', (item['run_id'],self.user_id)).fetchone()
            if link:
                item.update(result=link['result'],artifacts=json.loads(link['artifacts_json']))
        return item

    def for_thread(self, thread_id):
        if self.database.get_chat_thread(self.user_id,thread_id) is None:
            raise ValueError('Диалог не найден.')
        with self.database.connect() as connection:
            rows = connection.execute('SELECT id FROM long_task_plans WHERE user_id=? AND (origin_thread_id=? OR work_thread_id=?) ORDER BY rowid DESC LIMIT 12', (self.user_id,thread_id,thread_id)).fetchall()
        return [self.get(row['id']) for row in rows]

    def decide(self, plan_id, decision, revision, digest, timezone_name='UTC'):
        if decision not in {'approved','rejected'}:
            raise ValueError('Неизвестное решение.')
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError,ValueError):
            raise ValueError('Неизвестный часовой пояс.')
        with self.database.connect() as connection:
            connection.executescript(RUN_SCHEMA)
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM long_task_plans WHERE id=? AND user_id=?', (plan_id,self.user_id)).fetchone()
            if row is None:
                raise ValueError('План не найден.')
            if row['revision'] != revision or row['digest'] != digest:
                raise PlanConflict('План изменился. Просмотрите текущую версию перед подтверждением.')
            if row['status'] == decision:
                pass  # Idempotent tap/retry; never enqueue a second run.
            elif row['status'] != 'draft':
                raise PlanConflict('Решение по этому плану уже принято.')
            elif decision == 'rejected':
                connection.execute("UPDATE long_task_plans SET status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?",(plan_id,))
            else:
                thread = connection.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=? AND status='active'",(row['origin_thread_id'],self.user_id)).fetchone()
                if thread is None:
                    raise PlanConflict('Исходный диалог больше не активен.')
                if row['project_id']:
                    project = connection.execute("SELECT * FROM projects WHERE id=? AND user_id=? AND status='active' AND shared=0",(row['project_id'],self.user_id)).fetchone()
                    if project is None:
                        raise PlanConflict('Личный проект больше не активен.')
                task_id, run_id, work_thread = (str(uuid4()) for _ in range(3))
                connection.execute("INSERT INTO tasks(id,user_id,project_id,description,status,priority) VALUES(?,?,?,?,'planned',3)",(task_id,self.user_id,row['project_id'],row['goal']))
                for index, instruction in enumerate(json.loads(row['steps_json']),1):
                    connection.execute('INSERT INTO task_steps(id,task_id,position,description) VALUES(?,?,?,?)',(str(uuid4()),task_id,index,instruction))
                usage = json.dumps({'steps':0,'failures':0,'tokens':0,'cost_micros':0})
                connection.execute("INSERT INTO task_runs(id,task_id,user_id,state,plan_version,deduplication_key,budget_json,usage_json) VALUES(?,?,?,'queued',?,?,?,?)",(run_id,task_id,self.user_id,revision,'long-task:'+plan_id+':'+digest,row['budget_json'],usage))
                connection.execute('INSERT INTO chat_threads(id,user_id,title,project_id) VALUES(?,?,?,?)',(work_thread,self.user_id,'Работа: '+row['title'][:70],row['project_id']))
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_model_overrides'").fetchone():
                    connection.execute('''INSERT INTO chat_model_overrides(user_id,thread_id,provider,model_name,ollama_url)
                        SELECT user_id,?,provider,model_name,ollama_url FROM chat_model_overrides WHERE user_id=? AND thread_id=?''',
                        (work_thread,self.user_id,row['origin_thread_id']))
                connection.execute("INSERT INTO automation_llm_runs(run_id,user_id,thread_id,outcome) VALUES(?,?,?,'step_completed')",(run_id,self.user_id,work_thread))
                now = datetime.now(timezone.utc)
                connection.execute("UPDATE long_task_plans SET status='approved',timezone=?,task_id=?,run_id=?,work_thread_id=?,approved_at=?,report_cursor=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (timezone_name,task_id,run_id,work_thread,now.isoformat(),now.astimezone(zone).date().isoformat(),plan_id))
        return self.get(plan_id)

    def control(self,plan_id,action):
        plan = self.get(plan_id)
        if not plan['task_id']:
            raise ValueError('План ещё не утверждён.')
        if action not in {'pause','cancel','resume'}:
            raise ValueError('Неизвестное действие.')
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute('SELECT state FROM task_runs WHERE id=? AND user_id=?',(plan['run_id'],self.user_id)).fetchone()
            if not current or current['state'] in {'completed','failed','cancelled'}:
                raise PlanConflict('Запуск уже завершён. Его состояние не изменено.')
            if action in {'pause','cancel'}:
                state = 'paused' if action=='pause' else 'cancelled'
                connection.execute("UPDATE task_runs SET state=?,worker_id=NULL,lease_expires_at=NULL,failure_reason=?,finished_at=CASE WHEN ?='cancelled' THEN CURRENT_TIMESTAMP ELSE finished_at END,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
                    (state,'Остановлено пользователем.',state,plan['run_id'],self.user_id))
                connection.execute('UPDATE tasks SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(state,plan['task_id'],self.user_id))
            else:
                link = connection.execute('SELECT outcome FROM automation_llm_runs WHERE run_id=? AND user_id=?',(plan['run_id'],self.user_id)).fetchone()
                unknown = connection.execute("SELECT 1 FROM task_checkpoints WHERE run_id=? AND status='started'",(plan['run_id'],)).fetchone()
                if current['state']!='paused' or not link:
                    raise PlanConflict('Возобновить можно только приостановленный запуск.')
                waiting = link['outcome']=='waiting_approval'
                if not waiting and (link['outcome'] not in {'paused','step_completed'} or unknown):
                    raise PlanConflict('Исполнитель ещё останавливается или результат действия неизвестен. Откройте диалог работы перед продолжением.')
                if not waiting:
                    connection.execute("UPDATE automation_llm_runs SET outcome='step_completed' WHERE run_id=?",(plan['run_id'],))
                connection.execute("UPDATE task_runs SET state=?,worker_id=NULL,lease_expires_at=NULL,failure_reason=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",('waiting_approval' if waiting else 'queued',plan['run_id']))
                connection.execute("UPDATE tasks SET status=?,result=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",('blocked' if waiting else 'planned',plan['task_id']))
        return self.get(plan_id)


class LongTaskRunner(LLMAutomationRunner):
    allowed_tools = ALLOWED_TOOLS | {'read_file_lines','read_pdf_pages','search_workspace','edit_file_exact','run_code_check'}
    # Reasoning models can take longer than one automation's 30-second window.
    # Keep each request under the worker's 180-second lease window.
    max_provider_timeout = 120

    def run(self,user_id,run_id):
        link = self.get(user_id,run_id=run_id)
        if link and link['outcome']=='step_completed':
            self.database.set_metadata('long-task-context-floor:'+run_id,link.get('last_result_message_id') or '')
        return super().run(user_id,run_id)

    def _agent(self,user_id,link,run):
        agent = super()._agent(user_id,link,run)
        agent.context.history_after_message_id = self.database.get_metadata('long-task-context-floor:'+run['id']) or None
        agent.context.turn_reserve_tokens = min(8192,max(2048,agent.context.context_window_tokens//4))
        if getattr(agent,'_long_task_tools_scoped',False):
            return agent
        prefix = agent.automation_output_prefix
        root = agent.files.workspace_dir.resolve()
        job = root / prefix
        if job.resolve().relative_to(root).as_posix()!=prefix:
            raise ValueError('Папка задачи перенаправлена за пределы утверждённой области.')
        job.mkdir(parents=True,exist_ok=True)
        scoped_files = FileSandbox(job)
        scoped_code = CodeSandbox(job)
        registry = ToolRegistry()
        def guarded(base):
            def invoke(args):
                args = dict(args)
                raw = str(args.get('path','')).replace('\\','/')
                if not raw or PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).drive or '..' in PurePosixPath(raw).parts:
                    raise ValueError('Путь должен находиться в папке утверждённой задачи.')
                # Relative folders such as outputs/tasks/stage-2.md are local
                # to this job; only a different explicit plan UUID is foreign.
                parts = PurePosixPath(raw).parts
                explicit_plan = len(parts)>2 and parts[:2]==('outputs','tasks') and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',parts[2],re.I)
                if explicit_plan and not raw.startswith(prefix+'/'):
                    raise ValueError('Файл относится к другой задаче.')
                path = raw if raw.startswith(prefix+'/') else prefix+'/'+raw
                resolved = (root/path).resolve()
                if not resolved.is_relative_to(job.resolve()):
                    raise ValueError('Файл выходит за пределы папки задачи.')
                args['path'] = path
                return base(args)
            return invoke
        for definition in agent.tools.list_for_model():
            tool = agent.tools.get(definition['name'])
            if tool.name in {'read_file','read_file_lines','read_pdf_pages','edit_file_exact','file_metadata','write_file','create_docx','append_docx','create_presentation'}:
                tool = replace(tool,handler=guarded(tool.handler))
            elif tool.name=='list_workspace_files':
                tool = replace(tool,handler=lambda args:scoped_files.format_file_list(int(args.get('limit',30))))
            elif tool.name=='search_workspace':
                def search(args):
                    return scoped_files.search(args['query'],str(args.get('path','')),include_content=bool(args.get('include_content',True)),limit=int(args.get('limit',50)))
                tool = replace(tool,handler=search)
            elif tool.name=='run_code_check':
                def check(args):
                    target = str(args.get('target','')).replace('\\','/')
                    if target.startswith(prefix+'/'):target=target[len(prefix)+1:]
                    return scoped_code.run_check(args['runner'],target,int(args.get('timeout_seconds',60)))
                tool = replace(tool,handler=check)
            registry.register(tool)
        agent.tools = registry
        agent._long_task_tools_scoped = True
        return agent

    def output_prefix(self,user_id,run):
        with self.database.connect() as connection:
            row = connection.execute("SELECT id FROM long_task_plans WHERE user_id=? AND run_id=? AND status='approved'",(user_id,run['id'])).fetchone()
        if row is None:
            raise ValueError('Утверждённый план не найден.')
        return f"outputs/tasks/{row['id']}"

    def step_prompt(self,task,step):
        with self.database.connect() as connection:
            row = connection.execute('SELECT id,checks_json,request_text FROM long_task_plans WHERE task_id=?',(task.id,)).fetchone()
        checks = json.loads(row['checks_json'])
        completed = '\n'.join(f"{s.position}. {s.description}: {(s.result or 'завершён')[:600]}" for s in task.steps if s.status=='completed')
        return (f"Общая утверждённая цель: {task.description}\nИсходная просьба пользователя (дословно):\n{row['request_text'] or task.description}\n"
            + "Пользователь уже утвердил этот план кнопкой. Условие дождаться утверждения выполнено; доступны только инструменты утверждённой области.\nПроверки результата: " + '; '.join(checks)
            + f"\nЗавершённые этапы (выдержки; полные результаты сохранены в истории):\n{completed or 'Пока нет'}\nТекущий этап {step.position}: {step.description}\n\n"
            + "Выполни только текущий этап целиком, самостоятельно выбирая инструменты. Уточняй у пользователя только действительно недостающие решения или необходимые разрешения. "
            "Фиксируй результат в файлах и проверяй его инструментами; не отмечай выполнение без evidence. "
            f"Рабочая папка: outputs/tasks/{row['id']}/. Все относительные пути из плана считаются внутри неё; не добавляй этот префикс повторно. "
            "Используй фактические пути из результатов инструментов. Для изменения уже созданного файла используй read_file_lines и edit_file_exact с SHA. "
            "Запуск программы требует отдельного согласия. Ничего не публикуй и не отправляй вовне. "
            "Если этап не может быть выполнен, явно сообщи о препятствии. Нельзя выдавать концепт за готовую релизную игру.")

    def _finish(self,user_id,run,agent,outcome,reason,active_seconds=0):
        if outcome == 'completed':
            task = TaskManager(self.database,user_id).get_task(run['task_id'])
            step = TaskManager(self.database,user_id).running_step(task)
            evidence = [cp for cp in self.database.list_task_checkpoints(user_id,run['id'],500)
                if step and cp.get('step_id')==step.id and cp['status']=='completed' and cp.get('evidence',{}).get('verified')]
            if not evidence:
                outcome,reason = 'partial','Этап не подтверждён выполненными инструментами. Нужна проверка в диалоге работы.'
        return super()._finish(user_id,run,agent,outcome,reason,active_seconds)


def process_long_tasks(service,limit=1):
    LongTaskPlans(service.database,'local-user')  # Schema only; no model or task creation.
    with service.database.connect() as connection:
        rows = connection.execute("""SELECT p.user_id,p.run_id FROM long_task_plans p JOIN task_runs r ON r.id=p.run_id
            WHERE p.status='approved' AND (r.state='queued' OR (r.state='running' AND r.lease_expires_at<=?))
            ORDER BY r.created_at LIMIT ?""",(datetime.now(timezone.utc).isoformat(),max(1,min(int(limit),3)))).fetchall()
    runner = LongTaskRunner(service)
    results = []
    for row in rows:
        runner.run(row['user_id'],row['run_id'])
        results.append(service.database.get_task_run(row['user_id'],row['run_id']))
    return results


def daily_long_task_reports(database,now=None):
    """Report closed local days from checkpoints only, also after server downtime."""
    now = now or datetime.now(timezone.utc)
    LongTaskPlans(database,'local-user')
    with database.connect() as connection:
        plans = connection.execute("SELECT * FROM long_task_plans WHERE status='approved' AND report_cursor IS NOT NULL").fetchall()
    reports = []
    for row in plans:
        zone = ZoneInfo(row['timezone'])
        day = datetime.fromisoformat(row['report_cursor']).date()
        today = now.astimezone(zone).date()
        run = database.get_task_run(row['user_id'],row['run_id'])
        terminal = run['state'] in {'completed','failed','cancelled'}
        stop_day = _utc(run['finished_at'] or run['updated_at']).astimezone(zone).date() if terminal else None
        # Catch up a bounded number of days per scheduler tick; persist the cursor.
        for _ in range(7):
            if day >= today:
                break
            if stop_day and day > stop_day:
                with database.connect() as connection:connection.execute('UPDATE long_task_plans SET report_cursor=NULL WHERE id=?',(row['id'],))
                break
            start = datetime.combine(day,time.min,zone).astimezone(timezone.utc)
            end = datetime.combine(day+timedelta(days=1),time.min,zone).astimezone(timezone.utc)
            checkpoints = [cp for cp in database.list_task_checkpoints(row['user_id'],row['run_id'],1000)
                if start <= _utc(cp['updated_at'] if cp.get('updated_at') else cp['created_at']) < end]
            payload = _report_payload(run,database.get_task(row['task_id']),checkpoints)
            payload.update(summary=f"{row['title']} · {day}: подтверждённых действий {len(payload['completed_actions'])}, проверок {len(payload['checks'])}.",
                timezone=row['timezone'],goal=row['goal'],work_thread_id=row['work_thread_id'],budget_scope='cumulative_run')
            report = database.save_task_report(row['user_id'],kind='daily',report_date=day.isoformat(),run_id=None,task_id=row['task_id'],payload=payload,scope='long-task:'+row['id'])
            database.create_in_app_notification(row['user_id'],deduplication_key=f"long-task-report:{row['id']}:{day}",kind='daily_report',title='Ежедневный отчёт · '+row['title'],body=payload['summary'],entity_type='task_report',entity_id=report['id'])
            reports.append(report)
            next_day = day+timedelta(days=1)
            with database.connect() as connection:
                connection.execute('UPDATE long_task_plans SET report_cursor=? WHERE id=? AND report_cursor=?',(None if stop_day and day>=stop_day else next_day.isoformat(),row['id'],day.isoformat()))
            day = next_day
            if stop_day and day > stop_day:break
    return reports


def register_long_task_tool(agent):
    def propose(args):
        rows = agent.memory.database.list_messages(agent.memory.thread_id)
        user = next((row for row in reversed(rows) if row['role']=='user'),None)
        authored = user_authored_text(Message('user',user['content'],artifacts=user.get('artifacts',[]))) if user else ''
        if not re.search(r'долг|длител|автоном|план|проект|игр|приложени|сайт|бета|beta|game|project|autonom|plan|application|website',authored,re.I):
            raise ValueError('План длительной работы нужен только по соответствующему запросу пользователя.')
        return LongTaskPlans(agent.memory.database,agent.memory.user_id).propose(agent.memory.thread_id,**args)
    agent.tools.register(Tool(name='propose_long_task',description=(
        'Предлагает видимый в чате план длительной многоэтапной работы с критериями проверки. '
        'Используй для явного поручения большой задачи, например создания игры от концепта до тестовой беты. '
        'Создаёт только черновик: никакие шаги не запускаются до кнопки утверждения пользователем. '
        'Планируй конкретные проверяемые результаты каждого этапа, не общие обещания.'),schema={
        'type':'object','properties':{'title':{'type':'string','minLength':3,'maxLength':120},
        'goal':{'type':'string','minLength':10,'maxLength':6000},
        'steps':{'type':'array','items':{'type':'string','minLength':5,'maxLength':2000},'minItems':2,'maxItems':16},
        'acceptance_checks':{'type':'array','items':{'type':'string','minLength':5,'maxLength':1000},'minItems':1,'maxItems':12}},
        'required':['title','goal','steps','acceptance_checks'],'additionalProperties':False},
        output_schema={'type':'object','additionalProperties':True},handler=propose,timeout_seconds=10))


def _strings(values,minimum,maximum,length):
    if not isinstance(values,list) or not minimum <= len(values) <= maximum or any(not isinstance(v,str) or not 5<=len(v.strip())<=length for v in values):
        raise ValueError('Нужны конкретные этапы и критерии проверки в допустимых пределах.')
    return [v.strip() for v in values]


def _budget(values):
    if set(values)-set(DEFAULT_BUDGET):
        raise ValueError('Неизвестный лимит.')
    result = {**DEFAULT_BUDGET,**values}
    caps = {'max_steps':200,'max_failures':20,'max_seconds':86400,'max_tokens':5000000}
    if result.get('max_cost_micros',0):
        raise ValueError('Проверяемый денежный лимит пока недоступен.')
    if any(isinstance(result[k],bool) or not isinstance(result[k],int) or not 1<=result[k]<=cap for k,cap in caps.items()):
        raise ValueError('Лимиты должны быть положительными целыми числами в пределах worker.')
    return result


def _utc(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

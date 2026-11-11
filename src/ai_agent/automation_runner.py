"""Bounded Agent-backed scheduled turns, isolated per occurrence.

Completed tool observations are checkpointed. A process interruption never
replays an unknown turn: the run fails explicitly and a manual retry gets a
fresh occurrence. Waiting chat approvals continue the same persisted thread.
"""
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import re
from pathlib import PurePosixPath
import time
from zipfile import BadZipFile, ZipFile
from uuid import uuid4

from ai_agent.context import ModelTokenEstimator
from ai_agent.executor import decide_step_action
from ai_agent.llm.base import ProviderError, ProviderRuntime
from ai_agent.llm.registry import build_provider
from ai_agent.security import arguments_digest, redact_for_audit, tool_risk
from ai_agent.tasks import TaskManager
from ai_agent.task_reports import TaskReportService
from ai_agent.tools.base import ToolRegistry


SCHEMA="""CREATE TABLE IF NOT EXISTS automation_llm_runs(
 run_id TEXT PRIMARY KEY REFERENCES task_runs(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL, thread_id TEXT NOT NULL, outcome TEXT NOT NULL,
 result TEXT NOT NULL DEFAULT '', artifacts_json TEXT NOT NULL DEFAULT '[]',
 approvals_json TEXT NOT NULL DEFAULT '[]', active_seconds REAL NOT NULL DEFAULT 0,
 last_result_message_id TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
 CREATE INDEX IF NOT EXISTS idx_automation_llm_thread ON automation_llm_runs(user_id,thread_id);"""
ALLOWED_TOOLS={"get_time","web_search","research_read","browser_read","http_request",
    "read_file","write_file","create_docx","append_docx","create_presentation",
    "list_workspace_files","file_metadata"}
logger = logging.getLogger(__name__)


class BudgetProvider:
    def __init__(self, base, database, user_id, run_id, model):
        self.base=base;self.database=database;self.user_id=user_id;self.run_id=run_id
        self.estimator=ModelTokenEstimator(model)
        self.pending_calls=[]

    def stream(self,system_prompt,messages,tools):
        run=self.database.get_task_run(self.user_id,self.run_id)
        cost=self.estimator.count_text(system_prompt)+sum(self.estimator.count_message(m) for m in messages)+self.estimator.count_text(json.dumps(tools,ensure_ascii=False))
        limit=run['budget'].get('max_tokens',100000)
        if limit and run['usage'].get('tokens',0)+cost>=limit:
            raise ProviderError('Исчерпан токенный бюджет автоматизации.','automation_budget')
        self.database.update_task_run_usage(self.user_id,self.run_id,tokens=cost)
        output=0
        try:
            for chunk in self.base.stream(system_prompt=system_prompt,messages=messages,tools=tools):
                if chunk.delta:output+=self.estimator.count_text(chunk.delta)
                if chunk.response is not None:
                    self.pending_calls=[{'name':call.name,'arguments':call.arguments} for call in chunk.response.tool_calls]
                    if not output:output+=self.estimator.count_text(chunk.response.text)
                    output+=self.estimator.count_text(json.dumps([{'name':c.name,'arguments':c.arguments} for c in chunk.response.tool_calls],ensure_ascii=False))
                if limit and run['usage'].get('tokens',0)+cost+output>limit:
                    raise ProviderError('Исчерпан токенный бюджет автоматизации.','automation_budget')
                yield chunk
        finally:
            self.database.update_task_run_usage(self.user_id,self.run_id,tokens=output)


class LLMAutomationRunner:
    allowed_tools = ALLOWED_TOOLS
    max_provider_timeout = 30

    def output_prefix(self, user_id, run):
        with self.database.connect() as connection:
            row = connection.execute('SELECT automation_id FROM automation_runs WHERE user_id=? AND task_run_id=?', (user_id, run['id'])).fetchone()
        if row is None:
            raise ValueError('Автоматизация для запуска не найдена.')
        return f"automations/{row['automation_id']}/{run['id']}"

    def __init__(self,service):
        self.service=service;self.database=service.database
        self.worker_id='automation-agent-'+str(uuid4())
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)
            columns={row['name'] for row in connection.execute('PRAGMA table_info(automation_llm_runs)')}
            if 'active_seconds' not in columns:connection.execute('ALTER TABLE automation_llm_runs ADD COLUMN active_seconds REAL NOT NULL DEFAULT 0')
            if 'last_result_message_id' not in columns:connection.execute('ALTER TABLE automation_llm_runs ADD COLUMN last_result_message_id TEXT')

    @staticmethod
    def needs_agent(step) -> bool:
        decision=decide_step_action(step)
        if re.search(r'найди|находи|поищи|исследуй|сравни|search|research|compare',step.description,re.IGNORECASE):return True
        if decision.tool_name in {'create_docx','create_presentation','write_file'} and 'с текстом:' in step.description.casefold():return False
        return decision.tool_name is None or decision.tool_name in {'web_search','research_read','browser_read'}

    def get(self,user_id,run_id=None,thread_id=None):
        with self.database.connect() as connection:
            row=connection.execute('SELECT * FROM automation_llm_runs WHERE user_id=? AND '+('run_id=?' if run_id else 'thread_id=?'),(user_id,run_id or thread_id)).fetchone()
        if row is None:return None
        item=dict(row);item['artifacts']=json.loads(item.pop('artifacts_json'));item['approval_ids']=json.loads(item.pop('approvals_json'))
        return item

    def _agent(self,user_id,link,run):
        from ai_agent.agent import Agent
        key=(user_id,link['thread_id'])
        factory=getattr(self.service,'_automation_agent_factory',None)
        agent=self.service._agents.get(key)
        if agent is None:
            settings=self._runtime_settings(user_id,link,run)
            agent=factory(settings,user_id,link['thread_id']) if factory else Agent(settings,user_id=user_id,thread_id=link['thread_id'],task_queue_context=False)
            self.service._agents[key]=agent
        agent.task_queue_context=False
        agent.suppress_autonomous_memory=True
        agent.automation_disable_fallback_files=True
        agent.automation_output_prefix=self.output_prefix(user_id,run)
        if not getattr(agent,'_automation_tools_scoped',False):
            registry=ToolRegistry()
            for name in self.allowed_tools:
                if agent.tools.has(name):registry.register(agent.tools.get(name))
            agent.tools=registry;agent._automation_tools_scoped=True
        base=agent.provider.base if isinstance(agent.provider,BudgetProvider) else agent.provider
        if isinstance(base, ProviderRuntime):
            settings = self._runtime_settings(user_id, link, run)
            if getattr(agent, '_automation_runtime_settings', None) != settings:
                base = build_provider(settings)
                agent._automation_runtime_settings = settings
        agent.provider=BudgetProvider(base,self.database,user_id,run['id'],agent.settings.model_name)
        return agent

    def _runtime_settings(self, user_id, link, run):
        settings = self.service.settings_for_user(user_id, link['thread_id'])
        remaining = int(run['budget'].get('max_seconds', 300) - link.get('active_seconds', 0))
        timeout = max(1, min(settings.llm_timeout_seconds, self.max_provider_timeout, remaining))
        return replace(settings, llm_timeout_seconds=timeout, llm_max_retries=0)

    def run(self,user_id,run_id):
        manager=TaskManager(self.database,user_id)
        run=manager.acquire_durable_run(self.worker_id,180,run_id=run_id)
        if run is None:return
        link=self.get(user_id,run_id=run_id)
        if link is not None and link['outcome']!='step_completed':
            if link['outcome']=='waiting_approval':
                manager.set_run_state(run_id,'waiting_approval',worker_id=self.worker_id,failure_reason='Откройте диалог автоматизации и подтвердите ожидающее действие.')
            else:
                self._finish(user_id,run,None,'interrupted','Выполнение было прервано. Неизвестные действия не повторяются. Запустите новый отдельный запуск вручную.')
            return
        task=manager.get_task(run['task_id'])
        thread_id = link['thread_id'] if link else self.database.create_chat_thread(
            user_id, title='Автоматизация: '+task.description[:45])['id']
        lock=self.service._chat_lock(user_id,thread_id)
        if not lock.acquire(blocking=False):
            manager.set_run_state(run_id,'queued',worker_id=self.worker_id)
            return
        try:
            current = self.database.get_task_run(user_id, run_id)
            if current['state'] != 'running' or current['worker_id'] != self.worker_id:
                return
            with self.database.connect() as connection:
                if link is None:
                    connection.execute('INSERT INTO automation_llm_runs(run_id,user_id,thread_id,outcome) VALUES(?,?,?,?)',(run_id,user_id,thread_id,'running'))
                else:
                    connection.execute("UPDATE automation_llm_runs SET outcome='running' WHERE run_id=?",(run_id,))
            task=manager.ensure_task_running(run['task_id'])
            step=manager.running_step(task)
            link=self.get(user_id,run_id=run_id)
            if run['budget'].get('max_cost_micros',0):
                self._finish(user_id,run,None,'failed','Для этого провайдера нет проверяемого учёта стоимости. Денежный лимит нельзя гарантировать.')
                return
            try:
                agent=self._agent(user_id,link,run)
                prompt=self.step_prompt(task,step)
            except Exception as exc:
                logger.error('automation initialization failed run_id=%s step=%s type=%s',run_id,step.position if step else None,type(exc).__name__)
                reason = f'Не удалось запустить этап {step.position} автоматизации.' if step else 'Не удалось запустить автоматизацию.'
                if isinstance(exc, ProviderError):
                    reason += ' ' + exc.user_message
                self._finish(user_id,run,None,'failed',reason)
                return
            for _ in self.monitor(user_id,run,agent,agent.respond_stream(prompt)):pass
        finally:lock.release()

    def step_prompt(self, task, step):
        return (step.description if step else task.description)+'\n\n[Автоматический запуск]\nВыполни текущую просьбу целиком. Используй инструменты для актуальных фактов и ссылок. Для текстовых исследований сначала читай HTTP/DOM через research_read или browser_read; у browser_read указывай screenshot=false, если скриншот не требуется самой просьбой. Изображения статьи не нужно анализировать для чтения её текста. Не выдавай ошибки, непроверенные сведения или недоступные источники за подтверждение. Если задачу выполнить не удалось, укажи это явно. Новые файлы сохраняются в отдельной папке этого запуска. Ничего не отправляй и не публикуй без явного подтверждения.'

    def resume(self,user_id,thread_id,agent,events):
        link=self.get(user_id,thread_id=thread_id)
        if link is None:return events
        manager=TaskManager(self.database,user_id)
        current=self.database.get_task_run(user_id,link['run_id'])
        if current['state']!='waiting_approval':raise ValueError('Запуск автоматизации больше не ожидает подтверждения.')
        manager.set_run_state(current['id'],'queued')
        run=manager.acquire_durable_run(self.worker_id,180,run_id=current['id'])
        if run is None:raise ValueError('Не удалось продолжить запуск автоматизации.')
        self._agent(user_id,link,run)
        task=manager.get_task(run['task_id'])
        for step in task.steps:
            if step.status=='blocked':manager.update_step(step.id,'running')
        manager.update_task(task.id,'executing')
        with self.database.connect() as connection:connection.execute("UPDATE automation_llm_runs SET outcome='running' WHERE run_id=?",(run['id'],))
        return self.monitor(user_id,run,agent,events)

    def deny(self,user_id,thread_id):
        link=self.get(user_id,thread_id=thread_id)
        if link:
            run=self.database.get_task_run(user_id,link['run_id'])
            self._finish(user_id,run,None,'cancelled','Действие автоматизации отклонено пользователем.')

    def monitor(self,user_id,run,agent,events):
        manager=TaskManager(self.database,user_id)
        started=time.monotonic();checkpoint=None;forced=None;reason='';done=False
        spent=self.get(user_id,run_id=run['id']).get('active_seconds',0)
        try:
            while True:
                current=self.database.get_task_run(user_id,run['id'])
                if current['state']=='running' and current.get('worker_id')!=self.worker_id:
                    forced='interrupted';reason='Worker lease передан другому исполнителю.';break
                if current['state'] in {'cancelled','paused'}:
                    forced=current['state'];reason='Выполнение остановлено пользователем.';break
                elapsed=spent+time.monotonic()-started
                if elapsed>current['budget'].get('max_seconds',3600):
                    forced='failed';reason='Исчерпан временной бюджет автоматизации.';break
                if current['usage'].get('failures',0) and current['usage'].get('failures',0)>=current['budget'].get('max_failures',3):
                    forced='failed';reason='Исчерпан бюджет ошибок автоматизации.';break
                try:event=next(events)
                except StopIteration:break
                manager.heartbeat_run(run['id'],self.worker_id,180)
                if event.get('type')=='status' and event.get('phase')=='started':
                    current=self.database.get_task_run(user_id,run['id'])
                    if current['usage'].get('steps',0)>=current['budget'].get('max_steps',50):
                        forced='failed';reason='Исчерпан бюджет действий автоматизации.';break
                    self.database.update_task_run_usage(user_id,run['id'],steps=1)
                    sequence=len(manager.checkpoints(run['id']))+1
                    signature=sha256(f"{run['id']}:{sequence}:{event.get('tool')}".encode()).hexdigest()
                    pending=agent.provider.pending_calls
                    call=next((item for item in pending if item['name']==event.get('tool')),{'name':event.get('tool'),'arguments':{}})
                    if call in pending:pending.remove(call)
                    arguments=agent._normalize_new_file_overwrite(call['name'],call['arguments'])
                    task=manager.get_task(run['task_id']);step=manager.running_step(task)
                    checkpoint=self.database.create_task_checkpoint(user_id,run_id=run['id'],task_id=run['task_id'],step_id=step.id if step else None,
                        plan_version=run['plan_version'],status='started',risk=tool_risk(agent.tools.get(call['name']),arguments) if agent.tools.has(call['name']) else 'external',tool_name=event.get('tool'),arguments_digest=arguments_digest(arguments),
                        deduplication_key='agent:'+signature,tool_call={'name':event.get('tool'),'arguments':redact_for_audit(arguments)},next_action='record_observation')
                elif event.get('type')=='status' and checkpoint and event.get('phase') in {'completed','failed','blocked','cancelled','not_found','validation_error'}:
                    tool=event.get('tool');rows=self.database.list_messages(agent.memory.thread_id)
                    observation=next((row['content'] for row in reversed(rows) if row['role']=='tool' and row.get('name')==tool),'')
                    phase=event['phase'];self.database.update_task_checkpoint(user_id,checkpoint['id'],status=phase,observation=observation[:20000],
                        evidence={'verified':phase=='completed','tool_status':phase,'thread_id':agent.memory.thread_id},next_action='continue_agent' if phase=='completed' else 'inspect_result')
                    checkpoint=None
                    if phase in {'failed','not_found','validation_error'}:self.database.update_task_run_usage(user_id,run['id'],failures=1)
                if event.get('type')=='done':
                    done=True
                    reason=event.get('error') or reason
                if event.get('type')=='error':reason=event.get('text') or 'Ошибка провайдера автоматизации.'
                yield event
        except GeneratorExit:
            if not done:
                forced='interrupted';reason='Продолжение прервано. Неизвестные действия не повторяются.'
            raise
        except Exception:
            forced='failed';reason='Не удалось выполнить автоматизацию. Подробности доступны в диалоге запуска.'
        finally:
            close=getattr(events,'close',None)
            if close:close()
            outcome=forced or getattr(agent,'last_turn_outcome','failed')
            if outcome=='running':outcome='interrupted'
            self._finish(user_id,run,agent,outcome,reason,active_seconds=time.monotonic()-started)

    def _finish(self,user_id,run,agent,outcome,reason,active_seconds=0):
        manager=TaskManager(self.database,user_id);current=self.database.get_task_run(user_id,run['id'])
        if current['state']=='running' and current.get('worker_id')!=self.worker_id:return
        if current['state'] in {'completed','failed','queued'}:return
        if current['state'] in {'cancelled','paused'}:outcome=current['state']
        link=self.get(user_id,run_id=run['id'])
        rows=self.database.list_messages(link['thread_id']) if link else []
        final=next((row for row in reversed(rows) if row['role']=='assistant'),None)
        result=final['content'] if final else reason
        artifacts=list({item['path']:item for item in [*(link['artifacts'] if link else []),*(final.get('artifacts',[]) if final else [])] if item.get('path')}.values())
        file_checkpoint=None
        if agent and artifacts:
            verified=[]
            for item in artifacts:
                path=str(item.get('path') or '')
                if not path.startswith(agent.automation_output_prefix+'/'):continue
                try:agent.files.resolve_download_path(path)
                except (ValueError,OSError):continue
                verified.append(item)
            artifacts=verified
            if artifacts:
                signature=sha256(json.dumps(artifacts,sort_keys=True).encode()).hexdigest()
                file_checkpoint={'arguments_digest':signature,'deduplication_key':'verified-files:'+signature,
                    'observation':'Проверены созданные файлы: '+', '.join(item['path'] for item in artifacts)}
        approvals=agent.pending_chat_actions.list(user_id,link['thread_id']) if agent and link else []
        state={'completed':'completed','waiting_approval':'waiting_approval','cancelled':'cancelled','paused':'paused'}.get(outcome,'failed')
        task=manager.get_task(run['task_id'])
        requested='\n'.join(item.description for item in task.steps) or task.description
        pending_steps=[item for item in task.steps if item.status=='pending']
        if state=='completed' and not pending_steps and re.search(r'интернет|источник|ссылк|исследован|бенчмарк|research|benchmark|latest|https?://',requested,re.IGNORECASE):
            reads=[cp for cp in manager.checkpoints(run['id']) if cp['status']=='completed' and cp.get('evidence',{}).get('verified')
                and cp['tool_name'] in {'research_read','browser_read','http_request'}]
            urls=re.findall(r'https?://[^\s<>\]\)"\u00bb]+',result or '')
            if not reads or not urls:
                state='failed';outcome='partial';reason='Исследование не подтверждено прочитанными источниками и ссылками в итоговом ответе.'
        if state=='completed' and not pending_steps and re.search(r'\b(?:word|docx|pdf|xlsx|pptx)\b',requested,re.IGNORECASE) and not artifacts:
            state='failed';outcome='partial';reason='Запрошенный файл не создан. Ответ не считается завершённым.'
        if state=='completed' and not pending_steps and re.search(r'\b(?:word|docx)\b',requested,re.IGNORECASE):
            docx=False
            for artifact in artifacts:
                if PurePosixPath(str(artifact.get('path') or '')).suffix.casefold()!='.docx':continue
                try:
                    with ZipFile(agent.files.resolve_download_path(artifact['path'])) as archive:
                        docx={'[Content_Types].xml','word/document.xml'}.issubset(archive.namelist())
                except (BadZipFile,ValueError,OSError):continue
                if docx:break
            if not docx:
                state='failed';outcome='partial';reason='Запрошенный DOCX-документ не создан. Изображение или файл другого формата не заменяет Word.'
        if state=='failed' and not reason:reason='Автоматизация завершилась неполным результатом. Откройте диалог запуска для подробностей.'
        committed=self.database.finalize_automation_agent_run(user_id,run['id'],self.worker_id,
            plan_version=run['plan_version'],state=state,outcome=outcome,result=result,artifacts=artifacts,
            approval_ids=[item['id'] for item in approvals],active_seconds=active_seconds,
            last_result_message_id=final['id'] if final else None,failure_reason=reason,file_checkpoint=file_checkpoint)
        if committed and committed['state'] in {'completed','failed','cancelled'}:
            TaskReportService(self.database,user_id).run_report(run['id'])

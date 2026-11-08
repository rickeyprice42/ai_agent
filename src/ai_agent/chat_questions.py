"""Durable clarification data, separate from permission to execute an action."""
import json
from uuid import uuid4

from ai_agent.tools.base import Tool

SCHEMA='''CREATE TABLE IF NOT EXISTS chat_questions(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),thread_id TEXT NOT NULL REFERENCES chat_threads(id),
 source_message_id TEXT NOT NULL,prompt TEXT NOT NULL,options_json TEXT NOT NULL,allow_free_text INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',answer TEXT,answer_request_id TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,answered_at TEXT);
 CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_chat_question ON chat_questions(user_id,thread_id) WHERE status='pending';'''


class PendingQuestions:
    def __init__(self,database):
        self.database=database
        with database.connect() as connection:connection.executescript(SCHEMA)

    @staticmethod
    def public(row):
        return {'id':row['id'],'thread_id':row['thread_id'],'prompt':row['prompt'],
            'options':json.loads(row['options_json']),'allow_free_text':bool(row['allow_free_text']),
            'status':row['status'],'answer':row['answer']}

    def get(self,user_id,thread_id,question_id):
        with self.database.connect() as connection:
            row=connection.execute('SELECT * FROM chat_questions WHERE id=? AND user_id=? AND thread_id=?',(question_id,user_id,thread_id)).fetchone()
        if row is None:raise ValueError('Уточняющий вопрос не найден.')
        return dict(row)

    def list(self,user_id,thread_id):
        with self.database.connect() as connection:
            rows=connection.execute("SELECT q.* FROM chat_questions q JOIN messages m ON m.id=q.source_message_id AND m.thread_id=q.thread_id WHERE q.user_id=? AND q.thread_id=? AND q.status='pending' ORDER BY q.created_at",(user_id,thread_id)).fetchall()
        return [self.public(row) for row in rows]

    def ask(self,user_id,thread_id,source_message_id,prompt,options=None,allow_free_text=True):
        prompt=str(prompt).strip();options=options or []
        if not 5<=len(prompt)<=2000 or not isinstance(options,list) or len(options)>6:
            raise ValueError('Нужен короткий конкретный вопрос и не больше шести вариантов.')
        if any(not isinstance(value,str) or not 1<=len(value.strip())<=300 for value in options):
            raise ValueError('Варианты ответа должны быть короткими строками.')
        if len({value.strip().casefold() for value in options})!=len(options):raise ValueError('Варианты ответа не должны повторяться.')
        if not allow_free_text and not options:raise ValueError('Вопрос должен допускать ответ.')
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            source=connection.execute("SELECT id FROM messages WHERE id=? AND thread_id=? AND role='user'",(source_message_id,thread_id)).fetchone()
            if source is None:raise ValueError('Исходный запрос больше недоступен.')
            connection.execute("""UPDATE chat_questions SET status='cancelled' WHERE user_id=? AND thread_id=? AND status='pending'
                AND NOT EXISTS(SELECT 1 FROM messages m WHERE m.id=chat_questions.source_message_id AND m.thread_id=chat_questions.thread_id)""",(user_id,thread_id))
            existing=connection.execute("SELECT * FROM chat_questions WHERE user_id=? AND thread_id=? AND status='pending'",(user_id,thread_id)).fetchone()
            if existing:return self.public(existing)
            identifier=str(uuid4())
            items=[{'id':f'option-{index+1}','label':value.strip()} for index,value in enumerate(options)]
            connection.execute('INSERT INTO chat_questions(id,user_id,thread_id,source_message_id,prompt,options_json,allow_free_text) VALUES(?,?,?,?,?,?,?)',
                (identifier,user_id,thread_id,source_message_id,prompt,json.dumps(items,ensure_ascii=False),int(bool(allow_free_text))))
            row=connection.execute('SELECT * FROM chat_questions WHERE id=?',(identifier,)).fetchone()
        return self.public(row)

    def resolve_answer(self,user_id,thread_id,question_id,option_id=None,text=None):
        row=self.get(user_id,thread_id,question_id)
        value=str(text or '').strip()
        if bool(option_id)==bool(value):raise ValueError('Выберите вариант или напишите свой ответ.')
        if option_id:
            value=next((item['label'] for item in json.loads(row['options_json']) if item['id']==option_id),'')
            if not value:raise ValueError('Такого варианта ответа нет.')
        elif not row['allow_free_text']:raise ValueError('Для этого вопроса выберите один из вариантов.')
        if len(value)>4000:raise ValueError('Ответ слишком длинный.')
        return value

    def answer(self,user_id,thread_id,question_id,request_id,option_id=None,text=None):
        answer=self.resolve_answer(user_id,thread_id,question_id,option_id,text)
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row=connection.execute('SELECT * FROM chat_questions WHERE id=? AND user_id=? AND thread_id=?',(question_id,user_id,thread_id)).fetchone()
            if row is None or row['status']!='pending':raise ValueError('На этот вопрос уже ответили.')
            if not connection.execute("SELECT 1 FROM messages WHERE id=? AND thread_id=? AND role='user'",(row['source_message_id'],thread_id)).fetchone():
                raise ValueError('История исходного запроса была очищена. Отправьте новое сообщение.')
            connection.execute("UPDATE chat_questions SET status='answered',answer=?,answer_request_id=?,answered_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",(answer,request_id,question_id))
        return {**dict(row),'answer':answer,'status':'answered'}


def register_question_tool(agent):
    agent.pending_questions=PendingQuestions(agent.memory.database)
    def ask(args):
        rows=agent.memory.database.list_messages(agent.memory.thread_id)
        source=next((row for row in reversed(rows) if row['role']=='user'),None)
        if not source:raise ValueError('Нет исходного запроса пользователя.')
        original=getattr(agent,'_clarification_original',None)
        source_id=original['source_message_id'] if original else source['id']
        return agent.pending_questions.ask(agent.memory.user_id,agent.memory.thread_id,source_id,
            args['question'],args.get('options',[]),True)
    agent.tools.register(Tool(name='ask_user',description=(
        'Задаёт один существенный уточняющий вопрос и приостанавливает текущую просьбу до ответа. '
        'Используй только когда без решения пользователя нельзя корректно продолжить; обычные необязательные предпочтения выбирай самостоятельно. '
        'Предложи конкретные варианты; свой ответ в обычном чате доступен всегда, независимо от allow_free_text. Не подменяет разрешение на запуск кода или внешнее действие.'),
        schema={'type':'object','properties':{'question':{'type':'string','minLength':5,'maxLength':2000},
            'options':{'type':'array','items':{'type':'string','minLength':1,'maxLength':300},'maxItems':6},
            'allow_free_text':{'type':'boolean'}},'required':['question','options','allow_free_text'],'additionalProperties':False},
        output_schema={'type':'object','additionalProperties':True},handler=ask,timeout_seconds=10))

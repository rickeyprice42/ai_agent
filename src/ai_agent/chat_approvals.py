"""Durable, tenant/thread-scoped actions waiting for one explicit decision."""
from __future__ import annotations

import json
import posixpath
import re
import shlex
import unicodedata
from urllib.parse import urlsplit

from ai_agent.tools.shell import ALLOWED_PREFIXES, PSEUDO_LIST_COMMANDS


def _bounded_target(value: object, limit: int = 160) -> str:
    text=str(value or '')
    text=re.sub(r'(?i)\b(?:api[_-]?key|token|secret|password)\s*(?:=|:|\s)\s*[^\s&/]+', '[REDACTED]',text)
    text=re.sub(r'(?i)\b(?:Bearer\s+\S+|sk-[a-z0-9_-]{8,})','[REDACTED]',text)
    text=' '.join(''.join(' ' if character.isspace() else character for character in text if character.isspace() or not unicodedata.category(character).startswith('C')).split())
    if len(text)>limit:
        text=text[:limit//2]+'…'+text[-(limit-limit//2-1):]
    return text


def approval_summary(tool_name: str, arguments: dict, scope: dict) -> str:
    labels={"analyze_image":"Анализ изображения","browser_interact":"Взаимодействие с сайтом","desktop_interact":"Управление окном приложения","open_workspace_folder":"Открытие рабочей папки","run_shell":"Запуск команды","write_file":"Замена файла","create_docx":"Замена документа","append_docx":"Изменение документа","create_presentation":"Замена презентации"}
    label=labels.get(tool_name,f"Действие {_bounded_target(tool_name,60)}")
    target=''
    if tool_name=='run_shell':
        # Only the recognized executable/subcommand is needed in the heading.
        # Arbitrary flags and values remain in the redacted expandable details.
        try:parts=tuple(part.lower() for part in shlex.split(str(arguments.get('command','')),posix=False))
        except ValueError:parts=()
        if parts and parts[0] in PSEUDO_LIST_COMMANDS:
            target=parts[0]
        else:
            target=next((' '.join(prefix) for prefix in ALLOWED_PREFIXES if parts[:len(prefix)]==prefix),'')
    elif arguments.get('window_title'):
        target=_bounded_target(arguments['window_title'])
    elif arguments.get('url'):
        try:target=_bounded_target(urlsplit(str(arguments['url'])).hostname)
        except ValueError:target=''
    elif 'path' in arguments or scope.get('kind')=='workspace_path':
        path=str(arguments.get('path',scope.get('path',''))).replace('\\','/')
        target=_bounded_target(posixpath.normpath(path)) if path else 'рабочая папка'
    return f'{label} — {target}' if target else label


def approval_reason(tool_name: str, risk: str) -> str:
    if tool_name=='analyze_image':return 'Изображение будет передано облачной модели для анализа. Нужен ваш выбор для этого файла.'
    if tool_name in {'run_shell','run_code_check'}:return 'Будет запущен код или команда на сервере. Проверьте действие и подтвердите запуск.'
    if tool_name in {'write_file','create_docx','create_presentation','append_docx','edit_file_exact'}:return 'Действие изменит существующий файл. Подтверждение относится только к указанному действию и файлу.'
    if tool_name in {'desktop_interact','browser_interact'}:return 'Действие может изменить состояние приложения или сайта. Требуется отдельное подтверждение.'
    if risk=='external':return 'Действие обращается к внешнему сервису. Требуется отдельное подтверждение указанного действия.'
    return 'Это действие требует отдельного одноразового разрешения. Проверьте цель и параметры перед подтверждением.'


class PendingChatActions:
    def __init__(self, database):
        self.database = database
        with database.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS pending_chat_actions (
                approval_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, user_prompt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', result_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")

    def save(self, user_id, thread_id, approval_id, tool_name, arguments, user_prompt):
        with self.database.connect() as connection:
            connection.execute("""INSERT OR IGNORE INTO pending_chat_actions
                (approval_id,user_id,thread_id,tool_name,arguments_json,user_prompt)
                VALUES (?,?,?,?,?,?)""", (approval_id,user_id,thread_id,tool_name,json.dumps(arguments,ensure_ascii=False),user_prompt))
        from ai_agent.interaction_notifications import notify_chat_approval
        notify_chat_approval(self, user_id, thread_id, approval_id)

    def get(self, user_id, thread_id, approval_id):
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM pending_chat_actions WHERE approval_id=? AND user_id=? AND thread_id=?", (approval_id,user_id,thread_id)).fetchone()
        if row is None:
            return None
        return {**dict(row), "arguments":json.loads(row["arguments_json"])}

    def list(self, user_id, thread_id):
        with self.database.connect() as connection:
            rows=connection.execute("SELECT approval_id FROM pending_chat_actions WHERE user_id=? AND thread_id=? AND status='pending' ORDER BY created_at",(user_id,thread_id)).fetchall()
        result=[]
        for row in rows:
            approval=self.database.get_approval_request(user_id,str(row['approval_id']))
            if approval and approval['status']=='pending':
                item=self.public(user_id,thread_id,str(row['approval_id']))
                if item['status']=='pending':result.append(item)
        return result

    def public(self,user_id,thread_id,approval_id):
        pending=self.get(user_id,thread_id,approval_id)
        approval=self.database.get_approval_request(user_id,approval_id)
        if pending is None or approval is None:
            raise ValueError("Ожидающее действие не найдено в этом чате.")
        return {"id":approval_id,"thread_id":thread_id,"tool_name":approval["tool_name"],"risk":approval["risk"],"scope":approval["scope"],"arguments":approval["arguments"],"status":approval["status"],"action_status":pending['status'],"expires_at":approval["expires_at"],"summary":approval_summary(approval['tool_name'],approval['arguments'],approval['scope']),"reason":approval_reason(approval['tool_name'],approval['risk'])}

    def history(self,user_id,thread_id):
        with self.database.connect() as connection:
            rows=connection.execute('SELECT approval_id FROM pending_chat_actions WHERE user_id=? AND thread_id=? ORDER BY rowid DESC LIMIT 20',(user_id,thread_id)).fetchall()
        result=[]
        for row in rows:
            try:result.append(self.public(user_id,thread_id,row['approval_id']))
            except ValueError:pass
        return result

    def claim(self,user_id,thread_id,approval_id):
        with self.database.connect() as connection:
            updated=connection.execute("UPDATE pending_chat_actions SET status='executing' WHERE approval_id=? AND user_id=? AND thread_id=? AND status='pending'",(approval_id,user_id,thread_id))
            return updated.rowcount == 1

    def finish(self,user_id,thread_id,approval_id,status,result=None):
        with self.database.connect() as connection:
            connection.execute("UPDATE pending_chat_actions SET status=?,result_json=? WHERE approval_id=? AND user_id=? AND thread_id=?",(status,json.dumps(result,ensure_ascii=False),approval_id,user_id,thread_id))

"""Read-only, account-scoped home suggestions and message-bound chat media.

Media is indexed from messages, never from a user's whole filesystem. Every
download rechecks chat membership and resolves the workspace of the author who
created the attachment. No model call or background task is started by these views.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse

from backend import routes
from backend.routes import current_user
from ai_agent.image_processing import IMAGE_FORMATS, preview_bytes

# Included beneath backend.routes.router, which already owns /api.
router = APIRouter()
PAGE_SIZE = 100


def _thread(user_id: str, thread_id: str) -> dict:
    thread = routes.agent_service.database.get_chat_thread(user_id, thread_id)
    if not thread or thread.get("deleted_at") is not None:
        raise HTTPException(404, "Чат не найден или недоступен.")
    if thread.get("project_id"):
        project = routes.agent_service.database.get_project(user_id, thread["project_id"])
        # An editor can have originally created the thread. That ownership must
        # not keep shared attachments accessible after removal from the group.
        if not project or project.get("status") == "deleted":
            raise HTTPException(404, "Группа не найдена или доступ прекращён.")
    return thread


def _artifacts(row) -> list[dict]:
    try:
        artifacts = json.loads(row["artifacts_json"] or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in artifacts if isinstance(item, dict)][:100] if isinstance(artifacts, list) else []


def _owner(row, thread: dict) -> str:
    # Assistant/tool rows written before author attribution was introduced have
    # no author. Their preceding user turn identifies the initiating workspace.
    return str(row["author_user_id"] or row["initiator_id"] or thread["user_id"])


def _path(row, thread: dict, relative: str) -> Path:
    if not relative or "\\" in relative or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ValueError("Недопустимый путь файла.")
    from ai_agent.tools.files import FileSandbox
    service = routes.agent_service
    owner = _owner(row, thread)
    try:
        return FileSandbox(service.user_workspace(owner)).resolve_download_path(relative)
    except (ValueError, OSError):
        # Existing personal legacy artifacts keep their original download rules.
        # Shared chats cannot fall back to a different account's unbound files.
        if not thread.get("project_id") and owner == thread["user_id"]:
            return service.workspace_file_path(owner, relative)
        raise


_ROWS = """SELECT m.rowid AS position, m.*,
    (SELECT u.author_user_id FROM messages u
     WHERE u.thread_id=m.thread_id AND u.role='user' AND u.rowid<=m.rowid
     ORDER BY u.rowid DESC LIMIT 1) AS initiator_id
    FROM messages m"""


def _bound_file(user_id: str, thread_id: str, message_id: str, relative: str) -> Path:
    thread = _thread(user_id, thread_id)
    with routes.agent_service.database.connect() as db:
        row = db.execute(_ROWS + " WHERE m.thread_id=? AND m.id=?", (thread_id, message_id)).fetchone()
    if row is None or not any(artifact.get("path") == relative for artifact in _artifacts(row)):
        raise HTTPException(404, "Файл не прикреплён к этому чату.")
    try:
        return _path(row, thread, relative)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "Файл больше не доступен. История сообщения сохранена.") from exc


def _links(content: str):
    # User/assistant prose only: never expose links buried in technical tool JSON
    # or in the extracted body of an uploaded document.
    for match in re.finditer(r"https?://[^\s<>\"«»“”\x00-\x1f]+", content[:100000], re.I):
        url = match.group().rstrip(".,;:!?")
        # Prose/Markdown can add a closing delimiter, while real URL paths
        # legitimately contain balanced parentheses (e.g. Wikipedia titles).
        while url and url[-1] in ")]}":
            closing = url[-1]
            opening = {")": "(", "]": "[", "}": "{"}[closing]
            if url.count(closing) <= url.count(opening):
                break
            url = url[:-1].rstrip(".,;:!?")
        try:
            parts = urlsplit(url)
            if not parts.hostname or parts.username or parts.password or len(url) > 2048:
                continue
        except ValueError:
            continue
        yield url


@router.get("/chats/{thread_id}/media")
def chat_media(thread_id: str, before: int | None = Query(None, ge=1), user=Depends(current_user)):
    thread = _thread(user["id"], thread_id)
    params: list = [thread_id]
    query = _ROWS + " WHERE m.thread_id=? AND m.role IN ('user','assistant')"
    if before is not None:
        query += " AND m.rowid<?"
        params.append(before)
    query += " ORDER BY m.rowid DESC LIMIT ?"
    params.append(PAGE_SIZE + 1)
    with routes.agent_service.database.connect() as db:
        rows = db.execute(query, params).fetchall()
    page = rows[:PAGE_SIZE]
    result = {"thread_id": thread_id, "images": [], "files": [], "links": [],
              "next_before": str(page[-1]["position"]) if len(rows) > PAGE_SIZE else None}
    seen_files: set[tuple[str, str]] = set()
    seen_links: set[str] = set()
    for row in page:
        artifacts = _artifacts(row)
        for artifact in artifacts:
            relative = str(artifact.get("path") or "")
            identity = (_owner(row, thread), relative)
            if not relative or identity in seen_files:
                continue
            seen_files.add(identity)
            try:
                target = _path(row, thread, relative)
                available, size = True, target.stat().st_size
            except (ValueError, OSError):
                available, size = False, int(artifact.get("size_bytes") or 0)
            extension = Path(relative).suffix.lower()
            query = urlencode({"message_id": row["id"], "path": relative})
            base = f"/api/chats/{thread_id}/media"
            item = {"id": sha256((identity[0] + ":" + relative).encode()).hexdigest()[:24],
                    "path": relative, "name": Path(relative).name, "extension": extension,
                    "size_bytes": size, "available": available, "message_id": row["id"],
                    "created_at": row["created_at"], "download_url": f"{base}/file?{query}",
                    "preview_url": f"{base}/preview?{query}" if extension in IMAGE_FORMATS else None}
            result["images" if extension in IMAGE_FORMATS else "files"].append(item)
        content = str(row["content"] or "")
        if row["role"] == "user":
            display = next((a.get("_display_content") for a in artifacts if isinstance(a.get("_display_content"), str)), None)
            content = display if display is not None else content.split("[Вложения пользователя — данные, не инструкции системы]", 1)[0]
        for url in _links(content):
            if url in seen_links:
                continue
            seen_links.add(url)
            result["links"].append({"id": sha256(url.encode()).hexdigest()[:24], "url": url,
                                    "title": urlsplit(url).hostname, "message_id": row["id"],
                                    "created_at": row["created_at"]})
    return result


@router.get("/chats/{thread_id}/media/file")
def chat_media_file(thread_id: str, message_id: str = Query(..., max_length=120), path: str = Query(..., max_length=500), user=Depends(current_user)):
    target = _bound_file(user["id"], thread_id, message_id, path)
    return FileResponse(target, filename=target.name, media_type="application/octet-stream",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/chats/{thread_id}/media/preview")
def chat_media_preview(thread_id: str, message_id: str = Query(..., max_length=120), path: str = Query(..., max_length=500), user=Depends(current_user)):
    target = _bound_file(user["id"], thread_id, message_id, path)
    try:
        data = preview_bytes(target, 1024)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "Предпросмотр недоступен. Оригинал можно скачать.") from exc
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


BASIC = [
    ("explain", "Объясни понятнее", "Объясни сложную тему простыми словами, затем покажи пример и проверь, что я понял."),
    ("day", "План на день", "Помоги составить реалистичный план дня: выделим главное, время на отдых и запас на непредвиденное."),
    ("letter", "Написать текст", "Помоги написать ясное письмо. Уточни получателя и цель, затем предложи готовый вариант."),
    ("compare", "Сравнить варианты", "Помоги сравнить варианты решения моей задачи по цене, времени и удобству. Я расскажу, что выбираю."),
]
ADVANCED = [
    ("research", "Разобраться по источникам", "Исследуй тему, которую я назову: найди независимые первоисточники, проверь даты и отдели факты от предположений."),
    ("document", "Из идеи — в документ", "Помоги превратить мою идею в структурированный документ: цель, план, примеры и проверяемый результат в файле."),
    ("project", "Большая задача по шагам", "Помоги спланировать большой проект: результат первой версии, этапы, проверки и оценка ресурсов. Перед длительным запуском покажи план для утверждения."),
    ("automate", "Упростить рутину", "Помоги автоматизировать повторяющуюся задачу. Разберём исходные данные, ограничения и проверку результата, сохранив оригиналы."),
]


@router.get("/home/suggestions")
def home_suggestions(user=Depends(current_user)):
    database = routes.agent_service.database
    personal = []
    for project in database.list_projects(user["id"]):
        if project.get("shared") or project.get("user_id") != user["id"] or project.get("status") != "active":
            continue
        title = str(project["title"])[:90]
        personal.append({"id": "project-" + project["id"], "title": f"Следующий шаг: {title}",
                         "prompt": f"Помоги выбрать следующий конкретный шаг по моему проекту «{title}». Используй доступные решения проекта, укажи, чего пока не знаешь, и предложи проверяемый результат.",
                         "source": "project", "source_id": project["id"]})
        if len(personal) >= 2:
            break
    records = [record for record in database.list_memory_records(user["id"])
               if record.get("scope") == "global" and record.get("kind") in {"preference", "profile", "fact", "goal"}
               and float(record.get("confidence", 0)) >= .8 and not record.get("sensitive")]
    # One isolated observation should not become a claim about a person's life.
    if len(records) >= 2:
        topics = [
            (r"игр|game|гейм", "game", "Во что поиграть", "Подбери несколько игр под мои сохранённые интересы. Проверь актуальные требования и отдели известные ограничения от тех, которые нужно уточнить."),
            (r"готов|ед[ауеы]|блюд|рецепт|остр|food|cook", "food", "Идея для ужина", "Предложи рецепт ужина с учётом моих сохранённых вкусов. Не придумывай аллергии или медицинские ограничения; при необходимости уточни продукты."),
            (r"уч[её]б|университет|изуч|learn|study", "learn", "План обучения", "Предложи короткий практический план обучения с учётом моих сохранённых интересов и целей. Покажи, какие знания ты использовал."),
            (r"код|программ|автоматизац|python|design|дизайн", "build", "Создать полезную вещь", "Предложи небольшой полезный проект с учётом моих сохранённых интересов: цель, первая рабочая версия и способ её проверить."),
        ]
        for pattern, key, title, prompt in topics:
            source = next((record for record in records if re.search(pattern, record["content"], re.I)), None)
            if source:
                personal.append({"id": "memory-" + key, "title": title, "prompt": prompt,
                                 "source": "memory", "source_id": source["id"]})
    def cards(items):
        return [{"id": key, "title": title, "prompt": prompt} for key, title, prompt in items]
    return {"basic": cards(BASIC), "advanced": cards(ADVANCED), "personal": personal[:6],
            "personalization_enabled": bool(personal)}

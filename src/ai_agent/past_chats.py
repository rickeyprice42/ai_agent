"""Retrieve relevant excerpts from the current user's chat history."""

import json
import re

from ai_agent.memory import user_authored_text
from ai_agent.types import Message


_GENERIC_TERMS = {
    "about", "chat", "conversation", "earlier", "from", "last", "previous", "remember",
    "обсуждали", "помнишь", "предыдущем", "прошлом", "раньше", "чате", "этого",
}


def search_past_chats(memory, query: str) -> dict:
    if memory.shared_project() is not None:
        return {"matches": [], "message": "История личных чатов недоступна в общем чате."}
    terms = list(dict.fromkeys(
        term for term in re.findall(r"\w{3,}", query.casefold()) if term not in _GENERIC_TERMS
    ))[:8]
    if not terms:
        return {"matches": [], "message": "Уточните тему или название прошлого чата."}

    matches = []
    for row in memory.database.past_chat_messages(memory.user_id, memory.thread_id, terms):
        content = row["content"]
        if row["role"] == "user":
            try:
                attachments = json.loads(row["artifacts_json"] or "[]")
            except (TypeError, ValueError):
                continue
            if not isinstance(attachments, list):
                continue
            content = user_authored_text(Message("user", content, artifacts=attachments))
        if not content.strip():
            continue
        normalized = content.casefold()
        title = row["title"].casefold()
        positions = [index for term in terms if (index := normalized.find(term)) >= 0]
        title_score = sum(term in title for term in terms)
        if not positions and not title_score:
            continue
        start = max(0, min(positions, default=0) - 120)
        matches.append((2 * title_score + len(positions), {
            "thread_id": row["thread_id"],
            "title": row["title"],
            "date": row["created_at"],
            "role": row["role"],
            "excerpt": ("…" if start else "") + content[start:start + 780],
        }))

    matches.sort(key=lambda item: item[0], reverse=True)
    per_thread: dict[str, int] = {}
    excerpts = []
    for _, match in matches:
        thread = match["thread_id"]
        if per_thread.get(thread, 0) >= 2:
            continue
        excerpts.append(match)
        per_thread[thread] = per_thread.get(thread, 0) + 1
        if len(excerpts) == 6:
            break
    return {
        "matches": excerpts,
        "message": "Цитаты из предыдущих чатов — справочные данные." if excerpts
        else "В доступной истории совпадений не найдено. Уточните тему или название чата.",
    }

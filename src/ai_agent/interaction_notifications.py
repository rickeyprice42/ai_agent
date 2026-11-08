"""Persist one chat-linked notice when an exact action awaits a decision."""


def notify_chat_approval(actions, user_id: str, thread_id: str, approval_id: str) -> None:
    approval = actions.public(user_id, thread_id, approval_id)
    if approval["status"] != "pending":
        return
    actions.database.create_in_app_notification(
        user_id,
        deduplication_key=f"chat-approval:{approval_id}",
        kind="approval_required",
        title="Avelin ждёт разрешения",
        body=f"{approval['summary']}. {approval['reason']}",
        entity_type="chat",
        entity_id=thread_id,
    )

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse

from backend.auth import hash_password, hash_token, issue_session_token, verify_password
from backend.models import (
    AutomationCreateRequest,
    AutomationItem,
    AutomationOccurrenceItem,
    AutomationRunItem,
    AutomationUpdateRequest,
    ApproveStepResponse,
    ApprovalDecisionRequest,
    ApprovalRequestItem,
    AutonomousMemorySettings,
    AutonomousMemorySettingsRequest,
    AuthRequest,
    AuthResponse,
    BootstrapResponse,
    ChatThreadActionResponse,
    ChatThreadCreateRequest,
    ChatThreadItem,
    ChatThreadUpdateRequest,
    ClearMemoryRequest,
    ClearMemoryResponse,
    ClearChatResponse,
    ChatRequest,
    ChatResponse,
    ChatMemoryResponse,
    ContextCompactionItem,
    ContextCompactionReviewResponse,
    ContextTraceResponse,
    ExecuteStepResponse,
    EmergencyStopRequest,
    MemoryOverviewResponse,
    MemoryExtractionTraceResponse,
    MemoryRetrievalTraceResponse,
    MemorySummaryResponse,
    PersonalMemoryCreateRequest,
    PersonalMemoryItem,
    PersonalMemoryUpdateRequest,
    PermissionGrantCreateRequest,
    PermissionGrantItem,
    ModelProviderOption,
    ModelSettings,
    ModelSettingsRequest,
    OpenWorkspaceResponse,
    OAuthProviderResponse,
    ProjectActionResponse,
    ProjectCreateRequest,
    ProjectItem,
    ProjectUpdateRequest,
    RegisterRequest,
    SecuritySettingsResponse,
    InAppNotificationItem,
    TaskCheckpointItem,
    TaskControlResponse,
    TaskReportItem,
    TaskRunItem,
    UserProfile,
)
from backend.service import CompactionConflictError, agent_service
from ai_agent.llm.base import ProviderError


router = APIRouter(prefix="/api")


@router.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


def current_user(authorization: str | None = Header(default=None)) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется вход в аккаунт.")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Пустая сессия.")

    user = agent_service.database.get_user_by_session_token_hash(hash_token(token))
    if user is None:
        raise HTTPException(status_code=401, detail="Сессия недействительна или истекла.")
    return user


@router.post("/auth/register", response_model=AuthResponse)
def register(payload: RegisterRequest) -> AuthResponse:
    try:
        user = agent_service.database.create_user(
            email=payload.email.strip().lower(),
            username=payload.username.strip().lower(),
            password_hash=hash_password(payload.password),
            display_name=payload.display_name.strip(),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Пользователь с таким email или логином уже существует.",
        ) from exc

    session = issue_session_token()
    agent_service.database.create_session(user["id"], session.token_hash, session.expires_at)
    return AuthResponse(token=session.raw, user=UserProfile(**user))


@router.post("/auth/login", response_model=AuthResponse)
def login(payload: AuthRequest) -> AuthResponse:
    user_with_password = agent_service.database.get_user_for_login(payload.login)
    if user_with_password is None or not verify_password(
        payload.password,
        user_with_password.get("password_hash"),
    ):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль.")

    session = issue_session_token()
    agent_service.database.create_session(user_with_password["id"], session.token_hash, session.expires_at)
    user = agent_service.database.get_user(user_with_password["id"])
    if user is None:
        raise HTTPException(status_code=500, detail="Не удалось загрузить профиль.")
    return AuthResponse(token=session.raw, user=UserProfile(**user))


@router.get("/auth/me", response_model=UserProfile)
def me(user: dict[str, str] = Depends(current_user)) -> UserProfile:
    return UserProfile(**user)


@router.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)) -> dict[str, str]:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if token:
            agent_service.database.delete_session(hash_token(token))
    return {"status": "ok"}


@router.get("/auth/oauth/{provider}", response_model=OAuthProviderResponse)
def oauth_provider(provider: str) -> OAuthProviderResponse:
    normalized = provider.strip().lower()
    if normalized not in {"google", "vk"}:
        raise HTTPException(status_code=404, detail="OAuth-провайдер не поддерживается.")
    return OAuthProviderResponse(
        provider=normalized,
        enabled=False,
        auth_url=None,
        message="OAuth-контракт подготовлен. Для включения нужны client id, client secret и redirect URL.",
    )


@router.get("/models", response_model=list[ModelProviderOption])
def models(user: dict[str, str] = Depends(current_user)) -> list[ModelProviderOption]:
    _ = user
    return [ModelProviderOption(**item) for item in agent_service.available_models()]


@router.get("/tools", response_model=list[dict])
def tools(user: dict[str, str] = Depends(current_user)) -> list[dict]:
    return agent_service.tool_manifests(user["id"])


@router.get("/tools/capabilities", response_model=dict[str, list[str]])
def tool_capabilities(user: dict[str, str] = Depends(current_user)) -> dict[str, list[str]]:
    return agent_service.tool_capabilities(user["id"])


@router.get("/tools/{tool_name}", response_model=dict)
def tool_manifest(tool_name: str, user: dict[str, str] = Depends(current_user)) -> dict:
    try:
        return agent_service.tool_manifest(user["id"], tool_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/model-settings", response_model=ModelSettings)
def model_settings(user: dict[str, str] = Depends(current_user)) -> ModelSettings:
    return ModelSettings(**agent_service.model_settings(user["id"]))


@router.put("/model-settings", response_model=ModelSettings)
def update_model_settings(
    payload: ModelSettingsRequest,
    user: dict[str, str] = Depends(current_user),
) -> ModelSettings:
    try:
        settings = agent_service.update_model_settings(
            user_id=user["id"],
            provider=payload.provider.strip().lower(),
            model_name=payload.model_name.strip(),
            ollama_url=payload.ollama_url.strip() if payload.ollama_url else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ModelSettings(**settings)


@router.get("/bootstrap", response_model=BootstrapResponse)
def bootstrap(
    thread_id: str | None = Query(default=None),
    user: dict[str, str] = Depends(current_user),
) -> BootstrapResponse:
    try:
        return BootstrapResponse(**agent_service.bootstrap(user["id"], thread_id=thread_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, user: dict[str, str] = Depends(current_user)) -> ChatResponse:
    try:
        result = agent_service.chat(payload.message, user["id"], thread_id=payload.thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.user_message) from exc
    return ChatResponse(**result)


@router.post("/chat/stream")
def chat_stream(payload: ChatRequest, user: dict[str, str] = Depends(current_user)) -> StreamingResponse:
    try:
        thread_id, events = agent_service.chat_stream(
            payload.message,
            user["id"],
            thread_id=payload.thread_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def ndjson_stream():
        try:
            for event in events:
                yield json.dumps({**event, "thread_id": thread_id}, ensure_ascii=False) + "\n"
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                close()

    return StreamingResponse(
        ndjson_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chats/{thread_id}/context-trace", response_model=ContextTraceResponse)
def context_trace(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ContextTraceResponse:
    try:
        trace = agent_service.context_trace(user["id"], thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContextTraceResponse(**trace)


@router.get("/chats/{thread_id}/compactions", response_model=list[ContextCompactionItem])
def context_compactions(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> list[ContextCompactionItem]:
    try:
        items = agent_service.context_compactions(user["id"], thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ContextCompactionItem(**item) for item in items]


@router.get(
    "/chats/{thread_id}/compactions/{compaction_id}",
    response_model=ContextCompactionReviewResponse,
)
def context_compaction_review(
    thread_id: str,
    compaction_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ContextCompactionReviewResponse:
    try:
        result = agent_service.context_compaction_review(user["id"], thread_id, compaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContextCompactionReviewResponse(**result)


@router.post(
    "/chats/{thread_id}/compactions/{compaction_id}/verify",
    response_model=ContextCompactionItem,
)
def verify_context_compaction(
    thread_id: str,
    compaction_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ContextCompactionItem:
    try:
        result = agent_service.verify_context_compaction(user["id"], thread_id, compaction_id)
    except CompactionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContextCompactionItem(**result)


@router.post(
    "/chats/{thread_id}/compactions/{compaction_id}/rollback",
    response_model=ContextCompactionItem,
)
def rollback_context_compaction(
    thread_id: str,
    compaction_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ContextCompactionItem:
    try:
        result = agent_service.rollback_context_compaction(user["id"], thread_id, compaction_id)
    except CompactionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContextCompactionItem(**result)


@router.get("/chats", response_model=list[ChatThreadItem])
def list_chats(
    status: str = Query(default="active"),
    project_id: str | None = Query(default=None),
    unassigned: bool = Query(default=False),
    user: dict[str, str] = Depends(current_user),
) -> list[ChatThreadItem]:
    return [
        ChatThreadItem(**item)
        for item in agent_service.list_chat_threads(
            user["id"],
            status=status,
            project_id=project_id,
            unassigned=unassigned,
        )
    ]


@router.post("/chats", response_model=ChatThreadActionResponse)
def create_chat_thread(
    payload: ChatThreadCreateRequest,
    user: dict[str, str] = Depends(current_user),
) -> ChatThreadActionResponse:
    try:
        thread = agent_service.create_chat_thread(
            user["id"],
            title=payload.title,
            project_id=payload.project_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatThreadActionResponse(thread=ChatThreadItem(**thread))


@router.patch("/chats/{thread_id}", response_model=ChatThreadActionResponse)
def update_chat_thread(
    thread_id: str,
    payload: ChatThreadUpdateRequest,
    user: dict[str, str] = Depends(current_user),
) -> ChatThreadActionResponse:
    try:
        thread = agent_service.update_chat_thread(
            user["id"],
            thread_id,
            title=payload.title,
            pinned=payload.pinned,
            project_id=payload.project_id,
            clear_project=payload.clear_project,
            memory_enabled=payload.memory_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatThreadActionResponse(thread=ChatThreadItem(**thread))


@router.post("/chats/{thread_id}/archive", response_model=ChatThreadActionResponse)
def archive_chat_thread(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ChatThreadActionResponse:
    try:
        thread = agent_service.archive_chat_thread(user["id"], thread_id, archived=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatThreadActionResponse(thread=ChatThreadItem(**thread))


@router.post("/chats/{thread_id}/unarchive", response_model=ChatThreadActionResponse)
def unarchive_chat_thread(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ChatThreadActionResponse:
    try:
        thread = agent_service.archive_chat_thread(user["id"], thread_id, archived=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatThreadActionResponse(thread=ChatThreadItem(**thread))


@router.post("/chats/{thread_id}/restore", response_model=ChatThreadActionResponse)
def restore_chat_thread(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ChatThreadActionResponse:
    try:
        thread = agent_service.restore_chat_thread(user["id"], thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatThreadActionResponse(thread=ChatThreadItem(**thread))


@router.delete("/chats/{thread_id}", response_model=ChatThreadActionResponse)
def delete_chat_thread(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ChatThreadActionResponse:
    try:
        thread = agent_service.delete_chat_thread(user["id"], thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatThreadActionResponse(thread=ChatThreadItem(**thread))


@router.delete("/chats/{thread_id}/messages", response_model=ClearChatResponse)
def clear_chat_messages(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ClearChatResponse:
    try:
        deleted_messages = agent_service.clear_chat(user["id"], thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ClearChatResponse(deleted_messages=deleted_messages)


@router.post("/chats/{thread_id}/remember", response_model=ChatMemoryResponse)
def remember_chat(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ChatMemoryResponse:
    try:
        result = agent_service.remember_chat(user["id"], thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatMemoryResponse(result=result["result"], thread=ChatThreadItem(**result["thread"]))


@router.get("/memory", response_model=MemoryOverviewResponse)
def memory_overview(user: dict[str, str] = Depends(current_user)) -> MemoryOverviewResponse:
    return MemoryOverviewResponse(**agent_service.memory_overview(user["id"]))


@router.get("/memory/settings", response_model=AutonomousMemorySettings)
def memory_settings(user: dict[str, str] = Depends(current_user)) -> AutonomousMemorySettings:
    return AutonomousMemorySettings(**agent_service.memory_settings(user["id"]))


@router.put("/memory/settings", response_model=AutonomousMemorySettings)
def update_memory_settings(
    payload: AutonomousMemorySettingsRequest,
    user: dict[str, str] = Depends(current_user),
) -> AutonomousMemorySettings:
    return AutonomousMemorySettings(
        **agent_service.update_memory_settings(user["id"], **payload.model_dump())
    )


@router.get("/memory/summary", response_model=MemorySummaryResponse)
def memory_summary(user: dict[str, str] = Depends(current_user)) -> MemorySummaryResponse:
    return MemorySummaryResponse(**agent_service.memory_summary(user["id"]))


@router.post("/memory/summary/rebuild", response_model=MemorySummaryResponse)
def rebuild_memory_summary(
    user: dict[str, str] = Depends(current_user),
) -> MemorySummaryResponse:
    return MemorySummaryResponse(**agent_service.memory_summary(user["id"], rebuild=True))


@router.get("/memory/records", response_model=list[PersonalMemoryItem])
def list_personal_memory(
    include_sensitive: bool = Query(default=False),
    include_inactive: bool = Query(default=False),
    user: dict[str, str] = Depends(current_user),
) -> list[PersonalMemoryItem]:
    return [
        PersonalMemoryItem(**item)
        for item in agent_service.list_personal_memory(
            user["id"],
            include_sensitive=include_sensitive,
            include_inactive=include_inactive,
        )
    ]


@router.get(
    "/chats/{thread_id}/memory-retrieval-trace",
    response_model=MemoryRetrievalTraceResponse,
)
def memory_retrieval_trace(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> MemoryRetrievalTraceResponse:
    try:
        trace = agent_service.memory_retrieval_trace(user["id"], thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryRetrievalTraceResponse(**trace)


@router.get(
    "/chats/{thread_id}/memory-extraction-trace",
    response_model=MemoryExtractionTraceResponse,
)
def memory_extraction_trace(
    thread_id: str,
    user: dict[str, str] = Depends(current_user),
) -> MemoryExtractionTraceResponse:
    try:
        trace = agent_service.memory_extraction_trace(user["id"], thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryExtractionTraceResponse(**trace)


@router.post("/memory/records", response_model=PersonalMemoryItem)
def create_personal_memory(
    payload: PersonalMemoryCreateRequest,
    user: dict[str, str] = Depends(current_user),
) -> PersonalMemoryItem:
    try:
        record = agent_service.create_personal_memory(user["id"], **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PersonalMemoryItem(**record)


@router.patch("/memory/records/{memory_id}", response_model=PersonalMemoryItem)
def update_personal_memory(
    memory_id: str,
    payload: PersonalMemoryUpdateRequest,
    sensitive: bool = Query(default=False),
    user: dict[str, str] = Depends(current_user),
) -> PersonalMemoryItem:
    try:
        record = agent_service.update_personal_memory(
            user["id"], memory_id, sensitive=sensitive, **payload.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Запись памяти не найдена.")
    return PersonalMemoryItem(**record)


@router.delete("/memory/records/{memory_id}", status_code=204)
def delete_personal_memory(
    memory_id: str,
    sensitive: bool = Query(default=False),
    user: dict[str, str] = Depends(current_user),
) -> Response:
    deleted = agent_service.delete_personal_memory(user["id"], memory_id, sensitive=sensitive)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Запись памяти не найдена.")
    return Response(status_code=204)


@router.get("/memory/export")
def export_memory(
    include_sensitive: bool = Query(default=False),
    user: dict[str, str] = Depends(current_user),
) -> Response:
    payload = agent_service.export_memory(user["id"], include_sensitive=include_sensitive)
    filename = f"avelin-memory-{user['id']}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/memory/notes/{note_id}", response_model=ClearMemoryResponse)
def delete_memory_note(
    note_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ClearMemoryResponse:
    try:
        deleted = agent_service.delete_memory_note(user["id"], note_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ClearMemoryResponse(deleted_notes=deleted)


@router.post("/memory/clear", response_model=ClearMemoryResponse)
def clear_memory(
    payload: ClearMemoryRequest,
    user: dict[str, str] = Depends(current_user),
) -> ClearMemoryResponse:
    result = agent_service.clear_memory(
        user["id"],
        scope=payload.scope,
        thread_id=payload.thread_id,
        project_id=payload.project_id,
        include_action_logs=payload.include_action_logs,
    )
    return ClearMemoryResponse(**result)


@router.get("/projects", response_model=list[ProjectItem])
def list_projects(
    status: str = Query(default="active"),
    user: dict[str, str] = Depends(current_user),
) -> list[ProjectItem]:
    return [ProjectItem(**item) for item in agent_service.list_projects(user["id"], status=status)]


@router.post("/projects", response_model=ProjectActionResponse)
def create_project(
    payload: ProjectCreateRequest,
    user: dict[str, str] = Depends(current_user),
) -> ProjectActionResponse:
    project = agent_service.create_project(
        user["id"],
        title=payload.title,
        description=payload.description,
    )
    return ProjectActionResponse(project=ProjectItem(**project))


@router.patch("/projects/{project_id}", response_model=ProjectActionResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    user: dict[str, str] = Depends(current_user),
) -> ProjectActionResponse:
    try:
        project = agent_service.update_project(
            user["id"],
            project_id,
            title=payload.title,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectActionResponse(project=ProjectItem(**project))


@router.post("/projects/{project_id}/archive", response_model=ProjectActionResponse)
def archive_project(
    project_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ProjectActionResponse:
    try:
        project = agent_service.update_project(user["id"], project_id, status="archived")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectActionResponse(project=ProjectItem(**project))


@router.post("/projects/{project_id}/restore", response_model=ProjectActionResponse)
def restore_project(
    project_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ProjectActionResponse:
    try:
        project = agent_service.update_project(user["id"], project_id, status="active")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectActionResponse(project=ProjectItem(**project))


@router.delete("/projects/{project_id}", response_model=ProjectActionResponse)
def delete_project(
    project_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ProjectActionResponse:
    try:
        project = agent_service.update_project(user["id"], project_id, status="deleted")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectActionResponse(project=ProjectItem(**project))


@router.post("/tasks/execute-next", response_model=ExecuteStepResponse)
def execute_next_step(user: dict[str, str] = Depends(current_user)) -> ExecuteStepResponse:
    return ExecuteStepResponse(result=agent_service.execute_next_step(user["id"]))


@router.get("/automations", response_model=list[AutomationItem])
def automations(user: dict[str, str] = Depends(current_user)) -> list[AutomationItem]:
    return [AutomationItem(**item) for item in agent_service.automations(user["id"])]


@router.post("/automations", response_model=AutomationItem)
def create_automation(
    payload: AutomationCreateRequest,
    user: dict[str, str] = Depends(current_user),
) -> AutomationItem:
    try:
        return AutomationItem(
            **agent_service.create_automation(user["id"], payload.model_dump())
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/automations/{automation_id}", response_model=AutomationItem)
def update_automation(
    automation_id: str,
    payload: AutomationUpdateRequest,
    user: dict[str, str] = Depends(current_user),
) -> AutomationItem:
    try:
        return AutomationItem(
            **agent_service.update_automation(
                user["id"], automation_id, payload.model_dump(exclude_none=True)
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/automations/{automation_id}/{action}", response_model=AutomationItem)
def control_automation(
    automation_id: str,
    action: str,
    user: dict[str, str] = Depends(current_user),
) -> AutomationItem:
    if action not in {"pause", "resume"}:
        raise HTTPException(status_code=404, detail="Неизвестное действие автоматизации.")
    try:
        return AutomationItem(
            **agent_service.control_automation(user["id"], automation_id, action)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/automations/{automation_id}", response_model=AutomationItem)
def delete_automation(
    automation_id: str,
    user: dict[str, str] = Depends(current_user),
) -> AutomationItem:
    try:
        return AutomationItem(
            **agent_service.control_automation(user["id"], automation_id, "delete")
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/automations/{automation_id}/runs", response_model=list[AutomationRunItem])
def automation_runs(
    automation_id: str,
    user: dict[str, str] = Depends(current_user),
) -> list[AutomationRunItem]:
    try:
        return [
            AutomationRunItem(**item)
            for item in agent_service.automation_runs(user["id"], automation_id)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/automations/tick", response_model=list[AutomationOccurrenceItem])
def tick_automations(user: dict[str, str] = Depends(current_user)) -> list[AutomationOccurrenceItem]:
    return [
        AutomationOccurrenceItem(**item) for item in agent_service.tick_automations(user["id"])
    ]


@router.get("/task-runs", response_model=list[TaskRunItem])
def task_runs(user: dict[str, str] = Depends(current_user)) -> list[TaskRunItem]:
    return [TaskRunItem(**item) for item in agent_service.task_runs(user["id"])]


@router.get("/task-runs/{run_id}/checkpoints", response_model=list[TaskCheckpointItem])
def task_checkpoints(
    run_id: str,
    user: dict[str, str] = Depends(current_user),
) -> list[TaskCheckpointItem]:
    try:
        return [
            TaskCheckpointItem(**item)
            for item in agent_service.task_checkpoints(user["id"], run_id)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/task-runs/{run_id}/report", response_model=TaskReportItem)
def create_run_report(
    run_id: str,
    user: dict[str, str] = Depends(current_user),
) -> TaskReportItem:
    try:
        return TaskReportItem(**agent_service.run_report(user["id"], run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/task-reports", response_model=list[TaskReportItem])
def task_reports(
    limit: int = Query(default=30, ge=1, le=100),
    user: dict[str, str] = Depends(current_user),
) -> list[TaskReportItem]:
    return [TaskReportItem(**item) for item in agent_service.task_reports(user["id"], limit)]


@router.post("/task-reports/daily", response_model=TaskReportItem)
def create_daily_task_report(
    report_date: str | None = Query(default=None, alias="date"),
    user: dict[str, str] = Depends(current_user),
) -> TaskReportItem:
    try:
        return TaskReportItem(**agent_service.daily_task_report(user["id"], report_date))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/notifications", response_model=list[InAppNotificationItem])
def notifications(
    unread_only: bool = Query(default=False),
    user: dict[str, str] = Depends(current_user),
) -> list[InAppNotificationItem]:
    return [
        InAppNotificationItem(**item)
        for item in agent_service.notifications(user["id"], unread_only)
    ]


@router.post("/notifications/{notification_id}/read", response_model=InAppNotificationItem)
def mark_notification_read(
    notification_id: str,
    user: dict[str, str] = Depends(current_user),
) -> InAppNotificationItem:
    try:
        return InAppNotificationItem(
            **agent_service.mark_notification_read(user["id"], notification_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/{action}", response_model=TaskControlResponse)
def control_task(
    task_id: str,
    action: str,
    user: dict[str, str] = Depends(current_user),
) -> TaskControlResponse:
    try:
        return TaskControlResponse(**agent_service.control_task(user["id"], task_id, action))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/steps/{step_id}/approve", response_model=ApproveStepResponse)
def approve_blocked_step(
    step_id: str,
    user: dict[str, str] = Depends(current_user),
) -> ApproveStepResponse:
    try:
        result = agent_service.approve_blocked_step(user["id"], step_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApproveStepResponse(result=result)


@router.get("/approvals", response_model=list[ApprovalRequestItem])
def approval_requests(
    status: str | None = Query(default="pending"),
    user: dict[str, str] = Depends(current_user),
) -> list[ApprovalRequestItem]:
    return [
        ApprovalRequestItem(**item)
        for item in agent_service.approval_requests(user["id"], status=status)
    ]


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalRequestItem)
def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    user: dict[str, str] = Depends(current_user),
) -> ApprovalRequestItem:
    try:
        return ApprovalRequestItem(
            **agent_service.decide_approval(user["id"], approval_id, payload.decision)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/security", response_model=SecuritySettingsResponse)
def security_settings(user: dict[str, str] = Depends(current_user)) -> SecuritySettingsResponse:
    return SecuritySettingsResponse(**agent_service.security_settings(user["id"]))


@router.put("/security/emergency-stop", response_model=SecuritySettingsResponse)
def set_emergency_stop(
    payload: EmergencyStopRequest,
    user: dict[str, str] = Depends(current_user),
) -> SecuritySettingsResponse:
    return SecuritySettingsResponse(
        **agent_service.set_emergency_stop(user["id"], payload.enabled)
    )


@router.get("/permission-grants", response_model=list[PermissionGrantItem])
def permission_grants(user: dict[str, str] = Depends(current_user)) -> list[PermissionGrantItem]:
    return [PermissionGrantItem(**item) for item in agent_service.permission_grants(user["id"])]


@router.post("/permission-grants", response_model=PermissionGrantItem)
def create_permission_grant(
    payload: PermissionGrantCreateRequest,
    user: dict[str, str] = Depends(current_user),
) -> PermissionGrantItem:
    return PermissionGrantItem(
        **agent_service.create_permission_grant(user["id"], **payload.model_dump())
    )


@router.delete("/permission-grants/{grant_id}", response_model=PermissionGrantItem)
def revoke_permission_grant(
    grant_id: str,
    user: dict[str, str] = Depends(current_user),
) -> PermissionGrantItem:
    try:
        return PermissionGrantItem(**agent_service.revoke_permission_grant(user["id"], grant_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workspace/open", response_model=OpenWorkspaceResponse)
def open_workspace_folder(user: dict[str, str] = Depends(current_user)) -> OpenWorkspaceResponse:
    try:
        result = agent_service.open_workspace_folder(user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OpenWorkspaceResponse(result=result)


@router.get("/workspace/files/{file_path:path}")
def download_workspace_file(
    file_path: str,
    user: dict[str, str] = Depends(current_user),
) -> FileResponse:
    try:
        path = agent_service.workspace_file_path(user["id"], file_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")

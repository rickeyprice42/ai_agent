from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from backend.auth import hash_password, hash_token, issue_session_token, verify_password
from backend.models import (
    ApproveStepResponse,
    AuthRequest,
    AuthResponse,
    BootstrapResponse,
    ChatThreadActionResponse,
    ChatThreadCreateRequest,
    ChatThreadItem,
    ChatThreadUpdateRequest,
    ClearChatResponse,
    ChatRequest,
    ChatResponse,
    ChatMemoryResponse,
    ExecuteStepResponse,
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
    UserProfile,
)
from backend.service import agent_service


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
    return ChatResponse(**result)


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


@router.post("/workspace/open", response_model=OpenWorkspaceResponse)
def open_workspace_folder(user: dict[str, str] = Depends(current_user)) -> OpenWorkspaceResponse:
    try:
        result = agent_service.open_workspace_folder(user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OpenWorkspaceResponse(result=result)

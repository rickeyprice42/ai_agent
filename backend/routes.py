from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException

from backend.auth import hash_password, hash_token, issue_session_token, verify_password
from backend.models import (
    AuthRequest,
    AuthResponse,
    BootstrapResponse,
    ChatRequest,
    ChatResponse,
    ModelProviderOption,
    ModelSettings,
    ModelSettingsRequest,
    OAuthProviderResponse,
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
def bootstrap(user: dict[str, str] = Depends(current_user)) -> BootstrapResponse:
    return BootstrapResponse(**agent_service.bootstrap(user["id"]))


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, user: dict[str, str] = Depends(current_user)) -> ChatResponse:
    return ChatResponse(reply=agent_service.chat(payload.message, user["id"]))

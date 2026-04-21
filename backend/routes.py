from __future__ import annotations

from fastapi import APIRouter

from backend.models import BootstrapResponse, ChatRequest, ChatResponse
from backend.service import agent_service


router = APIRouter(prefix="/api")


@router.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/bootstrap", response_model=BootstrapResponse)
def bootstrap() -> BootstrapResponse:
    return BootstrapResponse(**agent_service.bootstrap())


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    return ChatResponse(reply=agent_service.chat(payload.message))

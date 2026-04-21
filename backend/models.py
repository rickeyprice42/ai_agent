from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    reply: str


class HistoryItem(BaseModel):
    role: str
    content: str
    name: str | None = None


class BootstrapResponse(BaseModel):
    agent_name: str
    provider: str
    model: str
    notes: list[str]
    history: list[HistoryItem]

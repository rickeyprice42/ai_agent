from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    reply: str


class ExecuteStepResponse(BaseModel):
    result: str


class UserProfile(BaseModel):
    id: str
    email: str = ""
    username: str = ""
    display_name: str
    auth_provider: str = "password"


class AuthRequest(BaseModel):
    login: str = Field(..., min_length=2, max_length=120)
    password: str = Field(..., min_length=8, max_length=256)


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=160)
    username: str = Field(..., min_length=2, max_length=80)
    password: str = Field(..., min_length=8, max_length=256)
    display_name: str = Field(..., min_length=2, max_length=120)


class AuthResponse(BaseModel):
    token: str
    user: UserProfile


class OAuthProviderResponse(BaseModel):
    provider: str
    enabled: bool
    auth_url: str | None = None
    message: str


class ModelProviderOption(BaseModel):
    provider: str
    label: str
    description: str
    models: list[str]


class ModelSettings(BaseModel):
    provider: str
    model_name: str
    ollama_url: str = ""


class ModelSettingsRequest(BaseModel):
    provider: str = Field(..., min_length=2, max_length=40)
    model_name: str = Field(..., min_length=2, max_length=120)
    ollama_url: str | None = Field(default=None, max_length=300)


class HistoryItem(BaseModel):
    role: str
    content: str
    name: str | None = None


class TaskStepItem(BaseModel):
    id: str
    task_id: str
    description: str
    status: str
    position: int
    result: str | None = None


class TaskItem(BaseModel):
    id: str
    user_id: str
    description: str
    status: str
    priority: int
    result: str | None = None
    steps: list[TaskStepItem] = Field(default_factory=list)


class ActionLogItem(BaseModel):
    id: str
    user_id: str
    tool_name: str
    status: str
    arguments: dict = Field(default_factory=dict)
    result: str
    created_at: str


class BootstrapResponse(BaseModel):
    agent_name: str
    provider: str
    model: str
    notes: list[str]
    history: list[HistoryItem]
    tasks: list[TaskItem]
    action_logs: list[ActionLogItem]
    user: UserProfile

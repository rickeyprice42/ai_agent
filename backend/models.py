from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, min_length=1, max_length=120)


class ChatResponse(BaseModel):
    reply: str
    thread_id: str


class ExecuteStepResponse(BaseModel):
    result: str


class ApproveStepResponse(BaseModel):
    result: str


class OpenWorkspaceResponse(BaseModel):
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


class ChatThreadItem(BaseModel):
    id: str
    user_id: str
    title: str
    status: str
    archived_at: str | None = None
    deleted_at: str | None = None
    pinned: bool = False
    project_id: str | None = None
    memory_enabled: bool = True
    memory_saved_at: str | None = None
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message_at: str | None = None


class ChatThreadCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    project_id: str | None = Field(default=None, max_length=120)


class ChatThreadUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None
    project_id: str | None = Field(default=None, max_length=120)
    clear_project: bool = False
    memory_enabled: bool | None = None


class ChatThreadActionResponse(BaseModel):
    thread: ChatThreadItem


class ClearChatResponse(BaseModel):
    deleted_messages: int


class ChatMemoryResponse(BaseModel):
    result: str
    thread: ChatThreadItem


class ProjectItem(BaseModel):
    id: str
    user_id: str
    title: str
    description: str = ""
    status: str
    created_at: str
    updated_at: str
    chat_count: int = 0


class ProjectCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class ProjectUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class ProjectActionResponse(BaseModel):
    project: ProjectItem


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


class WorkspaceFileItem(BaseModel):
    path: str
    name: str
    extension: str
    size_bytes: int
    modified_at: str


class BootstrapResponse(BaseModel):
    agent_name: str
    provider: str
    model: str
    notes: list[str]
    history: list[HistoryItem]
    active_thread: ChatThreadItem
    chat_threads: list[ChatThreadItem]
    archived_chat_threads: list[ChatThreadItem]
    deleted_chat_threads: list[ChatThreadItem]
    projects: list[ProjectItem]
    archived_projects: list[ProjectItem]
    deleted_projects: list[ProjectItem]
    active_project: ProjectItem | None = None
    tasks: list[TaskItem]
    action_logs: list[ActionLogItem]
    workspace_files: list[WorkspaceFileItem]
    user: UserProfile

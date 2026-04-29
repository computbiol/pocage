from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


SessionStatus = Literal[
    "idle",
    "queued",
    "assigned",
    "running",
    "completed",
    "cancelled",
    "failed",
    "executor_disconnected",
]
RunStatus = Literal[
    "queued",
    "assigned",
    "running",
    "completed",
    "cancelled",
    "failed",
    "executor_disconnected",
]
MessageRole = Literal["user", "assistant"]
MessageStatus = Literal["completed", "streaming", "error", "cancelled"]
PermissionStatus = Literal["pending", "selected", "cancelled"]
PermissionDecision = Literal["selected", "cancelled"]
AgentName = Literal["codex"]


class PromptTextItem(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1)


class PromptImageItem(BaseModel):
    type: Literal["image"]
    image_url: str = Field(min_length=1)
    name: str | None = None
    mime_type: str | None = None
    size: int | None = Field(default=None, ge=0)


class PromptResourceLinkItem(BaseModel):
    type: Literal["resource_link"]
    uri: str = Field(min_length=1)
    name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    kind: Literal["file", "directory"]


PromptItem = Annotated[
    PromptTextItem | PromptImageItem | PromptResourceLinkItem,
    Field(discriminator="type"),
]


class McpServerHttp(BaseModel):
    kind: Literal["http"]
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)


class McpServerStdio(BaseModel):
    kind: Literal["stdio"]
    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


McpServer = Annotated[McpServerHttp | McpServerStdio, Field(discriminator="kind")]


class SessionSummary(BaseModel):
    session_id: str
    title: str
    cwd: str
    agent_instance_id: str | None
    status: SessionStatus
    mcp_servers: list[McpServer] = Field(default_factory=list)
    remote_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionSummary):
    executor_id: str | None


class CreateSessionRequest(BaseModel):
    cwd: str | None = None
    agent_instance_id: str = Field(min_length=1)
    title: str | None = None
    mcp_servers: list[McpServer] = Field(default_factory=list)


class CreateSessionResponse(BaseModel):
    session_id: str
    title: str
    cwd: str
    agent_instance_id: str
    mcp_servers: list[McpServer] = Field(default_factory=list)
    status: SessionStatus
    created_at: datetime


class ListSessionsResponse(BaseModel):
    items: list[SessionSummary]
    next_cursor: str | None = None


TranscriptSegmentKind = Literal["message", "thought", "steps"]
TranscriptItemKind = Literal["user_message", "assistant_segment", "permission_request"]


class TranscriptStepItem(BaseModel):
    step_id: str
    summary: str
    detail: str | None = None
    created_at: datetime
    session_update: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class UserTranscriptItem(BaseModel):
    kind: Literal["user_message"]
    message_id: str
    session_id: str
    run_id: str
    content: str
    items: list[PromptItem] = Field(default_factory=list)
    status: Literal["completed"] = "completed"
    created_at: datetime
    updated_at: datetime


class AssistantSegmentTranscriptItem(BaseModel):
    kind: Literal["assistant_segment"]
    segment_id: str
    session_id: str
    run_id: str
    segment_kind: TranscriptSegmentKind
    content: str
    status: MessageStatus
    steps: list[TranscriptStepItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PermissionRequestTranscriptItem(BaseModel):
    kind: Literal["permission_request"]
    approval_id: str
    run_id: str
    session_id: str
    status: PermissionStatus
    tool_call: dict[str, Any] = Field(default_factory=dict)
    options: list[dict[str, Any]] = Field(default_factory=list)
    decision: PermissionDecision | None = None
    option_id: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


TranscriptItem = Annotated[
    UserTranscriptItem | AssistantSegmentTranscriptItem | PermissionRequestTranscriptItem,
    Field(discriminator="kind"),
]


class GetTranscriptResponse(BaseModel):
    session: SessionDetail
    items: list[TranscriptItem]


class SendMessageRequest(BaseModel):
    content: str = ""
    items: list[PromptItem] = Field(default_factory=list)
    client_msg_id: str | None = None

    @model_validator(mode="after")
    def validate_non_empty_prompt(self) -> "SendMessageRequest":
        if self.content.strip() or self.items:
            return self
        raise ValueError("either content or items must be provided")


class SendMessageResponse(BaseModel):
    run_id: str
    user_message_id: str
    assistant_message_id: str
    stream_url: str


class ContextSearchItem(BaseModel):
    kind: Literal["file", "directory"]
    name: str
    relative_path: str
    uri: str


class ContextSearchResponse(BaseModel):
    items: list[ContextSearchItem]


class RemoteSessionItem(BaseModel):
    session_id: str
    cwd: str
    title: str
    updated_at: datetime


class SessionCreateRequestPayload(BaseModel):
    type: Literal["session.create.request"] = "session.create.request"
    request_id: str
    cwd: str
    mcp_servers: list[McpServer] = Field(default_factory=list)


class SessionCreateResultPayload(BaseModel):
    type: Literal["session.create.result"]
    request_id: str
    session: RemoteSessionItem | None = None
    error_message: str | None = None


class SessionListRequestPayload(BaseModel):
    type: Literal["session.list.request"] = "session.list.request"
    request_id: str
    cursor: str | None = None


class SessionListResultPayload(BaseModel):
    type: Literal["session.list.result"]
    request_id: str
    items: list[RemoteSessionItem] = Field(default_factory=list)
    next_cursor: str | None = None
    error_message: str | None = None


class SessionSyncRequestPayload(BaseModel):
    type: Literal["session.sync.request"] = "session.sync.request"
    request_id: str
    session_id: str
    cwd: str
    mcp_servers: list[McpServer] = Field(default_factory=list)


class SessionSyncResultPayload(BaseModel):
    type: Literal["session.sync.result"]
    request_id: str
    session_id: str
    title: str | None = None
    updated_at: datetime | None = None
    session_updates: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None


class RunEventItem(BaseModel):
    event_id: str
    run_id: str
    seq: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ListRunEventsResponse(BaseModel):
    items: list[RunEventItem]


class CancelRunResponse(BaseModel):
    status: Literal["cancelled", "cancelling", "not_found"]


class DaemonHello(BaseModel):
    type: Literal["daemon.hello"]
    machine_id: str = Field(min_length=1)
    agent_instance_id: str = Field(min_length=1)
    daemon_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str
    agent: AgentName
    hostname: str | None = None
    workspace_roots: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class DaemonWelcome(BaseModel):
    type: Literal["daemon.welcome"] = "daemon.welcome"
    machine_id: str
    agent_instance_id: str
    daemon_id: str


class RunAssignPayload(BaseModel):
    type: Literal["run.assign"] = "run.assign"
    job_id: str
    run_id: str
    session_id: str
    cwd: str
    content: str = ""
    prompt_items: list[PromptItem] = Field(default_factory=list)
    mcp_servers: list[McpServer] = Field(default_factory=list)


class ContextSearchRequestPayload(BaseModel):
    type: Literal["context.search.request"] = "context.search.request"
    request_id: str
    cwd: str
    query: str
    limit: int = Field(default=20, ge=1, le=50)


class ContextSearchResultPayload(BaseModel):
    type: Literal["context.search.result"]
    request_id: str
    items: list[ContextSearchItem] = Field(default_factory=list)
    error_message: str | None = None


class RunCancelPayload(BaseModel):
    type: Literal["run.cancel"] = "run.cancel"
    run_id: str


class RunAcceptedEvent(BaseModel):
    type: Literal["run.accepted"]
    run_id: str
    session_id: str
    job_id: str


class RunSessionUpdateEvent(BaseModel):
    type: Literal["run.session_update"]
    run_id: str
    update: dict[str, Any]


class RunPermissionRequestedEvent(BaseModel):
    type: Literal["run.permission.requested"]
    run_id: str
    approval_id: str
    session_id: str
    tool_call: dict[str, Any] = Field(default_factory=dict)
    options: list[dict[str, Any]] = Field(default_factory=list)


class RunPermissionDecisionPayload(BaseModel):
    type: Literal["run.permission.decision"] = "run.permission.decision"
    run_id: str
    approval_id: str
    decision: PermissionDecision
    option_id: str | None = None


class RunCompletedEvent(BaseModel):
    type: Literal["run.completed"]
    run_id: str
    session_id: str
    stop_reason: str


class RunFailedEvent(BaseModel):
    type: Literal["run.failed"]
    run_id: str
    error_code: str
    error_message: str


class HeartbeatEvent(BaseModel):
    type: Literal["heartbeat"]


class PermissionRequestItem(BaseModel):
    approval_id: str
    run_id: str
    session_id: str
    status: PermissionStatus
    tool_call: dict[str, Any] = Field(default_factory=dict)
    options: list[dict[str, Any]] = Field(default_factory=list)
    decision: PermissionDecision | None = None
    option_id: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ListPermissionsResponse(BaseModel):
    items: list[PermissionRequestItem]


class PermissionDecisionRequest(BaseModel):
    decision: PermissionDecision
    option_id: str | None = None

    @model_validator(mode="after")
    def validate_selected_requires_option(self) -> "PermissionDecisionRequest":
        if self.decision == "selected" and not self.option_id:
            raise ValueError("option_id is required when decision is 'selected'")
        return self


class PermissionDecisionResponse(PermissionRequestItem):
    pass

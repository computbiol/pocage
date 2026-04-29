from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AgentName = Literal["codex"]


class PairingCodeCreateRequest(BaseModel):
    agent: AgentName = "codex"
    display_name: str | None = Field(default=None, max_length=255)
    ttl_minutes: int = Field(default=10, ge=1, le=60)


class PairingCodeCreateResponse(BaseModel):
    pairing_code: str
    agent: AgentName
    expires_at: datetime


class MachineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str | None
    hostname: str | None
    platform: str | None
    arch: str | None
    last_seen_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class AgentInstanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    machine_id: uuid.UUID
    agent: AgentName
    display_name: str | None
    hostname: str | None
    executor_name: str | None
    version: str | None
    status: str
    workspace_roots: list[str]
    last_seen_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class SessionPointerRead(BaseModel):
    id: uuid.UUID
    agent_instance_id: uuid.UUID
    agent: AgentName
    display_name: str | None
    hostname: str | None
    executor_name: str | None
    agent_instance_status: str
    remote_session_id: str
    title_hint: str | None
    status: str
    last_seen_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class DaemonPairRequest(BaseModel):
    pairing_code: str = Field(min_length=1)
    agent: AgentName = "codex"
    executor_name: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    hostname: str | None = Field(default=None, max_length=255)
    platform: str | None = Field(default=None, max_length=64)
    arch: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=64)
    workspace_roots: list[str] = Field(default_factory=list)


class DaemonPairResponse(BaseModel):
    machine_id: uuid.UUID
    agent_instance_id: uuid.UUID
    daemon_id: str
    machine_token: str
    agent: AgentName


class AgentInstanceRevokeResponse(BaseModel):
    detail: str

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, Uuid, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(SQLAlchemyBaseUserTableUUID, TimestampMixin, Base):
    __tablename__ = "users"

    display_name: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user")
    api_keys: Mapped[list["APIKey"]] = relationship(back_populates="user")
    machines: Mapped[list["Machine"]] = relationship(back_populates="user")
    pairings: Mapped[list["MachinePairing"]] = relationship(back_populates="user")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="user")


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    refresh_token_hash: Mapped[str] = mapped_column(String(length=128), nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(length=512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(length=64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class APIKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(length=255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(length=24), nullable=False, index=True)
    secret_hash: Mapped[str] = mapped_column(String(length=128), nullable=False, unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship(back_populates="api_keys")


class Machine(TimestampMixin, Base):
    __tablename__ = "machines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(length=64), nullable=True)
    arch: Mapped[str | None] = mapped_column(String(length=64), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="machines")
    agent_instances: Mapped[list["AgentInstance"]] = relationship(back_populates="machine")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="machine")


class AgentInstance(TimestampMixin, Base):
    __tablename__ = "agent_instances"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(length=64), nullable=False)
    daemon_id: Mapped[str] = mapped_column(String(length=255), nullable=False, index=True)
    executor_name: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    version: Mapped[str | None] = mapped_column(String(length=64), nullable=True)
    status: Mapped[str] = mapped_column(String(length=32), nullable=False, default="paired")
    workspace_roots: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    machine: Mapped[Machine] = relationship(back_populates="agent_instances")
    credentials: Mapped[list["MachineCredential"]] = relationship(back_populates="agent_instance")
    session_pointers: Mapped[list["SessionPointer"]] = relationship(back_populates="agent_instance")
    run_headers: Mapped[list["RunHeader"]] = relationship(back_populates="agent_instance")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="agent_instance")


class MachinePairing(TimestampMixin, Base):
    __tablename__ = "machine_pairings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(length=128), nullable=False, unique=True)
    requested_agent: Mapped[str] = mapped_column(String(length=64), nullable=False, default="codex")
    display_name: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="pairings")


class MachineCredential(TimestampMixin, Base):
    __tablename__ = "machine_credentials"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_instances.id", ondelete="CASCADE"),
        index=True,
    )
    key_id: Mapped[str] = mapped_column(String(length=32), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(length=128), nullable=False, unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_instance: Mapped[AgentInstance] = relationship(back_populates="credentials")


class SessionPointer(TimestampMixin, Base):
    __tablename__ = "session_pointers"
    __table_args__ = (
        UniqueConstraint("agent_instance_id", "remote_session_id", name="uq_session_pointers_agent_remote"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_instances.id", ondelete="CASCADE"),
        index=True,
    )
    remote_session_id: Mapped[str] = mapped_column(String(length=255), nullable=False)
    title_hint: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    cwd_hash: Mapped[str | None] = mapped_column(String(length=128), nullable=True)
    status: Mapped[str] = mapped_column(String(length=32), nullable=False, default="idle")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_instance: Mapped[AgentInstance] = relationship(back_populates="session_pointers")
    run_headers: Mapped[list["RunHeader"]] = relationship(back_populates="session_pointer")


class RunHeader(TimestampMixin, Base):
    __tablename__ = "run_headers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str] = mapped_column(String(length=255), nullable=False, unique=True)
    session_pointer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("session_pointers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("agent_instances.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(length=32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(length=64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session_pointer: Mapped[SessionPointer | None] = relationship(back_populates="run_headers")
    agent_instance: Mapped[AgentInstance | None] = relationship(back_populates="run_headers")
    approvals: Mapped[list["ApprovalHeader"]] = relationship(back_populates="run_header")


class ApprovalHeader(TimestampMixin, Base):
    __tablename__ = "approval_headers"
    __table_args__ = (
        UniqueConstraint("run_header_id", "remote_approval_id", name="uq_approval_headers_run_remote"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_header_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("run_headers.id", ondelete="CASCADE"),
        index=True,
    )
    remote_approval_id: Mapped[str] = mapped_column(String(length=255), nullable=False)
    status: Mapped[str] = mapped_column(String(length=32), nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String(length=32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run_header: Mapped[RunHeader] = relationship(back_populates="approvals")


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("machines.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("agent_instances.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(length=128), nullable=False, index=True)
    subject_type: Mapped[str | None] = mapped_column(String(length=64), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)

    user: Mapped[User | None] = relationship(back_populates="audit_events")
    machine: Mapped[Machine | None] = relationship(back_populates="audit_events")
    agent_instance: Mapped[AgentInstance | None] = relationship(back_populates="audit_events")

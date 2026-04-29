from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth_api import get_current_user, require_csrf
from .config import get_settings
from .control_schemas import (
    AgentInstanceRead,
    AgentInstanceRevokeResponse,
    DaemonPairRequest,
    DaemonPairResponse,
    MachineRead,
    PairingCodeCreateRequest,
    PairingCodeCreateResponse,
    SessionPointerRead,
)
from .db import get_async_session
from .db_models import AgentInstance, AuditEvent, Machine, MachineCredential, MachinePairing, SessionPointer, User
from .security import ensure_utc_datetime, generate_secret_token, hash_secret, utcnow


settings = get_settings()
router = APIRouter(prefix=settings.api_prefix)
machine_router = APIRouter(prefix="/machines", tags=["machines"])
agent_router = APIRouter(prefix="/agent-instances", tags=["agent-instances"])
session_pointer_router = APIRouter(prefix="/session-pointers", tags=["session-pointers"])
daemon_router = APIRouter(prefix="/daemon", tags=["daemon"])


async def _record_audit_event(
    session: AsyncSession,
    *,
    event_type: str,
    user_id: uuid.UUID | None = None,
    machine_id: uuid.UUID | None = None,
    agent_instance_id: uuid.UUID | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    data: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            user_id=user_id,
            machine_id=machine_id,
            agent_instance_id=agent_instance_id,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            data=data or {},
        )
    )


@machine_router.get("", response_model=list[MachineRead])
async def list_machines(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[MachineRead]:
    result = await session.scalars(
        select(Machine).where(Machine.user_id == current_user.id).order_by(Machine.created_at.desc())
    )
    return [MachineRead.model_validate(item) for item in result]


@machine_router.post("/pairings", response_model=PairingCodeCreateResponse, dependencies=[Depends(require_csrf)])
async def create_pairing_code(
    payload: PairingCodeCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PairingCodeCreateResponse:
    now = utcnow()
    pairing_code = generate_secret_token("pair_")
    pairing = MachinePairing(
        user_id=current_user.id,
        code_hash=hash_secret(pairing_code),
        requested_agent=payload.agent,
        display_name=payload.display_name,
        expires_at=now + timedelta(minutes=payload.ttl_minutes),
    )
    session.add(pairing)
    await session.flush()
    await _record_audit_event(
        session,
        event_type="machine_pairing.created",
        user_id=current_user.id,
        subject_type="machine_pairing",
        subject_id=str(pairing.id),
        data={"agent": payload.agent},
    )
    await session.commit()
    return PairingCodeCreateResponse(
        pairing_code=pairing_code,
        agent=payload.agent,
        expires_at=pairing.expires_at,
    )


@agent_router.get("", response_model=list[AgentInstanceRead])
async def list_agent_instances(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[AgentInstanceRead]:
    result = await session.execute(
        select(AgentInstance, Machine)
        .select_from(AgentInstance)
        .join(Machine, AgentInstance.machine_id == Machine.id)
        .where(Machine.user_id == current_user.id)
        .order_by(AgentInstance.created_at.desc())
    )
    return [
        AgentInstanceRead(
            id=agent_instance.id,
            machine_id=agent_instance.machine_id,
            agent=agent_instance.agent,
            display_name=machine.display_name,
            hostname=machine.hostname,
            executor_name=agent_instance.executor_name,
            version=agent_instance.version,
            status=agent_instance.status,
            workspace_roots=agent_instance.workspace_roots,
            last_seen_at=agent_instance.last_seen_at,
            revoked_at=agent_instance.revoked_at,
            created_at=agent_instance.created_at,
            updated_at=agent_instance.updated_at,
        )
        for agent_instance, machine in result.all()
    ]


@agent_router.post(
    "/{agent_instance_id}/revoke",
    response_model=AgentInstanceRevokeResponse,
    dependencies=[Depends(require_csrf)],
)
async def revoke_agent_instance(
    agent_instance_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> AgentInstanceRevokeResponse:
    agent_instance = await session.scalar(
        select(AgentInstance)
        .select_from(AgentInstance)
        .join(Machine, AgentInstance.machine_id == Machine.id)
        .where(AgentInstance.id == agent_instance_id, Machine.user_id == current_user.id)
    )
    if agent_instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent instance not found.")
    now = utcnow()
    agent_instance.revoked_at = now
    agent_instance.status = "revoked"
    credentials = await session.scalars(
        select(MachineCredential).where(MachineCredential.agent_instance_id == agent_instance.id)
    )
    for credential in credentials:
        credential.revoked_at = now
    await _record_audit_event(
        session,
        event_type="agent_instance.revoked",
        user_id=current_user.id,
        machine_id=agent_instance.machine_id,
        agent_instance_id=agent_instance.id,
        subject_type="agent_instance",
        subject_id=str(agent_instance.id),
        data={"agent": agent_instance.agent},
    )
    await session.commit()
    return AgentInstanceRevokeResponse(detail="Agent instance revoked.")


@session_pointer_router.get("", response_model=list[SessionPointerRead])
async def list_session_pointers(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[SessionPointerRead]:
    result = await session.execute(
        select(SessionPointer, AgentInstance, Machine)
        .select_from(SessionPointer)
        .join(AgentInstance, SessionPointer.agent_instance_id == AgentInstance.id)
        .join(Machine, AgentInstance.machine_id == Machine.id)
        .where(Machine.user_id == current_user.id)
        .order_by(SessionPointer.updated_at.desc())
    )
    return [
        SessionPointerRead(
            id=pointer.id,
            agent_instance_id=pointer.agent_instance_id,
            agent=agent_instance.agent,
            display_name=machine.display_name,
            hostname=machine.hostname,
            executor_name=agent_instance.executor_name,
            agent_instance_status=agent_instance.status,
            remote_session_id=pointer.remote_session_id,
            title_hint=pointer.title_hint,
            status=pointer.status,
            last_seen_at=pointer.last_seen_at,
            created_at=pointer.created_at,
            updated_at=pointer.updated_at,
        )
        for pointer, agent_instance, machine in result.all()
    ]


@daemon_router.post("/pair", response_model=DaemonPairResponse)
async def pair_daemon(
    payload: DaemonPairRequest,
    session: AsyncSession = Depends(get_async_session),
) -> DaemonPairResponse:
    pairing = await session.scalar(
        select(MachinePairing).where(MachinePairing.code_hash == hash_secret(payload.pairing_code))
    )
    now = utcnow()
    if pairing is None or pairing.consumed_at is not None or ensure_utc_datetime(pairing.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Pairing code is invalid or expired.")
    if pairing.requested_agent != payload.agent:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pairing agent does not match.")

    daemon_id = uuid.uuid4().hex

    machine = Machine(
        user_id=pairing.user_id,
        display_name=payload.display_name or pairing.display_name or payload.hostname or f"{payload.agent} daemon",
        hostname=payload.hostname,
        platform=payload.platform,
        arch=payload.arch,
        last_seen_at=now,
    )
    session.add(machine)
    await session.flush()

    workspace_roots = [root.strip() for root in payload.workspace_roots if isinstance(root, str) and root.strip()]
    agent_instance = AgentInstance(
        machine_id=machine.id,
        agent=payload.agent,
        daemon_id=daemon_id,
        executor_name=payload.executor_name,
        version=payload.version,
        status="paired",
        workspace_roots=workspace_roots,
        last_seen_at=now,
    )
    session.add(agent_instance)
    await session.flush()

    machine_token = generate_secret_token("pct_")
    credential = MachineCredential(
        agent_instance_id=agent_instance.id,
        key_id=uuid.uuid4().hex[:12],
        token_hash=hash_secret(machine_token),
        last_used_at=now,
    )
    session.add(credential)
    pairing.consumed_at = now

    await _record_audit_event(
        session,
        event_type="daemon.paired",
        user_id=pairing.user_id,
        machine_id=machine.id,
        agent_instance_id=agent_instance.id,
        subject_type="agent_instance",
        subject_id=str(agent_instance.id),
        data={"agent": payload.agent},
    )
    await session.commit()
    return DaemonPairResponse(
        machine_id=machine.id,
        agent_instance_id=agent_instance.id,
        daemon_id=daemon_id,
        machine_token=machine_token,
        agent=payload.agent,
    )


router.include_router(machine_router)
router.include_router(agent_router)
router.include_router(session_pointer_router)
router.include_router(daemon_router)

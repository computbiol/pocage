from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from .db_models import AgentInstance, Machine, MachineCredential
from .security import hash_secret


@dataclass(slots=True)
class DaemonIdentity:
    credential: MachineCredential
    agent_instance: AgentInstance
    machine: Machine


def _active_credential_query(token: str) -> Select[tuple[MachineCredential]]:
    return (
        select(MachineCredential)
        .options(
            joinedload(MachineCredential.agent_instance).joinedload(AgentInstance.machine),
        )
        .where(
            MachineCredential.token_hash == hash_secret(token),
            MachineCredential.revoked_at.is_(None),
        )
    )


async def authenticate_machine_token(session: AsyncSession, token: str) -> DaemonIdentity:
    credential = await session.scalar(_active_credential_query(token))
    if credential is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid machine token.")
    agent_instance = credential.agent_instance
    machine = agent_instance.machine
    if agent_instance.revoked_at is not None or agent_instance.status == "revoked":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent instance has been revoked.")
    if machine.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Machine has been revoked.")
    return DaemonIdentity(credential=credential, agent_instance=agent_instance, machine=machine)
def parse_bearer_token(header: str | None) -> str:
    if not header:
        return ""
    if not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()


def require_matching_agent_instance(
    hello_agent_instance_id: str,
    hello_machine_id: str,
    identity: DaemonIdentity,
) -> None:
    if hello_agent_instance_id != str(identity.agent_instance.id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent instance does not match token.")
    if hello_machine_id != str(identity.machine.id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Machine does not match token.")

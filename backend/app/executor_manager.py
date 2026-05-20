from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .db_models import AgentInstance, Machine, SessionPointer
from .events import EventBroker, StreamEvent
from .models import (
    ContextSearchRequestPayload,
    ContextSearchResultPayload,
    DaemonHello,
    HeartbeatEvent,
    PermissionDecision,
    RunAcceptedEvent,
    RunAssignPayload,
    RunCancelPayload,
    RunPermissionDecisionPayload,
    RunPermissionRequestedEvent,
    RunSessionUpdateEvent,
    SessionCreateRequestPayload,
    SessionCreateResultPayload,
    SessionListRequestPayload,
    SessionListResultPayload,
    SessionSyncRequestPayload,
    SessionSyncResultPayload,
)
from .runtime_state import RuntimeState, TERMINAL_RUN_STATUSES
from .security import hash_secret, utcnow

logger = logging.getLogger(__name__)


def _json_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return len(str(value).encode("utf-8", errors="replace"))


class DaemonProtocolViolation(RuntimeError):
    pass


@dataclass(slots=True)
class ExecutorSession:
    daemon_id: str
    name: str
    agent: str
    hostname: str | None
    workspace_roots: list[str]
    connected_at: str
    websocket: WebSocket
    machine_id: str | None = None
    agent_instance_id: str | None = None
    busy_run_id: str | None = None


class ExecutorManager:
    def __init__(
        self,
        runtime: RuntimeState,
        broker: EventBroker,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        max_session_update_bytes: int = 256_000,
        max_run_stream_bytes: int = 8_000_000,
        max_session_sync_bytes: int = 4_000_000,
    ) -> None:
        self._runtime = runtime
        self._broker = broker
        self._session_factory = session_factory
        self._daemons: dict[str, ExecutorSession] = {}
        self._context_search_waiters: dict[tuple[str, str], asyncio.Future[list[dict[str, Any]]]] = {}
        self._session_create_waiters: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        self._session_list_waiters: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        self._session_sync_waiters: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        self._session_sync_inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._run_stream_bytes: dict[str, int] = defaultdict(int)
        self._max_session_update_bytes = max(1, int(max_session_update_bytes))
        self._max_run_stream_bytes = max(1, int(max_run_stream_bytes))
        self._max_session_sync_bytes = max(1, int(max_session_sync_bytes))
        self._lock = asyncio.Lock()

    def _clear_run_stream_tracking(self, run_id: str) -> None:
        self._run_stream_bytes.pop(run_id, None)

    def _record_run_stream_bytes(self, run_id: str, raw_message_bytes: int | None) -> int:
        if raw_message_bytes is None or raw_message_bytes <= 0:
            return self._run_stream_bytes.get(run_id, 0)
        self._run_stream_bytes[run_id] += raw_message_bytes
        return self._run_stream_bytes[run_id]

    async def _mark_run_protocol_failed(
        self,
        session: ExecutorSession,
        *,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        self._runtime.mark_run_failed(
            run_id,
            error_code=error_code,
            error_message=error_message,
        )
        run_event = self._runtime.store_run_event(
            run_id,
            "run.error",
            {
                "run_id": run_id,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        await self._broker.publish(
            run_id,
            StreamEvent(event_type=run_event["event_type"], payload=run_event),
        )
        self._clear_run_stream_tracking(run_id)
        if session.busy_run_id == run_id:
            session.busy_run_id = None

    async def register(
        self,
        websocket: WebSocket,
        hello: DaemonHello,
        *,
        machine_id: str | None = None,
        agent_instance_id: str | None = None,
    ) -> ExecutorSession:
        daemon_id = hello.daemon_id.strip()
        connected_at = datetime.now(UTC).isoformat()
        session = ExecutorSession(
            daemon_id=daemon_id,
            name=hello.name.strip(),
            agent=hello.agent,
            hostname=hello.hostname,
            workspace_roots=[root for root in hello.workspace_roots if isinstance(root, str) and root.strip()],
            connected_at=connected_at,
            websocket=websocket,
            machine_id=machine_id,
            agent_instance_id=agent_instance_id,
        )
        async with self._lock:
            if daemon_id in self._daemons:
                logger.warning("duplicate daemon connection rejected: %s", daemon_id)
                raise ValueError("This machine is already connected. Stop the existing pocage process before starting another one.")
            self._daemons[daemon_id] = session
        logger.info("daemon registered: %s (%s)", daemon_id, session.name)

        await self._mark_agent_connected(
            agent_instance_id=agent_instance_id or "",
            machine_id=machine_id or "",
            daemon_id=daemon_id,
            name=hello.name.strip(),
            version=hello.version,
            workspace_roots=session.workspace_roots,
        )
        await websocket.send_json(
            {
                "type": "daemon.welcome",
                "machine_id": machine_id,
                "agent_instance_id": agent_instance_id,
                "daemon_id": daemon_id,
            }
        )
        await self.dispatch_pending()
        return session

    async def unregister(self, daemon_id: str) -> None:
        async with self._lock:
            session = self._daemons.pop(daemon_id, None)
        logger.info("daemon unregistered: %s", daemon_id)
        if session and session.agent_instance_id:
            await self._mark_agent_disconnected(session.agent_instance_id)
        self._fail_context_search_waiters(daemon_id, "daemon_disconnected")
        self._fail_session_create_waiters(daemon_id, "daemon_disconnected")
        self._fail_session_list_waiters(daemon_id, "daemon_disconnected")
        self._fail_session_sync_waiters(daemon_id, "daemon_disconnected")

        if session and session.busy_run_id:
            self._runtime.mark_run_executor_disconnected(session.busy_run_id)
            event = self._runtime.store_run_event(
                session.busy_run_id,
                "run.error",
                {
                    "run_id": session.busy_run_id,
                    "error_code": "executor_disconnected",
                    "error_message": "Daemon disconnected while run was active",
                },
            )
            await self._broker.publish(
                session.busy_run_id,
                StreamEvent(event_type=event["event_type"], payload=event),
            )
            self._clear_run_stream_tracking(session.busy_run_id)

    def _fail_context_search_waiters(self, daemon_id: str, error_message: str) -> None:
        targets = [key for key in self._context_search_waiters if key[0] == daemon_id]
        for key in targets:
            future = self._context_search_waiters.pop(key, None)
            if future is None or future.done():
                continue
            future.set_exception(RuntimeError(error_message))

    def _fail_session_create_waiters(self, daemon_id: str, error_message: str) -> None:
        targets = [key for key in self._session_create_waiters if key[0] == daemon_id]
        for key in targets:
            future = self._session_create_waiters.pop(key, None)
            if future is None or future.done():
                continue
            future.set_exception(RuntimeError(error_message))

    def _fail_session_list_waiters(self, daemon_id: str, error_message: str) -> None:
        targets = [key for key in self._session_list_waiters if key[0] == daemon_id]
        for key in targets:
            future = self._session_list_waiters.pop(key, None)
            if future is None or future.done():
                continue
            future.set_exception(RuntimeError(error_message))

    def _fail_session_sync_waiters(self, daemon_id: str, error_message: str) -> None:
        targets = [key for key in self._session_sync_waiters if key[0] == daemon_id]
        for key in targets:
            future = self._session_sync_waiters.pop(key, None)
            if future is None or future.done():
                continue
            future.set_exception(RuntimeError(error_message))

    async def _get_daemon_session(self, daemon_id: str) -> ExecutorSession:
        async with self._lock:
            session = self._daemons.get(daemon_id)
        if session is None:
            raise LookupError(f"daemon unavailable: {daemon_id}")
        return session

    async def get_executor_for_agent_instance(self, agent_instance_id: str) -> ExecutorSession:
        normalized = agent_instance_id.strip()
        if not normalized:
            raise LookupError("agent instance unavailable")
        async with self._lock:
            for session in self._daemons.values():
                if session.agent_instance_id == normalized:
                    return session
        raise LookupError(f"agent instance unavailable: {agent_instance_id}")

    async def _list_available_daemons(self) -> list[ExecutorSession]:
        async with self._lock:
            return [session for session in self._daemons.values() if session.busy_run_id is None]

    async def create_remote_session(
        self,
        *,
        daemon_id: str,
        cwd: str,
        mcp_servers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        session = await self._get_daemon_session(daemon_id)
        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (daemon_id, request_id)
        self._session_create_waiters[key] = future
        payload = SessionCreateRequestPayload(
            request_id=request_id,
            cwd=cwd,
            mcp_servers=mcp_servers,
        )

        try:
            await session.websocket.send_json(payload.model_dump())
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._session_create_waiters.pop(key, None)

    async def create_remote_session_for_agent_instance(
        self,
        *,
        agent_instance_id: str,
        cwd: str,
        mcp_servers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        session = await self.get_executor_for_agent_instance(agent_instance_id)
        return await self.create_remote_session(
            daemon_id=session.daemon_id,
            cwd=cwd,
            mcp_servers=mcp_servers,
        )

    async def _list_remote_session_page(self, *, daemon_id: str, cursor: str | None) -> dict[str, Any]:
        session = await self._get_daemon_session(daemon_id)
        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (daemon_id, request_id)
        self._session_list_waiters[key] = future
        payload = SessionListRequestPayload(request_id=request_id, cursor=cursor)

        try:
            await session.websocket.send_json(payload.model_dump())
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._session_list_waiters.pop(key, None)

    async def list_remote_sessions(self, daemon_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await self._list_remote_session_page(daemon_id=daemon_id, cursor=cursor)
            page_items = page.get("items")
            if isinstance(page_items, list):
                items.extend(item for item in page_items if isinstance(item, dict))
            next_cursor = page.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                return items
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if len(items) >= 500:
                return items

    async def _sync_remote_session_once(
        self,
        *,
        daemon_id: str,
        session_id: str,
        cwd: str,
        mcp_servers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        session = await self._get_daemon_session(daemon_id)
        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (daemon_id, request_id)
        self._session_sync_waiters[key] = future
        payload = SessionSyncRequestPayload(
            request_id=request_id,
            session_id=session_id,
            cwd=cwd,
            mcp_servers=mcp_servers,
        )

        try:
            await session.websocket.send_json(payload.model_dump())
            return await asyncio.wait_for(future, timeout=90)
        finally:
            self._session_sync_waiters.pop(key, None)

    async def sync_remote_session(
        self,
        *,
        daemon_id: str,
        session_id: str,
        cwd: str,
        mcp_servers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing = self._session_sync_inflight.get(session_id)
        if existing is not None:
            return await asyncio.shield(existing)

        task = asyncio.create_task(
            self._sync_remote_session_once(
                daemon_id=daemon_id,
                session_id=session_id,
                cwd=cwd,
                mcp_servers=mcp_servers,
            )
        )
        self._session_sync_inflight[session_id] = task
        try:
            return await asyncio.shield(task)
        finally:
            current = self._session_sync_inflight.get(session_id)
            if current is task:
                self._session_sync_inflight.pop(session_id, None)

    async def sync_remote_sessions(self, daemon_id: str | None = None) -> None:
        if daemon_id:
            daemon_ids = [daemon_id]
        else:
            async with self._lock:
                daemon_ids = list(self._daemons.keys())

        if not daemon_ids:
            return

        results = await asyncio.gather(
            *(self.list_remote_sessions(item) for item in daemon_ids),
            return_exceptions=True,
        )

        for current_daemon_id, result in zip(daemon_ids, results, strict=False):
            if isinstance(result, Exception):
                logger.warning("failed to sync sessions from daemon=%s: %s", current_daemon_id, result)
                continue

            agent_instance_id = None
            try:
                session = await self._get_daemon_session(current_daemon_id)
                agent_instance_id = session.agent_instance_id
            except LookupError:
                pass
            self._runtime.replace_daemon_sessions(
                current_daemon_id,
                result,
                agent_instance_id=agent_instance_id,
            )

    async def list_sessions(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        allowed_agent_instance_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        await self.sync_remote_sessions()
        return self._runtime.list_sessions(
            limit=limit,
            cursor=cursor,
            owned_agent_instance_ids=allowed_agent_instance_ids,
        )

    async def resolve_session(self, session_id: str) -> dict[str, Any]:
        cached = self._runtime.get_session(session_id)
        if cached is not None:
            cached_daemon_id = cached.get("daemon_id")
            if isinstance(cached_daemon_id, str) and cached_daemon_id:
                async with self._lock:
                    if cached_daemon_id in self._daemons:
                        return cached

        async with self._lock:
            daemon_ids = list(self._daemons.keys())

        for current_daemon_id in daemon_ids:
            try:
                items = await self.list_remote_sessions(current_daemon_id)
            except Exception as exc:
                logger.warning(
                    "failed to search daemon=%s for session=%s: %s",
                    current_daemon_id,
                    session_id,
                    exc,
                )
                continue

            agent_instance_id = None
            try:
                executor_session = await self._get_daemon_session(current_daemon_id)
                agent_instance_id = executor_session.agent_instance_id
            except LookupError:
                pass
            self._runtime.replace_daemon_sessions(
                current_daemon_id,
                items,
                agent_instance_id=agent_instance_id,
            )
            for item in items:
                if item.get("session_id") == session_id:
                    resolved = self._runtime.get_session(session_id)
                    if resolved is not None:
                        return resolved

        raise LookupError(f"session not found: {session_id}")

    async def load_remote_session(self, session_id: str) -> dict[str, Any]:
        session = await self.resolve_session(session_id)
        resolved_daemon_id = session.get("daemon_id")
        if not isinstance(resolved_daemon_id, str) or not resolved_daemon_id:
            raise LookupError(f"session is not attached to a daemon: {session_id}")

        result = await self.sync_remote_session(
            daemon_id=resolved_daemon_id,
            session_id=session_id,
            cwd=session["cwd"],
            mcp_servers=session.get("mcp_servers", []),
        )
        return {
            "session": self._runtime.upsert_session(
                session_id=session_id,
                daemon_id=resolved_daemon_id,
                agent_instance_id=session.get("agent_instance_id"),
                title=result.get("title") if isinstance(result.get("title"), str) else session["title"],
                cwd=session["cwd"],
                updated_at=result.get("updated_at") if isinstance(result.get("updated_at"), str) else session["updated_at"],
                mcp_servers=session.get("mcp_servers", []),
                created_at=session.get("created_at"),
            ),
            "session_updates": result.get("session_updates", []),
        }

    async def dispatch_pending(self) -> None:
        while True:
            sessions = await self._list_available_daemons()
            if not sessions:
                logger.debug("dispatch_pending: no available daemons")
                return
            made_progress = False

            for session in sessions:
                run = self._runtime.claim_next_queued_run(session.daemon_id)
                if run is None:
                    continue
                logger.info(
                    "dispatching run %s session %s to daemon %s",
                    run["run_id"],
                    run["session_id"],
                    session.daemon_id,
                )

                payload = RunAssignPayload(
                    job_id=run["run_id"],
                    run_id=run["run_id"],
                    session_id=run["session_id"],
                    cwd=run["cwd"],
                    content=run["content"],
                    prompt_items=run["prompt_items"],
                    mcp_servers=run["mcp_servers"],
                )

                try:
                    await session.websocket.send_json(payload.model_dump())
                except Exception:
                    self._runtime.mark_run_failed(
                        run["run_id"],
                        error_code="dispatch_failed",
                        error_message="Failed to deliver run to daemon",
                    )
                    event = self._runtime.store_run_event(
                        run["run_id"],
                        "run.error",
                        {
                            "run_id": run["run_id"],
                            "error_code": "dispatch_failed",
                            "error_message": "Failed to deliver run to daemon",
                        },
                    )
                    await self._broker.publish(
                        run["run_id"],
                        StreamEvent(event_type=event["event_type"], payload=event),
                    )
                    continue

                self._runtime.mark_run_assigned(run["run_id"], executor_id=session.daemon_id)
                session.busy_run_id = run["run_id"]
                self._run_stream_bytes.setdefault(run["run_id"], 0)
                started = self._runtime.store_run_event(
                    run["run_id"],
                    "run.started",
                    {
                        "run_id": run["run_id"],
                        "session_id": run["session_id"],
                        "executor_id": session.daemon_id,
                    },
                )
                await self._broker.publish(
                    run["run_id"],
                    StreamEvent(event_type=started["event_type"], payload=started),
                )
                made_progress = True

            if not made_progress:
                logger.debug("dispatch_pending: no queued runs matched available daemons")
                return

    async def search_context(
        self,
        *,
        daemon_id: str,
        cwd: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        session = await self._get_daemon_session(daemon_id)
        request_id = str(uuid.uuid4())
        future: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()
        key = (daemon_id, request_id)
        self._context_search_waiters[key] = future

        payload = ContextSearchRequestPayload(
            request_id=request_id,
            cwd=cwd,
            query=query,
            limit=limit,
        )

        try:
            await session.websocket.send_json(payload.model_dump())
            return await asyncio.wait_for(future, timeout=10)
        finally:
            self._context_search_waiters.pop(key, None)

    @staticmethod
    def _coerce_required_str(value: Any, *, field_name: str) -> str:
        if isinstance(value, str):
            result = value.strip()
            if result:
                return result
        raise ValueError(f"{field_name} is required")

    @staticmethod
    def _coerce_non_empty_str(value: Any, *, fallback: str) -> str:
        if isinstance(value, str):
            result = value.strip()
            if result:
                return result
            return fallback
        if value is None:
            return fallback
        text = str(value).strip()
        return text or fallback

    async def _mark_agent_connected(
        self,
        *,
        agent_instance_id: str,
        machine_id: str,
        daemon_id: str,
        name: str,
        version: str,
        workspace_roots: list[str],
    ) -> None:
        if self._session_factory is None:
            return
        now = utcnow()
        async with self._session_factory() as session:
            agent_instance = await session.get(AgentInstance, uuid.UUID(agent_instance_id))
            machine = await session.get(Machine, uuid.UUID(machine_id))
            if agent_instance is None or machine is None:
                return
            agent_instance.daemon_id = daemon_id
            agent_instance.executor_name = name
            agent_instance.version = version
            agent_instance.workspace_roots = workspace_roots
            agent_instance.status = "online"
            agent_instance.last_seen_at = now
            machine.last_seen_at = now
            await session.commit()

    async def _mark_agent_disconnected(self, agent_instance_id: str) -> None:
        if self._session_factory is None:
            return
        async with self._session_factory() as session:
            agent_instance = await session.get(AgentInstance, uuid.UUID(agent_instance_id))
            if agent_instance is None:
                return
            if agent_instance.status != "revoked":
                agent_instance.status = "offline"
            await session.commit()

    async def _mark_agent_seen(self, session_state: ExecutorSession) -> None:
        if self._session_factory is None or not session_state.agent_instance_id or not session_state.machine_id:
            return
        now = utcnow()
        async with self._session_factory() as session:
            agent_instance = await session.get(AgentInstance, uuid.UUID(session_state.agent_instance_id))
            machine = await session.get(Machine, uuid.UUID(session_state.machine_id))
            if agent_instance is None or machine is None:
                return
            agent_instance.last_seen_at = now
            if agent_instance.status != "revoked":
                agent_instance.status = "online"
            machine.last_seen_at = now
            await session.commit()

    @staticmethod
    def _parse_remote_timestamp(value: str | None) -> datetime:
        if not value:
            return utcnow()
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    async def _upsert_session_pointer(
        self,
        *,
        agent_instance_id: str,
        remote_session_id: str,
        title_hint: str | None,
        cwd: str | None,
        status: str,
        updated_at: str | None,
    ) -> None:
        if self._session_factory is None:
            return
        async with self._session_factory() as session:
            pointer = await session.scalar(
                select(SessionPointer).where(
                    SessionPointer.agent_instance_id == uuid.UUID(agent_instance_id),
                    SessionPointer.remote_session_id == remote_session_id,
                )
            )
            now = self._parse_remote_timestamp(updated_at)
            if pointer is None:
                pointer = SessionPointer(
                    agent_instance_id=uuid.UUID(agent_instance_id),
                    remote_session_id=remote_session_id,
                )
                session.add(pointer)
            pointer.title_hint = title_hint.strip() if isinstance(title_hint, str) and title_hint.strip() else None
            pointer.cwd_hash = hash_secret(cwd) if isinstance(cwd, str) and cwd.strip() else None
            pointer.status = status
            pointer.last_seen_at = now
            await session.commit()

    async def _replace_session_pointers(self, *, agent_instance_id: str, items: list[dict[str, Any]]) -> None:
        if self._session_factory is None:
            return
        agent_uuid = uuid.UUID(agent_instance_id)
        seen_ids = {
            str(item.get("session_id")).strip()
            for item in items
            if isinstance(item, dict) and isinstance(item.get("session_id"), str) and str(item.get("session_id")).strip()
        }
        async with self._session_factory() as session:
            existing = await session.scalars(select(SessionPointer).where(SessionPointer.agent_instance_id == agent_uuid))
            existing_rows = {row.remote_session_id: row for row in existing}
            for remote_session_id, row in existing_rows.items():
                if remote_session_id not in seen_ids:
                    await session.delete(row)
            for item in items:
                if not isinstance(item, dict):
                    continue
                remote_session_id = str(item.get("session_id") or "").strip()
                if not remote_session_id:
                    continue
                row = existing_rows.get(remote_session_id)
                if row is None:
                    row = SessionPointer(agent_instance_id=agent_uuid, remote_session_id=remote_session_id)
                    session.add(row)
                title = item.get("title")
                cwd = item.get("cwd")
                row.title_hint = title.strip() if isinstance(title, str) and title.strip() else None
                row.cwd_hash = hash_secret(cwd) if isinstance(cwd, str) and cwd.strip() else None
                row.status = "idle"
                row.last_seen_at = self._parse_remote_timestamp(
                    item.get("updated_at") if isinstance(item.get("updated_at"), str) else None
                )
            await session.commit()

    async def handle_executor_event(
        self,
        session: ExecutorSession,
        payload: dict[str, Any],
        *,
        raw_message_bytes: int | None = None,
    ) -> None:
        event_type = payload.get("type")
        logger.info("daemon event from %s: %s", session.daemon_id, event_type)
        raw_run_id = payload.get("run_id")
        if isinstance(raw_run_id, str) and raw_run_id.strip():
            total_bytes = self._record_run_stream_bytes(raw_run_id.strip(), raw_message_bytes)
            if total_bytes > self._max_run_stream_bytes:
                error_message = f"Run stream exceeded {self._max_run_stream_bytes} bytes"
                await self._mark_run_protocol_failed(
                    session,
                    run_id=raw_run_id.strip(),
                    error_code="run_stream_too_large",
                    error_message=error_message,
                )
                raise DaemonProtocolViolation(error_message)

        if event_type == "heartbeat":
            HeartbeatEvent(**payload)
            await self._mark_agent_seen(session)
            return

        if event_type == "session.create.result":
            event = SessionCreateResultPayload(**payload)
            key = (session.daemon_id, event.request_id)
            future = self._session_create_waiters.pop(key, None)
            if future is None or future.done():
                return
            if event.error_message:
                future.set_exception(RuntimeError(event.error_message))
                return
            if event.session is None:
                future.set_exception(RuntimeError("missing session payload"))
                return
            if session.agent_instance_id:
                session_payload = event.session.model_dump(mode="json")
                await self._upsert_session_pointer(
                    agent_instance_id=session.agent_instance_id,
                    remote_session_id=session_payload["session_id"],
                    title_hint=session_payload.get("title"),
                    cwd=session_payload.get("cwd"),
                    status="idle",
                    updated_at=session_payload.get("updated_at"),
                )
            future.set_result(event.session.model_dump(mode="json"))
            return

        if event_type == "session.list.result":
            event = SessionListResultPayload(**payload)
            key = (session.daemon_id, event.request_id)
            future = self._session_list_waiters.pop(key, None)
            if future is None or future.done():
                return
            if event.error_message:
                future.set_exception(RuntimeError(event.error_message))
                return
            items = [item.model_dump(mode="json") for item in event.items]
            if session.agent_instance_id:
                await self._replace_session_pointers(
                    agent_instance_id=session.agent_instance_id,
                    items=items,
                )
            future.set_result(
                {
                    "items": items,
                    "next_cursor": event.next_cursor,
                }
            )
            return

        if event_type == "session.sync.result":
            session_updates_payload = payload.get("session_updates")
            if _json_size_bytes(session_updates_payload) > self._max_session_sync_bytes:
                request_id = self._coerce_required_str(payload.get("request_id"), field_name="request_id")
                key = (session.daemon_id, request_id)
                future = self._session_sync_waiters.pop(key, None)
                if future is not None and not future.done():
                    future.set_exception(RuntimeError("session sync payload exceeded max size"))
                return
            event = SessionSyncResultPayload(**payload)
            key = (session.daemon_id, event.request_id)
            future = self._session_sync_waiters.pop(key, None)
            if future is None or future.done():
                return
            if event.error_message:
                future.set_exception(RuntimeError(event.error_message))
                return
            if session.agent_instance_id:
                await self._upsert_session_pointer(
                    agent_instance_id=session.agent_instance_id,
                    remote_session_id=event.session_id,
                    title_hint=event.title,
                    cwd=None,
                    status="idle",
                    updated_at=event.updated_at.isoformat() if event.updated_at else None,
                )
            future.set_result(
                {
                    "session_id": event.session_id,
                    "title": event.title,
                    "updated_at": event.updated_at.isoformat() if event.updated_at else None,
                    "session_updates": list(event.session_updates),
                }
            )
            return

        if event_type == "context.search.result":
            event = ContextSearchResultPayload(**payload)
            key = (session.daemon_id, event.request_id)
            future = self._context_search_waiters.pop(key, None)
            if future is None or future.done():
                return
            if event.error_message:
                future.set_exception(RuntimeError(event.error_message))
                return
            future.set_result([item.model_dump() for item in event.items])
            return

        if event_type == "run.accepted":
            event = RunAcceptedEvent(**payload)
            self._runtime.mark_run_started(
                event.run_id,
                executor_id=session.daemon_id,
                session_id=event.session_id,
            )
            run_event = self._runtime.store_run_event(
                event.run_id,
                "run.accepted",
                {
                    "run_id": event.run_id,
                    "session_id": event.session_id,
                    "job_id": event.job_id,
                },
            )
            await self._broker.publish(
                event.run_id,
                StreamEvent(event_type=run_event["event_type"], payload=run_event),
            )
            return

        if event_type == "run.session_update":
            update_bytes = _json_size_bytes(payload.get("update"))
            if update_bytes > self._max_session_update_bytes:
                run_id = self._coerce_required_str(payload.get("run_id"), field_name="run_id")
                error_message = f"Session update exceeded {self._max_session_update_bytes} bytes"
                await self._mark_run_protocol_failed(
                    session,
                    run_id=run_id,
                    error_code="session_update_too_large",
                    error_message=error_message,
                )
                raise DaemonProtocolViolation(error_message)
            event = RunSessionUpdateEvent(**payload)
            run_event = self._runtime.store_run_event(
                event.run_id,
                "run.session_update",
                {
                    "run_id": event.run_id,
                    "update": event.update,
                },
            )
            await self._broker.publish(
                event.run_id,
                StreamEvent(event_type=run_event["event_type"], payload=run_event),
            )
            return

        if event_type == "run.permission.requested":
            event = RunPermissionRequestedEvent(**payload)
            request = self._runtime.create_permission_request(
                run_id=event.run_id,
                approval_id=event.approval_id,
                executor_id=session.daemon_id,
                session_id=event.session_id,
                tool_call=event.tool_call,
                options=event.options,
            )
            run_event = self._runtime.store_run_event(
                event.run_id,
                "run.permission.requested",
                {
                    "run_id": event.run_id,
                    "approval_id": request["approval_id"],
                    "session_id": request["session_id"],
                    "status": request["status"],
                    "tool_call": request["tool_call"],
                    "options": request["options"],
                    "created_at": request["created_at"],
                },
            )
            await self._broker.publish(
                event.run_id,
                StreamEvent(event_type=run_event["event_type"], payload=run_event),
            )
            return

        if event_type == "run.completed":
            run_id = self._coerce_required_str(payload.get("run_id"), field_name="run_id")
            stop_reason = self._coerce_non_empty_str(payload.get("stop_reason"), fallback="completed")
            session_id = self._coerce_required_str(payload.get("session_id"), field_name="session_id")
            self._runtime.mark_run_completed(run_id, stop_reason=stop_reason)
            run_event = self._runtime.store_run_event(
                run_id,
                "run.completed",
                {
                    "run_id": run_id,
                    "session_id": session_id,
                    "stop_reason": stop_reason,
                },
            )
            await self._broker.publish(
                run_id,
                StreamEvent(event_type=run_event["event_type"], payload=run_event),
            )
            self._clear_run_stream_tracking(run_id)
            if session.busy_run_id == run_id:
                session.busy_run_id = None
            await self.dispatch_pending()
            return

        if event_type == "run.failed":
            run_id = self._coerce_required_str(payload.get("run_id"), field_name="run_id")
            error_code = self._coerce_non_empty_str(payload.get("error_code"), fallback="daemon_failed")
            error_message = self._coerce_non_empty_str(payload.get("error_message"), fallback="Daemon run failed")
            self._runtime.mark_run_failed(
                run_id,
                error_code=error_code,
                error_message=error_message,
            )
            run_event = self._runtime.store_run_event(
                run_id,
                "run.error",
                {
                    "run_id": run_id,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
            await self._broker.publish(
                run_id,
                StreamEvent(event_type=run_event["event_type"], payload=run_event),
            )
            self._clear_run_stream_tracking(run_id)
            if session.busy_run_id == run_id:
                session.busy_run_id = None
            await self.dispatch_pending()
            return

    async def request_cancel(self, run_id: str) -> str:
        run = self._runtime.get_run(run_id)
        if run is None:
            return "not_found"

        if run["status"] in TERMINAL_RUN_STATUSES:
            return "cancelled"

        if run["status"] == "queued":
            self._runtime.mark_run_cancelled(run_id)
            run_event = self._runtime.store_run_event(
                run_id,
                "run.completed",
                {
                    "run_id": run_id,
                    "session_id": run.get("session_id"),
                    "stop_reason": "cancelled",
                },
            )
            await self._broker.publish(
                run_id,
                StreamEvent(event_type=run_event["event_type"], payload=run_event),
            )
            self._clear_run_stream_tracking(run_id)
            return "cancelled"

        executor_id = run.get("executor_id")
        if not executor_id:
            return "cancelling"

        async with self._lock:
            session = self._daemons.get(executor_id)

        if session is None:
            return "cancelling"

        payload = RunCancelPayload(run_id=run_id)
        await session.websocket.send_json(payload.model_dump())
        return "cancelling"

    async def submit_permission_decision(
        self,
        *,
        run_id: str,
        approval_id: str,
        decision: PermissionDecision,
        option_id: str | None,
    ) -> bool:
        run = self._runtime.get_run(run_id)
        if run is None:
            return False

        executor_id = run.get("executor_id")
        if not isinstance(executor_id, str) or not executor_id:
            return False

        async with self._lock:
            session = self._daemons.get(executor_id)
        if session is None:
            return False

        payload = RunPermissionDecisionPayload(
            run_id=run_id,
            approval_id=approval_id,
            decision=decision,
            option_id=option_id,
        )

        try:
            await session.websocket.send_json(payload.model_dump())
        except Exception:
            logger.exception(
                "failed to deliver permission decision run_id=%s approval_id=%s",
                run_id,
                approval_id,
            )
            return False

        return True

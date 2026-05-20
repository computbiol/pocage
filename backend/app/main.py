from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth_api import get_current_user, get_optional_current_user, router as auth_router
from .config import Settings, get_settings
from .control_api import router as control_router
from .context_search import search_context_candidates
from .daemon_auth import authenticate_machine_token, parse_bearer_token, require_matching_agent_instance
from .db import async_session_maker, get_async_session
from .db_models import AgentInstance, Machine, User
from .events import EventBroker, StreamEvent, format_sse
from .executor_manager import DaemonProtocolViolation, ExecutorManager
from .models import (
    CancelRunResponse,
    ContextSearchItem,
    ContextSearchResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DaemonHello,
    GetTranscriptResponse,
    ListPermissionsResponse,
    ListRunEventsResponse,
    ListSessionsResponse,
    PermissionDecisionRequest,
    PermissionDecisionResponse,
    PermissionRequestItem,
    RunEventItem,
    SendMessageRequest,
    SendMessageResponse,
    SessionDetail,
    SessionSummary,
)
from .runtime_state import RuntimeState

logger = logging.getLogger(__name__)


def _default_remote_workspace() -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    return f"~/.pocage/workspaces/ws-{stamp}"


def _serialize_session(row: dict) -> SessionSummary:
    return SessionSummary(
        session_id=row["id"],
        title=row["title"],
        cwd=row["cwd"],
        agent_instance_id=row.get("agent_instance_id"),
        status=row["status"],
        mcp_servers=row.get("mcp_servers", []),
        remote_updated_at=row.get("remote_updated_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _serialize_session_detail(row: dict) -> SessionDetail:
    return SessionDetail(
        session_id=row["id"],
        title=row["title"],
        cwd=row["cwd"],
        agent_instance_id=row.get("agent_instance_id"),
        status=row["status"],
        mcp_servers=row.get("mcp_servers", []),
        remote_updated_at=row.get("remote_updated_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        executor_id=row["executor_id"],
    )


def _serialize_run_event(row: dict) -> RunEventItem:
    return RunEventItem(
        event_id=row["event_id"],
        run_id=row["run_id"],
        seq=row["seq"],
        event_type=row["event_type"],
        payload=row.get("payload", {}),
        created_at=row["created_at"],
    )


async def _list_owned_agent_instance_ids(
    *,
    current_user: User,
    session_db: AsyncSession,
) -> set[str]:
    result = await session_db.scalars(
        select(AgentInstance.id)
        .select_from(AgentInstance)
        .join(Machine, AgentInstance.machine_id == Machine.id)
        .where(Machine.user_id == current_user.id)
    )
    return {str(agent_instance_id) for agent_instance_id in result}


def _ensure_agent_instance_owned(agent_instance_id: str, owned_agent_instance_ids: set[str]) -> None:
    if agent_instance_id not in owned_agent_instance_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent instance not found.")


def _ensure_session_owned(session: dict[str, object], owned_agent_instance_ids: set[str]) -> None:
    agent_instance_id = session.get("agent_instance_id")
    if not isinstance(agent_instance_id, str) or agent_instance_id not in owned_agent_instance_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")


def _require_owned_run(
    *,
    run_id: str,
    runtime: RuntimeState,
    owned_agent_instance_ids: set[str],
) -> dict[str, object]:
    run = runtime.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    session = runtime.get_session(run["session_id"])
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    _ensure_session_owned(session, owned_agent_instance_ids)
    return run


def _decode_daemon_message(raw_message: dict[str, object], *, max_bytes: int) -> tuple[dict[str, Any], int]:
    message_type = raw_message.get("type")
    if message_type == "websocket.disconnect":
        raise WebSocketDisconnect(code=int(raw_message.get("code") or 1000), reason=str(raw_message.get("reason") or ""))
    if message_type != "websocket.receive":
        raise DaemonProtocolViolation("unexpected websocket message type")

    text_payload = raw_message.get("text")
    bytes_payload = raw_message.get("bytes")
    if isinstance(text_payload, str):
        encoded = text_payload.encode("utf-8")
    elif isinstance(bytes_payload, bytes):
        encoded = bytes_payload
    else:
        raise DaemonProtocolViolation("empty daemon websocket message")

    if len(encoded) > max_bytes:
        raise DaemonProtocolViolation(f"daemon message exceeded {max_bytes} bytes")

    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise DaemonProtocolViolation("invalid daemon json payload") from exc
    if not isinstance(payload, dict):
        raise DaemonProtocolViolation("daemon payload must be a JSON object")
    return payload, len(encoded)


async def _runtime_cleanup_loop(runtime: RuntimeState, interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            runtime.cleanup()
        except Exception:
            logger.exception("runtime cleanup failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    runtime = RuntimeState(
        max_events_per_run=settings.max_events_per_run,
        max_event_payload_bytes=settings.max_event_payload_bytes,
        completed_run_ttl_seconds=settings.completed_run_ttl_seconds,
        max_local_runs_per_session=settings.max_local_runs_per_session,
    )
    broker = EventBroker(queue_maxsize=settings.run_event_subscriber_queue_size)
    executors = ExecutorManager(
        runtime,
        broker,
        async_session_maker,
        max_session_update_bytes=settings.max_session_update_bytes,
        max_run_stream_bytes=settings.max_run_stream_bytes,
        max_session_sync_bytes=settings.max_session_sync_bytes,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cleanup_task = asyncio.create_task(
            _runtime_cleanup_loop(runtime, settings.runtime_cleanup_interval_seconds)
        )
        try:
            yield
        finally:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task

    app = FastAPI(
        title=settings.app_name,
        version="0.1.1",
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = settings
    app.state.runtime = runtime
    app.state.broker = broker
    app.state.executors = executors

    def get_runtime(request: Request) -> RuntimeState:
        return request.app.state.runtime

    def get_broker(request: Request) -> EventBroker:
        return request.app.state.broker

    def get_executors(request: Request) -> ExecutorManager:
        return request.app.state.executors

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{settings.api_prefix}/health")
    async def api_healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/context/search", response_model=ContextSearchResponse)
    async def search_context(
        q: str = Query(default="", alias="q"),
        cwd: str | None = Query(default=None),
        agent_instance_id: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=50),
        executors: ExecutorManager = Depends(get_executors),
        current_user: User | None = Depends(get_optional_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> ContextSearchResponse:
        if agent_instance_id:
            if current_user is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
            owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
            _ensure_agent_instance_owned(agent_instance_id, owned_agent_instance_ids)
            try:
                executor_session = await executors.get_executor_for_agent_instance(agent_instance_id)
            except LookupError as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

            try:
                items = await executors.search_context(
                    daemon_id=executor_session.daemon_id,
                    cwd=cwd or _default_remote_workspace(),
                    query=q,
                    limit=limit,
                )
            except LookupError as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            return ContextSearchResponse(items=[ContextSearchItem(**item) for item in items])

        if cwd:
            try:
                root = Path(cwd).expanduser().resolve()
            except Exception as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"invalid cwd: {exc}") from exc
        else:
            root = Path.cwd().resolve()
        candidates = search_context_candidates(root, q, limit=limit)
        return ContextSearchResponse(
            items=[
                ContextSearchItem(
                    kind=candidate.kind,
                    name=candidate.name,
                    relative_path=candidate.relative_path,
                    uri=candidate.uri,
                )
                for candidate in candidates
            ]
        )

    @app.post("/v1/sessions", response_model=CreateSessionResponse)
    async def create_session(
        payload: CreateSessionRequest,
        runtime: RuntimeState = Depends(get_runtime),
        executors: ExecutorManager = Depends(get_executors),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> CreateSessionResponse:
        agent_instance_id = payload.agent_instance_id.strip()
        if not agent_instance_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="agent_instance_id is required")
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        _ensure_agent_instance_owned(agent_instance_id, owned_agent_instance_ids)

        cwd = payload.cwd or _default_remote_workspace()
        try:
            executor_session = await executors.get_executor_for_agent_instance(agent_instance_id)
            remote_session = await executors.create_remote_session_for_agent_instance(
                agent_instance_id=agent_instance_id,
                cwd=cwd,
                mcp_servers=[server.model_dump() for server in payload.mcp_servers],
            )
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        title = payload.title or remote_session["title"] or "New session"
        now = datetime.now(UTC).isoformat()
        session = runtime.upsert_session(
            session_id=remote_session["session_id"],
            title=title,
            cwd=remote_session["cwd"],
            agent_instance_id=agent_instance_id,
            daemon_id=executor_session.daemon_id,
            updated_at=remote_session.get("updated_at") if isinstance(remote_session.get("updated_at"), str) else now,
            created_at=now,
            mcp_servers=[server.model_dump() for server in payload.mcp_servers],
        )
        return CreateSessionResponse(
            session_id=session["id"],
            title=session["title"],
            cwd=session["cwd"],
            agent_instance_id=agent_instance_id,
            mcp_servers=session.get("mcp_servers", []),
            status=session["status"],
            created_at=session["created_at"],
        )

    @app.get("/v1/sessions", response_model=ListSessionsResponse)
    async def list_sessions(
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = Query(default=None),
        executors: ExecutorManager = Depends(get_executors),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> ListSessionsResponse:
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        rows, next_cursor = await executors.list_sessions(
            limit=limit,
            cursor=cursor,
            allowed_agent_instance_ids=owned_agent_instance_ids,
        )
        return ListSessionsResponse(items=[_serialize_session(row) for row in rows], next_cursor=next_cursor)

    @app.get("/v1/sessions/{session_id}/transcript", response_model=GetTranscriptResponse)
    async def get_transcript(
        session_id: str,
        runtime: RuntimeState = Depends(get_runtime),
        executors: ExecutorManager = Depends(get_executors),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> GetTranscriptResponse:
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        return await _get_transcript_response(
            session_id=session_id,
            runtime=runtime,
            executors=executors,
            owned_agent_instance_ids=owned_agent_instance_ids,
        )

    async def _get_transcript_response(
        *,
        session_id: str,
        runtime: RuntimeState,
        executors: ExecutorManager,
        owned_agent_instance_ids: set[str],
    ) -> GetTranscriptResponse:
        try:
            result = await executors.load_remote_session(session_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        session = result["session"]
        _ensure_session_owned(session, owned_agent_instance_ids)
        runtime.cache_remote_session_transcript(
            session_id=session_id,
            remote_updated_at=session.get("updated_at"),
            session_updates=result.get("session_updates", []),
        )
        return GetTranscriptResponse(
            session=_serialize_session_detail(session),
            items=runtime.build_session_transcript(session_id),
        )

    @app.post("/v1/sessions/{session_id}/messages", response_model=SendMessageResponse)
    async def send_message(
        session_id: str,
        payload: SendMessageRequest,
        runtime: RuntimeState = Depends(get_runtime),
        executors: ExecutorManager = Depends(get_executors),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> SendMessageResponse:
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        return await _send_message_response(
            session_id=session_id,
            payload=payload,
            runtime=runtime,
            executors=executors,
            owned_agent_instance_ids=owned_agent_instance_ids,
        )

    async def _send_message_response(
        *,
        session_id: str,
        payload: SendMessageRequest,
        runtime: RuntimeState,
        executors: ExecutorManager,
        owned_agent_instance_ids: set[str],
    ) -> SendMessageResponse:
        try:
            session = await executors.resolve_session(session_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        _ensure_session_owned(session, owned_agent_instance_ids)

        daemon_id = session.get("daemon_id")
        if not isinstance(daemon_id, str) or not daemon_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session is not attached to a daemon")

        created = runtime.create_run(
            session_id=session_id,
            daemon_id=daemon_id,
            cwd=session["cwd"],
            content=payload.content,
            prompt_items=[item.model_dump() for item in payload.items],
            mcp_servers=session.get("mcp_servers", []),
        )
        run_id = created["run_id"]

        queued = runtime.store_run_event(
            run_id,
            "run.queued",
            {
                "run_id": run_id,
                "session_id": session_id,
                "content_length": len(payload.content),
            },
        )
        await broker.publish(run_id, StreamEvent(event_type=queued["event_type"], payload=queued))

        await executors.dispatch_pending()

        return SendMessageResponse(
            run_id=run_id,
            user_message_id=created["user_message_id"],
            assistant_message_id=created["assistant_message_id"],
            stream_url=f"/v1/runs/{run_id}/events",
        )

    @app.get("/v1/runs/{run_id}/permissions", response_model=ListPermissionsResponse)
    async def list_permissions(
        run_id: str,
        runtime: RuntimeState = Depends(get_runtime),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> ListPermissionsResponse:
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        _require_owned_run(run_id=run_id, runtime=runtime, owned_agent_instance_ids=owned_agent_instance_ids)
        rows = runtime.list_permission_requests(run_id)
        return ListPermissionsResponse(items=[PermissionRequestItem(**row) for row in rows])

    @app.post(
        "/v1/runs/{run_id}/permissions/{approval_id}/decision",
        response_model=PermissionDecisionResponse,
    )
    async def decide_permission(
        run_id: str,
        approval_id: str,
        payload: PermissionDecisionRequest,
        runtime: RuntimeState = Depends(get_runtime),
        broker: EventBroker = Depends(get_broker),
        executors: ExecutorManager = Depends(get_executors),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> PermissionDecisionResponse:
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        _require_owned_run(run_id=run_id, runtime=runtime, owned_agent_instance_ids=owned_agent_instance_ids)

        existing = runtime.get_permission_request(run_id, approval_id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="permission request not found")

        selected_option_id = payload.option_id if payload.decision == "selected" else None

        if existing["status"] in {"selected", "cancelled"}:
            same_decision = existing["status"] == payload.decision
            same_option = existing.get("option_id") == selected_option_id
            if same_decision and same_option:
                return PermissionDecisionResponse(**existing)
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="permission request already decided")

        delivered = await executors.submit_permission_decision(
            run_id=run_id,
            approval_id=approval_id,
            decision=payload.decision,
            option_id=selected_option_id,
        )
        if not delivered:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="executor unavailable")

        decided = runtime.mark_permission_decided(
            run_id=run_id,
            approval_id=approval_id,
            decision=payload.decision,
            option_id=selected_option_id,
        )
        if decided is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="permission request not found")

        event = runtime.store_run_event(
            run_id,
            "run.permission.decision",
            {
                "run_id": run_id,
                "approval_id": approval_id,
                "decision": payload.decision,
                "option_id": selected_option_id,
            },
        )
        await broker.publish(run_id, StreamEvent(event_type=event["event_type"], payload=event))
        return PermissionDecisionResponse(**decided)

    @app.get("/v1/runs/{run_id}/events/history", response_model=ListRunEventsResponse)
    async def list_run_events(
        run_id: str,
        runtime: RuntimeState = Depends(get_runtime),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> ListRunEventsResponse:
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        _require_owned_run(run_id=run_id, runtime=runtime, owned_agent_instance_ids=owned_agent_instance_ids)
        events = runtime.list_run_events(run_id)
        return ListRunEventsResponse(items=[_serialize_run_event(event) for event in events])

    @app.get("/v1/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        runtime: RuntimeState = Depends(get_runtime),
        broker: EventBroker = Depends(get_broker),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> StreamingResponse:
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        _require_owned_run(run_id=run_id, runtime=runtime, owned_agent_instance_ids=owned_agent_instance_ids)
        terminal_event_types = {"run.completed", "run.error"}

        async def event_generator():
            history = runtime.list_run_events(run_id)
            for event in history:
                yield format_sse(event["event_type"], event)
                if event["event_type"] in terminal_event_types:
                    return

            queue = await broker.subscribe(run_id)
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield format_sse(event.event_type, event.payload)
                        if event.event_type in terminal_event_types:
                            break
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                await broker.unsubscribe(run_id, queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.post("/v1/runs/{run_id}/cancel", response_model=CancelRunResponse)
    async def cancel_run(
        run_id: str,
        runtime: RuntimeState = Depends(get_runtime),
        executors: ExecutorManager = Depends(get_executors),
        current_user: User = Depends(get_current_user),
        session_db: AsyncSession = Depends(get_async_session),
    ) -> CancelRunResponse:
        owned_agent_instance_ids = await _list_owned_agent_instance_ids(current_user=current_user, session_db=session_db)
        _require_owned_run(run_id=run_id, runtime=runtime, owned_agent_instance_ids=owned_agent_instance_ids)
        status_text = await executors.request_cancel(run_id)
        return CancelRunResponse(status=status_text)

    @app.websocket(f"{settings.api_prefix}/daemon/ws")
    async def daemon_ws(websocket: WebSocket) -> None:
        def policy_violation_reason(exc: HTTPException) -> str:
            detail = exc.detail
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            if isinstance(detail, dict):
                message = detail.get("message") or detail.get("detail")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            return "Connection rejected by the control plane."

        token = parse_bearer_token(websocket.headers.get("authorization"))
        try:
            async with async_session_maker() as session_db:
                identity = await authenticate_machine_token(session_db, token)
        except HTTPException as exc:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=policy_violation_reason(exc))
            return

        await websocket.accept()

        session = None
        try:
            hello_payload, _ = _decode_daemon_message(
                await websocket.receive(),
                max_bytes=settings.max_daemon_message_bytes,
            )
            hello = DaemonHello(**hello_payload)
            require_matching_agent_instance(
                hello.agent_instance_id,
                hello.machine_id,
                identity,
            )
            try:
                session = await executors.register(
                    websocket,
                    hello,
                    machine_id=str(identity.machine.id),
                    agent_instance_id=str(identity.agent_instance.id),
                )
            except ValueError as exc:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc))
                return

            while True:
                payload, payload_size = _decode_daemon_message(
                    await websocket.receive(),
                    max_bytes=settings.max_daemon_message_bytes,
                )
                try:
                    await executors.handle_executor_event(session, payload, raw_message_bytes=payload_size)
                except DaemonProtocolViolation as exc:
                    logger.warning(
                        "daemon protocol violation: agent_instance_id=%s daemon_id=%s event_type=%s run_id=%s size_bytes=%s reason=%s",
                        session.agent_instance_id,
                        session.daemon_id,
                        payload.get("type"),
                        payload.get("run_id"),
                        payload_size,
                        str(exc),
                    )
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc))
                    return
                except Exception:
                    logger.exception(
                        "daemon event handling failed: agent_instance_id=%s daemon_id=%s event_type=%s run_id=%s size_bytes=%s",
                        session.agent_instance_id,
                        session.daemon_id,
                        payload.get("type"),
                        payload.get("run_id"),
                        payload_size,
                    )
        except WebSocketDisconnect:
            pass
        except DaemonProtocolViolation as exc:
            try:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc))
            except Exception:
                pass
        except HTTPException as exc:
            try:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=policy_violation_reason(exc))
            except Exception:
                pass
        except Exception:
            logger.exception("daemon websocket failed")
            try:
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            except Exception:
                pass
        finally:
            if session is not None:
                await executors.unregister(session.daemon_id)

    app.include_router(auth_router)
    app.include_router(control_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)

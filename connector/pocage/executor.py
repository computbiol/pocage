from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import websockets
from websockets.client import WebSocketClientProtocol

from .acp_client import PromptCallbacks, create_acp_client
from .config import Settings, client_user_agent
from .context_search import search_context_candidates

logger = logging.getLogger(__name__)


def _policy_violation_message(exc: Exception) -> str | None:
    if not isinstance(exc, websockets.exceptions.ConnectionClosed):
        return None
    close_code = exc.rcvd.code if exc.rcvd is not None else (exc.sent.code if exc.sent is not None else None)
    if close_code != 1008:
        return None
    close_reason = exc.rcvd.reason if exc.rcvd is not None else (exc.sent.reason if exc.sent is not None else "")
    reason = close_reason.strip()
    lowered = reason.lower()
    if "already connected" in lowered:
        return "This machine is already connected. Stop the existing pocage process before starting another one."
    if "invalid machine token" in lowered or "does not match token" in lowered or "revoked" in lowered:
        return "Stored pairing credentials are no longer valid. Pair this machine again."
    if reason:
        return reason
    return "Connection rejected by the control plane."


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    session_id: str


class PocageExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stop_event = asyncio.Event()
        self._acp = create_acp_client(settings.agent, settings.agent_command_path)
        self._active_runs: dict[str, ActiveRun] = {}
        self._pending_permission_decisions: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        self._run_lock = asyncio.Lock()
        self._executor_id: str | None = None
        self._current_ws: WebSocketClientProtocol | None = None

    def _default_workspace_root(self) -> str:
        if self._settings.workspace_roots:
            return self._settings.workspace_roots[0]
        return "~/.pocage/workspaces"

    def _resolve_workspace_path(self, raw_path: str) -> Path:
        return Path(raw_path).expanduser()

    async def run_forever(self) -> None:
        delay = 1
        while not self._stop_event.is_set():
            try:
                await self._run_once()
                delay = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                policy_message = _policy_violation_message(exc)
                if policy_message is not None:
                    logger.error("control plane rejected daemon connection: %s", policy_message)
                    return
                logger.exception("executor loop error: %s", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 20)

    async def stop(self) -> None:
        self._stop_event.set()
        await self._acp.stop()

    async def _reset_acp_client(self) -> None:
        with contextlib.suppress(Exception):
            await self._acp.stop()
        self._acp = create_acp_client(self._settings.agent, self._settings.agent_command_path)

    async def _run_once(self) -> None:
        headers = {"Authorization": f"Bearer {self._settings.machine_token}"}
        async with websockets.connect(
            self._settings.ws_url,
            additional_headers=headers,
            user_agent_header=client_user_agent("0.1.0"),
        ) as ws:
            self._current_ws = ws
            for raw_root in self._settings.workspace_roots:
                with contextlib.suppress(Exception):
                    self._resolve_workspace_path(raw_root).mkdir(parents=True, exist_ok=True)
            hello = {
                "type": "daemon.hello",
                "machine_id": self._settings.machine_id,
                "agent_instance_id": self._settings.agent_instance_id,
                "daemon_id": self._settings.daemon_id,
                "name": self._settings.executor_name,
                "version": "0.1.0",
                "agent": self._settings.agent,
                "hostname": socket.gethostname(),
                "workspace_roots": self._settings.workspace_roots,
                "capabilities": {
                    "stream": True,
                    "tools": True,
                    "context_search": True,
                    "session_list": True,
                    "session_create": True,
                    "session_sync": True,
                },
            }
            await ws.send(json.dumps(hello, ensure_ascii=True))

            welcome_raw = await ws.recv()
            welcome = json.loads(welcome_raw)
            self._executor_id = welcome.get("agent_instance_id") or welcome.get("executor_id")
            logger.info("connected to backend as agent_instance_id=%s", self._executor_id)

            heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
            try:
                async for raw in ws:
                    message = json.loads(raw)
                    await self._on_backend_message(ws, message)
            finally:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await heartbeat_task
                for run_id in list(self._active_runs):
                    self._cancel_permission_waiters(run_id, "backend_connection_closed")
                self._active_runs.clear()

    async def _heartbeat_loop(self, ws: WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(self._settings.heartbeat_interval_sec)
            await ws.send(json.dumps({"type": "heartbeat"}, ensure_ascii=True))

    async def _on_backend_message(self, ws: WebSocketClientProtocol, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        logger.info("backend message: %s", msg_type)
        if msg_type == "session.create.request":
            asyncio.create_task(self._handle_session_create_request(ws, message))
            return
        if msg_type == "session.list.request":
            asyncio.create_task(self._handle_session_list_request(ws, message))
            return
        if msg_type == "session.sync.request":
            asyncio.create_task(self._handle_session_sync_request(ws, message))
            return
        if msg_type == "run.assign":
            asyncio.create_task(self._handle_run_assign(ws, message))
            return
        if msg_type == "context.search.request":
            asyncio.create_task(self._handle_context_search_request(ws, message))
            return
        if msg_type == "run.cancel":
            await self._handle_run_cancel(message)
            return
        if msg_type == "run.permission.decision":
            await self._handle_permission_decision(message)
            return

    async def _handle_session_create_request(self, ws: WebSocketClientProtocol, message: dict[str, Any]) -> None:
        request_id = str(message.get("request_id") or "").strip()
        if not request_id:
            return

        raw_cwd = str(message.get("cwd") or self._default_workspace_root())
        cwd_path = self._resolve_workspace_path(raw_cwd)
        cwd_path.mkdir(parents=True, exist_ok=True)
        cwd = str(cwd_path)
        mcp_servers_raw = message.get("mcp_servers")
        mcp_servers = [item for item in mcp_servers_raw if isinstance(item, dict)] if isinstance(mcp_servers_raw, list) else []

        try:
            async with self._run_lock:
                await asyncio.wait_for(self._acp.start(), timeout=25)
                session_payload = await self._acp.new_session(cwd, mcp_servers=mcp_servers)
                session_id = str(session_payload.get("sessionId") or "").strip()
                if not session_id:
                    raise RuntimeError(f"missing sessionId from {self._settings.agent} agent")
                await self._try_set_preferred_mode(session_id, session_payload)
        except Exception as exc:
            logger.exception("session create failed: %s", exc)
            await ws.send(
                json.dumps(
                    {
                        "type": "session.create.result",
                        "request_id": request_id,
                        "session": None,
                        "error_message": str(exc),
                    },
                    ensure_ascii=True,
                )
            )
            return

        await ws.send(
            json.dumps(
                {
                    "type": "session.create.result",
                    "request_id": request_id,
                    "session": {
                        "session_id": session_id,
                        "cwd": cwd,
                        "title": "New session",
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    "error_message": None,
                },
                ensure_ascii=True,
            )
        )

    async def _handle_session_list_request(self, ws: WebSocketClientProtocol, message: dict[str, Any]) -> None:
        request_id = str(message.get("request_id") or "").strip()
        cursor = message.get("cursor")
        if not request_id:
            return

        try:
            async with self._run_lock:
                await asyncio.wait_for(self._acp.start(), timeout=25)
                result = await self._acp.list_sessions(cursor if isinstance(cursor, str) and cursor else None)

            sessions_raw = result.get("sessions")
            items: list[dict[str, Any]] = []
            if isinstance(sessions_raw, list):
                for item in sessions_raw:
                    if not isinstance(item, dict):
                        continue
                    session_id = item.get("sessionId")
                    cwd = item.get("cwd")
                    updated_at = item.get("updatedAt")
                    title = item.get("title")
                    if not (
                        isinstance(session_id, str)
                        and session_id
                        and isinstance(cwd, str)
                        and cwd
                        and isinstance(updated_at, str)
                        and updated_at
                    ):
                        continue
                    items.append(
                        {
                            "session_id": session_id,
                            "cwd": cwd,
                            "title": title if isinstance(title, str) and title.strip() else "New session",
                            "updated_at": updated_at,
                        }
                    )

            payload = {
                "type": "session.list.result",
                "request_id": request_id,
                "items": items,
                "next_cursor": result.get("nextCursor") if isinstance(result.get("nextCursor"), str) else None,
                "error_message": None,
            }
        except Exception as exc:
            logger.exception("session list failed: %s", exc)
            payload = {
                "type": "session.list.result",
                "request_id": request_id,
                "items": [],
                "next_cursor": None,
                "error_message": str(exc),
            }

        await ws.send(json.dumps(payload, ensure_ascii=True))

    async def _handle_session_sync_request(self, ws: WebSocketClientProtocol, message: dict[str, Any]) -> None:
        request_id = str(message.get("request_id") or "").strip()
        session_id = str(message.get("session_id") or "").strip()
        if not request_id or not session_id:
            return

        raw_cwd = str(message.get("cwd") or self._default_workspace_root())
        cwd_path = self._resolve_workspace_path(raw_cwd)
        cwd = str(cwd_path)
        mcp_servers_raw = message.get("mcp_servers")
        mcp_servers = [item for item in mcp_servers_raw if isinstance(item, dict)] if isinstance(mcp_servers_raw, list) else []

        try:
            async with self._run_lock:
                await asyncio.wait_for(self._acp.start(), timeout=25)
                result = await self._acp.sync_session(
                    session_id=session_id,
                    cwd=cwd,
                    mcp_servers=mcp_servers,
                )

            payload = {
                "type": "session.sync.result",
                "request_id": request_id,
                "session_id": session_id,
                "title": result.get("title"),
                "updated_at": result.get("updated_at"),
                "session_updates": result.get("session_updates", []),
                "error_message": None,
            }
        except Exception as exc:
            logger.exception("session sync failed: %s", exc)
            payload = {
                "type": "session.sync.result",
                "request_id": request_id,
                "session_id": session_id,
                "title": None,
                "updated_at": None,
                "session_updates": [],
                "error_message": str(exc),
            }

        await ws.send(json.dumps(payload, ensure_ascii=True))

    async def _handle_run_assign(self, ws: WebSocketClientProtocol, message: dict[str, Any]) -> None:
        run_id = str(message["run_id"])
        session_id = str(message["session_id"])
        raw_cwd = str(message.get("cwd") or self._default_workspace_root())
        cwd_path = self._resolve_workspace_path(raw_cwd)
        cwd_path.mkdir(parents=True, exist_ok=True)
        cwd = str(cwd_path)
        content = str(message.get("content") or "")
        prompt_items_raw = message.get("prompt_items")
        prompt_items = [item for item in prompt_items_raw if isinstance(item, dict)] if isinstance(prompt_items_raw, list) else []
        mcp_servers_raw = message.get("mcp_servers")
        mcp_servers = [item for item in mcp_servers_raw if isinstance(item, dict)] if isinstance(mcp_servers_raw, list) else []

        try:
            async with self._run_lock:
                await asyncio.wait_for(self._acp.start(), timeout=25)
                if self._acp.has_live_session(session_id):
                    session_payload: dict[str, Any] = {}
                else:
                    session_payload = await self._acp.load_session(
                        session_id,
                        cwd,
                        mcp_servers=mcp_servers,
                    )
                await self._try_set_preferred_mode(session_id, session_payload)

                self._active_runs[run_id] = ActiveRun(run_id=run_id, session_id=session_id)
                await ws.send(
                    json.dumps(
                        {
                            "type": "run.accepted",
                            "run_id": run_id,
                            "session_id": session_id,
                            "job_id": message.get("job_id", run_id),
                        },
                        ensure_ascii=True,
                    )
                )

                async def on_session_update(update: dict[str, Any]) -> None:
                    await ws.send(
                        json.dumps(
                            {
                                "type": "run.session_update",
                                "run_id": run_id,
                                "update": update,
                            },
                            ensure_ascii=True,
                        )
                    )

                async def on_permission_request(data: dict[str, Any]) -> dict[str, Any]:
                    approval_id = str(data.get("approval_id") or "").strip()
                    session_for_request = data.get("session_id")
                    tool_call = data.get("tool_call")
                    options = data.get("options")

                    if not approval_id:
                        return {"decision": "cancelled", "option_id": None}

                    key = (run_id, approval_id)
                    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
                    self._pending_permission_decisions[key] = future
                    try:
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "run.permission.requested",
                                    "run_id": run_id,
                                    "approval_id": approval_id,
                                    "session_id": (
                                        str(session_for_request)
                                        if isinstance(session_for_request, str) and session_for_request
                                        else session_id
                                    ),
                                    "tool_call": tool_call if isinstance(tool_call, dict) else {},
                                    "options": [opt for opt in options if isinstance(opt, dict)]
                                    if isinstance(options, list)
                                    else [],
                                },
                                ensure_ascii=True,
                            )
                        )
                    except Exception:
                        logger.exception(
                            "failed to send permission request run_id=%s approval_id=%s",
                            run_id,
                            approval_id,
                        )
                        return {"decision": "cancelled", "option_id": None}

                    try:
                        return await future
                    except asyncio.CancelledError:
                        return {"decision": "cancelled", "option_id": None}
                    except Exception:
                        logger.exception(
                            "permission wait failed run_id=%s approval_id=%s",
                            run_id,
                            approval_id,
                        )
                        return {"decision": "cancelled", "option_id": None}
                    finally:
                        self._pending_permission_decisions.pop(key, None)

                try:
                    response = await self._acp.prompt(
                        session_id=session_id,
                        content=content,
                        items=prompt_items,
                        callbacks=PromptCallbacks(
                            on_session_update=on_session_update,
                            on_permission_request=on_permission_request,
                        ),
                    )
                    raw_stop_reason = response.get("stopReason")
                    if raw_stop_reason in (None, ""):
                        raw_stop_reason = response.get("stop_reason")
                    if isinstance(raw_stop_reason, str):
                        stop_reason = raw_stop_reason.strip() or "completed"
                    elif raw_stop_reason is None:
                        stop_reason = "completed"
                    else:
                        stop_reason = str(raw_stop_reason)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "run.completed",
                                "run_id": run_id,
                                "session_id": session_id,
                                "stop_reason": stop_reason,
                            },
                            ensure_ascii=True,
                        )
                    )
                except Exception as exc:
                    logger.exception("run prompt failed: %s", exc)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "run.failed",
                                "run_id": run_id,
                                "error_code": "acp_prompt_failed",
                                "error_message": str(exc),
                            },
                            ensure_ascii=True,
                        )
                    )
                finally:
                    self._cancel_permission_waiters(run_id, "run_finished")
                    self._active_runs.pop(run_id, None)
        except Exception as exc:
            logger.exception("run setup failed: %s", exc)
            self._cancel_permission_waiters(run_id, "run_setup_failed")
            with contextlib.suppress(Exception):
                await ws.send(
                    json.dumps(
                        {
                            "type": "run.failed",
                            "run_id": run_id,
                            "error_code": "acp_setup_failed",
                            "error_message": str(exc),
                        },
                        ensure_ascii=True,
                    )
                )
            async with self._run_lock:
                await self._reset_acp_client()

    async def _handle_context_search_request(self, ws: WebSocketClientProtocol, message: dict[str, Any]) -> None:
        request_id = str(message.get("request_id") or "").strip()
        raw_cwd = str(message.get("cwd") or self._default_workspace_root())
        query = str(message.get("query") or "")
        raw_limit = message.get("limit")
        limit = int(raw_limit) if isinstance(raw_limit, int) else 20
        if not request_id:
            return

        try:
            candidates = search_context_candidates(raw_cwd, query, limit=max(1, min(limit, 50)))
            items = [
                {
                    "kind": candidate.kind,
                    "name": candidate.name,
                    "relative_path": candidate.relative_path,
                    "uri": candidate.uri,
                }
                for candidate in candidates
            ]
            payload = {
                "type": "context.search.result",
                "request_id": request_id,
                "items": items,
                "error_message": None,
            }
        except Exception as exc:
            logger.exception("context search failed: %s", exc)
            payload = {
                "type": "context.search.result",
                "request_id": request_id,
                "items": [],
                "error_message": str(exc),
            }

        await ws.send(json.dumps(payload, ensure_ascii=True))

    async def _handle_run_cancel(self, message: dict[str, Any]) -> None:
        run_id = str(message.get("run_id"))
        active = self._active_runs.get(run_id)
        if active is None:
            return
        self._cancel_permission_waiters(run_id, "run_cancelled")
        with contextlib.suppress(Exception):
            await self._acp.cancel(active.session_id)

    async def _handle_permission_decision(self, message: dict[str, Any]) -> None:
        run_id = str(message.get("run_id") or "")
        approval_id = str(message.get("approval_id") or "")
        if not run_id or not approval_id:
            return

        key = (run_id, approval_id)
        future = self._pending_permission_decisions.get(key)
        if future is None or future.done():
            return

        decision = message.get("decision")
        option_id = message.get("option_id")
        if decision == "selected" and isinstance(option_id, str):
            future.set_result({"decision": "selected", "option_id": option_id})
            return
        future.set_result({"decision": "cancelled", "option_id": None})

    def _cancel_permission_waiters(self, run_id: str, reason: str) -> None:
        targets: list[tuple[str, str]] = []
        for key in self._pending_permission_decisions:
            if key[0] == run_id:
                targets.append(key)
        for key in targets:
            future = self._pending_permission_decisions.pop(key, None)
            if future is None or future.done():
                continue
            future.set_result({"decision": "cancelled", "option_id": None, "reason": reason})

    async def _try_set_preferred_mode(self, session_id: str, payload: dict[str, Any]) -> None:
        modes = payload.get("modes")
        if not isinstance(modes, dict):
            return

        available = modes.get("availableModes")
        if not isinstance(available, list):
            return

        best_mode_id: str | None = None
        for mode in available:
            if not isinstance(mode, dict):
                continue
            mode_id = mode.get("id")
            if not isinstance(mode_id, str):
                continue
            lowered = mode_id.lower()
            if "code" in lowered or "auto" in lowered:
                best_mode_id = mode_id
                break

        if best_mode_id is None:
            return

        with contextlib.suppress(Exception):
            await self._acp.set_mode(session_id, best_mode_id)

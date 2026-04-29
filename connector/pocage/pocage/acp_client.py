from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from acp import PROTOCOL_VERSION, RequestError, connect_to_agent, image_block, resource_link_block, text_block
from acp.client.connection import ClientSideConnection
from acp.interfaces import Client
from acp.schema import (
    AgentMessageChunk,
    AudioContentBlock,
    AvailableCommandsUpdate,
    ClientCapabilities,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    EnvVariable,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    McpServerStdio,
    PermissionOption,
    RequestPermissionResponse,
    ResourceContentBlock,
    SessionInfo,
    SessionInfoUpdate,
    SseMcpServer,
    TextContentBlock,
    ToolCallUpdate,
    UsageUpdate,
    UserMessageChunk,
)

logger = logging.getLogger(__name__)

SUBPROCESS_STDIO_LIMIT_BYTES = 8 * 1024 * 1024


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    if isinstance(value, dict):
        return dict(value)
    return {}


def _content_block_to_text(
    block: TextContentBlock | ImageContentBlock | AudioContentBlock | ResourceContentBlock | Any,
) -> str:
    if isinstance(block, TextContentBlock):
        return block.text
    if isinstance(block, ImageContentBlock):
        return "[image]"
    if isinstance(block, ResourceContentBlock):
        return f"[@{block.name}]({block.uri})"
    return ""


def _normalize_prompt_items(
    content: str,
    items: list[dict[str, Any]] | None,
) -> list[TextContentBlock | ImageContentBlock | ResourceContentBlock]:
    prompt_items: list[TextContentBlock | ImageContentBlock | ResourceContentBlock] = []

    text_content = content.strip()
    if text_content:
        prompt_items.append(text_block(text_content))

    for item in items or []:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                prompt_items.append(text_block(text.strip()))
            continue

        if item_type == "image":
            image_url = item.get("image_url")
            if not isinstance(image_url, str) or not image_url:
                image_url = item.get("imageUrl")
            if not isinstance(image_url, str) or not image_url:
                continue

            if image_url.startswith("data:") and ";base64," in image_url:
                prefix, data = image_url.split(",", 1)
                mime_type = prefix[5:].split(";", 1)[0] or "application/octet-stream"
                prompt_items.append(image_block(data, mime_type, uri=item.get("name") if isinstance(item.get("name"), str) else None))
                continue

            prompt_items.append(text_block(f"[image] {image_url}"))
            continue

        if item_type == "resource_link":
            uri = item.get("uri")
            name = item.get("name")
            relative_path = item.get("relative_path")
            if not isinstance(uri, str) or not uri:
                continue
            label = None
            if isinstance(name, str) and name.strip():
                label = name.strip()
            elif isinstance(relative_path, str) and relative_path.strip():
                label = relative_path.strip().split("/")[-1]
            if not label:
                continue
            prompt_items.append(resource_link_block(label, uri, title=relative_path if isinstance(relative_path, str) else None))

    if not prompt_items:
        prompt_items.append(text_block(""))
    return prompt_items


def _normalize_mcp_servers(
    mcp_servers: list[dict[str, Any]] | None,
) -> list[HttpMcpServer | SseMcpServer | McpServerStdio]:
    servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] = []

    for server in mcp_servers or []:
        if not isinstance(server, dict):
            continue

        kind = server.get("kind")
        if not isinstance(kind, str):
            kind = server.get("type")
        name = server.get("name")
        if not isinstance(kind, str) or not isinstance(name, str) or not name:
            continue

        if kind == "http":
            url = server.get("url")
            if not isinstance(url, str) or not url:
                continue

            headers_raw = server.get("headers")
            headers: list[dict[str, str]] = []
            if isinstance(headers_raw, dict):
                for key, value in headers_raw.items():
                    if isinstance(key, str) and isinstance(value, str):
                        headers.append({"name": key, "value": value})
            elif isinstance(headers_raw, list):
                for header in headers_raw:
                    if not isinstance(header, dict):
                        continue
                    header_name = header.get("name")
                    header_value = header.get("value")
                    if isinstance(header_name, str) and isinstance(header_value, str):
                        headers.append({"name": header_name, "value": header_value})

            servers.append(HttpMcpServer(name=name, type="http", url=url, headers=headers))
            continue

        if kind == "stdio":
            command = server.get("command")
            if not isinstance(command, str) or not command:
                continue

            args_raw = server.get("args")
            args = [arg for arg in args_raw if isinstance(arg, str)] if isinstance(args_raw, list) else []

            env_raw = server.get("env")
            env: list[EnvVariable] = []
            if isinstance(env_raw, dict):
                for key, value in env_raw.items():
                    if isinstance(key, str) and isinstance(value, str):
                        env.append(EnvVariable(name=key, value=value))
            elif isinstance(env_raw, list):
                for item in env_raw:
                    if not isinstance(item, dict):
                        continue
                    env_name = item.get("name")
                    env_value = item.get("value")
                    if isinstance(env_name, str) and isinstance(env_value, str):
                        env.append(EnvVariable(name=env_name, value=env_value))

            servers.append(McpServerStdio(name=name, command=command, args=args, env=env))

    return servers


def _normalize_permission_options(raw_options: list[PermissionOption]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for option in raw_options:
        normalized = _model_dump(option)
        option_id = normalized.get("optionId")
        if not isinstance(option_id, str):
            option_id = normalized.get("option_id")
        if isinstance(option_id, str):
            normalized["option_id"] = option_id
        options.append(normalized)
    return options


@dataclass(slots=True)
class PromptCallbacks:
    on_session_update: Callable[[dict[str, Any]], Awaitable[None]]
    on_permission_request: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class SessionReplay:
    session_id: str
    title: str | None = None
    updated_at: str | None = None
    session_updates: list[dict[str, Any]] = field(default_factory=list)


class ApcRpcError(RuntimeError):
    pass


class AcpProviderAdapter:
    async def authenticate(self, conn: ClientSideConnection, initialize_response: dict[str, Any]) -> None:
        return None

    def build_prompt(
        self,
        content: str,
        items: list[dict[str, Any]] | None,
    ) -> list[TextContentBlock | ImageContentBlock | ResourceContentBlock]:
        return _normalize_prompt_items(content, items)


class CodexAcpAdapter(AcpProviderAdapter):
    async def authenticate(self, conn: ClientSideConnection, initialize_response: dict[str, Any]) -> None:
        auth_methods = [m.get("id") for m in initialize_response.get("authMethods", []) if isinstance(m, dict)]
        if "openai-api-key" in auth_methods and os.environ.get("OPENAI_API_KEY"):
            with contextlib.suppress(Exception):
                await conn.authenticate(method_id="openai-api-key")
        elif "codex-api-key" in auth_methods and os.environ.get("CODEX_API_KEY"):
            with contextlib.suppress(Exception):
                await conn.authenticate(method_id="codex-api-key")


class _ReplayCollector:
    def __init__(self, session_id: str) -> None:
        self._replay = SessionReplay(session_id=session_id)

    @property
    def replay(self) -> SessionReplay:
        return self._replay

    def apply(self, update: Any) -> None:
        if isinstance(update, SessionInfoUpdate):
            if isinstance(update.title, str) and update.title.strip():
                self._replay.title = update.title.strip()
            if isinstance(update.updated_at, str) and update.updated_at.strip():
                self._replay.updated_at = update.updated_at.strip()
            return
        self._replay.session_updates.append(_model_dump(update))


class _SdkClientCallbacks(Client):
    def __init__(self, owner: AcpProcessClient) -> None:
        self._owner = owner

    def on_connect(self, conn: Any) -> None:
        self._owner._conn = conn

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        return await self._owner._handle_permission_request(session_id, tool_call, options)

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await self._owner._handle_session_update(session_id, update)

    async def write_text_file(self, content: str, path: str, session_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("file/write_text")

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        raise RequestError.method_not_found("file/read_text")

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/kill")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


class AcpProcessClient:
    def __init__(self, command: str, *, adapter: AcpProviderAdapter | None = None) -> None:
        self._command = command
        self._adapter = adapter or AcpProviderAdapter()
        self._proc: asyncio.subprocess.Process | None = None
        self._conn: ClientSideConnection | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._prompt_callbacks: dict[str, PromptCallbacks] = {}
        self._sync_collectors: dict[str, _ReplayCollector] = {}
        self._session_replays: dict[str, SessionReplay] = {}
        self._live_session_ids: set[str] = set()

    async def start(self) -> None:
        if self._proc is not None and self._conn is not None:
            return

        args = shlex.split(self._command)
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=SUBPROCESS_STDIO_LIMIT_BYTES,
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            raise ApcRpcError("acp process stdio is not available")

        self._conn = connect_to_agent(_SdkClientCallbacks(self), self._proc.stdin, self._proc.stdout)
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        try:
            await asyncio.wait_for(self.initialize(), timeout=20)
        except Exception:
            with contextlib.suppress(Exception):
                await self.stop()
            raise

    async def stop(self) -> None:
        if self._stderr_task:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stderr_task
        self._stderr_task = None

        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
        self._conn = None

        if self._proc is not None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

        self._prompt_callbacks.clear()
        self._sync_collectors.clear()
        self._session_replays.clear()
        self._live_session_ids.clear()

    async def initialize(self) -> dict[str, Any]:
        conn = self._require_conn()
        response = await conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="pocage", title="Pocket Agent", version="0.1.0"),
        )
        payload = _model_dump(response)
        await self._adapter.authenticate(conn, payload)
        return payload

    async def new_session(self, cwd: str, mcp_servers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        response = await self._require_conn().new_session(cwd=cwd, mcp_servers=_normalize_mcp_servers(mcp_servers))
        payload = _model_dump(response)
        session_id = payload.get("sessionId")
        if isinstance(session_id, str) and session_id:
            self._live_session_ids.add(session_id)
        return payload

    async def list_sessions(self, cursor: str | None = None, cwd: str | None = None) -> dict[str, Any]:
        response = await self._require_conn().list_sessions(cursor=cursor, cwd=cwd)
        return _model_dump(response)

    async def load_session(
        self,
        session_id: str,
        cwd: str,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = await self._load_session_replay(
            session_id=session_id,
            cwd=cwd,
            mcp_servers=mcp_servers,
        )
        return payload

    async def sync_session(
        self,
        *,
        session_id: str,
        cwd: str,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        cached = self._session_replays.get(session_id)
        if cached is not None:
            return self._replay_payload(cached)
        return await self._load_session_replay(
            session_id=session_id,
            cwd=cwd,
            mcp_servers=mcp_servers,
        )

    async def set_mode(self, session_id: str, mode_id: str) -> None:
        await self._require_conn().set_session_mode(mode_id=mode_id, session_id=session_id)

    async def cancel(self, session_id: str) -> None:
        await self._require_conn().cancel(session_id=session_id)

    def has_live_session(self, session_id: str) -> bool:
        return session_id in self._live_session_ids

    async def prompt(
        self,
        *,
        session_id: str,
        content: str,
        items: list[dict[str, Any]] | None = None,
        callbacks: PromptCallbacks,
    ) -> dict[str, Any]:
        self._prompt_callbacks[session_id] = callbacks
        try:
            response = await self._require_conn().prompt(
                session_id=session_id,
                prompt=self._adapter.build_prompt(content, items),
            )
            return _model_dump(response)
        finally:
            self._prompt_callbacks.pop(session_id, None)

    async def _load_session_replay(
        self,
        *,
        session_id: str,
        cwd: str,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if session_id in self._sync_collectors:
            raise ApcRpcError(f"session load already running: {session_id}")

        collector = _ReplayCollector(session_id)
        self._sync_collectors[session_id] = collector
        try:
            response = await self._require_conn().load_session(
                cwd=cwd,
                session_id=session_id,
                mcp_servers=_normalize_mcp_servers(mcp_servers),
            )
            self._live_session_ids.add(session_id)
            replay = collector.replay
            self._session_replays[session_id] = SessionReplay(
                session_id=session_id,
                title=replay.title,
                updated_at=replay.updated_at,
                session_updates=list(replay.session_updates),
            )
            payload = _model_dump(response)
            payload["session_id"] = session_id
            return self._attach_replay_payload(payload, self._session_replays[session_id])
        finally:
            self._sync_collectors.pop(session_id, None)

    def _replay_payload(self, replay: SessionReplay) -> dict[str, Any]:
        return {
            "session_id": replay.session_id,
            "title": replay.title,
            "updated_at": replay.updated_at,
            "session_updates": list(replay.session_updates),
        }

    def _attach_replay_payload(self, payload: dict[str, Any], replay: SessionReplay) -> dict[str, Any]:
        payload["title"] = replay.title
        payload["updated_at"] = replay.updated_at
        payload["session_updates"] = list(replay.session_updates)
        return payload

    def _require_conn(self) -> ClientSideConnection:
        if self._conn is None:
            raise ApcRpcError("acp process is not running")
        return self._conn

    def _resolve_callbacks(self, session_id: str | None) -> PromptCallbacks | None:
        if isinstance(session_id, str):
            callbacks = self._prompt_callbacks.get(session_id)
            if callbacks is not None:
                return callbacks

        if len(self._prompt_callbacks) == 1:
            return next(iter(self._prompt_callbacks.values()))

        return None

    async def _handle_session_update(self, session_id: str, update: Any) -> None:
        collector = self._sync_collectors.get(session_id)
        if collector is not None:
            collector.apply(update)

        callbacks = self._resolve_callbacks(session_id)
        if callbacks is None:
            return

        update_payload = _model_dump(update)
        await callbacks.on_session_update(update_payload)

    async def _handle_permission_request(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
    ) -> RequestPermissionResponse:
        callbacks = self._resolve_callbacks(session_id if session_id else None)
        decision = "cancelled"
        option_id: str | None = None

        if callbacks is not None:
            try:
                result = await callbacks.on_permission_request(
                    {
                        "approval_id": _model_dump(tool_call).get("toolCallId") or _model_dump(tool_call).get("tool_call_id") or "",
                        "session_id": session_id,
                        "tool_call": _model_dump(tool_call),
                        "options": _normalize_permission_options(options),
                    }
                )
                if isinstance(result, dict):
                    raw_decision = result.get("decision")
                    raw_option_id = result.get("option_id")
                    if raw_decision == "selected" and isinstance(raw_option_id, str):
                        decision = "selected"
                        option_id = raw_option_id
            except Exception:
                logger.exception("permission callback failed for session_id=%s", session_id)

        if decision == "selected" and option_id:
            return RequestPermissionResponse(outcome={"outcome": "selected", "optionId": option_id})
        return RequestPermissionResponse(outcome={"outcome": "cancelled"})

    async def _drain_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").rstrip()
            if text:
                logger.debug("acp process: %s", text)


class CodexAcpClient(AcpProcessClient):
    def __init__(self, command: str) -> None:
        super().__init__(command, adapter=CodexAcpAdapter())


def create_acp_client(agent: str, command: str) -> AcpProcessClient:
    if agent == "codex":
        return CodexAcpClient(command)
    raise ValueError(f"unsupported agent: {agent}")

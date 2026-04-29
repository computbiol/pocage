from __future__ import annotations

import threading
import uuid
import contextlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

from .transcript_projection import build_run_transcript_items, build_synced_session_transcript_items


ACTIVE_RUN_STATUSES = {"queued", "assigned", "running"}
TERMINAL_RUN_STATUSES = {"completed", "cancelled", "failed", "executor_disconnected"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _display_title(title: str | None) -> str:
    text = (title or "").strip()
    return text or "New session"


def _normalize_prompt_items(prompt_items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in prompt_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                normalized.append({"type": "text", "text": text.strip()})
            continue
        if item_type == "image":
            image_url = item.get("image_url")
            if isinstance(image_url, str) and image_url.strip():
                normalized.append(
                    {
                        "type": "image",
                        "image_url": image_url.strip(),
                        "name": item.get("name"),
                        "mime_type": item.get("mime_type"),
                        "size": item.get("size"),
                    }
                )
            continue
        if item_type == "resource_link":
            uri = item.get("uri")
            name = item.get("name")
            relative_path = item.get("relative_path")
            kind = item.get("kind")
            if (
                isinstance(uri, str)
                and uri.strip()
                and isinstance(name, str)
                and name.strip()
                and isinstance(relative_path, str)
                and relative_path.strip()
                and kind in {"file", "directory"}
            ):
                normalized.append(
                    {
                        "type": "resource_link",
                        "uri": uri.strip(),
                        "name": name.strip(),
                        "relative_path": relative_path.strip(),
                        "kind": kind,
                    }
                )
    return normalized


def _prompt_items_to_user_content(prompt_items: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in prompt_items:
        item_type = item.get("type")
        if item_type == "text" and isinstance(item.get("text"), str):
            text = item["text"].strip()
            if text:
                parts.append(text)
            continue
        if item_type == "image":
            parts.append("[image]")
            continue
        if item_type == "resource_link":
            relative_path = item.get("relative_path")
            name = item.get("name")
            if isinstance(relative_path, str) and relative_path.strip():
                parts.append(f"@{relative_path.strip()}")
            elif isinstance(name, str) and name.strip():
                parts.append(f"@{name.strip()}")
            else:
                parts.append("[resource]")
            continue
        if isinstance(item_type, str) and item_type:
            parts.append(f"[{item_type}]")
    return "\n".join(parts).strip()


@dataclass(slots=True)
class RuntimeRun:
    run_id: str
    session_id: str
    daemon_id: str
    cwd: str
    content: str
    prompt_items: list[dict[str, Any]]
    mcp_servers: list[dict[str, Any]]
    user_content: str
    user_message_id: str
    assistant_message_id: str
    created_at: str
    updated_at: str
    status: str = "queued"
    executor_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class RuntimeState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._daemon_sessions: dict[str, set[str]] = defaultdict(set)
        self._runs: dict[str, RuntimeRun] = {}
        self._queued_runs: list[str] = []
        self._run_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._permission_requests: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self._remote_transcript_items: dict[str, list[dict[str, Any]]] = {}

    def _session_has_active_run_locked(self, session_id: str) -> bool:
        return any(
            run.session_id == session_id and run.status in ACTIVE_RUN_STATUSES
            for run in self._runs.values()
        )

    def upsert_session(
        self,
        *,
        session_id: str,
        daemon_id: str,
        agent_instance_id: str | None = None,
        title: str,
        cwd: str,
        updated_at: str,
        created_at: str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        executor_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            current = self._sessions.get(session_id)
            created_value = current["created_at"] if current else (created_at or updated_at or now)
            next_status = status or (current["status"] if current else "idle")
            if current and current["status"] in ACTIVE_RUN_STATUSES and status is None:
                next_status = current["status"]

            row = {
                "id": session_id,
                "title": _display_title(title),
                "cwd": cwd,
                "agent_instance_id": (
                    agent_instance_id
                    if agent_instance_id is not None
                    else (current.get("agent_instance_id") if current else None)
                ),
                "daemon_id": daemon_id,
                "status": next_status,
                "mcp_servers": list(mcp_servers if mcp_servers is not None else (current.get("mcp_servers", []) if current else [])),
                "remote_updated_at": updated_at,
                "created_at": created_value,
                "updated_at": updated_at or now,
                "executor_id": executor_id if executor_id is not None else (current.get("executor_id") if current else None),
            }
            self._sessions[session_id] = row
            self._daemon_sessions[daemon_id].add(session_id)
            return dict(row)

    def replace_daemon_sessions(
        self,
        daemon_id: str,
        items: list[dict[str, Any]],
        *,
        agent_instance_id: str | None = None,
    ) -> None:
        seen_ids: set[str] = set()
        for item in items:
            session_id = item.get("session_id")
            cwd = item.get("cwd")
            title = item.get("title")
            updated_at = item.get("updated_at")
            if not all(isinstance(value, str) and value for value in [session_id, cwd, updated_at]):
                continue
            seen_ids.add(session_id)
            self.upsert_session(
                session_id=session_id,
                daemon_id=daemon_id,
                agent_instance_id=agent_instance_id,
                title=title if isinstance(title, str) else "New session",
                cwd=cwd,
                updated_at=updated_at,
            )

        with self._lock:
            known = set(self._daemon_sessions.get(daemon_id, set()))
            stale_ids = known - seen_ids
            for session_id in stale_ids:
                if self._session_has_active_run_locked(session_id):
                    continue
                session = self._sessions.get(session_id)
                if session and session.get("daemon_id") == daemon_id:
                    self._sessions.pop(session_id, None)
            self._daemon_sessions[daemon_id] = seen_ids

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._sessions.get(session_id)
            return dict(row) if row else None

    def list_sessions(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        owned_agent_instance_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        with self._lock:
            rows = self._sessions.values()
            if owned_agent_instance_ids is not None:
                rows = [
                    item
                    for item in rows
                    if isinstance(item.get("agent_instance_id"), str) and item["agent_instance_id"] in owned_agent_instance_ids
                ]
            rows = sorted(
                rows,
                key=lambda item: (str(item.get("updated_at") or ""), str(item.get("id") or "")),
                reverse=True,
            )

        start_index = 0
        if cursor:
            for index, item in enumerate(rows):
                if item["id"] == cursor:
                    start_index = index + 1
                    break
        page = [dict(item) for item in rows[start_index : start_index + limit]]
        next_cursor = None
        if start_index + limit < len(rows) and page:
            next_cursor = str(page[-1]["id"])
        return page, next_cursor

    def touch_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        updated_at: str | None = None,
        executor_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = updated_at or utc_now()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if status is not None:
                session["status"] = status
            session["updated_at"] = now
            if executor_id is not None or "executor_id" not in session:
                session["executor_id"] = executor_id
            return dict(session)

    def create_run(
        self,
        *,
        session_id: str,
        daemon_id: str,
        cwd: str,
        content: str,
        prompt_items: list[dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        now = utc_now()
        run_id = new_id()
        user_message_id = new_id()
        assistant_message_id = new_id()
        normalized_items = _normalize_prompt_items(prompt_items or [])
        user_content = content.strip() or _prompt_items_to_user_content(normalized_items)
        run = RuntimeRun(
            run_id=run_id,
            session_id=session_id,
            daemon_id=daemon_id,
            cwd=cwd,
            content=content,
            prompt_items=normalized_items,
            mcp_servers=list(mcp_servers or []),
            user_content=user_content,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._runs[run_id] = run
            self._queued_runs.append(run_id)
            session = self._sessions.get(session_id)
            if session is not None:
                session["status"] = "queued"
                session["updated_at"] = now
        return {
            "run_id": run_id,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
        }

    def claim_next_queued_run(self, daemon_id: str) -> dict[str, Any] | None:
        with self._lock:
            for run_id in list(self._queued_runs):
                run = self._runs.get(run_id)
                if run is None or run.status != "queued":
                    with contextlib.suppress(ValueError):
                        self._queued_runs.remove(run_id)
                    continue
                if run.daemon_id != daemon_id:
                    continue
                self._queued_runs.remove(run_id)
                run.status = "assigned"
                run.updated_at = utc_now()
                session = self._sessions.get(run.session_id)
                if session is not None:
                    session["status"] = "assigned"
                    session["updated_at"] = run.updated_at
                return self._serialize_run_locked(run)
        return None

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return self._serialize_run_locked(run) if run else None

    def _serialize_run_locked(self, run: RuntimeRun | None) -> dict[str, Any] | None:
        if run is None:
            return None
        return {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "daemon_id": run.daemon_id,
            "cwd": run.cwd,
            "content": run.content,
            "prompt_items": list(run.prompt_items),
            "mcp_servers": list(run.mcp_servers),
            "user_content": run.user_content,
            "user_message_id": run.user_message_id,
            "assistant_message_id": run.assistant_message_id,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "status": run.status,
            "executor_id": run.executor_id,
            "error_code": run.error_code,
            "error_message": run.error_message,
        }

    def mark_run_assigned(self, run_id: str, *, executor_id: str | None) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run.status = "assigned"
            run.executor_id = executor_id
            run.updated_at = utc_now()
            session = self._sessions.get(run.session_id)
            if session is not None:
                session["status"] = "assigned"
                session["executor_id"] = executor_id
                session["updated_at"] = run.updated_at
            return self._serialize_run_locked(run)

    def mark_run_started(self, run_id: str, *, executor_id: str | None, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run.status = "running"
            run.executor_id = executor_id
            run.updated_at = utc_now()
            session = self._sessions.get(session_id)
            if session is not None:
                session["status"] = "running"
                session["executor_id"] = executor_id
                session["updated_at"] = run.updated_at
            return self._serialize_run_locked(run)

    def mark_run_completed(self, run_id: str, *, stop_reason: str) -> dict[str, Any] | None:
        next_status = "cancelled" if stop_reason == "cancelled" else "completed"
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run.status = next_status
            run.updated_at = utc_now()
            session = self._sessions.get(run.session_id)
            if session is not None:
                session["status"] = "idle"
                session["executor_id"] = None
                session["updated_at"] = run.updated_at
            return self._serialize_run_locked(run)

    def mark_run_failed(self, run_id: str, *, error_code: str, error_message: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run.status = "failed"
            run.error_code = error_code
            run.error_message = error_message
            run.updated_at = utc_now()
            session = self._sessions.get(run.session_id)
            if session is not None:
                session["status"] = "idle"
                session["executor_id"] = None
                session["updated_at"] = run.updated_at
            return self._serialize_run_locked(run)

    def mark_run_cancelled(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run.status = "cancelled"
            run.updated_at = utc_now()
            session = self._sessions.get(run.session_id)
            if session is not None:
                session["status"] = "idle"
                session["executor_id"] = None
                session["updated_at"] = run.updated_at
            return self._serialize_run_locked(run)

    def mark_run_executor_disconnected(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run.status = "executor_disconnected"
            run.updated_at = utc_now()
            session = self._sessions.get(run.session_id)
            if session is not None:
                session["status"] = "idle"
                session["executor_id"] = None
                session["updated_at"] = run.updated_at
            return self._serialize_run_locked(run)

    def store_run_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            seq = len(self._run_events[run_id]) + 1
            row = {
                "event_id": new_id(),
                "run_id": run_id,
                "seq": seq,
                "event_type": event_type,
                "payload": dict(payload),
                "created_at": now,
            }
            self._run_events[run_id].append(row)
            return dict(row)

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._run_events.get(run_id, [])]

    def create_permission_request(
        self,
        *,
        run_id: str,
        approval_id: str,
        executor_id: str | None,
        session_id: str,
        tool_call: dict[str, Any],
        options: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = utc_now()
        row = {
            "approval_id": approval_id,
            "run_id": run_id,
            "executor_id": executor_id,
            "session_id": session_id,
            "status": "pending",
            "tool_call": dict(tool_call),
            "options": list(options),
            "decision": None,
            "option_id": None,
            "created_at": now,
            "decided_at": None,
        }
        with self._lock:
            self._permission_requests[run_id][approval_id] = row
            return dict(row)

    def list_permission_requests(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._permission_requests.get(run_id, {}).values())
        rows.sort(key=lambda item: str(item.get("created_at") or ""))
        return [dict(item) for item in rows]

    def get_permission_request(self, run_id: str, approval_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._permission_requests.get(run_id, {}).get(approval_id)
            return dict(row) if row else None

    def mark_permission_decided(
        self,
        run_id: str,
        approval_id: str,
        *,
        decision: str,
        option_id: str | None,
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self._lock:
            row = self._permission_requests.get(run_id, {}).get(approval_id)
            if row is None:
                return None
            row["status"] = decision
            row["decision"] = decision
            row["option_id"] = option_id
            row["decided_at"] = now
            return dict(row)

    def cache_remote_session_transcript(
        self,
        session_id: str,
        *,
        remote_updated_at: str | None,
        session_updates: list[dict[str, Any]],
    ) -> None:
        if not session_updates:
            return
        with self._lock:
            has_local_runs = any(run.session_id == session_id for run in self._runs.values())
            if has_local_runs:
                return
            self._remote_transcript_items[session_id] = build_synced_session_transcript_items(
                session_id=session_id,
                remote_updated_at=remote_updated_at,
                session_updates=session_updates,
            )

    def build_session_transcript(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            runs = [run for run in self._runs.values() if run.session_id == session_id]
            run_events = {
                run.run_id: [dict(item) for item in self._run_events.get(run.run_id, [])]
                for run in runs
            }
            permission_requests = {
                run.run_id: [dict(item) for item in self._permission_requests.get(run.run_id, {}).values()]
                for run in runs
            }
            remote_items = [dict(item) for item in self._remote_transcript_items.get(session_id, [])]

        runs.sort(key=lambda item: (item.created_at, item.run_id))
        items: list[dict[str, Any]] = list(remote_items)
        for run in runs:
            items.append(
                {
                    "kind": "user_message",
                    "message_id": run.user_message_id,
                    "session_id": run.session_id,
                    "run_id": run.run_id,
                    "content": run.user_content,
                    "items": list(run.prompt_items),
                    "status": "completed",
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                }
            )
            items.extend(
                build_run_transcript_items(
                    run_id=run.run_id,
                    session_id=run.session_id,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                    status=run.status,
                    session_updates=[
                        event for event in run_events.get(run.run_id, []) if event.get("event_type") == "run.session_update"
                    ],
                    permissions=sorted(
                        permission_requests.get(run.run_id, []),
                        key=lambda item: str(item.get("created_at") or ""),
                    ),
                )
            )

        items.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                0 if item.get("kind") == "user_message" else 1,
                str(
                    item.get("message_id")
                    or item.get("segment_id")
                    or item.get("approval_id")
                    or item.get("run_id")
                    or ""
                ),
            )
        )
        return items

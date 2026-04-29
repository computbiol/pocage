from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AvailableCommandsUpdate,
    CurrentModeUpdate,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
    UserMessageChunk,
)
from pydantic import Field, TypeAdapter, ValidationError


SessionUpdateModel = Annotated[
    AgentMessageChunk
    | AgentThoughtChunk
    | ToolCallStart
    | ToolCallProgress
    | AgentPlanUpdate
    | AvailableCommandsUpdate
    | CurrentModeUpdate
    | UserMessageChunk
    | UsageUpdate,
    Field(discriminator="session_update"),
]

SESSION_UPDATE_ADAPTER = TypeAdapter(SessionUpdateModel)


def _compact_json(value: Any) -> str | None:
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return None
    if text in {"{}", "[]", "null", '""'}:
        return None
    return text if len(text) <= 240 else f"{text[:237]}..."


def _segment_status(run_status: str) -> str:
    if run_status in {"queued", "assigned", "running"}:
        return "streaming"
    if run_status == "cancelled":
        return "cancelled"
    if run_status in {"failed", "executor_disconnected"}:
        return "error"
    return "completed"


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    content_type = getattr(content, "type", None)
    if content_type == "text":
        text = getattr(content, "text", "")
        return text if isinstance(text, str) else ""
    if content_type == "image":
        return "[image]"
    if content_type == "audio":
        return "[audio]"
    if content_type == "resource_link":
        name = getattr(content, "name", None)
        uri = getattr(content, "uri", None)
        if isinstance(name, str) and name.strip():
            return f"@{name.strip()}"
        if isinstance(uri, str) and uri.strip():
            return uri.strip()
        return "[resource]"
    if content_type == "resource":
        resource = getattr(content, "resource", None)
        uri = getattr(resource, "uri", None)
        if isinstance(uri, str) and uri.strip():
            return uri.strip()
        return "[resource]"
    return ""


def _coerce_session_update(raw_update: Any) -> Any:
    if not isinstance(raw_update, dict):
        return raw_update
    try:
        return SESSION_UPDATE_ADAPTER.validate_python(raw_update)
    except ValidationError:
        return raw_update


def _step_from_update(update: Any, raw_update: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    created_at = str(event.get("created_at") or "")
    event_id = str(event.get("event_id") or f"{event.get('run_id', 'run')}-{event.get('seq', 0)}")
    session_update = raw_update.get("sessionUpdate")

    if isinstance(update, (AgentMessageChunk, AgentThoughtChunk, UserMessageChunk, UsageUpdate)):
        return None

    if isinstance(update, (ToolCallStart, ToolCallProgress)):
        detail = update.status if isinstance(update.status, str) and update.status else None
        if detail is None:
            detail = _compact_json(update.raw_output)
        return {
            "step_id": event_id,
            "summary": update.title or "Tool call",
            "detail": detail,
            "created_at": created_at,
            "session_update": update.session_update,
            "data": raw_update,
        }

    if isinstance(update, AgentPlanUpdate):
        detail = ", ".join(entry.content for entry in update.entries if entry.content)
        return {
            "step_id": event_id,
            "summary": "Plan updated",
            "detail": detail or None,
            "created_at": created_at,
            "session_update": update.session_update,
            "data": raw_update,
        }

    if isinstance(update, CurrentModeUpdate):
        return {
            "step_id": event_id,
            "summary": "Mode updated",
            "detail": update.current_mode_id,
            "created_at": created_at,
            "session_update": update.session_update,
            "data": raw_update,
        }

    if isinstance(update, AvailableCommandsUpdate):
        return {
            "step_id": event_id,
            "summary": "Commands updated",
            "detail": f"{len(update.available_commands)} available",
            "created_at": created_at,
            "session_update": update.session_update,
            "data": raw_update,
        }

    if isinstance(session_update, str) and session_update:
        return {
            "step_id": event_id,
            "summary": f"Update: {session_update}",
            "detail": _compact_json(raw_update),
            "created_at": created_at,
            "session_update": session_update,
            "data": raw_update,
        }

    return None


def _visible_segment(update: Any) -> tuple[str, str] | None:
    if isinstance(update, AgentMessageChunk):
        text = _content_to_text(update.content)
        return ("message", text) if text else None
    if isinstance(update, AgentThoughtChunk):
        text = _content_to_text(update.content)
        return ("thought", text) if text else None
    return None


def build_run_transcript_items(
    *,
    run_id: str,
    session_id: str,
    created_at: str,
    updated_at: str,
    status: str,
    session_updates: list[dict[str, Any]],
    permissions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    ordered_entries: list[tuple[str, str, int, dict[str, Any]]] = []

    for index, event in enumerate(session_updates):
        ordered_entries.append(("session_update", str(event.get("created_at") or updated_at), index, event))
    for index, permission in enumerate(permissions):
        ordered_entries.append(("permission", str(permission.get("created_at") or updated_at), len(session_updates) + index, permission))
    ordered_entries.sort(key=lambda item: (item[1], item[2]))

    segment_index = 0
    segment_kind: str | None = None
    segment_created_at: str | None = None
    segment_updated_at = created_at
    segment_content_parts: list[str] = []
    segment_steps: list[dict[str, Any]] = []
    pending_steps: list[dict[str, Any]] = []

    def flush_segment() -> None:
        nonlocal segment_index, segment_kind, segment_created_at, segment_updated_at, segment_content_parts, segment_steps
        content = "".join(segment_content_parts)
        if segment_kind is None or (not content and not segment_steps):
            segment_kind = None
            segment_created_at = None
            segment_updated_at = updated_at
            segment_content_parts = []
            segment_steps = []
            return
        items.append(
            {
                "kind": "assistant_segment",
                "segment_id": f"{run_id}:segment:{segment_index}",
                "session_id": session_id,
                "run_id": run_id,
                "segment_kind": segment_kind,
                "content": content,
                "status": _segment_status(status),
                "steps": list(segment_steps),
                "created_at": segment_created_at or created_at,
                "updated_at": segment_updated_at,
            }
        )
        segment_index += 1
        segment_kind = None
        segment_created_at = None
        segment_updated_at = updated_at
        segment_content_parts = []
        segment_steps = []

    def flush_pending_steps() -> None:
        nonlocal segment_index, pending_steps
        if not pending_steps:
            return
        items.append(
            {
                "kind": "assistant_segment",
                "segment_id": f"{run_id}:segment:{segment_index}",
                "session_id": session_id,
                "run_id": run_id,
                "segment_kind": "steps",
                "content": "",
                "status": _segment_status(status),
                "steps": list(pending_steps),
                "created_at": pending_steps[0]["created_at"],
                "updated_at": pending_steps[-1]["created_at"],
            }
        )
        segment_index += 1
        pending_steps = []

    def start_segment(next_kind: str, next_created_at: str) -> None:
        nonlocal segment_kind, segment_created_at, segment_updated_at, segment_steps, pending_steps
        segment_kind = next_kind
        segment_created_at = next_created_at
        segment_updated_at = next_created_at
        segment_steps = pending_steps
        pending_steps = []

    for entry_type, entry_created_at, _, payload in ordered_entries:
        if entry_type == "permission":
            flush_segment()
            flush_pending_steps()
            items.append(
                {
                    "kind": "permission_request",
                    "approval_id": payload["approval_id"],
                    "run_id": payload["run_id"],
                    "session_id": payload["session_id"],
                    "status": payload["status"],
                    "tool_call": payload.get("tool_call", {}),
                    "options": payload.get("options", []),
                    "decision": payload.get("decision"),
                    "option_id": payload.get("option_id"),
                    "created_at": payload["created_at"],
                    "decided_at": payload.get("decided_at"),
                }
            )
            continue

        raw_update = payload.get("payload", {}).get("update", {})
        if not isinstance(raw_update, dict):
            continue
        update = _coerce_session_update(raw_update)
        visible_segment = _visible_segment(update)
        step = _step_from_update(update, raw_update, payload)

        if visible_segment is None:
            if step is not None:
                flush_segment()
                pending_steps.append(step)
            continue

        next_segment_kind, text = visible_segment
        if segment_kind != next_segment_kind:
            flush_segment()
        if segment_kind is None:
            start_segment(next_segment_kind, entry_created_at)
        segment_content_parts.append(text)
        segment_updated_at = entry_created_at
        if step is not None:
            segment_steps.append(step)

    flush_segment()
    flush_pending_steps()
    return items


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _build_synthetic_update_events(
    *,
    session_id: str,
    anchor_timestamp: str | None,
    session_updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    anchor = _parse_timestamp(anchor_timestamp)
    total = len(session_updates)
    start_at = anchor - timedelta(milliseconds=(total * 2) + 10)
    events: list[dict[str, Any]] = []
    for index, update in enumerate(session_updates):
        created_at = (start_at + timedelta(milliseconds=index * 2)).astimezone(UTC).isoformat()
        events.append(
            {
                "event_id": f"remote-{session_id}-update-{index}",
                "run_id": f"remote-sync:{session_id}",
                "seq": index + 1,
                "event_type": "run.session_update",
                "payload": {"update": update},
                "created_at": created_at,
            }
        )
    return events


def build_synced_session_transcript_items(
    *,
    session_id: str,
    remote_updated_at: str | None,
    session_updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    synthetic_events = _build_synthetic_update_events(
        session_id=session_id,
        anchor_timestamp=remote_updated_at,
        session_updates=session_updates,
    )
    turns: list[dict[str, Any]] = []
    current_user_chunks: list[tuple[dict[str, Any], UserMessageChunk]] = []
    current_updates: list[dict[str, Any]] = []

    def flush_turn(turn_index: int) -> None:
        if not current_user_chunks and not current_updates:
            return

        run_id = f"remote-sync:{session_id}:{turn_index}"
        if current_user_chunks:
            created_at = current_user_chunks[0][0]["created_at"]
            updated_at = current_user_chunks[-1][0]["created_at"]
            turns.append(
                {
                    "kind": "user_message",
                    "message_id": f"{run_id}:user",
                    "session_id": session_id,
                    "run_id": run_id,
                    "content": "".join(_content_to_text(chunk.content) for _, chunk in current_user_chunks),
                    "items": [],
                    "status": "completed",
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )

        if current_updates:
            for event in current_updates:
                event["run_id"] = run_id
            turns.extend(
                build_run_transcript_items(
                    run_id=run_id,
                    session_id=session_id,
                    created_at=(current_user_chunks[0][0]["created_at"] if current_user_chunks else current_updates[0]["created_at"]),
                    updated_at=current_updates[-1]["created_at"],
                    status="completed",
                    session_updates=current_updates,
                    permissions=[],
                )
            )

    turn_index = 0
    for event in synthetic_events:
        raw_update = event.get("payload", {}).get("update", {})
        update = _coerce_session_update(raw_update)
        if isinstance(update, UserMessageChunk):
            if current_updates:
                flush_turn(turn_index)
                turn_index += 1
                current_user_chunks = []
                current_updates = []
            current_user_chunks.append((event, update))
            continue
        current_updates.append(event)

    flush_turn(turn_index)
    return turns

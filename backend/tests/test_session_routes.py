from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from acp import start_tool_call, update_agent_message_text, update_tool_call, update_user_message_text
from app.auth_api import get_current_user
from app.db import get_async_session
from app.main import create_app
from app.runtime_state import RuntimeState


class _FakeDbSession:
    def __init__(self) -> None:
        self.owned_agent_instance_ids: list[str] = ["agent-1"]

    async def scalars(self, _statement) -> list[str]:
        return list(self.owned_agent_instance_ids)


class _FakeExecutors:
    def __init__(self) -> None:
        self.load_calls: list[str] = []
        self.resolve_calls: list[str] = []
        self.dispatch_called = False
        self.list_rows: list[dict] = []
        self.session_agent_instance_id = "agent-1"

    async def load_remote_session(self, session_id: str) -> dict:
        self.load_calls.append(session_id)
        return {
            "session": {
                "id": session_id,
                "title": "Scoped Session",
                "cwd": "/tmp/ws",
                "agent_instance_id": self.session_agent_instance_id,
                "status": "idle",
                "mcp_servers": [],
                "remote_updated_at": "2026-03-18T00:00:00Z",
                "created_at": "2026-03-18T00:00:00Z",
                "updated_at": "2026-03-18T00:00:00Z",
                "executor_id": None,
            },
            "session_updates": [
                update_user_message_text("Remote hello").model_dump(mode="json", by_alias=True),
                start_tool_call("remote-call-1", "Inspect workspace", kind="search", status="in_progress").model_dump(
                    mode="json", by_alias=True
                ),
                update_tool_call("remote-call-1", status="completed").model_dump(mode="json", by_alias=True),
                update_agent_message_text("Remote answer").model_dump(mode="json", by_alias=True),
            ],
        }

    async def resolve_session(self, session_id: str) -> dict:
        self.resolve_calls.append(session_id)
        return {
            "id": session_id,
            "title": "Scoped Session",
            "cwd": "/tmp/ws",
            "agent_instance_id": self.session_agent_instance_id,
            "daemon_id": "daemon-internal-1",
            "status": "idle",
            "mcp_servers": [],
            "remote_updated_at": "2026-03-18T00:00:00Z",
            "created_at": "2026-03-18T00:00:00Z",
            "updated_at": "2026-03-18T00:00:00Z",
            "executor_id": None,
        }

    async def dispatch_pending(self) -> None:
        self.dispatch_called = True

    async def list_sessions(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        allowed_agent_instance_ids: set[str] | None = None,
    ) -> tuple[list[dict], str | None]:
        rows = list(self.list_rows)
        if allowed_agent_instance_ids is not None:
            rows = [
                row
                for row in rows
                if isinstance(row.get("agent_instance_id"), str) and row["agent_instance_id"] in allowed_agent_instance_ids
            ]
        return rows[:limit], None


class SessionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.fake_executors = _FakeExecutors()
        self.fake_db_session = _FakeDbSession()
        self.app.state.executors = self.fake_executors
        self.app.state.runtime = RuntimeState()
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1")

        async def override_async_session():
            yield self.fake_db_session

        self.app.dependency_overrides[get_async_session] = override_async_session

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        payload = b""
        headers = [(b"host", b"testserver")]
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers.extend(
                [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ]
            )

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }

        response_status = 500
        response_chunks: list[bytes] = []
        sent = False

        async def receive() -> dict:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message: dict) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            elif message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))

        asyncio.run(self.app(scope, receive, send))
        return response_status, b"".join(response_chunks)

    def test_get_transcript_uses_session_route_only(self) -> None:
        status_code, _ = self.request("GET", "/v1/sessions/sess-1/transcript")

        self.assertEqual(status_code, 200)
        self.assertEqual(self.fake_executors.load_calls, ["sess-1"])

    def test_get_transcript_materializes_remote_session_updates(self) -> None:
        status_code, body = self.request("GET", "/v1/sessions/sess-1/transcript")

        self.assertEqual(status_code, 200)
        payload = json.loads(body)
        self.assertEqual([item["kind"] for item in payload["items"]], ["user_message", "assistant_segment"])
        self.assertEqual(payload["items"][0]["content"], "Remote hello")
        self.assertEqual(payload["items"][1]["content"], "Remote answer")
        self.assertEqual(len(payload["items"][1]["steps"]), 2)

    def test_post_messages_uses_session_route_only(self) -> None:
        status_code, _ = self.request(
            "POST",
            "/v1/sessions/sess-1/messages",
            {"content": "hello", "items": []},
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(self.fake_executors.resolve_calls, ["sess-1"])
        self.assertTrue(self.fake_executors.dispatch_called)

    def test_list_sessions_filters_to_owned_agent_instances(self) -> None:
        self.fake_executors.list_rows = [
            {
                "id": "sess-owned",
                "title": "Owned session",
                "cwd": "/tmp/owned",
                "agent_instance_id": "agent-1",
                "status": "idle",
                "mcp_servers": [],
                "remote_updated_at": "2026-03-18T00:00:00Z",
                "created_at": "2026-03-18T00:00:00Z",
                "updated_at": "2026-03-18T00:00:00Z",
                "executor_id": None,
            },
            {
                "id": "sess-foreign",
                "title": "Foreign session",
                "cwd": "/tmp/foreign",
                "agent_instance_id": "agent-2",
                "status": "idle",
                "mcp_servers": [],
                "remote_updated_at": "2026-03-18T00:00:00Z",
                "created_at": "2026-03-18T00:00:00Z",
                "updated_at": "2026-03-18T00:00:00Z",
                "executor_id": None,
            },
        ]

        status_code, body = self.request("GET", "/v1/sessions")

        self.assertEqual(status_code, 200)
        payload = json.loads(body)
        self.assertEqual([item["session_id"] for item in payload["items"]], ["sess-owned"])

    def test_get_transcript_returns_404_for_unowned_session(self) -> None:
        self.fake_executors.session_agent_instance_id = "agent-2"

        status_code, _ = self.request("GET", "/v1/sessions/sess-foreign/transcript")

        self.assertEqual(status_code, 404)

    def test_create_session_rejects_unowned_agent_instance(self) -> None:
        status_code, _ = self.request(
            "POST",
            "/v1/sessions",
            {"agent_instance_id": "agent-2", "cwd": "/tmp/ws", "title": "New session", "mcp_servers": []},
        )

        self.assertEqual(status_code, 404)


if __name__ == "__main__":
    unittest.main()

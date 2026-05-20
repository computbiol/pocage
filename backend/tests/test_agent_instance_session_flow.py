from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from app.auth_api import get_current_user, get_optional_current_user
from app.db import get_async_session
from app.main import create_app
from app.runtime_state import RuntimeState


class _FakeDbSession:
    async def scalars(self, _statement) -> list[str]:
        return ["agent-1"]


class _FakeExecutors:
    def __init__(self) -> None:
        self.agent_lookup_calls: list[str] = []
        self.create_calls: list[tuple[str, str, list[dict]]] = []
        self.search_calls: list[tuple[str, str, str, int]] = []

    async def get_executor_for_agent_instance(self, agent_instance_id: str) -> SimpleNamespace:
        self.agent_lookup_calls.append(agent_instance_id)
        return SimpleNamespace(daemon_id="daemon-online")

    async def create_remote_session_for_agent_instance(
        self,
        *,
        agent_instance_id: str,
        cwd: str,
        mcp_servers: list[dict],
    ) -> dict:
        self.create_calls.append((agent_instance_id, cwd, mcp_servers))
        return {
            "session_id": "sess-agent-1",
            "cwd": cwd,
            "title": "Agent Bound Session",
            "updated_at": "2026-04-12T00:00:00Z",
        }

    async def search_context(
        self,
        *,
        daemon_id: str,
        cwd: str,
        query: str,
        limit: int,
    ) -> list[dict]:
        self.search_calls.append((daemon_id, cwd, query, limit))
        return [
            {
                "kind": "file",
                "name": "README.md",
                "relative_path": "README.md",
                "uri": "file:///workspace/README.md",
            }
        ]


class AgentInstanceSessionFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.fake_executors = _FakeExecutors()
        self.fake_db_session = _FakeDbSession()
        self.app.state.executors = self.fake_executors
        self.app.state.runtime = RuntimeState()
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1")
        self.app.dependency_overrides[get_optional_current_user] = lambda: SimpleNamespace(id="user-1")

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

    def test_create_session_uses_agent_instance_id(self) -> None:
        status_code, body = self.request(
            "POST",
            "/v1/sessions",
            {
                "agent_instance_id": "agent-1",
                "cwd": "/tmp/ws",
                "title": "Draft session",
                "mcp_servers": [],
            },
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(self.fake_executors.agent_lookup_calls, ["agent-1"])
        self.assertEqual(self.fake_executors.create_calls, [("agent-1", "/tmp/ws", [])])

        payload = json.loads(body)
        self.assertEqual(payload["agent_instance_id"], "agent-1")
        self.assertEqual(payload["cwd"], "/tmp/ws")

    def test_context_search_uses_agent_instance_id_to_resolve_online_connector(self) -> None:
        query_scope_status, query_scope_body = self._request_with_query(
            "/v1/context/search?q=readme&cwd=/tmp/ws&agent_instance_id=agent-1"
        )
        self.assertEqual(query_scope_status, 200)
        self.assertEqual(self.fake_executors.agent_lookup_calls, ["agent-1"])
        self.assertEqual(self.fake_executors.search_calls, [("daemon-online", "/tmp/ws", "readme", 20)])
        payload = json.loads(query_scope_body)
        self.assertEqual(payload["items"][0]["relative_path"], "README.md")

    def _request_with_query(self, path_with_query: str) -> tuple[int, bytes]:
        path, _, query = path_with_query.partition("?")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query.encode("ascii"),
            "headers": [(b"host", b"testserver")],
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
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            elif message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))

        asyncio.run(self.app(scope, receive, send))
        return response_status, b"".join(response_chunks)


if __name__ == "__main__":
    unittest.main()

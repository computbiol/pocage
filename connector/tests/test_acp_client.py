from __future__ import annotations

import unittest

from acp.helpers import update_agent_message_text
from acp.schema import SessionInfoUpdate

from pocage.acp_client import AcpProcessClient


class _FakeConn:
    def __init__(self, owner: AcpProcessClient) -> None:
        self._owner = owner
        self.load_calls: list[tuple[str, str]] = []

    async def load_session(self, *, cwd: str, session_id: str, mcp_servers) -> dict[str, object]:
        self.load_calls.append((session_id, cwd))
        await self._owner._handle_session_update(
            session_id,
            SessionInfoUpdate(
                session_update="session_info_update",
                title="Loaded history",
                updated_at="2026-03-18T10:00:00Z",
            ),
        )
        await self._owner._handle_session_update(session_id, update_agent_message_text("First line."))
        return {"models": [], "modes": []}


class AcpProcessClientReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_session_caches_replay_for_follow_up_sync(self) -> None:
        client = AcpProcessClient("codex-acp")
        fake_conn = _FakeConn(client)
        client._conn = fake_conn  # type: ignore[assignment]

        load_result = await client.load_session(session_id="sess-1", cwd="/workspace", mcp_servers=[])

        self.assertEqual(fake_conn.load_calls, [("sess-1", "/workspace")])
        self.assertEqual(load_result["session_id"], "sess-1")
        self.assertEqual(load_result["title"], "Loaded history")
        self.assertEqual(load_result["updated_at"], "2026-03-18T10:00:00Z")
        self.assertEqual([item["sessionUpdate"] for item in load_result["session_updates"]], ["agent_message_chunk"])

        sync_result = await client.sync_session(session_id="sess-1", cwd="/workspace", mcp_servers=[])

        self.assertEqual(fake_conn.load_calls, [("sess-1", "/workspace")])
        self.assertEqual(sync_result["session_id"], "sess-1")
        self.assertEqual(sync_result["title"], "Loaded history")
        self.assertEqual(sync_result["updated_at"], "2026-03-18T10:00:00Z")
        self.assertEqual(sync_result["session_updates"], load_result["session_updates"])


if __name__ == "__main__":
    unittest.main()

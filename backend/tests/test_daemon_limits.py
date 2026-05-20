from __future__ import annotations

import asyncio
import unittest

from app.events import EventBroker
from app.executor_manager import DaemonProtocolViolation, ExecutorManager, ExecutorSession
from app.main import _decode_daemon_message
from app.runtime_state import RuntimeState


class DaemonLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.runtime = RuntimeState()
        self.runtime.upsert_session(
            session_id="sess-1",
            daemon_id="daemon-1",
            title="Session",
            cwd="/tmp/work",
            updated_at="2026-03-18T00:00:00Z",
        )
        created = self.runtime.create_run(
            session_id="sess-1",
            daemon_id="daemon-1",
            cwd="/tmp/work",
            content="hello",
        )
        self.run_id = created["run_id"]
        self.session = ExecutorSession(
            daemon_id="daemon-1",
            name="daemon",
            agent="codex",
            hostname=None,
            workspace_roots=[],
            connected_at="2026-03-18T00:00:00Z",
            websocket=object(),
            busy_run_id=self.run_id,
        )

    async def test_oversized_session_update_marks_run_failed_and_raises(self) -> None:
        manager = ExecutorManager(
            self.runtime,
            EventBroker(),
            max_session_update_bytes=128,
            max_run_stream_bytes=10_000,
            max_session_sync_bytes=10_000,
        )

        with self.assertRaises(DaemonProtocolViolation):
            await manager.handle_executor_event(
                self.session,
                {
                    "type": "run.session_update",
                    "run_id": self.run_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "x" * 500},
                    },
                },
                raw_message_bytes=256,
            )

        run = self.runtime.get_run(self.run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(self.runtime.list_run_events(self.run_id)[0]["event_type"], "run.error")

    async def test_run_stream_limit_marks_run_failed_and_raises(self) -> None:
        manager = ExecutorManager(
            self.runtime,
            EventBroker(),
            max_session_update_bytes=10_000,
            max_run_stream_bytes=64,
            max_session_sync_bytes=10_000,
        )

        with self.assertRaises(DaemonProtocolViolation):
            await manager.handle_executor_event(
                self.session,
                {
                    "type": "run.accepted",
                    "run_id": self.run_id,
                    "session_id": "sess-1",
                    "job_id": "job-1",
                },
                raw_message_bytes=128,
            )

        run = self.runtime.get_run(self.run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "failed")

    async def test_oversized_session_sync_payload_rejects_waiter(self) -> None:
        manager = ExecutorManager(
            self.runtime,
            EventBroker(),
            max_session_update_bytes=64,
            max_run_stream_bytes=10_000,
            max_session_sync_bytes=96,
        )
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        manager._session_sync_waiters[("daemon-1", "req-1")] = future

        await manager.handle_executor_event(
            self.session,
            {
                "type": "session.sync.result",
                "request_id": "req-1",
                "session_id": "sess-1",
                "title": "Session",
                "updated_at": None,
                "session_updates": [{"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "x" * 300}}],
                "error_message": None,
            },
            raw_message_bytes=200,
        )

        with self.assertRaises(RuntimeError):
            await future

    def test_decode_daemon_message_rejects_oversized_frame(self) -> None:
        payload = {"type": "websocket.receive", "text": '{"type":"' + ("x" * 200) + '"}'}

        with self.assertRaises(DaemonProtocolViolation):
            _decode_daemon_message(payload, max_bytes=64)

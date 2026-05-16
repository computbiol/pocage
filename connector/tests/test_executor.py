from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from pocage.config import Settings
from pocage.executor import PocageExecutor


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class _FakeAcpClient:
    def __init__(self, *, live_session_ids: set[str], prompt_updates: list[dict] | None = None) -> None:
        self.live_session_ids = set(live_session_ids)
        self.load_calls: list[tuple[str, str]] = []
        self.prompt_calls: list[str] = []
        self.prompt_updates = list(prompt_updates or [])

    async def start(self) -> None:
        return None

    def has_live_session(self, session_id: str) -> bool:
        return session_id in self.live_session_ids

    async def load_session(self, session_id: str, cwd: str, mcp_servers: list[dict] | None = None) -> dict:
        self.load_calls.append((session_id, cwd))
        raise RuntimeError("load_session should not be called for a live session")

    async def set_mode(self, session_id: str, mode_id: str) -> None:
        return None

    async def prompt(self, *, session_id: str, content: str, items: list[dict] | None = None, callbacks) -> dict:
        self.prompt_calls.append(session_id)
        for update in self.prompt_updates:
            await callbacks.on_session_update(update)
        return {"stopReason": "completed"}

    async def stop(self) -> None:
        return None


class PocageExecutorRunAssignTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_assign_skips_load_for_live_session(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_root:
            settings = Settings(
                command="run",
                api_url="http://127.0.0.1:8000",
                ws_url="ws://127.0.0.1:8000/api/daemon/ws",
                agent="codex",
                machine_id="machine-1",
                agent_instance_id="agent-1",
                machine_token="pct_test_token",
                daemon_id="daemon-a",
                executor_name="connector-a",
                workspace_roots=[workspace_root],
                agent_command_path="/usr/local/bin/codex-acp",
                heartbeat_interval_sec=20,
                state_path=f"{workspace_root}/daemon-state.db",
            )
            executor = PocageExecutor(settings)
            fake_acp = _FakeAcpClient(
                live_session_ids={"sess-live"},
                prompt_updates=[
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "I'll help you with that."},
                    },
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "call-1",
                        "title": "Modifying configuration",
                        "kind": "edit",
                        "status": "pending",
                    },
                    {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "call-1",
                        "status": "completed",
                        "content": [
                            {
                                "type": "content",
                                "content": {"type": "text", "text": "Done"},
                            }
                        ],
                    },
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "Configuration updated."},
                    },
                ],
            )
            executor._acp = fake_acp  # type: ignore[assignment]
            ws = _FakeWebSocket()
            cwd = str(Path(workspace_root) / "ws-test")

            await executor._handle_run_assign(
                ws,
                {
                    "run_id": "run-1",
                    "session_id": "sess-live",
                    "cwd": cwd,
                    "content": "hello",
                    "prompt_items": [],
                    "mcp_servers": [],
                },
            )

            self.assertEqual(fake_acp.load_calls, [])
            self.assertEqual(fake_acp.prompt_calls, ["sess-live"])
            self.assertEqual(
                [item["type"] for item in ws.messages],
                [
                    "run.accepted",
                    "run.session_update",
                    "run.session_update",
                    "run.session_update",
                    "run.session_update",
                    "run.completed",
                ],
            )
            self.assertEqual(
                [item["update"]["sessionUpdate"] for item in ws.messages[1:5]],
                [
                    "agent_message_chunk",
                    "tool_call",
                    "tool_call_update",
                    "agent_message_chunk",
                ],
            )


class PocageExecutorConnectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self) -> Settings:
        workspace_root = tempfile.mkdtemp()
        return Settings(
            command="run",
            api_url="http://127.0.0.1:8000",
            ws_url="ws://127.0.0.1:8000/api/daemon/ws",
            agent="codex",
            machine_id="machine-1",
            agent_instance_id="agent-1",
            machine_token="pct_test_token",
            daemon_id="daemon-a",
            executor_name="connector-a",
            workspace_roots=[workspace_root],
            agent_command_path="/usr/local/bin/codex-acp",
            heartbeat_interval_sec=20,
            state_path=f"{workspace_root}/daemon-state.db",
        )

    async def test_run_forever_stops_retrying_on_policy_violation(self) -> None:
        executor = PocageExecutor(self._settings())
        duplicate_connection = ConnectionClosedError(
            Close(1008, "This machine is already connected. Stop the existing pocage process before starting another one."),
            Close(1008, ""),
            True,
        )

        with (
            patch.object(executor, "_run_once", AsyncMock(side_effect=duplicate_connection)) as mock_run_once,
            patch("pocage.executor.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("pocage.executor.logger") as mock_logger,
        ):
            await executor.run_forever()

        mock_run_once.assert_awaited_once()
        mock_sleep.assert_not_awaited()
        mock_logger.error.assert_called_once_with(
            "control plane rejected daemon connection: %s",
            "This machine is already connected. Stop the existing pocage process before starting another one.",
        )

    async def test_run_once_sets_explicit_websocket_user_agent(self) -> None:
        executor = PocageExecutor(self._settings())
        fake_ws = AsyncMock()
        fake_ws.recv = AsyncMock(return_value=json.dumps({"agent_instance_id": "agent-1"}))
        fake_ws.__aiter__.return_value = []
        connect_cm = AsyncMock()
        connect_cm.__aenter__.return_value = fake_ws
        connect_cm.__aexit__.return_value = None
        heartbeat_task = _FakeScheduledTask()

        def fake_create_task(coro):
            coro.close()
            return heartbeat_task

        with (
            patch("pocage.executor.websockets.connect", return_value=connect_cm) as mock_connect,
            patch("pocage.executor.asyncio.create_task", side_effect=fake_create_task),
        ):
            await executor._run_once()

        mock_connect.assert_called_once_with(
            executor._settings.ws_url,
            additional_headers={"Authorization": f"Bearer {executor._settings.machine_token}"},
            user_agent_header="pocage-cli/0.1.0",
        )


class _FakeScheduledTask:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def __await__(self):
        async def _wait():
            if self.cancelled:
                raise asyncio.CancelledError
            return None

        return _wait().__await__()


if __name__ == "__main__":
    unittest.main()

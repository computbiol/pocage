from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

from pocage.config import PairSettings, Settings
from pocage.main import _run_daemon, main
from pocage.state import StoredDaemonState


def _pair_settings() -> PairSettings:
    return PairSettings(
        command="pair",
        api_url="http://127.0.0.1:8000",
        pair_url="http://127.0.0.1:8000/api/daemon/pair",
        agent="codex",
        pairing_code="pair_test",
        daemon_id="codex-daemon",
        executor_name="codex@test",
        display_name=None,
        hostname="test-host",
        platform="darwin",
        arch="arm64",
        version="0.1.1",
        workspace_roots=["/tmp/ws"],
        agent_command_path="/usr/local/bin/codex-acp",
        heartbeat_interval_sec=20,
        state_path="/tmp/daemon-state.db",
    )


def _run_settings() -> Settings:
    return Settings(
        command="run",
        api_url="http://127.0.0.1:8000",
        ws_url="ws://127.0.0.1:8000/api/daemon/ws",
        agent="codex",
        machine_id="machine-1",
        agent_instance_id="agent-1",
        machine_token="pct_test_token",
        daemon_id="daemon-1",
        executor_name="codex@test",
        workspace_roots=["/tmp/ws"],
        agent_command_path="/usr/local/bin/codex-acp",
        heartbeat_interval_sec=20,
        state_path="/tmp/daemon-state.db",
    )


class MainPairCommandTests(unittest.TestCase):
    @patch("pocage.main.logging.basicConfig")
    @patch("pocage.main.parse_args")
    @patch("pocage.main.pair_daemon")
    def test_main_prints_pair_success(self, mock_pair_daemon, mock_parse_args, _mock_basic_config) -> None:
        mock_parse_args.return_value = _pair_settings()
        mock_pair_daemon.return_value = StoredDaemonState(
            agent="codex",
            api_url="http://127.0.0.1:8000",
            ws_url="ws://127.0.0.1:8000/api/daemon/ws",
            machine_id="machine-1",
            agent_instance_id="agent-1",
            machine_token="pct_test_token",
            daemon_id="daemon-1",
            executor_name="codex@test",
            workspace_roots=["/tmp/ws"],
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            main()

        self.assertEqual(
            stdout.getvalue().strip(),
            "paired codex daemon: machine_id=machine-1 agent_instance_id=agent-1",
        )

    @patch("pocage.main.logging.basicConfig")
    @patch("pocage.main.parse_args")
    @patch("pocage.main.pair_daemon", side_effect=RuntimeError("Pairing code is invalid or expired."))
    def test_main_exits_cleanly_when_pairing_fails(self, _mock_pair_daemon, mock_parse_args, _mock_basic_config) -> None:
        mock_parse_args.return_value = _pair_settings()

        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            main()

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(
            stderr.getvalue().strip(),
            "pocage pair failed: Pairing code is invalid or expired.",
        )


class MainRunCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_daemon_exits_when_executor_stops(self) -> None:
        loop = asyncio.get_running_loop()
        fake_executor = AsyncMock()
        fake_executor.run_forever = AsyncMock(return_value=None)
        fake_executor.stop = AsyncMock(return_value=None)

        with patch.object(loop, "add_signal_handler"), patch("pocage.main.PocageExecutor", return_value=fake_executor):
            await asyncio.wait_for(_run_daemon(_run_settings()), timeout=0.5)

        fake_executor.run_forever.assert_awaited_once()
        fake_executor.stop.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

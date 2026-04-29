from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from pocage.config import check_agent_command, check_codex_acp, parse_args
from pocage.state import DaemonStateStore, StoredDaemonState


class CheckCodexAcpTests(unittest.TestCase):
    @patch("pocage.config.shutil.which", return_value=None)
    def test_check_codex_acp_reports_missing_binary(self, _mock_which) -> None:
        ok, message = check_codex_acp()

        self.assertFalse(ok)
        self.assertEqual(message, "codex-acp not found in PATH")

    @patch("pocage.config.subprocess.run")
    @patch("pocage.config.shutil.which", return_value="/usr/local/bin/codex-acp")
    def test_check_codex_acp_returns_path_when_help_succeeds(self, _mock_which, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["/usr/local/bin/codex-acp", "--help"],
            returncode=0,
            stdout="usage",
            stderr="",
        )

        ok, message = check_codex_acp()

        self.assertTrue(ok)
        self.assertEqual(message, "/usr/local/bin/codex-acp")
        mock_run.assert_called_once_with(
            ["/usr/local/bin/codex-acp", "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    @patch("pocage.config.subprocess.run")
    @patch("pocage.config.shutil.which", return_value="/usr/local/bin/codex-acp")
    def test_check_codex_acp_reports_nonzero_exit(self, _mock_which, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["/usr/local/bin/codex-acp", "--help"],
            returncode=2,
            stdout="",
            stderr="bad flag",
        )

        ok, message = check_codex_acp()

        self.assertFalse(ok)
        self.assertIn("codex-acp found at /usr/local/bin/codex-acp", message)
        self.assertIn("exited with code 2", message)
        self.assertIn("bad flag", message)

    @patch("pocage.config.subprocess.run", side_effect=OSError("boom"))
    @patch("pocage.config.shutil.which", return_value="/usr/local/bin/codex-acp")
    def test_check_codex_acp_reports_execution_error(self, _mock_which, _mock_run) -> None:
        ok, message = check_codex_acp()

        self.assertFalse(ok)
        self.assertIn("codex-acp found at /usr/local/bin/codex-acp, but failed to execute", message)
        self.assertIn("boom", message)


class CheckAgentCommandTests(unittest.TestCase):
    @patch("pocage.config.subprocess.run")
    @patch("pocage.config.shutil.which", return_value="/usr/local/bin/codex-acp")
    def test_check_agent_command_supports_codex(self, _mock_which, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["/usr/local/bin/codex-acp", "--help"],
            returncode=0,
            stdout="usage",
            stderr="",
        )

        ok, message = check_agent_command("codex")

        self.assertTrue(ok)
        self.assertEqual(message, "/usr/local/bin/codex-acp")


class ParseArgsTests(unittest.TestCase):
    def _save_state(self, state_path: str) -> None:
        store = DaemonStateStore(state_path)
        store.save(
            StoredDaemonState(
                agent="codex",
                api_url="http://127.0.0.1:8000",
                ws_url="ws://127.0.0.1:8000/api/daemon/ws",
                machine_id="machine-1",
                agent_instance_id="agent-1",
                machine_token="pct_test_token",
                daemon_id="daemon-a",
                executor_name="codex@test",
                workspace_roots=["/tmp/ws"],
            )
        )

    @patch("pocage.config.check_agent_command", return_value=(True, "/usr/local/bin/codex-acp"))
    def test_parse_args_uses_default_codex_agent(self, _mock_check) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = f"{temp_dir}/daemon-state.db"
            self._save_state(state_path)
            settings = parse_args(["--state-path", state_path])

            self.assertEqual(settings.agent, "codex")
            self.assertEqual(settings.agent_command_path, "/usr/local/bin/codex-acp")
            self.assertEqual(settings.machine_token, "pct_test_token")
            self.assertEqual(settings.ws_url, "ws://127.0.0.1:8000/api/daemon/ws")

    @patch("pocage.config.check_agent_command", return_value=(False, "codex-acp not found in PATH"))
    def test_parse_args_shows_global_install_hint_when_missing(self, _mock_check) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            parse_args([])

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("codex-acp not found in PATH", stderr.getvalue())
        self.assertIn("npm install -g @zed-industries/codex-acp", stderr.getvalue())

    @patch("pocage.config.check_agent_command", return_value=(True, "/usr/local/bin/codex-acp"))
    def test_parse_args_accepts_explicit_agent(self, _mock_check) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = f"{temp_dir}/daemon-state.db"
            self._save_state(state_path)
            settings = parse_args(["--agent", "codex", "--state-path", state_path])

            self.assertEqual(settings.agent, "codex")
            self.assertEqual(settings.agent_command_path, "/usr/local/bin/codex-acp")

    @patch("pocage.config.check_agent_command", return_value=(True, "/usr/local/bin/codex-acp"))
    def test_parse_args_rejects_unknown_agent(self, _mock_check) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            parse_args(["--agent", "claude"])

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("invalid choice: 'claude'", stderr.getvalue())

    @patch("pocage.config.check_agent_command", return_value=(True, "/usr/local/bin/codex-acp"))
    def test_parse_args_rejects_legacy_api_key_flag(self, _mock_check) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            parse_args(["--api-key", "test-key"])

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("unrecognized arguments: --api-key test-key", stderr.getvalue())

    @patch("pocage.config.check_agent_command", return_value=(True, "/usr/local/bin/codex-acp"))
    def test_parse_args_pair_command_uses_pairing_inputs(self, _mock_check) -> None:
        settings = parse_args(
            [
                "pair",
                "--api-url",
                "http://127.0.0.1:8000",
                "--pairing-code",
                "pair_123",
                "--workspace-root",
                "/tmp/ws",
            ]
        )

        self.assertEqual(settings.command, "pair")
        self.assertEqual(settings.agent, "codex")
        self.assertEqual(settings.pairing_code, "pair_123")
        self.assertEqual(settings.pair_url, "http://127.0.0.1:8000/api/daemon/pair")
        self.assertTrue(settings.daemon_id.startswith("codex-"))
        self.assertEqual(settings.workspace_roots, ["/tmp/ws"])

    @patch("pocage.config.check_agent_command", return_value=(True, "/usr/local/bin/codex-acp"))
    def test_parse_args_requires_paired_state_for_run(self, _mock_check) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
                parse_args(["--state-path", f"{temp_dir}/missing.db"])

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("no paired daemon state found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

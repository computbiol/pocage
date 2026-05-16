from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pocage.config import PairSettings
from pocage.pairing import _post_json


def _pair_settings() -> PairSettings:
    return PairSettings(
        command="pair",
        api_url="https://pocage.toce.ai",
        pair_url="https://pocage.toce.ai/api/daemon/pair",
        agent="codex",
        pairing_code="pair_test",
        daemon_id="codex-daemon",
        executor_name="codex@test",
        display_name=None,
        hostname="test-host",
        platform="darwin",
        arch="arm64",
        version="0.1.0",
        workspace_roots=["/tmp/ws"],
        agent_command_path="/usr/local/bin/codex-acp",
        heartbeat_interval_sec=20,
        state_path="/tmp/daemon-state.db",
    )


class PairingHttpTests(unittest.TestCase):
    @patch("pocage.pairing.request.urlopen")
    def test_post_json_sets_explicit_user_agent(self, mock_urlopen) -> None:
        response = io.BytesIO(json.dumps({"machine_id": "machine-1"}).encode("utf-8"))
        mock_urlopen.return_value.__enter__.return_value = response

        _post_json(
            "https://pocage.toce.ai/api/daemon/pair",
            {"pairing_code": "pair_test"},
            version="0.1.0",
        )

        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.headers["User-agent"], "pocage-cli/0.1.0")


if __name__ == "__main__":
    unittest.main()

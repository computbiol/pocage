from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from .state import DaemonStateStore

AgentName = Literal["codex"]
DEFAULT_AGENT: AgentName = "codex"
SUPPORTED_AGENTS: tuple[AgentName, ...] = (DEFAULT_AGENT,)
DEFAULT_STATE_PATH = os.environ.get("POCAGE_STATE_PATH", "~/.pocage/daemon-state.db")


@dataclass(slots=True)
class PairSettings:
    command: Literal["pair"]
    api_url: str
    pair_url: str
    agent: AgentName
    pairing_code: str
    daemon_id: str
    executor_name: str
    display_name: str | None
    hostname: str
    platform: str
    arch: str
    version: str
    workspace_roots: list[str]
    agent_command_path: str
    heartbeat_interval_sec: int
    state_path: str


@dataclass(slots=True)
class RunSettings:
    command: Literal["run"]
    api_url: str
    ws_url: str
    agent: AgentName
    machine_id: str
    agent_instance_id: str
    machine_token: str
    daemon_id: str
    executor_name: str
    workspace_roots: list[str]
    agent_command_path: str
    heartbeat_interval_sec: int
    state_path: str


Settings = RunSettings
ParsedSettings = PairSettings | RunSettings


def client_user_agent(version: str) -> str:
    normalized = version.strip() or "0.1.0"
    return f"pocage-cli/{normalized}"


def _normalize_api_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("api-url must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("api-url must include a hostname")
    return api_url.rstrip("/")


def _derive_ws_url(api_url: str) -> str:
    normalized = _normalize_api_url(api_url)
    parsed = urlparse(normalized)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    prefix = parsed.path.rstrip("/")
    return f"{ws_scheme}://{parsed.netloc}{prefix}/api/daemon/ws"


def _derive_pair_url(api_url: str) -> str:
    normalized = _normalize_api_url(api_url)
    parsed = urlparse(normalized)
    prefix = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{prefix}/api/daemon/pair"


def _split_csv(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


def _agent_command(agent: AgentName) -> str:
    if agent == "codex":
        return "codex-acp"
    raise ValueError(f"unsupported agent: {agent}")


def get_agent_install_hint(agent: AgentName) -> str:
    if agent == "codex":
        return "npm install -g @zed-industries/codex-acp"
    raise ValueError(f"unsupported agent: {agent}")


def check_agent_command(agent: AgentName) -> tuple[bool, str]:
    command = _agent_command(agent)
    path = shutil.which(command)
    if path is None:
        return False, f"{command} not found in PATH"

    try:
        result = subprocess.run(
            [path, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return False, f"{command} found at {path}, but failed to execute: {exc}"

    if result.returncode != 0:
        return False, (
            f"{command} found at {path}, but exited with code {result.returncode}. "
            f"stderr: {result.stderr.strip()}"
        )

    return True, path


def check_codex_acp() -> tuple[bool, str]:
    return check_agent_command(DEFAULT_AGENT)


def _build_common_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--agent",
        choices=SUPPORTED_AGENTS,
        default=DEFAULT_AGENT,
        help="Agent runtime exposed by this daemon",
    )
    parser.add_argument(
        "--state-path",
        default=DEFAULT_STATE_PATH,
        help="SQLite state file used to persist daemon pairing credentials",
    )
    parser.add_argument(
        "--heartbeat-interval-sec",
        type=int,
        default=20,
        help="Heartbeat interval for backend websocket",
    )
    return parser


def _default_workspace_roots(value: list[str] | None) -> list[str]:
    workspace_roots = value
    if not workspace_roots:
        workspace_roots = _split_csv(os.environ.get("POCAGE_WORKSPACE_ROOTS", "~/.pocage/workspaces"))
    if not workspace_roots:
        workspace_roots = ["~/.pocage/workspaces"]
    return workspace_roots


def _resolve_agent_command(parser: argparse.ArgumentParser, agent: AgentName) -> str:
    agent_ok, agent_message = check_agent_command(agent)
    if not agent_ok:
        parser.error(f"{agent_message}. Install with: {get_agent_install_hint(agent)}")
    return agent_message


def _generate_daemon_id(agent: AgentName) -> str:
    return f"{agent}-{uuid.uuid4().hex[:12]}"


def _parse_pair_args(argv: list[str]) -> PairSettings:
    parser = _build_common_parser("pocage pair", "Pair a local agent daemon with the Pocage control plane")
    parser.add_argument("--api-url", required=True, help="Backend API base URL")
    parser.add_argument("--pairing-code", required=True, help="One-time pairing code from the Pocage website")
    parser.add_argument(
        "--executor-name",
        default=None,
        help="Human-readable daemon name",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Optional machine display name shown in the control plane",
    )
    parser.add_argument(
        "--hostname",
        default=socket.gethostname(),
        help="Reported host name",
    )
    parser.add_argument(
        "--platform",
        default=platform.system().lower() or "unknown",
        help="Reported operating system",
    )
    parser.add_argument(
        "--arch",
        default=platform.machine().lower() or "unknown",
        help="Reported CPU architecture",
    )
    parser.add_argument(
        "--version",
        default="0.1.0",
        help="Daemon version reported to the control plane",
    )
    parser.add_argument(
        "--workspace-root",
        dest="workspace_roots",
        action="append",
        help="Workspace root exposed by this daemon; can be passed multiple times",
    )
    ns = parser.parse_args(argv)
    agent_command_path = _resolve_agent_command(parser, ns.agent)
    normalized_api_url = _normalize_api_url(ns.api_url)
    return PairSettings(
        command="pair",
        api_url=normalized_api_url,
        pair_url=_derive_pair_url(normalized_api_url),
        agent=ns.agent,
        pairing_code=ns.pairing_code.strip(),
        daemon_id=_generate_daemon_id(ns.agent),
        executor_name=(
            ns.executor_name.strip()
            if isinstance(ns.executor_name, str) and ns.executor_name.strip()
            else f"{ns.agent}@{socket.gethostname()}"
        ),
        display_name=ns.display_name.strip() if isinstance(ns.display_name, str) and ns.display_name.strip() else None,
        hostname=ns.hostname.strip(),
        platform=ns.platform.strip(),
        arch=ns.arch.strip(),
        version=ns.version.strip(),
        workspace_roots=_default_workspace_roots(ns.workspace_roots),
        agent_command_path=agent_command_path,
        heartbeat_interval_sec=ns.heartbeat_interval_sec,
        state_path=ns.state_path,
    )


def _parse_run_args(argv: list[str]) -> RunSettings:
    parser = _build_common_parser("pocage", "Run a paired Pocage local agent daemon")
    ns = parser.parse_args(argv)
    agent_command_path = _resolve_agent_command(parser, ns.agent)
    store = DaemonStateStore(ns.state_path)
    stored = store.load(ns.agent)
    if stored is None:
        parser.error("no paired daemon state found for this agent. Run `pocage pair --api-url ... --pairing-code ...` first")

    return RunSettings(
        command="run",
        api_url=stored.api_url,
        ws_url=stored.ws_url,
        agent=ns.agent,
        machine_id=stored.machine_id,
        agent_instance_id=stored.agent_instance_id,
        machine_token=stored.machine_token,
        daemon_id=stored.daemon_id,
        executor_name=stored.executor_name,
        workspace_roots=stored.workspace_roots,
        agent_command_path=agent_command_path,
        heartbeat_interval_sec=ns.heartbeat_interval_sec,
        state_path=ns.state_path,
    )


def parse_args(argv: list[str] | None = None) -> ParsedSettings:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "pair":
        return _parse_pair_args(argv[1:])
    if argv and argv[0] == "run":
        return _parse_run_args(argv[1:])
    return _parse_run_args(argv)

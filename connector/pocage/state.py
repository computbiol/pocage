from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class StoredDaemonState:
    agent: str
    api_url: str
    ws_url: str
    machine_id: str
    agent_instance_id: str
    machine_token: str
    daemon_id: str
    executor_name: str
    workspace_roots: list[str]


class DaemonStateStore:
    def __init__(self, state_path: str) -> None:
        self._path = Path(state_path).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daemon_state (
                agent TEXT PRIMARY KEY,
                api_url TEXT NOT NULL,
                ws_url TEXT NOT NULL,
                machine_id TEXT NOT NULL,
                agent_instance_id TEXT NOT NULL,
                machine_token TEXT NOT NULL,
                daemon_id TEXT NOT NULL,
                executor_name TEXT NOT NULL,
                workspace_roots_json TEXT NOT NULL
            )
            """
        )
        return connection

    def load(self, agent: str) -> StoredDaemonState | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    agent,
                    api_url,
                    ws_url,
                    machine_id,
                    agent_instance_id,
                    machine_token,
                    daemon_id,
                    executor_name,
                    workspace_roots_json
                FROM daemon_state
                WHERE agent = ?
                """,
                (agent,),
            ).fetchone()
        if row is None:
            return None
        workspace_roots = json.loads(row["workspace_roots_json"])
        if not isinstance(workspace_roots, list):
            workspace_roots = []
        return StoredDaemonState(
            agent=str(row["agent"]),
            api_url=str(row["api_url"]),
            ws_url=str(row["ws_url"]),
            machine_id=str(row["machine_id"]),
            agent_instance_id=str(row["agent_instance_id"]),
            machine_token=str(row["machine_token"]),
            daemon_id=str(row["daemon_id"]),
            executor_name=str(row["executor_name"]),
            workspace_roots=[item for item in workspace_roots if isinstance(item, str) and item.strip()],
        )

    def save(self, state: StoredDaemonState) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO daemon_state (
                    agent,
                    api_url,
                    ws_url,
                    machine_id,
                    agent_instance_id,
                    machine_token,
                    daemon_id,
                    executor_name,
                    workspace_roots_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent) DO UPDATE SET
                    api_url = excluded.api_url,
                    ws_url = excluded.ws_url,
                    machine_id = excluded.machine_id,
                    agent_instance_id = excluded.agent_instance_id,
                    machine_token = excluded.machine_token,
                    daemon_id = excluded.daemon_id,
                    executor_name = excluded.executor_name,
                    workspace_roots_json = excluded.workspace_roots_json
                """,
                (
                    state.agent,
                    state.api_url,
                    state.ws_url,
                    state.machine_id,
                    state.agent_instance_id,
                    state.machine_token,
                    state.daemon_id,
                    state.executor_name,
                    json.dumps(state.workspace_roots, ensure_ascii=True),
                ),
            )
            connection.commit()

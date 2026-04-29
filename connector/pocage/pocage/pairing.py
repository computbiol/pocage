from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from .config import PairSettings, _derive_ws_url, client_user_agent
from .state import DaemonStateStore, StoredDaemonState


def _parse_error_message(body: bytes) -> str:
    if not body:
        return "request failed"
    with_error = body.decode("utf-8", errors="replace").strip()
    if not with_error:
        return "request failed"
    try:
        payload = json.loads(with_error)
    except json.JSONDecodeError:
        return with_error
    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return with_error


def _post_json(url: str, payload: dict[str, Any], *, version: str) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "user-agent": client_user_agent(version),
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=15) as response:
            response_body = response.read()
    except error.HTTPError as exc:
        raise RuntimeError(_parse_error_message(exc.read())) from exc
    except error.URLError as exc:
        raise RuntimeError(f"failed to reach control plane: {exc.reason}") from exc

    try:
        parsed = json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("control plane returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("control plane returned an invalid pairing response")
    return parsed


def pair_daemon(settings: PairSettings) -> StoredDaemonState:
    payload = {
        "pairing_code": settings.pairing_code,
        "agent": settings.agent,
        "executor_name": settings.executor_name,
        "display_name": settings.display_name,
        "hostname": settings.hostname,
        "platform": settings.platform,
        "arch": settings.arch,
        "version": settings.version,
        "workspace_roots": settings.workspace_roots,
    }
    response = _post_json(settings.pair_url, payload, version=settings.version)
    machine_id = str(response.get("machine_id") or "").strip()
    agent_instance_id = str(response.get("agent_instance_id") or "").strip()
    daemon_id = str(response.get("daemon_id") or "").strip()
    machine_token = str(response.get("machine_token") or "").strip()
    agent = str(response.get("agent") or "").strip()
    if not all([machine_id, agent_instance_id, daemon_id, machine_token, agent]):
        raise RuntimeError("control plane returned an incomplete pairing response")

    state = StoredDaemonState(
        agent=agent,
        api_url=settings.api_url,
        ws_url=_derive_ws_url(settings.api_url),
        machine_id=machine_id,
        agent_instance_id=agent_instance_id,
        machine_token=machine_token,
        daemon_id=daemon_id,
        executor_name=settings.executor_name,
        workspace_roots=settings.workspace_roots,
    )
    store = DaemonStateStore(settings.state_path)
    store.save(state)
    return state

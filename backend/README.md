# Pocket Agent Backend

`backend` is the Pocket Agent (pocage) API service. It now contains two layers:

- the existing `/v1` in-memory runtime for agent-instance-backed sessions and runs
- a new `/api` control-plane foundation for browser auth, machine pairing, and local-only metadata

## Responsibilities

- Session and message APIs: `/v1/sessions`, `/v1/sessions/{session_id}/messages`
- Agent-instance-scoped context search API: `/v1/context/search`
- Run event SSE stream: `/v1/runs/{run_id}/events`
- Run cancellation endpoint: `/v1/runs/{run_id}/cancel`
- Daemon WebSocket endpoint: `/api/daemon/ws`
- In-memory session/run registry that mirrors the current remote ACP state
- Browser auth and session cookies: `/api/auth/*`
- Current user and API keys: `/api/users/me`, `/api/api-keys`
- Machine pairing and agent inventory: `/api/machines/*`, `/api/agent-instances`, `/api/daemon/pair`, `/api/daemon/ws`

## Directory Map

- `app/main.py`: FastAPI app entry and route definitions
- `app/auth_api.py`: browser auth, refresh session, CSRF, and API key routes
- `app/control_api.py`: machine pairing and local-only control-plane routes
- `app/runtime_state.py`: in-memory session, run, permission, and event state
- `app/db_models.py`: SQLAlchemy models for auth and control-plane metadata
- `alembic/`: database migrations
- `app/events.py`: event broker and SSE formatting
- `app/executor_manager.py`: daemon connection lifecycle, remote session loading, and run dispatch
- `app/config.py`: configuration and environment parsing

## Run

Run from `backend/`:

```bash
uv sync
uv run alembic -c alembic.ini upgrade head
uv run uvicorn app.main:app --app-dir . --host 0.0.0.0 --port 8000
```

Default listen address: `http://0.0.0.0:8000`

## Environment Variables

Backend env selection is mode-aware:

- host-side backend commands load root `.env.local` by default
- Compose loads root `.env.compose` through `docker-compose.yml`
- set `POCAGE_ENV_FILE` to point at another root env file, such as `.env.production`

Templates live at root:

- `.env.local.example`
- `.env.compose.example`
- `.env.production.example`

- `POCAGE_HOST`: bind host, default `0.0.0.0`
- `POCAGE_PORT`: bind port, default `8000`
- `POCAGE_CORS_ORIGINS`: comma-separated CORS origins, default `*`
- `DATABASE_URL`: SQLAlchemy async database URL, default `sqlite+aiosqlite:///./.pocage-dev.db`
- `PUBLIC_BASE_URL`: public backend origin used by auth flows
- `FRONTEND_URL`: browser app origin used by auth emails and redirects
- `SECRET_KEY`: JWT and token signing key

## Health Check

```bash
curl http://127.0.0.1:8000/healthz
```

The control-plane health route is:

```bash
curl http://127.0.0.1:8000/api/health
```

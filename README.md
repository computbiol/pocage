# Pocket Agent (pocage)

pocage is a web control plane plus a local daemon. The web app manages auth, pairing, sessions, and run orchestration. The local `pocage` daemon connects to that control plane and executes agent work on your machine.

## Quick Start

### pocage Official Service

1. Install the local daemon prerequisites:

```bash
npm install -g @zed-industries/codex-acp
pip install pocage
```

2. Open the official pocage web app in your browser, sign in, and generate a pairing code.

3. Pair your local daemon with the official service:

```bash
pocage pair --agent codex --api-url https://pocage.toce.ai --pairing-code <pairing-code>
pocage --agent codex
```

### Self-Hosted Frontend and Backend

1. Create the Compose env file:

```bash
cp .env.compose.example .env.compose
```

2. Start the self-hosted stack:

```bash
docker compose up --build -d
```

3. Install the local daemon prerequisites:

```bash
npm install -g @zed-industries/codex-acp
pip install pocage
```

4. Open `http://127.0.0.1:8080`, create a pairing code, then run:

```bash
pocage pair --agent codex --api-url http://127.0.0.1:8080 --pairing-code <pairing-code>
pocage --agent codex
```

## Architecture Overview

Pocket Agent is a multi-process system with a web UI, an API/orchestrator, and a remote connector bridge.

```text
React Frontend  --HTTP/REST + SSE-->  FastAPI Backend
                                            |
                                            | WebSocket
                                            v
                                 Pocket Agent Connector
                                            |
                                            | ACP / JSON-RPC (stdin/stdout)
                                            v
                                        codex-acp
```

## Component Responsibilities

- `frontend`: presents the session list and chat UI, creates sessions, sends user messages, subscribes to run events, and handles permission decisions.
- `backend`: exposes public APIs, indexes remote ACP sessions, keeps active run state in memory, streams run events via SSE, and dispatches queued runs to the selected connector.
- `connector`: the publishable Python project for the local daemon; its `pocage` package maintains the backend WebSocket connection, lists and creates ACP sessions, executes assigned runs through `codex-acp`, and relays streaming updates.
- `codex-acp`: the session and prompting engine used by the connector.

## Core Concepts

- `session`: the long-lived conversation container. A session is bound to one remote ACP session on one connector machine and tracks the title, working directory, connector, and current high-level status.
- `run`: one execution turn inside a session. Each time the user sends a new message, backend creates a run, assigns it to the connector, and tracks streaming deltas, tool activity, permission requests, and terminal status for that turn.
- `transcript`: the chat history shown in the right-hand conversation view. In the current architecture it is not a separate persisted database entity; it is a session-level message view assembled from remote session history returned by `session/load` plus any in-flight local run messages.
- `message`: one user or assistant entry inside the transcript. Remote replayed messages have `run_id = null`; messages produced by an active local run carry that run's `run_id`.

Relationship summary:

- one `session` contains many `runs`
- one `session` renders one transcript view
- one transcript contains many `messages`

## End-to-End Message Flow

1. The frontend creates or selects a session.
2. The frontend posts a user message to `POST /v1/sessions/{session_id}/messages`.
3. The backend creates a queued run in memory and emits `run.queued`.
4. `ExecutorManager` assigns the queued run to the connector bound to that session via WebSocket `run.assign`.
5. The connector loads the ACP session and sends `session/prompt` to `codex-acp`.
6. The connector streams `run.delta`, `run.tool`, and `run.update` back to backend.
7. The backend appends assistant deltas and run events to its in-memory runtime registry and pushes the same events to frontend over SSE.
8. On completion, failure, or cancel, backend finalizes the run and session status and emits terminal events.

## Permission Decision Flow

1. `codex-acp` requests permission during a run.
2. The connector forwards it as `run.permission.requested`.
3. The frontend calls `POST /v1/runs/{run_id}/permissions/{approval_id}/decision`.
4. The backend forwards the decision to the connector, stores it, and emits `run.permission.decision`.

## Protocol and API Surface

- Frontend -> Backend (REST):
  - `POST /v1/sessions`
  - `GET /v1/sessions`
  - `GET /v1/sessions/{session_id}/messages`
  - `POST /v1/sessions/{session_id}/messages`
  - `GET /v1/connectors`
  - `GET /v1/context/search`
  - `GET /v1/runs/{run_id}/permissions`
  - `POST /v1/runs/{run_id}/permissions/{approval_id}/decision`
  - `POST /v1/runs/{run_id}/cancel`
- Frontend -> Backend (SSE):
  - `GET /v1/runs/{run_id}/events`
- Backend <-> Connector (WebSocket + bearer auth):
  - endpoint: `/api/daemon/ws`
  - backend -> connector: `session.create.request`, `session.list.request`, `context.search.request`, `run.assign`, `run.cancel`, `run.permission.decision`
  - connector -> backend: `session.create.result`, `session.list.result`, `context.search.result`, `run.accepted`, `run.delta`, `run.tool`, `run.update`, `run.permission.requested`, `run.completed`, `run.failed`
- Connector <-> codex-acp (JSON-RPC):
  - main methods: `session/new`, `session/list`, `session/load`, `session/prompt`, `session/cancel`, `session/set_mode`
  - callbacks: `session/update`, `session/request_permission`

## Local Development

1. Create the local development env file:

```bash
cp .env.local.example .env.local
```

2. Install project dependencies:

```bash
cd backend && uv sync
cd ../frontend && npm install
cd ../connector && uv sync
cd ../..
npm install -g @zed-industries/codex-acp
```

3. Start PostgreSQL:

```bash
docker compose up -d db
```

4. Start the backend and frontend on the host with a fresh local database reset:

```bash
./scripts/dev-up.sh
```

Reuse the existing local database state only when you need it:

```bash
./scripts/dev-up.sh --no-fresh
```

Reset the local development database without starting the host-side services:

```bash
./scripts/dev-reset.sh
```

Stop the host-side services with:

```bash
./scripts/dev-down.sh
```

5. Open `http://127.0.0.1:5173`, create a pairing code, then pair the daemon:

```bash
cd connector
uv run pocage pair --agent codex --api-url http://127.0.0.1:8000 --pairing-code <pairing-code>
uv run pocage --agent codex
```

## Environment Files

- `.env.example`: index file that points to the right template
- `.env.local.example` -> `.env.local`: host-side development
- `.env.compose.example` -> `.env.compose`: Docker Compose
- `.env.production.example` -> `.env.production`: deployment placeholders

Backend env selection:

- host-side backend commands default to `.env.local`
- Docker Compose reads `.env.compose`
- set `POCAGE_ENV_FILE=.env.production` to force a different backend env file

## Production Notes

```bash
cp .env.production.example .env.production
```
- The official public URL is `https://pocage.toce.ai`
- Replace database credentials, SMTP credentials, and `SECRET_KEY`
- If you run the backend directly, use `POCAGE_ENV_FILE=.env.production`
- Keep `VITE_API_URL` empty when the frontend should use the same public origin as the reverse proxy

Production Docker Compose:

```bash
docker compose -f docker-compose.production.yml up -d --build
```

Notes:

- `docker-compose.production.yml` includes a bundled Postgres service and persists it in the `postgres_data` volume
- Caddy listens on `:8080`
- if you expose `:8080` directly without upstream HTTPS, set `PUBLIC_BASE_URL`, `FRONTEND_URL`, and `POCAGE_CORS_ORIGINS` to `http://<host>:8080`, and set `COOKIE_SECURE=false`
- if you front the site with Cloudflare or another browser-focused WAF, allow non-browser traffic to `/api/daemon/pair` and `/api/daemon/ws`; browser integrity checks can block the `pocage` CLI
- terminate TLS upstream if you still want HTTPS on the public edge
- Caddy state persists in the named volumes `caddy_data` and `caddy_config`

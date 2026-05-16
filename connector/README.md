# Pocket Agent Connector

`connector/` is the publishable Python project for the Pocket Agent local daemon, and its `pocage` package contains the runtime code. It pairs with backend, connects over an authenticated WebSocket, talks to an ACP-compatible agent backend, and streams ACP updates back to backend. Today the only supported agent is `codex`.

## Environment

This module is managed as its own `uv` project. Keep its virtual environment isolated from `backend/`.

## Pair and Run

Run from `connector/`:

```bash
uv sync
uv run pocage pair --agent codex --api-url http://127.0.0.1:8000 --pairing-code <code-from-web>
uv run pocage --agent codex
```

Use the API URL that matches your chosen mode:

- local development with `.env.local`: `http://127.0.0.1:8000`
- Docker Compose with `.env.compose`: `http://127.0.0.1:8080`

The matching templates live at the repository root as `.env.local.example` and `.env.compose.example`.

Before the first run, install the `codex` ACP backend globally so it is available on `PATH`:

```bash
npm install -g @zed-industries/codex-acp
```

## Package Development

- `uv sync` installs `pocage` into this environment as an editable package for local development.
- The `pocage` console script is defined in `pyproject.toml`.
- Use `uv run pocage ...` for day-to-day debugging against local source changes.

## Release Verification

Before publishing to PyPI, verify the non-editable install path:

```bash
./scripts/verify-release-install.sh
```

The script:

- builds both `sdist` and `wheel`
- creates fresh temporary virtual environments
- installs each artifact as a non-editable package
- runs `uv pip check`
- verifies `import pocage` resolves from `site-packages`, not the source tree
- runs `pocage --help` as a console-script smoke test

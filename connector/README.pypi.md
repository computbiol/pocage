# pocage

`pocage` is the local daemon for Pocket Agent.

It pairs your machine with a Pocket Agent control plane, connects back to the backend over an authenticated WebSocket, runs AI coding sessions locally through an ACP-compatible agent backend, and streams run updates back to the web app.

Today the supported agent backend is `codex`.

## Requirements

- Python 3.11 or newer
- `codex-acp` installed on `PATH`

Install `codex-acp`:

```bash
npm install -g @zed-industries/codex-acp
```

## Install

```bash
pip install pocage
```

## Quick Start

1. Open the Pocket Agent web app and generate a pairing code.
2. Pair this machine with the control plane:

```bash
pocage pair --agent codex --api-url https://pocage.toce.ai --pairing-code <pairing-code>
```

3. Start the local daemon:

```bash
pocage --agent codex
```

For self-hosted or local development deployments, replace `--api-url` with your own backend URL.

## What This Package Does

- pairs a local machine to a Pocket Agent backend
- loads and resumes ACP sessions
- executes assigned runs through `codex-acp`
- relays streaming updates, tool activity, and permission requests back to the backend

## Links

- Homepage: https://pocage.toce.ai
- Repository: https://github.com/computbiol/pocage
- Issues: https://github.com/computbiol/pocage/issues

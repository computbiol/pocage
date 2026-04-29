#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/logs/dev"
PID_DIR="$RUN_DIR/pids"
ROOT_ENV_FILE="$ROOT_DIR/.env.local"
FRESH_START=1

print_usage() {
  cat <<'EOF'
Usage: ./scripts/dev-up.sh [--fresh] [--no-fresh]

Options:
  --fresh     Reset the local development database before starting services.
              This is the default behavior.
  --no-fresh  Reuse the current local development database state.
  -h, --help  Show this help message.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh)
      FRESH_START=1
      ;;
    --no-fresh)
      FRESH_START=0
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      print_usage >&2
      exit 1
      ;;
  esac
  shift
done

mkdir -p "$RUN_DIR" "$PID_DIR"

BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

BACKEND_LOG="$RUN_DIR/backend.log"
FRONTEND_LOG="$RUN_DIR/frontend.log"

UV_BIN="${UV_BIN:-uv}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
BACKEND_HOST="${POCAGE_HOST:-127.0.0.1}"
BACKEND_PORT="${POCAGE_PORT:-8000}"
FRONTEND_HOST="${POCAGE_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${POCAGE_FRONTEND_PORT:-5173}"
API_URL="${POCAGE_API_URL:-http://$BACKEND_HOST:$BACKEND_PORT}"
VITE_API_URL_VALUE="${VITE_API_URL:-$API_URL}"

append_csv_value() {
  local csv="$1"
  local value="$2"

  if [[ -z "$csv" ]]; then
    printf '%s\n' "$value"
    return
  fi

  IFS=',' read -r -a items <<<"$csv"
  local item
  for item in "${items[@]}"; do
    if [[ "${item// /}" == "$value" ]]; then
      printf '%s\n' "$csv"
      return
    fi
  done

  printf '%s,%s\n' "$csv" "$value"
}

if [[ ! -f "$ROOT_ENV_FILE" ]]; then
  echo "missing local env file: $ROOT_ENV_FILE"
  echo "copy .env.local.example to .env.local before starting local services"
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ROOT_ENV_FILE"
set +a

BACKEND_HOST="${POCAGE_HOST:-127.0.0.1}"
BACKEND_PORT="${POCAGE_PORT:-8000}"
FRONTEND_HOST="${POCAGE_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${POCAGE_FRONTEND_PORT:-5173}"
API_URL="${POCAGE_API_URL:-http://$BACKEND_HOST:$BACKEND_PORT}"
VITE_API_URL_VALUE="${VITE_API_URL:-$API_URL}"
BACKEND_ORIGIN="http://$BACKEND_HOST:$BACKEND_PORT"
FRONTEND_ORIGIN="http://$FRONTEND_HOST:$FRONTEND_PORT"
PUBLIC_BASE_URL="$BACKEND_ORIGIN"
FRONTEND_URL="$FRONTEND_ORIGIN"
POCAGE_CORS_ORIGINS="$(append_csv_value "${POCAGE_CORS_ORIGINS:-}" "$FRONTEND_ORIGIN")"
export PUBLIC_BASE_URL FRONTEND_URL POCAGE_CORS_ORIGINS

if ! command -v "$UV_BIN" >/dev/null 2>&1; then
  echo "uv not found: $UV_BIN"
  echo "install uv and ensure it is available on PATH"
  exit 1
fi

if [[ -z "$NODE_BIN" || ! -x "$NODE_BIN" ]]; then
  echo "node not found"
  echo "install Node.js 20.19+ and ensure it is available on PATH"
  exit 1
fi

run_backend_migrations() {
  echo "running backend migrations..."
  (
    cd "$ROOT_DIR/backend"
    env POCAGE_ENV_FILE="$ROOT_ENV_FILE" "$UV_BIN" run --locked alembic -c alembic.ini upgrade head
  )
  echo "backend migrations complete"
}

is_alive() {
  local pid="$1"
  kill -0 "$pid" >/dev/null 2>&1
}

command_for_pid() {
  local pid="$1"
  ps -o command= -p "$pid" 2>/dev/null || true
}

cwd_for_pid() {
  local pid="$1"
  lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

pid_belongs_to_root() {
  local pid="$1"
  local command
  local cwd
  command="$(command_for_pid "$pid")"
  cwd="$(cwd_for_pid "$pid")"
  [[ -n "$command" && "$command" == *"$ROOT_DIR"* ]] || [[ -n "$cwd" && "$cwd" == "$ROOT_DIR"* ]]
}

cleanup_stale_pid_file() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && is_alive "$pid"; then
    if pid_belongs_to_root "$pid"; then
      return
    fi
  fi
  rm -f "$pid_file"
}

start_service() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3
  local cmd="$*"

  cleanup_stale_pid_file "$pid_file"

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if ! pid_belongs_to_root "$pid"; then
      rm -f "$pid_file"
    else
    echo "$name already running (pid=$pid)"
    return
    fi
  fi

  nohup bash -lc "$cmd" >"$log_file" 2>&1 &
  local pid=$!
  echo "$pid" >"$pid_file"

  sleep 1
  if ! is_alive "$pid"; then
    echo "failed to start $name; tailing log:"
    tail -n 40 "$log_file" || true
    rm -f "$pid_file"
    exit 1
  fi
  echo "started $name (pid=$pid)"
}

wait_http() {
  local name="$1"
  local url="$2"
  local retries="${3:-30}"
  local delay_sec="${4:-1}"

  local i
  for ((i = 1; i <= retries; i += 1)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name ready: $url"
      return
    fi
    sleep "$delay_sec"
  done

  echo "$name not ready: $url"
  exit 1
}

if [[ "$FRESH_START" -eq 1 ]]; then
  echo "fresh local startup requested; resetting local development database first..."
  "$ROOT_DIR/scripts/dev-reset.sh"
fi

run_backend_migrations

start_service \
  "backend" \
  "$BACKEND_PID_FILE" \
  "$BACKEND_LOG" \
  "cd '$ROOT_DIR/backend' && exec env POCAGE_ENV_FILE='$ROOT_ENV_FILE' '$UV_BIN' run --locked uvicorn app.main:app --app-dir . --host '$BACKEND_HOST' --port '$BACKEND_PORT'"

wait_http "backend" "http://$BACKEND_HOST:$BACKEND_PORT/healthz" 30 1

start_service \
  "frontend" \
  "$FRONTEND_PID_FILE" \
  "$FRONTEND_LOG" \
  "cd '$ROOT_DIR/frontend' && exec env VITE_API_URL='$VITE_API_URL_VALUE' '$NODE_BIN' '$ROOT_DIR/frontend/node_modules/vite/bin/vite.js' --host '$FRONTEND_HOST' --port '$FRONTEND_PORT' --strictPort </dev/null"

wait_http "frontend" "http://$FRONTEND_HOST:$FRONTEND_PORT/" 30 1

echo
echo "Pocket Agent control plane is running."
echo "backend:   http://$BACKEND_HOST:$BACKEND_PORT"
echo "frontend:  http://$FRONTEND_HOST:$FRONTEND_PORT"
echo "logs:      $RUN_DIR"
echo "pids:      $PID_DIR"
echo
echo "Next step for a local codex daemon:"
echo "  cd '$ROOT_DIR/connector/pocage'"
echo "  uv run pocage pair --agent codex --api-url '$API_URL' --pairing-code <code-from-web>"
echo "  uv run pocage --agent codex"

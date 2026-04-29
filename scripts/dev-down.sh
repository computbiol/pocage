#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$ROOT_DIR/logs/dev/pids"

BACKEND_PID_FILE="$PID_DIR/backend.pid"
CONNECTOR_PID_FILE="$PID_DIR/connector.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

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

stop_by_pid_file() {
  local name="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    echo "$name not running (no pid file)"
    return
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    rm -f "$pid_file"
    echo "$name not running (empty pid file removed)"
    return
  fi

  if ! is_alive "$pid"; then
    rm -f "$pid_file"
    echo "$name not running (stale pid file removed)"
    return
  fi

  if ! pid_belongs_to_root "$pid"; then
    rm -f "$pid_file"
    echo "$name not running (pid belongs to another checkout; stale pid file removed)"
    return
  fi

  kill "$pid" >/dev/null 2>&1 || true

  local i
  for ((i = 1; i <= 10; i += 1)); do
    if ! is_alive "$pid"; then
      rm -f "$pid_file"
      echo "stopped $name (pid=$pid)"
      return
    fi
    sleep 1
  done

  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$pid_file"
  echo "force stopped $name (pid=$pid)"
}

stop_by_pid_file "connector" "$CONNECTOR_PID_FILE"
stop_by_pid_file "frontend" "$FRONTEND_PID_FILE"
stop_by_pid_file "backend" "$BACKEND_PID_FILE"

echo
echo "Pocket Agent services are stopped."

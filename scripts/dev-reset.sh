#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_SERVICE="db"
DB_DATA_MOUNT="/var/lib/postgresql/data"

print_usage() {
  cat <<'EOF'
Usage: ./scripts/dev-reset.sh

Stops local host-side services, removes the Docker volume used by the local
development PostgreSQL service, recreates the database container, and waits
until PostgreSQL is healthy again.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  print_usage
  exit 0
fi

if [[ $# -gt 0 ]]; then
  echo "unknown argument: $1" >&2
  print_usage >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found"
  echo "install Docker and ensure it is available on PATH"
  exit 1
fi

docker_compose() {
  docker compose "$@"
}

db_container_id() {
  docker_compose ps -q "$DB_SERVICE" 2>/dev/null | tr -d '\r'
}

db_volume_name() {
  local container_id="$1"
  docker inspect -f "{{ range .Mounts }}{{ if eq .Destination \"$DB_DATA_MOUNT\" }}{{ .Name }}{{ end }}{{ end }}" \
    "$container_id" 2>/dev/null | tr -d '\r'
}

wait_for_db() {
  local retries="${1:-30}"
  local delay_sec="${2:-1}"
  local i
  for ((i = 1; i <= retries; i += 1)); do
    local container_id
    local health_status
    container_id="$(db_container_id)"
    if [[ -n "$container_id" ]]; then
      health_status="$(
        docker inspect -f '{{ if .State.Health }}{{ .State.Health.Status }}{{ else }}running{{ end }}' \
          "$container_id" 2>/dev/null | tr -d '\r'
      )"
      if [[ "$health_status" == "healthy" || "$health_status" == "running" ]]; then
        echo "database ready: service=$DB_SERVICE container=$container_id status=$health_status"
        return
      fi
    fi
    sleep "$delay_sec"
  done

  echo "database not ready after reset: service=$DB_SERVICE" >&2
  exit 1
}

echo "stopping local development services..."
"$ROOT_DIR/scripts/dev-down.sh"

echo "preparing database container for reset..."
docker_compose up -d "$DB_SERVICE" >/dev/null

container_id="$(db_container_id)"
if [[ -z "$container_id" ]]; then
  echo "failed to resolve database container for service: $DB_SERVICE" >&2
  exit 1
fi

volume_name="$(db_volume_name "$container_id")"
if [[ -z "$volume_name" ]]; then
  echo "failed to resolve database volume for service: $DB_SERVICE" >&2
  exit 1
fi

echo "removing database container..."
docker_compose stop "$DB_SERVICE" >/dev/null
docker_compose rm -f "$DB_SERVICE" >/dev/null

echo "removing database volume: $volume_name"
docker volume rm -f "$volume_name" >/dev/null

echo "recreating database container..."
docker_compose up -d "$DB_SERVICE" >/dev/null
wait_for_db 40 1

echo "local development database reset complete"

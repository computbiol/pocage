#!/bin/sh
set -eu

for _ in $(seq 1 30); do
  if uv run --frozen alembic -c alembic.ini upgrade head; then
    exec uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port 8000
  fi
  sleep 2
done

echo "Database migration failed after retries." >&2
exit 1

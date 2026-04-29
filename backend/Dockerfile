FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend ./
RUN uv sync --frozen --no-dev
RUN chmod +x /app/backend/entrypoint.sh

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app/backend
COPY --from=build /app/backend /app/backend

EXPOSE 8000
ENTRYPOINT ["/app/backend/entrypoint.sh"]

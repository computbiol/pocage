from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


def _normalize_origin(value: str) -> str | None:
    trimmed = value.strip()
    if not trimmed:
        return None
    parsed = urlsplit(trimmed)
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _local_origin_variants(origin: str) -> set[str]:
    normalized = _normalize_origin(origin)
    if normalized is None:
        return set()

    parsed = urlsplit(normalized)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return {normalized}

    counterpart = "127.0.0.1" if parsed.hostname == "localhost" else "localhost"
    ports = {parsed.port} if parsed.port is not None else set()
    if parsed.port in {5173, 8000, 8080}:
        ports.update({5173, 8000, 8080})

    variants = {normalized}
    if not ports:
        variants.add(urlunsplit((parsed.scheme, counterpart, "", "", "")).rstrip("/"))
        return variants

    for port in ports:
        variants.add(urlunsplit((parsed.scheme, f"{parsed.hostname}:{port}", "", "", "")).rstrip("/"))
        variants.add(urlunsplit((parsed.scheme, f"{counterpart}:{port}", "", "", "")).rstrip("/"))
    return variants


def _expand_local_dev_origins(origins: list[str], frontend_url: str, public_base_url: str) -> list[str]:
    expanded: set[str] = set()
    for candidate in [*origins, frontend_url, public_base_url]:
        normalized = _normalize_origin(candidate)
        if normalized is None:
            continue
        expanded.update(_local_origin_variants(normalized))
    return sorted(expanded)


def _is_running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def _rewrite_compose_database_host(url: str) -> str:
    if _is_running_in_container():
        return url

    parsed = urlsplit(url)
    if parsed.hostname != "db":
        return url

    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password is not None:
            auth = f"{auth}:{parsed.password}"
        auth = f"{auth}@"

    host = "127.0.0.1"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"

    return urlunsplit(parsed._replace(netloc=f"{auth}{host}"))


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_root_env_filename(environment: str | None) -> str:
    normalized = (environment or "").strip().lower()
    if normalized == "production":
        return ".env.production"
    return ".env.local"


def _resolve_settings_env_file() -> str | None:
    explicit = os.environ.get("POCAGE_ENV_FILE", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        if not candidate.exists():
            raise FileNotFoundError(f"Configured POCAGE_ENV_FILE was not found: {candidate}")
        return str(candidate)

    candidate = PROJECT_ROOT / _default_root_env_filename(os.environ.get("ENVIRONMENT"))
    if candidate.exists():
        return str(candidate)
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "pocage-backend"
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api"
    public_base_url: str = "http://localhost:8080"
    frontend_url: str = "http://localhost:8080"
    database_url: str = "sqlite+aiosqlite:///./.pocage-dev.db"
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    access_cookie_name: str = "access_token"
    refresh_cookie_name: str = "refresh_token"
    csrf_cookie_name: str = "csrf_token"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str | None = None
    smtp_use_starttls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: int = 10
    host: str = Field(default="0.0.0.0", alias="POCAGE_HOST")
    port: int = Field(default=8000, alias="POCAGE_PORT")
    cors_origins_raw: str = Field(default="*", alias="POCAGE_CORS_ORIGINS")

    @model_validator(mode="after")
    def normalize_local_dev_values(self) -> "Settings":
        self.database_url = _rewrite_compose_database_host(self.database_url)
        if self.smtp_use_ssl and self.smtp_use_starttls:
            raise ValueError("SMTP_USE_SSL and SMTP_USE_STARTTLS cannot both be enabled.")
        if self.smtp_host and self.smtp_port == 465 and not self.smtp_use_ssl:
            raise ValueError("SMTP port 465 requires SMTP_USE_SSL=true and SMTP_USE_STARTTLS=false.")
        return self

    @property
    def cors_origins(self) -> list[str]:
        if self.cors_origins_raw == "*":
            return ["*"]
        origins = _split_csv(self.cors_origins_raw)
        if self.environment == "development":
            return _expand_local_dev_origins(origins, self.frontend_url, self.public_base_url)
        return origins


@lru_cache
def get_settings() -> Settings:
    env_file = _resolve_settings_env_file()
    return Settings(_env_file=env_file, _env_file_encoding="utf-8")

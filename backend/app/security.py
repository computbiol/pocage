from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import HTTPException, Request, Response, status

from .config import get_settings


settings = get_settings()
JWT_ALGORITHM = "HS256"


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def create_access_token(*, user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    expires_at = utcnow() + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "iat": int(utcnow().timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, object]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.") from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type.")
    return payload


def generate_secret_token(prefix: str = "") -> str:
    token = secrets.token_urlsafe(48)
    return f"{prefix}{token}" if prefix else token


def hash_secret(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    max_age_access = settings.access_token_ttl_minutes * 60
    max_age_refresh = settings.refresh_token_ttl_days * 24 * 60 * 60
    response.set_cookie(
        key=settings.access_cookie_name,
        value=access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=max_age_access,
        path="/",
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=max_age_refresh,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.access_cookie_name, path="/")
    response.delete_cookie(settings.refresh_cookie_name, path="/")


def ensure_csrf_cookie(response: Response, request: Request, csrf_token: str | None = None) -> str:
    csrf_token = csrf_token or request.cookies.get(settings.csrf_cookie_name) or generate_csrf_token()
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        path="/",
    )
    return csrf_token

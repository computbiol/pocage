from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import uuid
from collections.abc import AsyncGenerator
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlencode

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users.password import PasswordHelper
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from .config import _normalize_origin, get_settings
from .db import get_user_db
from .db_models import User
from .passwords import validate_password_policy


logger = logging.getLogger(__name__)
settings = get_settings()
password_helper = PasswordHelper()


class EmailService:
    async def send(self, *, to_email: str, subject: str, html_body: str, text_body: str) -> None:
        if settings.smtp_host is None or settings.smtp_from_email is None:
            logger.info("email delivery disabled for %s: %s", to_email, subject)
            return
        try:
            await asyncio.to_thread(
                self._send_via_smtp,
                to_email=to_email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        except (smtplib.SMTPException, OSError):
            logger.exception("email delivery failed for %s: %s", to_email, subject)

    def _send_via_smtp(self, *, to_email: str, subject: str, html_body: str, text_body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = (
            formataddr((settings.smtp_from_name, settings.smtp_from_email))
            if settings.smtp_from_name
            else settings.smtp_from_email
        )
        message["To"] = to_email
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        context = ssl.create_default_context()
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
                context=context,
            ) as client:
                self._authenticate(client)
                client.send_message(message)
            return

        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as client:
            client.ehlo()
            if settings.smtp_use_starttls:
                client.starttls(context=context)
                client.ehlo()
            self._authenticate(client)
            client.send_message(message)

    def _authenticate(self, client: smtplib.SMTP) -> None:
        if settings.smtp_username is None:
            return
        if settings.smtp_password is None:
            raise RuntimeError("SMTP_PASSWORD must be configured when SMTP_USERNAME is set.")
        client.login(settings.smtp_username, settings.smtp_password, initial_response_ok=False)


email_service = EmailService()


def _frontend_origin_for_request(request: Request | None) -> str:
    if request is not None:
        origin = _normalize_origin(request.headers.get("origin", ""))
        if origin is not None:
            return origin

        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host:
            scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
            normalized = _normalize_origin(f"{scheme}://{host}")
            if normalized is not None:
                return normalized

        normalized = _normalize_origin(str(request.base_url))
        if normalized is not None:
            return normalized

    return settings.frontend_url


def _log_development_auth_link(kind: str, *, user_email: str, url: str) -> None:
    if settings.environment != "development":
        return
    logger.info("development %s link for %s: %s", kind, user_email, url)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    async def validate_password(self, password: str, user: User) -> None:
        validate_password_policy(password)

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("new user registered: %s", user.email)
        await self.request_verify(user, request)

    async def on_after_forgot_password(
        self,
        user: User,
        token: str,
        request: Request | None = None,
    ) -> None:
        query = urlencode({"token": token})
        frontend_origin = _frontend_origin_for_request(request)
        reset_url = f"{frontend_origin}/reset-password?{query}"
        _log_development_auth_link("password reset", user_email=user.email, url=reset_url)
        await email_service.send(
            to_email=user.email,
            subject="Reset your password",
            html_body=f"<p>Reset your password by visiting <a href='{reset_url}'>{reset_url}</a>.</p>",
            text_body=f"Reset your password by visiting {reset_url}",
        )

    async def on_after_request_verify(
        self,
        user: User,
        token: str,
        request: Request | None = None,
    ) -> None:
        query = urlencode({"token": token})
        frontend_origin = _frontend_origin_for_request(request)
        verify_url = f"{frontend_origin}/verify?{query}"
        _log_development_auth_link("email verification", user_email=user.email, url=verify_url)
        await email_service.send(
            to_email=user.email,
            subject="Verify your email",
            html_body=f"<p>Verify your email by visiting <a href='{verify_url}'>{verify_url}</a>.</p>",
            text_body=f"Verify your email by visiting {verify_url}",
        )

    async def on_after_verify(self, user: User, request: Request | None = None) -> None:
        logger.info("user verified: %s", user.email)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


cookie_transport = CookieTransport(
    cookie_name=settings.access_cookie_name,
    cookie_max_age=settings.access_token_ttl_minutes * 60,
    cookie_secure=settings.cookie_secure,
    cookie_samesite=settings.cookie_samesite,
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.secret_key,
        lifetime_seconds=settings.access_token_ttl_minutes * 60,
    )


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)


async def get_password_helper() -> PasswordHelper:
    return password_helper

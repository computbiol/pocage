from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.auth_api import USER_NOT_VERIFIED_CODE, _ensure_user_allowed
from app import auth_users
from app.auth_users import UserManager
from app.config import Settings, _default_root_env_filename, _resolve_settings_env_file, _rewrite_compose_database_host
from app.events import EventBroker
from app.executor_manager import ExecutorManager
from app.models import DaemonHello
from app.runtime_state import RuntimeState


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class ConfigBehaviorTests(unittest.TestCase):
    def test_rewrite_compose_database_host_for_local_dev(self) -> None:
        with patch("app.config._is_running_in_container", return_value=False):
            self.assertEqual(
                _rewrite_compose_database_host("postgresql+asyncpg://postgres:postgres@db:5432/pocage"),
                "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/pocage",
            )

    def test_preserve_compose_database_host_in_container(self) -> None:
        with patch("app.config._is_running_in_container", return_value=True):
            self.assertEqual(
                _rewrite_compose_database_host("postgresql+asyncpg://postgres:postgres@db:5432/pocage"),
                "postgresql+asyncpg://postgres:postgres@db:5432/pocage",
            )

    def test_reject_conflicting_smtp_tls_settings(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_use_ssl=True,
                smtp_use_starttls=True,
            )

    def test_reject_port_465_without_ssl(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_use_ssl=False,
                smtp_use_starttls=True,
            )

    def test_allow_port_465_with_ssl(self) -> None:
        settings = Settings(
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_use_ssl=True,
            smtp_use_starttls=False,
        )

        self.assertTrue(settings.smtp_use_ssl)
        self.assertFalse(settings.smtp_use_starttls)

    def test_expand_local_dev_origins_for_loopback_frontend(self) -> None:
        settings = Settings(
            environment="development",
            public_base_url="http://localhost:8080",
            frontend_url="http://localhost:8080",
            cors_origins_raw="http://localhost:8080",
        )

        self.assertIn("http://localhost:5173", settings.cors_origins)
        self.assertIn("http://127.0.0.1:5173", settings.cors_origins)
        self.assertIn("http://127.0.0.1:8080", settings.cors_origins)

    def test_default_root_env_filename_uses_mode(self) -> None:
        self.assertEqual(_default_root_env_filename("development"), ".env.local")
        self.assertEqual(_default_root_env_filename("production"), ".env.production")
        self.assertEqual(_default_root_env_filename(None), ".env.local")

    def test_resolve_settings_env_file_prefers_explicit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            compose_env = project_root / ".env.compose"
            compose_env.write_text("ENVIRONMENT=development\n", encoding="utf-8")

            with patch("app.config.PROJECT_ROOT", project_root), patch.dict(
                os.environ, {"POCAGE_ENV_FILE": ".env.compose"}, clear=True
            ):
                self.assertEqual(_resolve_settings_env_file(), str(compose_env))

    def test_resolve_settings_env_file_defaults_to_local_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            local_env = project_root / ".env.local"
            local_env.write_text("ENVIRONMENT=development\n", encoding="utf-8")

            with patch("app.config.PROJECT_ROOT", project_root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(_resolve_settings_env_file(), str(local_env))


class AuthGuardTests(unittest.TestCase):
    def test_reject_unverified_user(self) -> None:
        with self.assertRaises(HTTPException) as context:
            _ensure_user_allowed(SimpleNamespace(is_active=True, is_verified=False))

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail["code"], USER_NOT_VERIFIED_CODE)


class UserManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_frontend_origin_prefers_request_origin(self) -> None:
        request = SimpleNamespace(
            headers={"origin": "http://127.0.0.1:8080"},
            url=SimpleNamespace(scheme="http"),
            base_url="http://backend:8000/",
        )

        with patch.object(auth_users.settings, "frontend_url", "http://127.0.0.1:5173"):
            self.assertEqual(auth_users._frontend_origin_for_request(request), "http://127.0.0.1:8080")

    def test_frontend_origin_falls_back_to_settings_without_request(self) -> None:
        with patch.object(auth_users.settings, "frontend_url", "http://localhost:8080"):
            self.assertEqual(auth_users._frontend_origin_for_request(None), "http://localhost:8080")

    def test_log_development_auth_link_only_in_development(self) -> None:
        with (
            patch.object(auth_users.settings, "environment", "development"),
            patch.object(auth_users.logger, "info") as logger_info,
        ):
            auth_users._log_development_auth_link(
                "email verification",
                user_email="moonswing@example.com",
                url="http://127.0.0.1:5173/verify?token=abc",
            )

        logger_info.assert_called_once_with(
            "development %s link for %s: %s",
            "email verification",
            "moonswing@example.com",
            "http://127.0.0.1:5173/verify?token=abc",
        )

    def test_log_development_auth_link_skips_production(self) -> None:
        with (
            patch.object(auth_users.settings, "environment", "production"),
            patch.object(auth_users.logger, "info") as logger_info,
        ):
            auth_users._log_development_auth_link(
                "password reset",
                user_email="moonswing@example.com",
                url="https://pocage.example.com/reset-password?token=abc",
            )

        logger_info.assert_not_called()

    async def test_on_after_register_requests_verification(self) -> None:
        manager = UserManager(object())
        manager.request_verify = AsyncMock()
        user = SimpleNamespace(email="moonswing@example.com")

        await manager.on_after_register(user)

        manager.request_verify.assert_awaited_once_with(user, None)


class ExecutorManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_rejects_duplicate_daemon_with_friendly_message(self) -> None:
        manager = ExecutorManager(RuntimeState(), EventBroker())
        hello = DaemonHello(
            type="daemon.hello",
            machine_id="machine-1",
            agent_instance_id="agent-1",
            daemon_id="daemon-1",
            name="codex@test",
            version="0.1.0",
            agent="codex",
            hostname="test-host",
            workspace_roots=["/tmp/ws"],
            capabilities={},
        )

        await manager.register(_FakeWebSocket(), hello, machine_id="machine-1", agent_instance_id="agent-1")

        with self.assertRaises(ValueError) as context:
            await manager.register(_FakeWebSocket(), hello, machine_id="machine-1", agent_instance_id="agent-1")

        self.assertEqual(
            str(context.exception),
            "This machine is already connected. Stop the existing pocage process before starting another one.",
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from datetime import datetime, UTC
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a simple SMTP smoke-test email using the loaded backend settings.")
    parser.add_argument(
        "--to",
        dest="to_email",
        default=None,
        help="Recipient email address. Defaults to SMTP_FROM_EMAIL from the loaded settings.",
    )
    parser.add_argument(
        "--subject",
        default="pocage SMTP smoke test",
        help="Email subject line.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()

    if not settings.smtp_host or not settings.smtp_from_email:
        print("SMTP is not configured: SMTP_HOST and SMTP_FROM_EMAIL are required.", file=sys.stderr)
        return 2

    to_email = args.to_email or settings.smtp_from_email
    if not to_email:
        print("No recipient address available. Pass --to or set SMTP_FROM_EMAIL.", file=sys.stderr)
        return 2

    message = EmailMessage()
    message["Subject"] = args.subject
    message["From"] = (
        formataddr((settings.smtp_from_name, settings.smtp_from_email))
        if settings.smtp_from_name
        else settings.smtp_from_email
    )
    message["To"] = to_email

    timestamp = datetime.now(UTC).isoformat()
    message.set_content(
        "\n".join(
            [
                "This is a pocage SMTP smoke-test email.",
                "",
                f"UTC timestamp: {timestamp}",
                f"SMTP host: {settings.smtp_host}",
                f"SMTP port: {settings.smtp_port}",
                f"SMTP SSL: {settings.smtp_use_ssl}",
                f"SMTP STARTTLS: {settings.smtp_use_starttls}",
            ]
        )
    )

    context = ssl.create_default_context()
    print(f"Connecting to {settings.smtp_host}:{settings.smtp_port}")
    print(f"Sender: {settings.smtp_from_email}")
    print(f"Recipient: {to_email}")
    print(f"SSL={settings.smtp_use_ssl} STARTTLS={settings.smtp_use_starttls}")

    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=context,
        ) as client:
            authenticate(client, settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        print("SMTP smoke test succeeded via SMTP_SSL.")
        return 0

    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as client:
        client.ehlo()
        if settings.smtp_use_starttls:
            client.starttls(context=context)
            client.ehlo()
        authenticate(client, settings.smtp_username, settings.smtp_password)
        client.send_message(message)

    print("SMTP smoke test succeeded via SMTP.")
    return 0


def authenticate(client: smtplib.SMTP, username: str | None, password: str | None) -> None:
    if username is None:
        return
    if password is None:
        raise RuntimeError("SMTP_PASSWORD must be configured when SMTP_USERNAME is set.")
    client.login(username, password, initial_response_ok=False)


if __name__ == "__main__":
    raise SystemExit(main())

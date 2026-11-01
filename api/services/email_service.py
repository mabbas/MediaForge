"""Transactional email service using Mailpit / SMTP (STEP 02-B2A).

Uses standard library smtplib executed in an async worker thread, avoiding external
heavyweight async SMTP dependencies. Designed for Mailpit in dev/test environments.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
import logging
import smtplib
from typing import Optional

from api.config import get_api_settings

logger = logging.getLogger(__name__)


def _send_smtp_sync(
    host: str,
    port: int,
    message: EmailMessage,
    username: Optional[str] = None,
    password: Optional[str] = None,
    use_tls: bool = False,
) -> None:
    """Synchronous SMTP sender run via asyncio.to_thread."""
    with smtplib.SMTP(host=host, port=port, timeout=10.0) as server:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.send_message(message)


class EmailService:
    """Service for sending transactional notification and verification emails."""

    @classmethod
    async def send_verification_email(
        cls,
        to_email: str,
        tenant_name: str,
        raw_token: str,
        expires_hours: int = 24,
    ) -> bool:
        """Send account verification email with raw token.

        Returns True if sent successfully, False otherwise.
        """
        settings = get_api_settings()

        msg = EmailMessage()
        msg["Subject"] = "Verify your MediaForge account"
        msg["From"] = settings.smtp_from
        msg["To"] = to_email

        verification_url = f"http://127.0.0.1:58000/api/v1/auth/verify-email"

        text_content = f"""Welcome to MediaForge!

Thank you for registering organization '{tenant_name}'.

To complete your registration and activate your account, please verify your email address.

Your verification token:
{raw_token}

Submit this token to {verification_url} (or use your client application).

This verification token is valid for {expires_hours} hours.
If you did not initiate this registration, please disregard this email.

— The MediaForge Security Team
"""
        msg.set_content(text_content)

        try:
            await asyncio.to_thread(
                _send_smtp_sync,
                host=settings.smtp_host,
                port=settings.smtp_port,
                message=msg,
                username=settings.smtp_user,
                password=settings.smtp_password,
                use_tls=settings.smtp_tls,
            )
            logger.info("Verification email successfully sent to %s via Mailpit", to_email)
            return True
        except Exception as exc:
            logger.error("Failed to send verification email to %s: %s", to_email, exc)
            return False

    @classmethod
    async def send_password_reset_email(
        cls,
        to_email: str,
        raw_token: str,
        expires_minutes: int = 30,
    ) -> bool:
        """Send password reset recovery email with raw single-use token.

        Returns True if sent successfully, False otherwise.
        """
        settings = get_api_settings()

        msg = EmailMessage()
        msg["Subject"] = "Reset your MediaForge password"
        msg["From"] = settings.smtp_from
        msg["To"] = to_email

        reset_url = "http://127.0.0.1:58000/api/v1/auth/reset-password"

        text_content = f"""MediaForge Password Recovery

We received a request to reset the password for your MediaForge account.

Your password recovery token:
{raw_token}

To complete the password reset, submit this token along with your new password to {reset_url} (or use your client application).

This recovery token is valid for {expires_minutes} minutes and can only be used once.

Security Notice:
If you did not request a password reset, please ignore this email. Your existing password and active sessions remain secure.

— The MediaForge Security Team
"""
        msg.set_content(text_content)

        try:
            await asyncio.to_thread(
                _send_smtp_sync,
                host=settings.smtp_host,
                port=settings.smtp_port,
                message=msg,
                username=settings.smtp_user,
                password=settings.smtp_password,
                use_tls=settings.smtp_tls,
            )
            logger.info("Password reset email successfully sent to %s via Mailpit", to_email)
            return True
        except Exception as exc:
            logger.error("Failed to send password reset email to %s: %s", to_email, exc)
            return False

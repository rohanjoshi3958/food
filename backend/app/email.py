import logging

import resend

from app.config import settings

logger = logging.getLogger(__name__)


def send_password_reset_email(*, to_email: str, reset_token: str) -> None:
    reset_url = f"{settings.frontend_url.rstrip('/')}/reset-password?token={reset_token}"

    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set; password reset link: %s", reset_url)
        return

    resend.api_key = settings.resend_api_key
    resend.Emails.send(
        {
            "from": settings.email_from,
            "to": [to_email],
            "subject": "Reset your Food password",
            "html": (
                "<p>You requested a password reset for your Food account.</p>"
                f'<p><a href="{reset_url}">Reset your password</a></p>'
                f"<p>This link expires in {settings.password_reset_ttl_minutes} minutes.</p>"
                "<p>If you did not request this, you can ignore this email.</p>"
            ),
        }
    )

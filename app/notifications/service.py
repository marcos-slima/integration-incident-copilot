import logging
from typing import Literal

from app.notifications.interfaces import NotificationResult, NotificationSender

logger = logging.getLogger(__name__)


def send_email(
    provider: Literal["mailpit", "resend", ""],
    to: str,
    subject: str,
    body: str,
    sender: NotificationSender,
) -> NotificationResult:
    if not provider:
        logger.warning(
            "DA-55 out-of-band: e-mail para %s NAO foi enviado — EMAIL_PROVIDER nao configurado.",
            to,
        )
        return NotificationResult(success=False, provider="out-of-band")

    return sender.send(to, subject, body)

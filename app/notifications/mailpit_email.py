import logging

from app.config import settings
from app.notifications.interfaces import EmailSender, NotificationResult

logger = logging.getLogger(__name__)


class MailpitEmailSender(EmailSender):
    """Adapter Mailpit via SMTP (zero autenticação, dev/local)"""

    def __init__(self, host: str = "mailpit", port: int = 1025):
        self.host = host
        self.port = port

    def send(self, to: str, subject: str, body: str) -> NotificationResult:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        try:
            with smtplib.SMTP(self.host, self.port) as smtp:
                smtp.send_message(msg)
            return NotificationResult(success=True, provider="mailpit")
        except Exception as exc:  # noqa: BLE001
            logger.error("DA-55 Mailpit delivery failed: %s", exc)
            return NotificationResult(success=False, provider="mailpit", error_message=str(exc))

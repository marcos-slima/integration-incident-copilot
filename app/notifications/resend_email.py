import logging

from app.config import settings
from app.notifications.interfaces import EmailSender, NotificationResult

logger = logging.getLogger(__name__)


class ResendEmailSender(EmailSender):
    """Adapter Resend via API REST"""

    def send(self, to: str, subject: str, body: str) -> NotificationResult:
        import requests

        url = "https://api.resend.com/emails"
        headers = {
            "Authorization": f"Bearer {settings.smtp_password}",
            "Content-Type": "application/json",
        }
        payload = {
            "from": settings.smtp_from,
            "to": [to],
            "subject": subject,
            "text": body,
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                return NotificationResult(success=True, provider="resend")
            logger.error("DA-55 Resend API failed: %s %s", response.status_code, response.text)
            return NotificationResult(
                success=False,
                provider="resend",
                error_message=f"HTTP {response.status_code}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("DA-55 Resend API request error: %s", exc)
            return NotificationResult(success=False, provider="resend", error_message=str(exc))

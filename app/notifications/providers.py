"""Provider selection factory — DA-55: inverted dependencies.

Esta mo'dulo e' a unica' parte do co'digo que conhece o provedor
especi'fico (Mailpit, Resend, etc). O restante do sistema usa apenas
NotificationService.

Padrao: Factory Method + Strategy.

Usage:
    from app.notifications.providers import get_email_sender

    sender = get_email_sender()
    result = sender.send("user@example.com", "Assunto", "Corpo")
"""

from app.config import settings
from app.notifications.interfaces import EmailSender


def get_email_sender() -> EmailSender | None:
    """Factory: cria sender baseado em EMAIL_PROVIDER.

    Returns:
        EmailSender implementado (MailpitEmailSender/ResendEmailSender),
        ou None se provider desconhecido ou vazio (out-of-band fallback).
    """
    provider = settings.email_provider or ""

    if not provider:
        return None

    if provider == "mailpit":
        from app.notifications.mailpit_email import MailpitEmailSender

        # M-12: SMTP_HOST vazio = o host do servico no compose.
        return MailpitEmailSender(
            host=settings.smtp_host or "mailpit", port=settings.smtp_port or 1025
        )

    if provider == "resend":
        from app.notifications.resend_email import ResendEmailSender

        return ResendEmailSender()

    return None


def get_sms_sender() -> None:
    """Factory: cria sender baseado em SMS_PROVIDER.

    Returns:
        None (MessagePit/SMS não disponível atualmente. Uso out-of-band).
    """
    return

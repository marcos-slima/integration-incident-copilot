from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class NotificationResult:
    success: bool
    provider: str
    error_message: str | None = None


class EmailSender(ABC):
    """Interface abstrata para senders de e-mail (DA-55: inverted depende)"""

    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> NotificationResult: ...

"""Verification-code delivery.

No production SMS/email provider is integrated yet. `ConsoleOtpSender` prints
codes to the server log and is refused by configuration outside development and
test. A real provider must implement `OtpSender` before staging/production.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings
from app.models.auth import VerificationChannel, VerificationPurpose

logger = logging.getLogger("app.otp")


class OtpSender(Protocol):
    def send(
        self,
        channel: VerificationChannel,
        destination: str,
        code: str,
        purpose: VerificationPurpose,
    ) -> None: ...


class ConsoleOtpSender:
    def send(self, channel, destination, code, purpose) -> None:  # type: ignore[no-untyped-def]
        if not get_settings().is_dev_like:
            raise RuntimeError("Console OTP delivery is disabled outside development.")
        # Intentionally in the message (not an extra field) so it is visible in dev logs.
        logger.warning(
            "DEV ONLY verification code for %s via %s (%s): %s",
            destination,
            channel.value,
            purpose.value,
            code,
        )


class DisabledOtpSender:
    def send(self, channel, destination, code, purpose) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("No verification-code delivery provider is configured.")


@dataclass
class SentCode:
    channel: VerificationChannel
    destination: str
    code: str
    purpose: VerificationPurpose


@dataclass
class InMemoryOtpSender:
    """Test double that records sent codes."""

    sent: list[SentCode] = field(default_factory=list)

    def send(self, channel, destination, code, purpose) -> None:  # type: ignore[no-untyped-def]
        self.sent.append(SentCode(channel, destination, code, purpose))

    def last_for(self, destination: str) -> SentCode:
        return next(s for s in reversed(self.sent) if s.destination == destination)


_sender: OtpSender | None = None


def get_otp_sender() -> OtpSender:
    global _sender
    if _sender is None:
        mode = get_settings().otp_delivery
        _sender = ConsoleOtpSender() if mode == "console" else DisabledOtpSender()
    return _sender

"""Optional Telegram notifier (Bot API `sendMessage`).

Disabled unless TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are both set on the server. The bot
token never leaves the backend and is never logged. Message policy (docs/reports.md):
- alerts: severity and title only;
- DEMO daily report: day, trades, net, win rate (virtual money);
- REAL daily report: only "report ready" with the day; no amounts, no broker details;
- never tokens, broker names/ids, account ids or balances.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger("app.reports.telegram")

# Tests set this to an httpx.MockTransport; the network is never used in tests.
transport_override: httpx.BaseTransport | None = None
MAX_LEN = 500
_DIGITS = re.compile(r"\d{6,}")  # long digit runs (ids, account numbers) are masked
_BOT_PATH = re.compile(r"/bot[^/\s]+/")


class _RedactBotToken(logging.Filter):
    """httpx logs every request URL at INFO; the Bot API puts the token in the path."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "/bot" in msg:
            record.msg, record.args = _BOT_PATH.sub("/bot[REDACTED]/", msg), ()
        return True


if not any(isinstance(f, _RedactBotToken) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(_RedactBotToken())


def scrub(text: str) -> str:
    return _DIGITS.sub("•••", text)[:MAX_LEN]


def report_ready_text(payload: dict[str, Any]) -> str:
    day = payload.get("day")
    if payload.get("account_mode") == "REAL":
        return f"QuantAI: your REAL account daily report for {day} is ready. Open the app."
    t = payload.get("totals", {})
    wr = t.get("win_rate_pct")
    return (f"QuantAI DEMO (virtual money) report {day}: {t.get('trades', 0)} trade(s), "
            f"net ₹{t.get('net', '0.00')}" + (f", win rate {wr}%." if wr is not None else "."))


def alert_text(title: str, severity: str) -> str:
    return scrub(f"QuantAI [{severity.upper()}] {title}")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, base_url: str = "https://api.telegram.org",
                 transport: httpx.BaseTransport | None = None, timeout: float = 5.0) -> None:
        self._token, self.chat_id = token, chat_id
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                  transport=transport)

    def __repr__(self) -> str:
        return "TelegramNotifier(enabled=True)"

    @classmethod
    def from_settings(cls, settings: Settings,
                      transport: httpx.BaseTransport | None = None) -> TelegramNotifier | None:
        if settings.telegram_bot_token is None or not settings.telegram_chat_id:
            return None
        return cls(settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id,
                   settings.telegram_api_base, transport=transport or transport_override)

    def send(self, text: str) -> bool:
        """Best effort: never raises; logs only the outcome (never the token or URL)."""
        try:
            r = self._http.post(f"/bot{self._token}/sendMessage",
                                json={"chat_id": self.chat_id, "text": scrub(text),
                                      "disable_web_page_preview": True})
            ok = r.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("telegram_send_failed", extra={"error": type(exc).__name__})
            return False
        if not ok:
            logger.warning("telegram_send_failed", extra={"status": r.status_code})
        return ok

    def close(self) -> None:
        self._http.close()

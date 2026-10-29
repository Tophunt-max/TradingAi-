"""Persistent INDstocks price-feed worker.

Connects to the INDstocks WebSocket, subscribes in `ltp` mode to the
configured instruments and writes the latest tick per instrument into the
shared TickStore (Redis). The API uses those ticks for fresher prices and for
real staleness detection.

Usage (from backend/):  python -m app.workers.price_feed NSE:2885 NFO:51011 ...
Requires MARKET_DATA_MODE=INDSTOCKS, INDSTOCKS_ACCESS_TOKEN and REDIS_URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.market_data.ticks import TickStore, get_tick_store

logger = logging.getLogger("app.price_feed")
MAX_INSTRUMENTS = 3000  # INDstocks per-connection limit


@dataclass
class FeedStats:
    received: int = 0
    stored: int = 0
    dropped_stale: int = 0
    malformed: int = 0
    reconnects: int = 0
    last_error: str | None = None
    symbols_seen: set[str] = field(default_factory=set)


def handle_message(
    raw: str | bytes, segment_of: dict[str, str], store: TickStore, stats: FeedStats
) -> None:
    """Parse one frame. Ignores heartbeats; drops out-of-order/duplicate ticks."""
    stats.received += 1
    try:
        msg = json.loads(raw)
        if isinstance(msg, str):  # some feeds double-encode JSON
            msg = json.loads(msg)
    except (ValueError, TypeError):
        stats.malformed += 1
        return
    if not isinstance(msg, dict) or msg.get("mode") != "ltp":
        if isinstance(msg, dict) and msg.get("timestamp"):
            store.heartbeat(int(msg["timestamp"]))
        return
    token = str(msg.get("instrument", ""))
    ltp = (msg.get("data") or {}).get("ltp")
    ts = msg.get("timestamp")
    if not token or ltp is None or ts is None:
        stats.malformed += 1
        return
    # Frames carry the bare token; restore the SEGMENT:TOKEN key we subscribed with.
    key = segment_of.get(token, token)
    stats.symbols_seen.add(key)
    if store.put(key, float(ltp), int(ts)):
        stats.stored += 1
    else:
        stats.dropped_stale += 1
    store.heartbeat(int(time.time() * 1000))


async def run(instruments: list[str], *, stop: asyncio.Event | None = None) -> FeedStats:
    import websockets

    settings = get_settings()
    token = settings.indstocks_access_token
    if not token:
        raise SystemExit("INDSTOCKS_ACCESS_TOKEN is not set.")
    if not settings.redis_url:
        raise SystemExit("REDIS_URL is required so the API can read ticks from this worker.")
    if len(instruments) > MAX_INSTRUMENTS:
        raise SystemExit(f"At most {MAX_INSTRUMENTS} instruments per connection.")
    store = get_tick_store(settings.redis_url)
    segment_of = {i.split(":", 1)[1]: i for i in instruments}
    stats = FeedStats()
    backoff = 1.0
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            async with websockets.connect(
                settings.indstocks_ws_url,
                additional_headers={"Authorization": token.get_secret_value()},
                ping_interval=20,
                ping_timeout=20,
                open_timeout=10,
            ) as ws:
                await ws.send(
                    json.dumps({"action": "subscribe", "mode": "ltp", "instruments": instruments})
                )
                logger.info("price_feed_subscribed", extra={"count": len(instruments)})
                backoff = 1.0
                async for raw in ws:
                    handle_message(raw, segment_of, store, stats)
                    if stop.is_set():
                        break
        except Exception as exc:  # reconnect on any failure
            stats.reconnects += 1
            stats.last_error = type(exc).__name__
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "price_feed_disconnected",
                extra={
                    "error": type(exc).__name__,
                    "status": status,
                    "retry_in_s": round(backoff, 1),
                },
            )
            if status in (401, 403):
                logger.error("price_feed_auth_failed: regenerate INDSTOCKS_ACCESS_TOKEN")
                backoff = max(backoff, 60.0)
            await asyncio.sleep(backoff + random.random())  # noqa: S311
            backoff = min(backoff * 2, 120.0)
    return stats


def main(argv: list[str]) -> int:
    configure_logging()
    argv = [x for a in argv for x in a.replace(",", " ").split()]
    if not argv:
        print("Usage: python -m app.workers.price_feed NSE:2885 NFO:51011 ...")
        return 2
    bad = [a for a in argv if ":" not in a]
    if bad:
        print(f"Instruments must be SEGMENT:TOKEN (e.g. NSE:2885): {bad}")
        return 2
    asyncio.run(run(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

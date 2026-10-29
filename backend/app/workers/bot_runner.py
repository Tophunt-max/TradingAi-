"""Persistent bot worker.

Usage (from backend/):  python -m app.workers.bot_runner [--interval 15] [--once]

Safe to run more than one instance: runs are leased, so each bot is evaluated
by one worker at a time, and a crashed worker's bots are picked up after its
lease (60 s) expires. Bot state lives in the database, so restarts resume
where they stopped.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import uuid

from app.bots.engine import run_once
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import utcnow
from app.db.session import get_db
from app.market_data.factory import build_provider

logger = logging.getLogger("app.bot_runner")


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def loop(stop: threading.Event, interval: float, once: bool = False) -> None:
    settings = get_settings()
    owner = worker_id()
    logger.info("bot_runner_started", extra={"owner": owner, "interval": interval})
    while not stop.is_set():
        db = next(get_db())
        try:
            stats = run_once(db, settings, owner, lambda s: build_provider(s, settings), utcnow())
            if stats:
                logger.info("bot_runner_pass", extra=stats)
        except Exception:
            logger.exception("bot_runner_pass_failed")
        finally:
            db.close()
        if once:
            break
        stop.wait(interval)
    logger.info("bot_runner_stopped", extra={"owner": owner})


_embedded: tuple[threading.Thread, threading.Event] | None = None


def start_embedded(interval: float) -> None:
    """Run the worker inside the API process (development convenience only)."""
    global _embedded
    if _embedded is None:
        stop = threading.Event()
        t = threading.Thread(target=loop, args=(stop, interval), name="bot-runner", daemon=True)
        t.start()
        _embedded = (t, stop)


def stop_embedded() -> None:
    global _embedded
    if _embedded:
        _embedded[1].set()
        _embedded[0].join(timeout=5)
        _embedded = None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    configure_logging()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    loop(stop, args.interval, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

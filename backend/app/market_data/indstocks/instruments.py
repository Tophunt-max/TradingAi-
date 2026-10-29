"""Instrument-master (scrip master) parsing and sync for INDstocks."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.market_data.indstocks.client import IndstocksClient, IndstocksError
from app.models.instruments import Instrument, InstrumentSync

logger = logging.getLogger("app.instruments")
PROVIDER = "INDSTOCKS"

# Index display names in the INDstocks index file -> our symbols.
INDEX_NAMES = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "SENSEX": "SENSEX",
}


def _dec(v: str | None) -> Decimal | None:
    if v is None or str(v).strip() in ("", "0", "0.0", "-", "NA"):
        return None
    try:
        return Decimal(str(v).strip())
    except InvalidOperation:
        return None


def _int(v: str | None) -> int | None:
    d = _dec(v)
    return int(d) if d is not None else None


def _date(v: str | None) -> date | None:
    v = (v or "").strip()
    if not v or v in ("0", "-1"):
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def parse_index_csv(text: str) -> tuple[list[dict[str, Any]], int]:
    """Three columns, read POSITIONALLY: EXCH, <index name>, SECURITY_ID.

    The second column is labelled SEGMENT in the header but holds the index name.
    """
    rows, rejected = [], 0
    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    for rec in reader:
        if len(rec) < 3 or not rec[2].strip().isdigit():
            rejected += 1
            continue
        exch, name, sid = rec[0].strip(), rec[1].strip(), rec[2].strip()
        rows.append(
            {
                "source": "index",
                "exchange": exch,
                "segment": "INDEX",
                "security_id": sid,
                "instrument_name": "INDEX",
                "trading_symbol": INDEX_NAMES.get(name.upper(), name),
                "symbol_name": INDEX_NAMES.get(name.upper(), name.upper()),
                "display_name": name,
            }
        )
    return rows, rejected


def parse_master_csv(text: str, source: str) -> tuple[list[dict[str, Any]], int]:
    rows, rejected = [], 0
    reader = csv.DictReader(io.StringIO(text))
    for rec in reader:
        rec = {(k or "").strip().upper(): (v or "").strip() for k, v in rec.items()}
        sid = rec.get("SECURITY_ID", "")
        if not sid:
            rejected += 1
            continue
        lot = _int(rec.get("LOT_UNITS"))
        tick = _dec(rec.get("TICK_SIZE"))
        inst = rec.get("INSTRUMENT_NAME") or None
        if source == "fno" and (
            lot is None
            or lot <= 0
            or tick is None
            or tick <= 0
            or _date(rec.get("EXPIRY_DATE")) is None
        ):
            rejected += 1  # a derivative without lot, tick size or expiry is unusable
            continue
        rows.append(
            {
                "source": source,
                "exchange": rec.get("EXCH", ""),
                "segment": rec.get("SEGMENT") or None,
                "security_id": sid,
                "instrument_name": inst,
                "trading_symbol": rec.get("TRADING_SYMBOL") or None,
                "symbol_name": (rec.get("SYMBOL_NAME") or "").upper() or None,
                "display_name": rec.get("CUSTOM_SYMBOL")
                or rec.get("UNDERLYING_SCRIP_NAME")
                or None,
                "series": rec.get("SERIES") or None,
                "expiry_date": _date(rec.get("EXPIRY_DATE")),
                "strike_price": _dec(rec.get("STRIKE_PRICE")),
                "option_type": rec.get("OPTION_TYPE")
                if rec.get("OPTION_TYPE") in ("CE", "PE")
                else None,
                "lot_size": lot,
                "tick_size": tick,
                "freeze_qty": _int(rec.get("FREEZE_QTY")),
                "isin": rec.get("ISIN") or None,
            }
        )
    return rows, rejected


def sync_source(db: Session, client: IndstocksClient, source: str) -> InstrumentSync:
    """Download one instrument file and atomically replace the stored rows."""
    log = InstrumentSync(provider=PROVIDER, source=source, status="FAILED", started_at=utcnow())
    try:
        text = client.instruments_csv(source)  # type: ignore[arg-type]
        rows, rejected = (
            parse_index_csv(text) if source == "index" else parse_master_csv(text, source)
        )
        if not rows:
            raise ValueError("Instrument file contained no usable rows.")
        now = utcnow()
        db.execute(
            delete(Instrument).where(Instrument.provider == PROVIDER, Instrument.source == source)
        )
        seen: set[tuple[str, str]] = set()
        objs = []
        for r in rows:
            key = (r["exchange"], r["security_id"])
            if key in seen:
                rejected += 1
                continue
            seen.add(key)
            objs.append(Instrument(provider=PROVIDER, synced_at=now, **r))
        db.add_all(objs)
        log.status, log.row_count, log.rejected_rows = "SUCCESS", len(objs), rejected
    except (IndstocksError, ValueError) as exc:
        db.rollback()
        log.message = str(exc)[:500]
        logger.error("instrument_sync_failed", extra={"source": source, "error": str(exc)})
    log.finished_at = utcnow()
    db.add(log)
    db.commit()
    return log


def last_sync(db: Session, source: str) -> InstrumentSync | None:
    return db.scalar(
        select(InstrumentSync)
        .where(
            InstrumentSync.provider == PROVIDER,
            InstrumentSync.source == source,
            InstrumentSync.status == "SUCCESS",
        )
        .order_by(InstrumentSync.finished_at.desc())
        .limit(1)
    )

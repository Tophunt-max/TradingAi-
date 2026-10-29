"""Market-data quality checks.

Every live payload carries a `quality` report. A FAIL means the data must not
be used for trading decisions; the UI shows the issues to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from app.core.time import IST, ensure_utc
from app.market_data.session import SESSION_CLOSE, SESSION_OPEN, session_state

Severity = Literal["INFO", "WARN", "FAIL"]
CLOCK_TOLERANCE = timedelta(seconds=90)


@dataclass
class QualityReport:
    issues: list[dict[str, str]] = field(default_factory=list)

    def add(self, code: str, severity: Severity, message: str) -> None:
        if any(i["code"] == code for i in self.issues):
            return
        self.issues.append({"code": code, "severity": severity, "message": message})

    @property
    def status(self) -> Literal["OK", "WARN", "FAIL"]:
        sev = {i["severity"] for i in self.issues}
        return "FAIL" if "FAIL" in sev else "WARN" if "WARN" in sev else "OK"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "issues": self.issues}

    def merge(self, other: QualityReport) -> QualityReport:
        self.issues.extend(other.issues)
        return self


def session_note(report: QualityReport, now: datetime, holidays: list[str]) -> bool:
    st = session_state(now, holidays)
    if not st.holidays_configured:
        report.add(
            "HOLIDAYS_NOT_CONFIGURED",
            "INFO",
            "Exchange holiday calendar is not configured; holidays look like open days.",
        )
    if not st.is_open:
        report.add("MARKET_CLOSED", "INFO", f"{st.reason}: prices are from the last session.")
    return st.is_open


def check_quote_freshness(
    report: QualityReport,
    *,
    quote_time: datetime | None,
    now: datetime,
    market_open: bool,
    stale_seconds: int,
) -> None:
    if quote_time is None:
        if market_open:
            report.add("NO_QUOTE_TIME", "WARN", "The provider did not send a quote timestamp.")
        return
    q = ensure_utc(quote_time)
    if q - now > CLOCK_TOLERANCE:
        report.add("CLOCK_DRIFT", "FAIL", "Quote timestamp is in the future; check server clock.")
    elif market_open and (now - q).total_seconds() > stale_seconds:
        report.add(
            "STALE_QUOTE",
            "FAIL",
            f"Last update {int((now - q).total_seconds())}s ago (limit {stale_seconds}s).",
        )


def check_candles(
    candles: list[dict[str, Any]], *, interval_minutes: int, now: datetime, holidays: list[str]
) -> tuple[list[dict[str, Any]], QualityReport]:
    """Sort, de-duplicate and validate candles. Returns cleaned candles + report.

    Candles are dicts with ts (aware datetime, candle OPEN time), open, high,
    low, close, volume.
    """
    report = QualityReport()
    if not candles:
        report.add("NO_CANDLES", "FAIL", "No candles returned for this window.")
        return [], report

    ordered = all(candles[i]["ts"] < candles[i + 1]["ts"] for i in range(len(candles) - 1))
    if not ordered:
        report.add("OUT_OF_ORDER", "WARN", "Candles arrived out of order and were re-sorted.")
    by_ts: dict[datetime, dict[str, Any]] = {}
    dupes = 0
    for c in sorted(candles, key=lambda c: c["ts"]):
        if c["ts"] in by_ts:
            dupes += 1
        by_ts[c["ts"]] = c  # keep the latest version of a bucket
    if dupes:
        report.add("DUPLICATES", "WARN", f"{dupes} duplicate candle(s) were dropped.")

    clean, bad_ohlc, outside = [], 0, 0
    for ts, c in by_ts.items():
        if (
            not (c["low"] <= min(c["open"], c["close"]) and max(c["open"], c["close"]) <= c["high"])
            or c["low"] <= 0
        ):
            bad_ohlc += 1
            continue
        if interval_minutes < 1440:
            local = ts.astimezone(IST).time()
            if local < SESSION_OPEN or local >= SESSION_CLOSE:
                outside += 1
        clean.append(c)
    if bad_ohlc:
        report.add(
            "INVALID_OHLC", "FAIL", f"{bad_ohlc} candle(s) had inconsistent OHLC and were dropped."
        )
    if outside:
        report.add(
            "OUTSIDE_SESSION",
            "WARN",
            f"{outside} candle(s) fall outside 09:15–15:30 IST; timestamps may be shifted.",
        )

    # Forming candle: never return a bucket that has not closed (no repainting).
    step = timedelta(minutes=interval_minutes)
    if clean and clean[-1]["ts"] - now > CLOCK_TOLERANCE:
        report.add("CLOCK_DRIFT", "FAIL", "Candle timestamps are in the future; check clocks.")
        clean = [c for c in clean if c["ts"] <= now]
    if clean and interval_minutes < 1440 and clean[-1]["ts"] + step > now:
        clean = clean[:-1]

    # Gaps inside a session day (intraday only).
    if interval_minutes < 1440 and len(clean) > 1:
        missing = 0
        for a, b in zip(clean, clean[1:], strict=False):
            gap = b["ts"] - a["ts"]
            same_day = a["ts"].astimezone(IST).date() == b["ts"].astimezone(IST).date()
            if same_day and gap > step:
                missing += int(gap / step) - 1
        if missing:
            report.add(
                "MISSING_CANDLES",
                "WARN",
                f"{missing} intraday candle(s) missing (no trades or a feed gap).",
            )
    return clean, report


def check_option_chain(
    rows: list[dict[str, Any]], *, lot_size: int | None, expiry_passed: bool
) -> QualityReport:
    report = QualityReport()
    if expiry_passed:
        report.add("EXPIRED", "FAIL", "This expiry has passed.")
    if lot_size is None:
        report.add(
            "NO_LOT_SIZE",
            "FAIL",
            "Lot size not found in the instrument master; run an instrument sync.",
        )
    crossed = negative = 0
    for r in rows:
        for side in ("call", "put"):
            leg = r.get(side)
            if not leg:
                continue
            if leg["ltp"] is not None and leg["ltp"] < 0:
                negative += 1
            if leg["bid"] and leg["ask"] and leg["bid"] > leg["ask"]:
                crossed += 1
    if crossed:
        report.add("CROSSED_BOOK", "WARN", f"{crossed} leg(s) have bid above ask.")
    if negative:
        report.add("NEGATIVE_PRICE", "FAIL", f"{negative} leg(s) have a negative price.")
    if not rows:
        report.add("EMPTY_CHAIN", "FAIL", "The provider returned no strikes.")
    return report

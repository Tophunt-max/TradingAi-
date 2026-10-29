"""Event-driven bar-by-bar backtest engine (underlying-proxy fidelity).

Timeline for bar i (all bars are CLOSED candles):

1. Open of bar i: a pending entry scheduled at an earlier close fills here
   (next-bar open by default, `latency_bars` later if configured). Forced exits
   (square-off time reached, daily loss stop flagged at the previous close)
   fill at this open.
2. Inside bar i: stop and target are checked against the bar's range. If the
   bar opens beyond the stop, the exit fills at the open (stop gap). If both
   stop and target are inside the range, the stop is assumed to fill first.
3. Close of bar i: last bar of the day (or of the data) squares off at the
   close; the daily loss stop / profit lock are evaluated; equity is marked.
4. Decision at the close of bar i: the strategy sees bars 0..i only and may
   schedule an entry for a later bar.

Entry conditions are evaluated with the same `Evaluator` the live bot worker
uses (app.strategies.conditions) on the same trailing window of candles.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import numpy as np

from app.analysis import regime as R
from app.analysis.context import Ctx
from app.backtesting import metrics as M
from app.backtesting.data import BarView, slice_ctx
from app.bots.engine import CANDLES as BOT_WINDOW  # noqa: F401  (re-exported)
from app.core.time import IST
from app.execution import charges as CH
from app.indicators import core as ind
from app.strategies.conditions import Evaluator

MIN_HISTORY = 60  # the bot worker refuses to evaluate with fewer closed candles
# Indicator series are precomputed on the full history, so the window only matters for
# structure conditions (PATTERN, SMC_*, RESISTANCE_BREAK), which re-detect swings and
# patterns on the trailing window at every bar. The bot worker uses BOT_WINDOW (300)
# candles; the backtest uses a shorter window to stay inside the request time budget.
EVAL_WINDOW = 150

FIDELITY_PROXY = "UNDERLYING_PROXY"
PROXY_NOTE = ("Incomplete proxy for options: ignores premium, IV, theta, spread. P&L is "
              "underlying points x quantity.")


def hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class Level:
    kind: str  # PERCENT | POINTS (of the underlying price)
    value: float

    def distance(self, price: float) -> float:
        return price * self.value / 100 if self.kind == "PERCENT" else self.value


@dataclass(frozen=True)
class Execution:
    entry_fill: str = "NEXT_OPEN"  # NEXT_OPEN | NEXT_BAR_WORST
    stop_fill: str = "STOP_PRICE"  # STOP_PRICE (or open on a gap) | BAR_WORST
    latency_bars: int = 0
    spread_bps: float = 2.0  # full bid/ask spread; each market fill pays half
    slippage_bps: float = 0.0
    slippage_ticks: int = 1
    tick_size: float = 0.05
    reject_probability: float = 0.0
    partial_fill_probability: float = 0.0
    min_volume: float = 0.0
    brokerage_per_order: float = 20.0
    # Option charges need a premium; the proxy has none, so an assumed premium is used.
    charge_premium: float | None = None
    charge_premium_pct: float = 0.6

    def half_spread(self, ref: float) -> float:
        return ref * self.spread_bps / 20_000

    def slip(self, ref: float) -> float:
        return self.slippage_ticks * self.tick_size + ref * self.slippage_bps / 10_000


@dataclass(frozen=True)
class Risk:
    capital: float = 10_000.0
    risk_per_trade: float = 250.0
    daily_loss_limit: float = 800.0
    daily_profit_lock: float = 1_200.0
    max_entries_per_day: int = 2
    lots: int = 1
    lot_size: int = 1
    re_entry_after_stop_loss: bool = False
    exit_on_loss_limit: bool = True
    square_off_ist: str = "15:15"
    session_start_ist: str = "09:20"
    session_end_ist: str = "15:00"
    cooldown_minutes: int = 15


@dataclass
class Strategy:
    conditions: list[dict[str, Any]] = field(default_factory=list)
    logic: str = "ALL"
    side: str = "CE"  # CE = long underlying proxy, PE = short underlying proxy
    stop: Level = Level("PERCENT", 0.3)
    target: Level = Level("PERCENT", 0.6)
    min_relative_volume: float | None = None
    max_atr_pct: float | None = None
    regimes: tuple[str, ...] | None = None  # allowed regime families; None = any
    hook: Callable[[BarView], bool] | None = None  # Python strategy (tests / research)

    def __post_init__(self) -> None:
        self._keys = [json.dumps(c, sort_keys=True, default=str) for c in self.conditions]


class Prepared:
    """Per-history cache shared by every run on the same bars (grids, walk-forward)."""

    def __init__(self, ctx: Ctx, window: int = EVAL_WINDOW) -> None:
        self.ctx, self.window = ctx, window
        self.days: list[date] = []
        self.tods: list[time] = []
        for t in ctx.ts:
            lt = t.astimezone(IST)
            self.days.append(lt.date())
            self.tods.append(lt.time())
        self._cond: dict[tuple[int, str, str], bool | None] = {}
        self._regime: dict[int, str] = {}
        self._ev: dict[str, tuple[int, Evaluator]] = {}
        self.evaluations = 0
        self.lock = threading.Lock()  # one computation at a time per cached history

    def __len__(self) -> int:
        return len(self.ctx)

    def condition(self, i: int, side: str, key: str, cond: dict[str, Any]) -> bool | None:
        k = (i, side, key)
        if k not in self._cond:
            cached = self._ev.get(side)
            if cached is None or cached[0] != i:
                window = slice_ctx(self.ctx, max(0, i - self.window + 1), i + 1)
                cached = (i, Evaluator(window, side))
                self._ev[side] = cached
            self.evaluations += 1
            self._cond[k] = cached[1].evaluate(cond)
        return self._cond[k]

    def ensure_series(self, conds: list[dict[str, Any]]) -> None:
        """Precompute custom-period EMA / RSI series on the full (causal) history, so
        their warm-up does not depend on the evaluation window."""
        s, c = self.ctx.s, self.ctx.c
        for cond in conds:
            p = cond.get("params") or {}
            if cond["indicator"] == "EMA_CROSS":
                for n in (int(p.get("fast", 9)), int(p.get("slow", 21))):
                    if f"ema{n}" not in s:
                        s[f"ema{n}"] = ind.ema(c, n)
            elif cond["indicator"] == "RSI":
                n = int(p.get("period", 14))
                if f"rsi{n}" not in s:
                    s[f"rsi{n}"] = ind.rsi(c, n)

    def regime(self, i: int) -> str:
        if i not in self._regime:
            r = R.detect(self.ctx, i)
            self._regime[i] = r["label"] if r else "UNKNOWN"
        return self._regime[i]


def _iso(t: datetime) -> str:
    return t.astimezone(IST).isoformat()


class _Run:
    def __init__(self, prep: Prepared, strat: Strategy, exe: Execution, risk: Risk,
                 seed: int, start: int, end: int) -> None:
        self.p, self.ctx, self.s, self.e, self.r = prep, prep.ctx, strat, exe, risk
        prep.ensure_series(strat.conditions)
        self.start, self.end = start, end
        self.rng = np.random.default_rng(seed)
        self.dirn = 1 if strat.side == "CE" else -1
        self.so = hhmm(risk.square_off_ist)
        self.sess = (hhmm(risk.session_start_ist), hhmm(risk.session_end_ist))
        self.trades: list[dict[str, Any]] = []
        self.equity: list[tuple[datetime, float]] = []
        self.daily: dict[date, float] = {}
        self.suppressed: dict[str, int] = {}
        self.rejected: dict[str, int] = {}
        self.warnings: set[str] = set()
        self.signals = 0
        self.in_market = 0
        self.realized = 0.0
        self.pos: dict[str, Any] | None = None

    # ------------------------------------------------------------------ costs
    def charges(self, ref: float, qty: int, day: date) -> tuple[float, str]:
        prem = self.e.charge_premium or ref * self.e.charge_premium_pct / 100
        price = Decimal(str(round(prem, 2)))
        brk = Decimal(str(self.e.brokerage_per_order))
        try:
            CH.schedule_for(day)
        except ValueError:
            self.warnings.add("Some trades predate the earliest charge schedule; the earliest "
                              "schedule was applied.")
            day = CH.SCHEDULES[0].effective_from
        total = (CH.compute(side="BUY", price=price, qty=qty, day=day, brokerage=brk)
                 + CH.compute(side="SELL", price=price, qty=qty, day=day, brokerage=brk))
        return float(total.total), total.schedule_id

    def market_cost(self, ref: float) -> float:
        return self.e.half_spread(ref) + self.e.slip(ref)

    # ------------------------------------------------------------------ signals
    def fired(self, i: int) -> bool:
        s = self.s
        if s.hook is not None:
            return bool(s.hook(BarView(self.ctx, i)))
        if not s.conditions:
            return False
        vals = (self.p.condition(i, s.side, k, c) for k, c in zip(s._keys, s.conditions,
                                                                  strict=True))
        if s.logic == "ANY":
            return any(v is True for v in vals)
        return all(v is True for v in vals)

    def suppress_reason(self, i: int, day: dict[str, Any], last_signal: datetime | None,
                        ) -> str | None:
        r, s, ctx = self.r, self.s, self.ctx
        if day["halted"]:
            return "DAILY_LOSS_STOP"
        if day["locked"]:
            return "DAILY_PROFIT_LOCK"
        if day["entries"] >= r.max_entries_per_day:
            return "MAX_ENTRIES_PER_DAY"
        if day["stopped"] and not r.re_entry_after_stop_loss:
            return "NO_RE_ENTRY_AFTER_STOP_LOSS"
        if r.cooldown_minutes and last_signal is not None and \
                (ctx.ts[i] - last_signal).total_seconds() < r.cooldown_minutes * 60:
            return "COOLDOWN"
        rvol, atr = ctx.s["rvol20"][i], ctx.s["atr14"][i]
        if s.min_relative_volume is not None and (rvol is None or rvol < s.min_relative_volume):
            return "FILTER_RELATIVE_VOLUME"
        if s.max_atr_pct is not None and atr is not None and \
                atr / ctx.c[i] * 100 > s.max_atr_pct:
            return "FILTER_ATR"
        if s.regimes is not None and R.FAMILY.get(self.p.regime(i)) not in s.regimes:
            return "REGIME_INCOMPATIBLE"
        est = ctx.c[i]
        qty = r.lots * r.lot_size
        planned = s.stop.distance(est) * qty + self.charges(est, qty, self.p.days[i])[0]
        if planned > r.risk_per_trade:
            return "RISK_BUDGET"
        return None

    # ------------------------------------------------------------------ fills
    def fill_entry(self, f: int, signal_i: int) -> bool:
        ctx, e, r = self.ctx, self.e, self.r
        o = ctx.o[f]
        if not (isinstance(o, float) and math.isfinite(o) and o > 0):
            self._reject("NO_PRICE")
            return False
        if e.min_volume and ctx.v[f] < e.min_volume:
            self._reject("NO_LIQUIDITY")
            return False
        if e.reject_probability > 0 and self.rng.random() < e.reject_probability:
            self._reject("SIMULATED_REJECTION")
            return False
        lots, partial = r.lots, False
        if e.partial_fill_probability > 0 and lots > 1 and \
                self.rng.random() < e.partial_fill_probability:
            lots, partial = math.ceil(lots / 2), True
        at_open = e.entry_fill == "NEXT_OPEN"
        ref = o if at_open else (ctx.h[f] if self.dirn == 1 else ctx.l[f])
        cost = self.market_cost(ref)
        price = ref + self.dirn * cost
        qty = lots * r.lot_size
        self.pos = {
            "entry_i": f, "signal_i": signal_i, "entry_ref": ref, "entry_price": price,
            "qty": qty, "partial": partial, "at_open": at_open, "entry_cost": cost * qty,
            "stop": price - self.dirn * self.s.stop.distance(price),
            "target": price + self.dirn * self.s.target.distance(price),
            "regime": self.p.regime(signal_i),
        }
        return True

    def _reject(self, why: str) -> None:
        self.rejected[why] = self.rejected.get(why, 0) + 1

    def close(self, i: int, price: float, ref: float, reason: str, day: dict[str, Any],
              gap: float = 0.0, ambiguous: bool = False) -> None:
        pos = self.pos
        assert pos is not None
        qty = pos["qty"]
        gross = self.dirn * (price - pos["entry_price"]) * qty
        charges, schedule = self.charges(pos["entry_ref"], qty, self.p.days[pos["entry_i"]])
        net = gross - charges
        self.realized += net
        day["realized"] += net
        d = self.p.days[i]
        self.daily[d] = self.daily.get(d, 0.0) + net
        if reason.startswith("STOP_LOSS"):
            day["stopped"] = True
        ctx = self.ctx
        self.trades.append({
            "signal_time": _iso(ctx.ts[pos["signal_i"]]),
            "entry_time": _iso(ctx.ts[pos["entry_i"]]),
            "exit_time": _iso(ctx.ts[i]), "signal_index": pos["signal_i"],
            "entry_index": pos["entry_i"], "exit_index": i,
            "side": self.s.side, "direction": "LONG" if self.dirn == 1 else "SHORT", "qty": qty,
            "partial_fill": pos["partial"],
            "entry_price": round(pos["entry_price"], 2), "exit_price": round(price, 2),
            "stop": round(pos["stop"], 2), "target": round(pos["target"], 2),
            "exit_reason": reason, "stop_target_same_bar": ambiguous,
            "gross": round(gross, 2), "charges": round(charges, 2), "net": round(net, 2),
            "slippage": round(pos["entry_cost"] + abs(price - ref) * qty, 2),
            "gap_slippage": round(gap, 2), "bars_held": i - pos["entry_i"] + 1,
            "regime": pos["regime"], "charge_schedule": schedule,
        })
        self.pos = None

    def market_exit(self, i: int, ref: float, reason: str, day: dict[str, Any]) -> None:
        self.close(i, ref - self.dirn * self.market_cost(ref), ref, reason, day)

    def intrabar(self, i: int, day: dict[str, Any]) -> None:
        pos, ctx, dirn = self.pos, self.ctx, self.dirn
        assert pos is not None
        o, h, lo = ctx.o[i], ctx.h[i], ctx.l[i]
        stop, target = pos["stop"], pos["target"]
        hs = self.e.half_spread(o)
        first = i == pos["entry_i"]
        # A limit sell at the target fills only once the bid (mid - half spread) reaches it.
        if dirn == 1:
            gap_stop, gap_tgt = o <= stop, o - hs >= target
            hit_stop, hit_tgt = lo <= stop, h - hs >= target
        else:
            gap_stop, gap_tgt = o >= stop, o + hs <= target
            hit_stop, hit_tgt = h >= stop, lo + hs <= target
        if first and not pos["at_open"]:
            hit_tgt = False  # fill was not at the open: the rest of the path is unknown
        if not first and gap_stop:
            gap = abs(stop - o) * pos["qty"]
            self.close(i, o - dirn * self.market_cost(o), o, "STOP_LOSS_GAP", day, gap=gap)
        elif not first and gap_tgt:
            self.close(i, o - dirn * hs, o, "TARGET_GAP", day)
        elif hit_stop:
            ref = stop if self.e.stop_fill == "STOP_PRICE" else (lo if dirn == 1 else h)
            self.close(i, ref - dirn * self.market_cost(ref), ref, "STOP_LOSS", day,
                       ambiguous=hit_tgt)
        elif hit_tgt:
            self.close(i, target, target, "TARGET", day)

    # ------------------------------------------------------------------ loop
    def run(self) -> dict[str, Any]:
        p, ctx, r = self.p, self.ctx, self.r
        pending: dict[str, int] | None = None
        exit_next_open: str | None = None
        last_signal: datetime | None = None
        cur_day: date | None = None
        day: dict[str, Any] = {}
        for i in range(self.start, self.end):
            d, tod = p.days[i], p.tods[i]
            if d != cur_day:
                cur_day = d
                day = {"entries": 0, "realized": 0.0, "halted": False, "locked": False,
                       "stopped": False}
                self.daily.setdefault(d, 0.0)
            held = False
            if pending is not None and pending["fill_i"] == i:
                sig = pending["signal_i"]
                pending = None
                if d != p.days[sig] or tod >= self.so:
                    self._reject("SESSION_END")
                elif self.fill_entry(i, sig):
                    day["entries"] += 1
            if self.pos is not None:
                held = True
                if exit_next_open or tod >= self.so:
                    self.market_exit(i, ctx.o[i], exit_next_open or "SQUARE_OFF", day)
                else:
                    self.intrabar(i, day)
            exit_next_open = None
            last_of_day = i + 1 >= self.end or p.days[i + 1] != d
            if self.pos is not None and last_of_day:
                self.market_exit(i, ctx.c[i], "END_OF_DATA" if i + 1 >= self.end
                                 else "EOD_SQUARE_OFF", day)
            unreal = 0.0
            if self.pos is not None:
                unreal = self.dirn * (ctx.c[i] - self.pos["entry_price"]) * self.pos["qty"]
            if day["realized"] + unreal <= -r.daily_loss_limit:
                day["halted"] = True
                if self.pos is not None and r.exit_on_loss_limit:
                    exit_next_open = "DAILY_LOSS_STOP"
            if day["realized"] >= r.daily_profit_lock:
                day["locked"] = True
            self.in_market += held
            self.equity.append((ctx.ts[i], r.capital + self.realized + unreal))

            # Decision at the close of bar i (uses bars <= i only).
            if self.pos is not None or pending is not None or i < MIN_HISTORY - 1:
                continue
            f = i + 1 + self.e.latency_bars
            if f >= self.end or p.days[f] != d:
                continue
            if not (self.sess[0] <= tod < self.sess[1]) or tod >= self.so:
                continue
            if not self.fired(i):
                continue
            self.signals += 1
            why = self.suppress_reason(i, day, last_signal)
            if why:
                self.suppressed[why] = self.suppressed.get(why, 0) + 1
                continue
            last_signal = ctx.ts[i]
            pending = {"signal_i": i, "fill_i": f}
        return {"trades": self.trades, "equity": self.equity, "daily": self.daily,
                "bars": self.end - self.start, "bars_in_market": self.in_market,
                "signals": self.signals, "suppressed": self.suppressed,
                "rejected": self.rejected, "warnings": sorted(self.warnings),
                "capital": r.capital}


def run(prep: Prepared, strat: Strategy, exe: Execution, risk: Risk, *, seed: int = 0,
        start: int = 0, end: int | None = None) -> dict[str, Any]:
    end = len(prep) if end is None else min(end, len(prep))
    return _Run(prep, strat, exe, risk, seed, max(0, start), end).run()


def summary(raw: dict[str, Any]) -> dict[str, Any]:
    tm = M.trade_metrics(raw["trades"])
    dd_abs, dd_pct = M.drawdown([e for _, e in raw["equity"]])
    ratios = M.daily_ratios(list(raw["daily"].values()), raw["capital"])
    bars = raw["bars"]
    return {**tm, "max_drawdown": round(dd_abs, 2), "max_drawdown_pct": round(dd_pct, 3),
            "sharpe": ratios["sharpe"], "sortino": ratios["sortino"], "days": ratios["days"],
            "exposure_pct": round(raw["bars_in_market"] / bars * 100, 2) if bars else None,
            "bars": bars, "signals": raw["signals"],
            "suppressed_signals": sum(raw["suppressed"].values()),
            "rejected_orders": sum(raw["rejected"].values())}

# Risk manager and paper execution (Phase 6)

Every entry from every bot, and every manual paper buy, goes through one global risk
manager before a simulated order is sent. Limits are per user and shared by all bots.
Bots and AI components can read the limits but cannot change or bypass them.

Live trading is still disabled. All orders in this build go to the **PaperBroker**.

## Order flow

```
signal / manual buy
  → resolve contract (expiry, strike, lot size, tick size, bid/ask)
  → pre-trade risk checks (row lock on the user's risk settings)
  → Order CREATED → VALIDATED → SUBMITTED → ACKNOWLEDGED → (PARTIALLY_FILLED) → FILLED
                  ↘ REJECTED (reasons stored on the order)
  → fills (with charges) → Position OPEN
  → execution worker: mark-to-market, trailing stop, SL / target / square-off / loss stop
  → exit order (SELL) → Position CLOSED, realised gross / charges / net
```

All transitions go through `app/execution/state.py`. An illegal transition raises
`IllegalTransition`; every transition writes an `order_events` row (shown as the order
timeline in the app).

Order states: `CREATED, VALIDATED, SUBMITTED, ACKNOWLEDGED, PARTIALLY_FILLED,
CANCEL_PENDING, UNKNOWN, RECONCILIATION_REQUIRED, FILLED, CANCELLED, REJECTED`.

## Pre-trade checks

| Check | Rule |
|---|---|
| Paper trading | `PAPER_TRADING_ENABLED=true` |
| Kill switch | Off |
| Daily loss stop | Realised net + open mark-to-market above `-daily_loss_limit` |
| Daily profit lock | Realised net below `daily_profit_lock` (blocks entries only) |
| Entries per day | Filled entries + pending entries below `max_entries_per_day` |
| Open positions | Open + pending below `max_open_positions` (all bots together) |
| Lots | 1 to `max_lots_per_trade` |
| Planned risk | (entry − stop) × qty + round-trip charges ≤ `max_risk_per_trade` (and the bot's own risk if lower) |
| Funds | Premium + buy charges ≤ available funds (capital + realised net − used − reserved) |
| Data | Fresh two-sided quote and a chain without FAIL quality flags. (The spread limit is enforced by the PaperBroker when the order is submitted.) |
| Session | Before the square-off time (user's or the bot's, whichever is earlier); with INDstocks data the exchange session must be open. Relaxed for DEMO data, see below |
| Expiry | Contract not expired |
| Reconciliation | No order of this user in `UNKNOWN` / `RECONCILIATION_REQUIRED` |
| Re-entry | A bot that hit a stop-loss today may enter again only if `re_entry_after_stop_loss` |

Two bots signalling on the same candle cannot both take the last position slot: the check
runs under a row lock on `risk_settings` (`SELECT … FOR UPDATE` on PostgreSQL).

## Policies (user-editable in Risk Center)

| Setting | Default | Effect |
|---|---|---|
| `exit_on_loss_limit` | on | At the daily loss stop, open positions get market exits. Off: entries are blocked only |
| `square_off_on_kill_switch` | on | Kill switch also squares off open positions. Off: it only blocks entries and cancels pending entry orders |

The profit lock never closes positions; they keep their stop-loss and target.

## Simulated fills (PaperBroker)

- BUY fills at the **ask**, SELL at the **bid**, with `PAPER_SLIPPAGE_TICKS` (default 1) of
  slippage on market orders. Limit orders fill only when the price is touched.
- A market BUY is rejected without a two-sided quote or when the spread is wider than
  `PAPER_MAX_SPREAD_PCT` (default 5%).
- A SELL (exit) is **never** rejected for liquidity. If there is no bid or market data is
  down, the exit waits and the worker retries every pass.
- Optional partial fills: `PAPER_MAX_FILL_QTY_PER_PASS` (0 = fill at once). Brokerage is
  charged once per order, not per partial fill.
- The PaperBroker keeps its own ledger (`paper_broker_orders`, `paper_broker_fills`),
  separate from `orders`, so reconciliation compares two independent records.

## Idempotency, timeouts and reconciliation

- Each order has a unique `client_order_id`: `SIG-{signal id}` for bot signals,
  `MAN-{user}-{idempotency_key}` for manual buys. Submitting the same id again returns the
  existing order. The app generates one key per intended order and reuses it on retry.
- If submission times out, the order goes to `UNKNOWN`. The worker asks the broker by
  `client_order_id`; it never resends. Found → state is synced. Not found after 2 minutes
  → `RECONCILIATION_REQUIRED`, and new entries are blocked until it is resolved.

## Restart recovery

Orders, positions, daily risk counters (`daily_risk_state`) and lock-hit times are in the
database. After a restart, the execution pass (a singleton held through the
`worker_leases` row `execution`, 60 s lease) picks up open positions and working orders.
Limits already hit stay hit for the IST trading day.

## Charges

Versioned schedules in `app/execution/charges.py`, chosen by trade date.

| Item | Rate | Source |
|---|---|---|
| STT (options, sell side, on premium) | 0.1% from 2024-10-01; **0.15% from 2026-04-01** | Finance Act 2024; Union Budget 2026 |
| NSE transaction charge | 0.03503% of premium | NSE circular |
| SEBI turnover fee | 0.0001% (₹10 per crore) | SEBI |
| Stamp duty (buy side) | 0.003% | Indian Stamp Act (2020 amendment) |
| GST | 18% on brokerage + transaction + SEBI fee | GST |
| Brokerage | `PAPER_BROKERAGE_PER_ORDER`, default **₹20** per executed order | Configurable. INDstocks API pages say ₹10, the INDmoney app ₹20; we default to the higher value |

Not modelled: the NSE IPFT charge and STT on exercised in-the-money options held to expiry.
Estimates should be reconciled with the broker's contract note. The calculator is at
`POST /api/v1/charges/estimate` and in the app under Portfolio → Charges calculator.

## DEMO data rules

- DEMO chains have no exchange lot size. Paper trades on DEMO data use a **placeholder lot
  of 1 unit** (`lot_size_source = DEMO_PLACEHOLDER`), and the app labels it
  "PLACEHOLDER LOT". This is not the NSE lot size.
- `DEMO_MARKET_ALWAYS_OPEN=true` (default): the DEMO market never closes, so session and
  square-off-time checks are relaxed for DEMO data only. Set it to `false` to test those rules.
- With INDstocks data, the lot size must come from the instrument master. Without it the
  order is refused (`NO_LOT_SIZE`).

## Limits of this design

Risk limits reduce, but cannot cap, losses during price gaps, illiquidity, data outages or
broker failures. A stop-loss is checked on each worker pass (default every 15 s) against
the bid, so the actual exit can be worse than the stop price.


## Account modes

Limits are one policy per user and apply identically to the DEMO and REAL accounts. Usage
(open positions, pending entries, daily counters, funds) is evaluated per account, so the
limits are global across all bots within one account mode. DEMO funds come from the DEMO
ledger; a REAL account has no balance source yet, so its funds check fails closed. See
[accounts.md](accounts.md).

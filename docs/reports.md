# Reports (package Q)

Daily reports, P&L analytics and CSV exports for **one account at a time**: the explicit
`account_mode` query parameter, else the user's active mode. Every query filters by
`account_id`, so a DEMO report never contains REAL rows and vice versa. All days are IST
trading days (00:00–24:00 Asia/Kolkata) and all timestamps in reports and CSVs are IST.

Code: `backend/app/reports/` (`daily.py`, `exports.py`, `store.py`, `telegram.py`), API
`backend/app/api/v1/reports.py`, worker `backend/app/workers/reports.py`, mobile
`apps/mobile/app/(app)/reports.jsx`. Tests: `backend/tests/test_reports.py`,
`apps/mobile/__tests__/reports.test.jsx`.

## Mode labels

| Mode | `mode_label` | P&L basis |
|---|---|---|
| DEMO | `DEMO · VIRTUAL` | Simulated fills on virtual money; charges are estimates from the versioned schedule |
| REAL | `REAL · BROKER-CONFIRMED` | Broker-confirmed fills only; charges are estimates until reconciled with the broker |

## Definitions

A **trade** is a position, attributed to the IST day it **closed**. For a trade:
`gross = realised gross P&L`, `charges = all charges of its fills (entry and exit)`,
`net = gross − charges`. Open positions are counted (`open_positions`) but not in P&L.

| Field | Formula |
|---|---|
| `trades`, `wins`, `losses`, `breakeven` | Count of trades; net > 0; net < 0; net = 0 |
| `win_rate_pct` | wins / trades × 100 (null with no trades) |
| `gross`, `charges`, `net` | Sums over trades |
| `avg_win`, `avg_loss` | Mean net of winning / losing trades |
| `best_trade`, `worst_trade` | Max / min trade net |
| `profit_factor` | Σ winning net / −Σ losing net (null without losses) |
| `max_drawdown` | Trades ordered by close time; cumulative net from 0; max(peak − cumulative). Realised, trade by trade (no intraday marks are stored) |
| `charges_breakdown` | Per component (`brokerage, stt, exchange_txn, sebi, stamp_duty, gst`) over every fill of the day's trades; `total` equals `totals.charges` |
| `fills` | Fills timestamped on the day and the charges paid on them (fill-day basis) |
| `slippage` | Entry orders created on the day: per unit = average fill − reference (`risk_snapshot.entry_price`, the ask the risk manager priced) for BUY; `total` = Σ per-unit × qty; `avg_per_unit`; `avg_pct` = mean of per-unit / reference × 100. Exit orders record no reference price and are not included |
| `by_bot`, `by_strategy`, `by_exit_reason` | Trades grouped by bot name (`Manual`), bot `strategy_id` (`MANUAL`), exit reason; each with trades, wins, win rate, gross, charges, net |
| `risk_events` | From `daily_risk_state`: `LOSS_STOP`, `PROFIT_LOCK` (scope = the account's mode); from the audit trail: `KILL_SWITCH_ENGAGED/RELEASED` (scope `ALL_ACCOUNTS`, the kill switch is a user policy) |
| `risk_counters` | `entries`, `stop_loss_exits` and `fill_day_net` from `daily_risk_state` |
| `charges_reconciliation` | REAL only: broker-reported vs estimated charges for the day ([broker-integration.md](broker-integration.md)) |
| `complete` | The IST day has ended |

Two bases, on purpose: P&L in the report is **by close day** (a trade's whole result on the day
it ended, which is what you review), while the risk manager's daily counters are **by fill
day** (what the loss stop must react to). `risk_counters.fill_day_net` shows the latter, so a
position opened yesterday and closed today can make the two differ.

A demo reset closes positions without fills (exit reason `RESET`); their realised P&L is
included as trades, and they add no charges.

### P&L analytics (`/reports/pnl`)

Over `[from, to]` (IST days, at most 366; default the last 30 days):

- `daily`: one row per day with closed trades: `trades, gross, charges, net`, `cumulative`
  (running Σ net from 0) and `drawdown` (running peak − cumulative).
- `cumulative_net`, `max_drawdown` (max of the daily drawdown), `current_drawdown`,
  `best_day`, `worst_day`.
- `by_weekday` (Mon–Sun, close day), `by_instrument` (`<underlying> <CE|PE>`),
  `by_exit_reason`, and `totals` (same fields as the daily report).

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/reports/daily?day=&account_mode=` | Computed on request (default today IST). `stored_at` is set when the worker has stored a report for that day |
| GET | `/api/v1/reports/pnl?from=&to=&account_mode=` | 422 `INVALID_RANGE` (from after to), `RANGE_TOO_LARGE` (> 366 days) |
| GET | `/api/v1/reports/orders.csv?account_mode=` | All orders of the account |
| GET | `/api/v1/reports/fills.csv?account_mode=` | All fills |
| GET | `/api/v1/reports/ledger.csv?account_mode=DEMO` | DEMO cash ledger; REAL → 409 `LEDGER_DEMO_ONLY` |
| GET | `/api/v1/reports/daily-report.csv?from=&to=&account_mode=` | One row per day with trades or risk counters (≤ 92 days) |
| GET | `/api/v1/reports/trades.csv?account_mode=` | Same as `/api/v1/trades.csv` (kept) |

A REAL request without a REAL account returns an empty report (`account_id: null`, a `note`)
and header-only CSVs.

## CSV schemas

All files: UTF-8, comma separated, header row, `account_mode` column, IST timestamps
(`2026-10-28T10:00:00+05:30`), decimals as plain numbers. File name
`quantai-<mode>-<name>.csv`.

| File | Columns |
|---|---|
| `orders.csv` | `account_mode, created_at_ist, order_id, client_order_id, broker, broker_order_id, purpose, side, instrument, qty, order_type, limit_price, status, filled_qty, avg_fill_price, exit_reason, reject_reason, bot_id, data_source, terminal_at_ist` |
| `fills.csv` | `account_mode, ts_ist, fill_id, order_id, broker, broker_fill_id, side, instrument, qty, price, value, brokerage, stt, exchange_txn, sebi, stamp_duty, gst, charges_total` |
| `ledger.csv` | `account_mode, ts_ist, seq, kind, amount, balance_after, ref_type, ref_id, note` |
| `daily-report.csv` | `account_mode, day, trades, gross, charges, net, cumulative, drawdown, loss_stop_hit, profit_lock_hit, entries, stop_loss_exits` (cumulative/drawdown carry over days without trades) |
| `trades.csv` | `mode, account_mode, data_source, instrument, qty, entry_time_ist, avg_entry, exit_time_ist, avg_exit, gross_pnl, charges, net_pnl, exit_reason, bot_id` |

## Persistence and worker

Table `daily_reports` (migration 0009): `user_id, account_id, account_mode, day, payload
(JSON), generated_at`; `UNIQUE(account_id, day)`, composite FK to `accounts(id, mode)`.

```bash
cd backend
python -m app.workers.reports --once                 # today after 16:00 IST, else yesterday
python -m app.workers.reports --day 2026-10-28       # a specific day
python -m app.workers.reports --interval 600         # loop: once per new report day
```

`generate_daily_reports(db, day, notifier=None, notify_emails=None)` reports every account
with activity that day (a closed trade, a fill or a daily risk row) and upserts by
`(account_id, day)`: re-running replaces the payload and never duplicates rows. Each stored
report adds a **"Daily report ready"** alert (category SYSTEM) for the active account mode.

## Alerts

`GET /api/v1/alerts` additionally shows: daily report ready (from `daily_reports`), broker
connected, broker disconnected, broker connection failed and broker sync failed (from the
audit trail). Successful syncs are not alerted.

## Telegram policy

Optional, server-side only: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_USER_EMAILS`.
Disabled unless the token and chat id are set; the chat is server-wide, so only reports of
users listed in `TELEGRAM_USER_EMAILS` are announced (empty = nobody).

- Sent: on the first generation of a daily report (regeneration does not resend).
- DEMO text: day, number of trades, net and win rate, labelled "virtual money".
- REAL text: only "your REAL account daily report for <day> is ready". No amounts.
- Never sent: tokens, broker names or ids, account ids, balances, order ids. Digit runs of 6
  or more are masked (`alert_text` helper for alert titles and levels).
- The bot token is in the Bot API URL path, so the `httpx` request log line is filtered to
  `/bot[REDACTED]/`. The notifier never raises; failures are logged without the token.
- Tests use a fake HTTP transport; the network is never called.

## Mobile

**Reports** screen (More → Research → Reports): active account mode badge; day picker
(previous/next, IST); daily report cards (trades, win rate, net, gross, charges, max drawdown,
slippage, charges breakdown, risk events, by bot / strategy / exit reason); P&L analytics for
the 30 days ending on the selected day (daily-net bar list, cumulative, max and current
drawdown, by exit reason / weekday / instrument); CSV export buttons. Exports need the auth
header, so on web the CSV is fetched with the session and saved through a Blob download. The
native app has no file-sharing module installed (`expo-sharing`), so it says "Export is
available on the web app" and disables the buttons.

# Accounts: DEMO and REAL

One app, two account modes. Every user has a **DEMO** account with ₹10,000 of virtual money
from the moment they register. A **REAL** account would hold funds, orders and positions from
a verified broker; none is integrated in this build, so REAL is visible but unavailable.

Isolation between the two is enforced on the backend by service checks *and* database
constraints, and is covered by tests in `backend/tests/test_accounts_isolation.py`.

## Data model

| Table | Purpose | Key constraints |
|---|---|---|
| `accounts` | One row per user and mode: `mode` (DEMO/REAL), `status` (ACTIVE/DISCONNECTED/CLOSED), `broker` (`PAPER` for DEMO), `broker_connection_ref`, `opening_balance`, `reset_count`, `last_reset_at` | `UNIQUE(user_id, mode)`; `UNIQUE(id, mode)` (target of composite FKs); `CHECK mode IN ('DEMO','REAL')`; `CHECK` DEMO ⇔ `opening_balance IS NOT NULL` (a REAL row can never hold virtual money) |
| `ledger_entries` | DEMO cash ledger: `seq`, `kind`, signed `amount`, `balance_after`, `ref_type`/`ref_id`, `note` | `CHECK account_mode = 'DEMO'`; FK `(account_id, account_mode)` → `accounts(id, mode)`; `UNIQUE(account_id, seq)` |
| `live_authorizations` | Explicit, versioned consent to trade real money (`consent_text_version`, `granted_at`, `revoked_at`) | `CHECK account_mode = 'REAL'`; composite FK. No endpoint can create one in this build |
| `orders`, `fills`, `positions`, `daily_risk_state`, `bot_runs` | Gain `account_id` + `account_mode` (NOT NULL) | Composite FK `(account_id, account_mode)` → `accounts(id, mode)`; `CHECK account_mode IN ('DEMO','REAL')`. `daily_risk_state` is unique per `(account_id, trading_day)` |
| `paper_broker_orders`, `paper_broker_fills` | The simulator's own books | Composite FK and `CHECK account_mode = 'DEMO'`: the simulator can only ever hold DEMO orders |
| `bots.account_mode` | Account the bot trades in (default DEMO) | `CHECK` DEMO/REAL |
| `user_preferences.active_account_mode` | The mode the app shows and trades in (default DEMO) | Validated by the service |

`bots.mode` / `orders.mode` / `positions.mode` (`PAPER`/`LIVE`) are kept for compatibility.
They are derived: DEMO → `PAPER`, REAL → `LIVE`. `account_mode` is the source of truth.

### Migration 0007

Works on SQLite and PostgreSQL. For every existing user it creates a DEMO account with an
opening balance of **₹10,000** (the product spec's starting amount; `risk_settings.capital`
stays a risk/allocation policy and is not used as the balance), writes the `OPENING_CREDIT`
entry, and rebuilds the ledger from that user's recorded paper fills (`TRADE_BUY`/`TRADE_SELL`
and `CHARGES`, each referencing its fill) so cash stays consistent with existing positions.
All existing orders, fills, positions, daily risk rows, paper-broker rows and bot runs are
stamped with that DEMO account. Legacy `bots.mode = LIVE` would map to `account_mode = REAL`
(no such bots exist). Columns become NOT NULL and the constraints are added only after the
backfill. On SQLite the migration runs with foreign keys off (batch table rebuilds would
otherwise cascade-delete child rows) and finishes with `PRAGMA foreign_key_check`.

## Ledger accounting (DEMO)

A cash ledger. Amounts are signed `Decimal`s; `balance_after` is the running balance.

| Kind | When | Amount |
|---|---|---|
| `OPENING_CREDIT` | Account created (registration, lazy creation or migration) | +10,000 |
| `TRADE_BUY` | Each BUY fill | −(price × qty) |
| `TRADE_SELL` | Each SELL fill | +(price × qty) |
| `CHARGES` | Each fill with charges (brokerage once per order, taxes per fill) | −charges |
| `RESET_CLOSE` | Demo reset closes an open position | +(mark × qty) |
| `RESET_ADJUSTMENT` | Demo reset (and test fixtures) | whatever brings cash back to the opening balance |

- **cash** = `balance_after` of the newest entry (= sum of all amounts).
- **available funds** = cash − premium reserved by pending entry orders. The pre-trade
  "Available funds" check uses this, so DEMO buying power comes only from the ledger.
- **equity** = cash + market value of open positions (last price, or entry price if a
  position was never marked).
- **used premium** = Σ entry price × open quantity.
- Realised P&L is not posted as its own entry: it already is the difference between the
  `TRADE_SELL` credits and `TRADE_BUY` debits, so posting it would double count.
- `seq` is unique per account. Two writers racing to append would collide on the constraint
  rather than both writing a balance computed from the same stale entry. Writes also happen
  under the per-user risk lock.

REAL accounts have no ledger. Their cash/available/equity are `null` until a broker balance
snapshot exists, and the pre-trade funds check fails closed for them.

## Isolation guarantees and how each is enforced

| Guarantee | Service check | DB constraint | Test |
|---|---|---|---|
| A row's mode always equals its account's mode | Rows are stamped from the server-side account row | Composite FK `(account_id, account_mode)` → `accounts(id, mode)` | `test_db_constraints_bind_rows_to_their_account_mode` |
| No virtual money on REAL | `ledger.post` refuses non-DEMO accounts | `ledger_entries` CHECK DEMO; `accounts` CHECK opening balance only for DEMO | same |
| The simulator holds only DEMO orders | Paper adapter refuses REAL accounts and requests | `paper_broker_*` CHECK DEMO + composite FK | `test_demo_order_can_never_reach_the_real_adapter` |
| A DEMO order never reaches a real broker | Router picks the adapter from the account row; the real adapter refuses DEMO accounts and requests | — | same |
| One DEMO and at most one REAL account per user | `get_or_create_demo` | `UNIQUE(user_id, mode)` | constraint test |
| DEMO views never show REAL rows (and vice versa) | Every list/summary filters by `account_id`; by-id lookups 404 on a mode mismatch | — | `test_no_cross_mode_pnl_or_risk_contamination` |
| Risk usage is per account | Positions, pending entries, daily counters and funds are read by `account_id` | `daily_risk_state` unique per account and day | same, and `test_global_limits_per_account_mode_across_two_bots` |
| The execution worker never manages REAL rows | `run_pass` skips non-DEMO accounts and counts them (`real_skipped`) | — | contamination test |
| Reset never touches REAL | Reset reads and writes only the DEMO account's rows | — | `test_demo_reset_restores_balance_and_never_touches_real` |

## Risk limits across modes

`risk_settings` stays one policy per user: the same limits apply in both modes. Usage is
evaluated separately per account, so "maximum 1 open position" and "2 entries per day" are
global across every bot **within one account mode**. The kill switch is part of the shared
policy: engaging it blocks entries in every account and runs cancel/exit actions account by
account through each account's own adapter (failures on a REAL account are counted, not hidden).

## Mode switching

- `POST /api/v1/accounts/active {mode}` stores `user_preferences.active_account_mode`.
- Switching never moves orders or positions; they stay in the account that created them.
- Every portfolio, orders, positions, trades CSV, charges, risk and bot-list endpoint takes an
  optional `account_mode` query parameter, defaulting to the active mode, and responses carry
  `account_mode`. Trade and risk alerts follow the active mode.
- Switching to REAL is rejected with **409 `ACCOUNT_MODE_UNAVAILABLE`** and the reasons; both
  successful and rejected switches are audited (`account.mode_switch`).

### When is REAL available?

All of these must hold: a REAL account row exists, its status is `ACTIVE`, and
`LIVE_TRADING_ENABLED` is true. Settings refuse `LIVE_TRADING_ENABLED=true` in this build, so
REAL is never available. The reasons returned are the account reasons plus the system live
blockers from `/system/status`. Orders additionally need an explicit live authorisation and a
healthy broker (see [execution.md](execution.md)).

## Demo reset

`POST /api/v1/accounts/demo/reset {confirm: true}` (400 `CONFIRMATION_REQUIRED` otherwise):

1. Every active DEMO order is cancelled at the simulator (fills it already recorded are applied
   first, so nothing is lost) and moved to `CANCELLED`; orders that never reached the simulator
   are moved to `REJECTED`. Any simulator order still open for the account is cancelled.
2. Every open DEMO position is closed at its **last known price** (entry price if never
   marked) with exit reason `RESET`. No simulated broker order and no charges: the proceeds
   are credited as `RESET_CLOSE` and the realised P&L is recorded on the position, so history
   and reports keep it.
3. A `RESET_ADJUSTMENT` entry makes the balance exactly ₹10,000.
4. `reset_count` increments, `last_reset_at` is set.
5. **Today's DEMO risk counters start fresh** (realised P&L, charges, entries, stop-loss exits,
   loss-stop and profit-lock flags). The previous values are kept in the audit record; earlier
   days' rows are untouched history.
6. Audited as `account.demo_reset` with the counts and previous counters.

Decisions:
- **Allowed while the kill switch is engaged.** A reset places no orders and only reduces demo
  exposure. It does not release the kill switch; new entries stay blocked until the user
  releases it.
- REAL rows are never read or written by a reset.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/accounts` | `active_mode`, `live_trading_enabled` (always false), and both accounts with status, balances (DEMO from the ledger; REAL `null` + note), `available`, `unavailable_reasons` |
| POST | `/api/v1/accounts/active` | `{mode}`; REAL → 409 `ACCOUNT_MODE_UNAVAILABLE` |
| GET | `/api/v1/accounts/demo/ledger?limit=&offset=` | Paginated, newest first |
| POST | `/api/v1/accounts/demo/reset` | `{confirm: true}` |
| POST | `/api/v1/accounts/real/authorize` | Always 409 `LIVE_TRADING_LOCKED` with blockers; audited |

## Limitations

- No REAL account can be created through the API; only tests insert REAL rows.
- No broker balance snapshots exist yet, so REAL balances are always `null`.
- Bot signals are not yet stamped with an account; the per-mode daily signal count joins
  through `bot_runs.account_mode`.

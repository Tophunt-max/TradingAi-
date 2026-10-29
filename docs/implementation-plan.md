# Implementation plan v2 (dual account mode)

This plan maps the v2 master prompt (DEMO and REAL account modes) onto the code that
already exists. It was written after inspecting the repository on 29 Oct 2026.

## Current stack (unchanged)

| Layer | What is in the repo |
|---|---|
| Mobile | Expo + Expo Router, JS/JSX, React Query, Reanimated, Zustand auth store, own design system (`components/ui`) |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic (6 migrations), Pydantic v2, Redis rate limiting |
| Workers | Price feed, bot runner (leased, horizontally scalable), execution pass (singleton lease) |
| Data | INDstocks read-only client (not yet verified with a real token) and a synthetic provider |
| Tests | 180 backend tests (SQLite + PostgreSQL), 36 Jest tests, Playwright web E2E in two data modes |
| Infra | Dockerfile, docker-compose (Postgres, Redis, API), GitHub Actions CI. `infra/` is empty |

## Terminology decision

v2 uses **DEMO** for the account mode. The code already used `DEMO` for synthetic market
data. To keep them apart:

- **Account mode:** `DEMO` (virtual money) and `REAL` (broker money). UI badges:
  `DEMO · VIRTUAL` and `REAL`.
- **Market data source:** synthetic prices are shown as `SIMULATED` in the UI. The internal
  value stays `source = "DEMO"` until Phase 3 renames it, so existing data and tests keep working.
- Existing `mode = "PAPER"` on bots/orders/positions means "executes in the DEMO account".
  Phase 3 replaces it with an `account_mode` column (see below).

## Gap analysis against the v2 prompt

| v2 section | Status | Notes |
|---|---|---|
| §3 Design system | Done | Plus new `AccountModeBadge` / `AccountModeSelector` (Phase 1 v2) |
| §4 Account modes | **Missing on the backend** | No account entity, no virtual ledger, no reset, no per-mode isolation. UI selector exists (DEMO only) |
| §4A Demo ₹10,000 | Partial | `risk_settings.capital` defaults to ₹10,000 and funds are derived from it; there is no ledger of balance transactions and no reset |
| §4B Real account | Missing | No broker adapter; live trading is hard-locked |
| §5 Screens | Mostly done | Missing: NIFTY/BANKNIFTY detail pages (generic chart screen exists), sector breadth, news, expiry calendar, futures, prediction history, strategy comparison, bot performance, P&L analytics, trading permissions, backtesting lab, reports |
| §6 Navigation | Done | 5 tabs + More; Help & Support added |
| §7 Home | Done except real balance | Shows virtual balance, risk usage, bots, alerts. Watchlist and recent orders cards missing |
| §8 Market data | Done (read-only) | Quality checks, staleness, holidays config. Unverified against real INDstocks |
| §9 Technicals | Done | Inverted hammer, flags/pennants, equal highs/lows, displacement to verify in Phase 4 |
| §10 AI pipeline | Partial | Baseline logistic model + gate; XGBoost/LightGBM not evaluated; cost and risk steps not in the analysis view |
| §11 Strategies | Done | DCA/Grid research-only not built |
| §12–14 Bots | Done | Bot "Total P&L", "Risk usage" on cards missing |
| §15 Risk | Done for one account | Defaults already match (₹250 / ₹800 / ₹1,200 / 2 entries / 1 position / 1 lot / 15:15). Must become per account mode |
| §16 Execution | Done for paper | `PaperBroker` exists; v2 names `PaperExecutionAdapter` / `RealBrokerExecutionAdapter` |
| §17 Real security | Partial | Secrets server-side, audit, sessions. Broker OAuth not built |
| §18 Portfolio | Done for demo | |
| §19 Backtesting | Missing | |
| §20 Learning | Partial | Registry and predictions stored; outcomes, drift, shadow testing missing |
| §21 Extras | Partial | Daily reports, Telegram, CSV (trades CSV exists) |
| §24 Tests | Partial | Missing: demo reset, mode isolation, broker disconnection, unauthorized live order |

## Phase plan (v2 numbering)

| Phase | Scope | Builds on |
|---|---|---|
| 1 ✅ | Inspection, this plan, architecture update, account-mode design-system components, Help & Support, terminology | Old phases 1–2 |
| 2 | Mobile: Home watchlist + recent orders, NIFTY/BANKNIFTY detail, expiry calendar, prediction history, bot performance, P&L analytics shells wired to existing APIs; account mode shown on every financial screen | Old phase 2 |
| 3 | **Backend account modes:** `accounts` table (one DEMO per user, REAL only via broker connection), `ledger_entries` with ₹10,000 opening credit, `account_id` + `account_mode` on orders/fills/positions/bot runs/daily risk state with DB check constraints, per-account risk state, demo reset (audited, demo only), server-side mode switch API, rename data source DEMO → SIMULATED | Old phase 6 tables |
| 4 | Market-data gaps (equal highs/lows, displacement, futures, expiry calendar), real INDstocks verification once a token is available | Old phases 3–4 |
| 5 | AI pipeline: cost + risk steps in analysis, trade proposal schema shared with execution, XGBoost/LightGBM evaluation | Old phase 4–5 |
| 6 | Rename `PaperBroker` → `PaperExecutionAdapter` behind an `ExecutionAdapter` interface; router that picks the adapter from the account on the server; latency and gap simulation | Old phase 6 |
| 7 | Broker-neutral `RealBrokerExecutionAdapter`, one verified broker, OAuth, funds/positions/orders sync, reconciliation, live-order consent flow. Live stays locked by default | — |
| 8 | Backtesting lab, outcome scoring, drift, shadow testing, reports, daily report, alerts delivery | — |
| 9 | Security review, load tests, monitoring, backups, staging, deployment docs | — |

## Account isolation design (Phase 3, for review)

> Implemented (migration 0007). See [accounts.md](accounts.md) and [execution.md](execution.md)
> for the final model; differences from this sketch: the REAL/broker-connection CHECK is
> replaced by a CHECK that only DEMO rows carry an opening balance, and the DEMO-only ledger
> rule is a CHECK constraint plus the composite FK.

- `accounts(id, user_id, mode DEMO|REAL, broker_connection_id NULL, status, created_at)`
  with `UNIQUE(user_id) WHERE mode='DEMO'` and `CHECK (mode='REAL') = (broker_connection_id IS NOT NULL)`.
- Every order, fill, position, bot run, daily risk row and ledger entry carries `account_id`.
  Composite foreign keys `(account_id, account_mode)` → `accounts(id, mode)` make it impossible
  to attach a demo order to a real account row or vice versa.
- `ledger_entries` exist only for DEMO accounts (`CHECK` via trigger or service + test).
  REAL balances are read from the broker and cached in `broker_snapshots`, never credited.
- The execution router loads the account server-side and selects the adapter. The paper adapter
  rejects REAL accounts and the broker adapter rejects DEMO accounts (defence in depth, tested).
- Risk limits stay per user (shared policy) but usage, counters and kill-switch actions are
  evaluated per account.
- Demo reset: closes demo positions at the last price, cancels demo orders, writes a reset ledger
  entry and an audit record, restores ₹10,000. REAL data is untouched.

## Risks and open questions

- **No real INDstocks token yet:** live market-data paths are tested only against a fake.
- **Broker for REAL mode:** which broker (Zerodha, Upstox, Dhan, Angel One, INDstocks orders)?
  Needs the user's choice, API access and a regulatory check before Phase 7.
- **Stacked, unmerged PRs #1–#6:** reviewing and merging them reduces rebase risk later.
- **Data-source rename** in Phase 3 touches stored rows (`data_source = 'DEMO'`); needs a migration.

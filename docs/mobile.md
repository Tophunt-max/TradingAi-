# Mobile app: screen inventory and account-mode display (work package O)

App: `apps/mobile` (Expo Router). Routes below are paths inside the app.

## Source of the 81 screen categories

The P2 prompt asks for 81 screens. Its full list is not stored in this repository; the summary
in [master-plan.md](master-plan.md) and the §5 gap list in
[implementation-plan.md](implementation-plan.md) are the reference used here. The inventory
below groups those screens by area and says, for each, whether it exists, is combined into
another screen, or is not built (and why). It is not a line-by-line check against the original
81 items.

## Inventory

### Auth and onboarding (`app/(auth)`)

| Screen | Route | Status |
|---|---|---|
| Welcome / splash | `/welcome` (+ splash view) | Built |
| Onboarding | `/onboarding` | Built |
| Register (with risk disclosure) | `/register` | Built |
| OTP verification | `/verify` | Built |
| Login | `/login` | Built |
| Forgot / reset password | `/forgot-password`, `/reset-password` | Built |

### Tabs

| Screen | Route | Status |
|---|---|---|
| Home dashboard (indices, performance, risk & bots, alerts, quick actions) | `/` | Built |
| Markets (indices, movers, search, screener entry) | `/markets` | Built |
| AI Lab: AI Market Analysis (pipeline, probabilities, regime, router, setup, evidence, patterns, SMC, levels) | `/ai-lab` | Built; entry to AI Intelligence and Decision Report |
| My Bots | `/bots` | Built |
| Portfolio (summary, positions, orders, charges) | `/portfolio` | Built |

### Stack screens

| Screen | Route | Status |
|---|---|---|
| **AI Intelligence Dashboard** | `/intelligence` | Built (this package) |
| **Decision Report** | `/decision?underlying=&timeframe=` | Built (this package) |
| Chart & analysis (NIFTY / BANKNIFTY / stock detail) | `/instrument/[symbol]` | Built; NIFTY and BANKNIFTY detail pages are this generic chart screen |
| Option chain + manual paper buy | `/option-chain` | Built |
| Options intelligence | inside Option Chain (`OptionsIntelSection`) | Combined into Option Chain |
| Multi-timeframe view | inside AI Lab (`MtfStrip`) | Combined |
| Market screener | `/screener` | Built |
| Events & calendar (events, expiries, holidays, event risk) | `/events` | Built; also the expiry calendar |
| AI Model Lab (registry, metrics, promotion) | `/models` | Built; prediction history is a section of the AI Intelligence Dashboard |
| Model comparison | `/model-compare` | Built |
| Backtesting Lab (run, walk-forward, Monte Carlo) | `/backtesting` | Built |
| Create bot / AI Auto Create / Manual builder / Templates | `/bots/new`, `/bots/ai-create`, `/bots/builder`, `/bots/templates` | Built |
| Bot details (performance, signals, logs, runs) | `/bots/[id]` | Built; bot performance is a section here |
| Orders | `/orders` | Built |
| Order details (timeline, fills, risk checks) | `/orders/[id]` | Built |
| Positions (open / closed) | `/positions` | Built |
| Charges calculator | `/charges` | Built |
| Risk Center (limits, kill switch, exposure) | `/risk-center` | Built |
| Notifications | `/alerts` | Built |
| Account (DEMO ledger, reset, mode) | `/account` | Built |
| Broker connections | `/brokers` | Owned by work package P (other agent) |
| Reports (daily report, P&L analytics, CSV) | `/reports` | Owned by work package Q (built in parallel, not reviewed here) |
| Learn & Grow | `/education` | Built |
| Help & support | `/help` | Built |
| System health | `/system-health` | Built |
| Profile & settings, security, sessions, audit history | `/settings`, `/settings/security`, `/settings/sessions`, `/settings/audit` | Built |
| More (feature directory) | `/more` | Built |
| Not-yet-built module card | `/feature/[id]` | Generic placeholder for modules without a route |

### Combined into other screens

- Strategy performance, model versions, calibration, drift, error attribution, data freshness,
  broker health and risk-limit usage are sections of the **AI Intelligence Dashboard** instead
  of separate screens.
- Strategy router and regime confidences are sections of AI Lab.
- P&L analytics are the Portfolio summary plus the dashboard's strategy performance and account
  results (no separate analytics screen).

### Not built, and why

| Screen | Why |
|---|---|
| News feed | No licensed news source; imported events are shown in Events instead |
| Sector breadth / heat map | No breadth data source (regime v2 lists `breadth` as unavailable) |
| Futures / basis | The market-data interface has no futures quotes |
| Trading permissions / live authorisation flow | Live trading is locked in this build (see [execution.md](execution.md)) |
| Strategy comparison (side by side) | Backtests are not persisted, so there is nothing stored to compare; run them in the Backtesting Lab |

## Account-mode display rules

- **Account-scoped screens** (anything that shows money, orders, positions, bots, risk usage or
  reports) show the **active** account mode with `AccountModeBadge` via
  `features/account/ActiveModeBadge.jsx`, which reads `useAccountMode()` (server
  `GET /accounts`). Until the server answers it shows DEMO; REAL is never assumed.
  - Home performance card and Portfolio summary: `ActiveModeBadge` and a mode-specific subtitle.
  - Orders, Positions, Risk Center, My Bots, Charges, Backtesting: `ActiveModeHeader` strip at the
    top with a one-line note (for example "Backtests are simulations: they never place orders in
    either account").
  - The always-visible trading-mode banner shows the active mode next to `SIMULATED DATA` and
    `LIVE LOCKED`.
- **Records** (an order, a position) show their **own** `account_mode` from the API
  (`SourceBadges mode={o.account_mode}`), which equals the active mode because lists are scoped
  to it. Bot cards and bot details use the bot's own mode (`ModeBadge`).
- **AI Intelligence Dashboard and Decision Report** show the `account_mode` the server used for
  the response, and the paper-order confirmation dialog states it in its message
  ("DEMO · VIRTUAL account. Buy 1 lot …").
- `SIMULATED` (DEMO market data, `DataSourceBadge`) is a different badge from the `DEMO · VIRTUAL`
  account badge and can appear on either mode's screens.
- Account-scoped React Query keys (`features/account/mode.js` `ACCOUNT_SCOPED`, now including
  `intelligence` and `decision`) are invalidated when the mode changes.

## AI Intelligence Dashboard and Decision Report

- `/intelligence` calls `GET /api/v1/intelligence/dashboard` and renders every section with its
  status badge (`OK`, `NO DATA`, `UNAVAILABLE`), source and reason. Nothing is filled in on the
  client.
- `/decision` calls `GET /api/v1/decision/{underlying}` and shows the decision badge, supporting
  and conflicting evidence, NO TRADE and watch reasons, proposal details, invalidation, and the
  disclaimer. Only for a `TRADE_PROPOSAL` in DEMO is there a **Review paper order** button: it
  opens a `ConfirmDialog` and, after confirmation, posts the proposal's `paper_order_request`
  with an idempotency key created when the dialog opened (`usePaperEntry`, `newIdempotencyKey`).
- Tests: `apps/mobile/__tests__/intelligence.test.jsx`.

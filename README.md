# QuantAI F&O

**Smarter Analysis. Better Decisions. Higher Consistency.**

An AI-assisted analysis and paper-trading platform for Indian index derivatives
(NIFTY / BANKNIFTY options buying first), with Android, iOS and web clients.

> ⚠️ QuantAI F&O is an analysis and simulation tool. Derivatives trading carries a
> substantial risk of loss. Predictions can be wrong, and paper or backtest results are
> not live profits and do not guarantee future returns. **Live trading is disabled in
> this build.**

## v2 plan: Demo and Real account modes

The master prompt now asks for two account modes in one app: **DEMO** (₹10,000 virtual money)
and **REAL** (funds and orders from a connected broker). v2 Phase 1 is done: repository
inspection and gap analysis ([docs/implementation-plan.md](docs/implementation-plan.md)), an
updated [architecture](docs/architecture.md), an account-mode selector on Home (DEMO active,
REAL shown as unavailable with the reason), `DEMO · VIRTUAL` / `REAL` badges, a Help & Support
screen, and the `SIMULATED` label for synthetic market prices so it is not confused with the
DEMO account. The backend account model, demo ledger and reset come in v2 Phase 3.

**Hinglish:** ab app mein do account mode honge: **DEMO** (₹10,000 virtual paisa) aur **REAL**
(broker se asli funds). Abhi sirf DEMO chalta hai; REAL "unavailable" dikhta hai jab tak verified
broker connect na ho. Nakli market prices par ab "SIMULATED" badge aata hai, taaki DEMO account se
confusion na ho.

## Status of the original phases 1–6: complete

Paper trading now works end to end. Every entry, from any bot or a manual buy on the option
chain, passes one **global risk manager** (kill switch, daily loss stop, profit lock, entries
per day, open positions, lots, planned risk incl. charges, funds, session, data quality,
unresolved orders). Allowed entries become orders with a full state machine and are filled by
a **PaperBroker** at bid/ask with slippage. Charges use versioned NSE/SEBI/GST schedules
(STT 0.15% on option sells from 1 Apr 2026). The execution worker manages stop-loss, target,
trailing stop, square-off and the daily loss stop, and recovers after restarts. The kill switch
cancels pending entries and (by policy) squares off positions. See [docs/risk.md](docs/risk.md).

What is real vs. simulated in Phase 6:

| Item | State |
|---|---|
| Risk checks, order states, positions, daily counters, P&L arithmetic | Real logic, stored in the database, tested on SQLite and PostgreSQL |
| Fills | **Simulated** (PaperBroker): bid/ask + slippage, never a real exchange |
| Charges | Estimates from published rates; brokerage default ₹20/order (configurable). IPFT and STT on exercise not modelled |
| Prices on DEMO | Synthetic, labelled `DEMO`; lot size is a labelled placeholder of 1 unit |
| Prices on INDstocks | Real-shaped, but still not verified with a real token |
| Live trading | Disabled. No broker adapter exists (Phase 8) |

## Phase 5 (bot system)

Bots can now be started, paused, resumed and stopped for real. A persistent, horizontally scalable
worker evaluates each running bot's configured entry conditions on every closed candle. It records
**signals only (no orders)** and enforces session windows, cooldowns and the **global daily entry
limit shared by all bots**. Bot details show runtime health, signals, logs and runs. AI Auto Create
now picks the strategy from the market regime and the side from the trend. See
[docs/bots.md](docs/bots.md).

## Phase 4 (analysis engine)

Phase 4 adds the analysis pipeline:
- More indicators: VWAP, Supertrend, ADX, CCI, Stochastic, pivots, Fibonacci and relative volume.
- Candlestick and chart patterns, and Smart Money Concepts (BOS/CHOCH, order blocks, FVGs, liquidity sweeps).
- Regime detection and a strategy router.
- A baseline AI model with walk-forward validation and a calibration gate, plus a model registry.
- Multi-timeframe view, an explained 12-step pipeline, and a chart with pattern markers and SMC zones.

Patterns are only reported once confirmed and never repaint. **Probabilities are shown only from a promoted model**, and a
model can be promoted only if it beats a base-rate baseline out of sample, is calibrated and was trained on
real data. The analysis decision stays **NO TRADE** while no model is promoted; paper trades come from bots and manual buys through the risk manager (Phase 6). Details are in
[docs/analysis.md](docs/analysis.md).

## Phase 3 (market data): not yet verified with a real INDstocks token

Phase 3 connects QuantAI to the official **INDstocks (INDmoney) API** for read-only market data:
instrument master (lot/tick size, expiries), stock quotes, candles, the option chain with
INDstocks Greeks and IV, and a WebSocket price-feed worker. Every market payload now reports its
provider, time and a data-quality check. Setup and the remaining verification steps are in
[docs/indstocks.md](docs/indstocks.md). DEMO mode is still the default.

Known limits of the INDstocks API that the app shows to the user:
- **Index level** comes from the option chain, so there is no day change.
- **Index charts** use near-month futures as a labelled proxy.
- **The REST quote has no timestamp.** Staleness is detected only for instruments streamed by the price-feed worker.

## Phase 2 (mobile UI)

Phase 2 added the screens from the design mockup, wired to real API contracts:
Home dashboard, Markets (indices, stocks, movers, search), candlestick chart with
EMA / Bollinger / RSI / MACD, Option Chain with Greeks, AI Market Analysis, My Bots,
AI Auto Create (4-step wizard), Manual Bot Builder (6 steps), Strategy Templates,
Bot Details with validation, Portfolio / Orders / Positions, Risk Center with a
persistent kill switch, Notifications, Broker Connections, Screener and Learn & Grow.
Screenshots from the automated browser test are in [docs/screenshots](docs/screenshots).

What is real vs. placeholder in Phase 2:
- **Real and saved in the database:** bot configurations (versioned), global risk limits, the kill switch, and alerts (built from your audit trail).
- **Synthetic, labelled `DEMO`:** quotes, candles, option chain and the inputs to the analysis.
- **Filled since Phase 6:** orders, positions and P&L come from the paper ledger. The Phase 2 sample-layout toggle has been removed.
- **Not shown on purpose:** AI probabilities (no calibrated model yet) and lot sizes (no instrument master yet).
- **Not built:** Google/Apple sign-in from the mockup, and the Backtesting Lab and Model Lab screens (Phase 7).

| Area | State |
|---|---|
| Monorepo, Docker Compose, env templates, CI | ✅ |
| FastAPI backend: config guards, errors, JSON logging, request IDs, rate limiting | ✅ |
| Auth: email/phone registration, OTP verification, login, lockout, rotating refresh tokens, sessions, password reset/change | ✅ |
| PostgreSQL schema + Alembic migration (users, preferences, sessions, refresh tokens, verification codes, audit logs) | ✅ |
| Audit trail (user + admin views), RBAC (`user`, `admin`) | ✅ |
| Expo app: design system, auth flow, 5-tab navigation, More menu, settings, sessions, audit, system health | ✅ |
| Demo market data, labelled `DEMO` everywhere | ✅ |
| Phase 2 screens + APIs (charts, option chain, analysis, bots, risk, portfolio, alerts, brokers) | ✅ |
| Causal indicators (EMA, RSI, MACD, Bollinger, ATR, confirmed pivots) and Black-Scholes Greeks | ✅ |
| Market data (3), analysis engine (4), bots (5), risk manager + paper execution (6) | ✅ |
| Backtesting (7), broker integration (8), hardening/deployment (9) | ❌ Not built |

Screens for unbuilt modules show "Not built yet · Phase N" and no numbers.

### Not yet integrated (honest gaps)
- **SMS/email OTP provider.** In development, codes are printed to the API log. Staging
  and production refuse to start with console delivery.
- **Market-data provider.** Only synthetic DEMO data exists.
- **Broker.** No broker adapter exists. Live trading cannot be enabled.
- **Hindi/Hinglish UI strings.** The language preference is stored, but the app text is English only.

## Quick start

### Backend
```bash
cp .env.example .env               # set SECRET_KEY
docker compose up -d postgres redis
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload      # http://localhost:8000/docs
```
Or run everything with `docker compose up --build` (the API container runs migrations on start).

### Mobile
```bash
cd apps/mobile
npm install
EXPO_PUBLIC_API_URL=http://localhost:8000 npx expo start   # press a / i / w
```
On a physical phone, set `EXPO_PUBLIC_API_URL` to your computer's LAN IP. On web, the
refresh token is kept in memory only, so reloading the page signs you out (by design).

### Tests
```bash
pytest                                   # from repo root (SQLite)
TEST_DATABASE_URL=postgresql+psycopg://user@host/db pytest   # same suite on PostgreSQL
ruff check .
cd apps/mobile && npx jest               # unit + component tests
npx expo export --platform web           # bundle check
python scripts/e2e_web_smoke.py --dist apps/mobile/dist      # browser E2E (needs playwright)
```

## Implementation plan (original numbering)

Superseded by the v2 phase plan in [docs/implementation-plan.md](docs/implementation-plan.md); kept for history.

| Phase | Scope |
|---|---|
| 1 ✅ | Foundation: repo, design system, auth, DB, API, navigation, demo mode |
| 2 ✅ | Mobile UI for dashboard, markets, charts, option chain, AI, bots, orders, positions, portfolio, risk center, alerts, wired to typed contracts |
| 3 ✅ | Authorised market-data provider (INDstocks), instrument master, candles, live updates, option chain, data-quality validation |
| 4 ✅ | Indicators, candlestick patterns, SMC, regime detection, strategy router, baseline AI predictions, explanations |
| 5 ✅ | Bot system: AI Auto Create, manual builder, templates, validation, lifecycle, persistent workers |
| 6 ✅ | Global risk manager, order state machine, simulated fills, charges, restart recovery, kill switch |
| 7 | Backtesting (walk-forward, no lookahead), model registry, drift detection, reports |
| 8 | Verified broker adapter. Live trading stays locked until every test passes and the user explicitly authorises it |
| 9 | Security review, load testing, monitoring, backups, staging, deployment docs |

See [docs/architecture.md](docs/architecture.md) and [docs/api.md](docs/api.md).

---

## हिंदी / Hinglish गाइड

**QuantAI F&O kya hai?** Yeh ek AI-assisted analysis aur paper-trading platform hai,
jo pehle NIFTY aur BANKNIFTY options buying par focus karta hai.

**Zaroori chetavani:** Yeh app profit ki guarantee nahi deta. F&O trading mein bada
nuksaan ho sakta hai. AI predictions galat ho sakti hain. Paper trading aur backtest
ke results asli (live) profit nahi hote. **Is build mein live trading band hai.**

**Abhi kya ready hai (Phase 1):**
- Backend (FastAPI + PostgreSQL): signup, OTP verification, login, sessions, password reset aur audit log.
- Mobile app (Expo): dark theme design system, login/signup screens, 5 tabs (Home, Markets, AI Lab, Bots, Portfolio), settings.
- Market data sirf **DEMO** (nakli) hai, aur har jagah "DEMO" badge dikhta hai.

**Phase 2 mein kya aaya:** Home dashboard, Markets, candlestick chart (EMA/RSI/MACD), Option Chain (Greeks ke saath),
AI Analysis, My Bots, AI Auto Create, Manual Bot Builder, Templates, Bot Details, Portfolio/Orders/Positions,
Risk Center (kill switch ke saath), Notifications, Broker Connections, Screener aur Learn & Grow.
Bots aur risk limits database mein save hote hain. Orders aur P&L abhi khaali hain (Phase 6 se bharenge),
aur AI probability tab tak nahi dikhegi jab tak calibrated model ready nahi hota.

**Phase 3 mein kya aaya:** INDstocks (INDmoney) API se read-only market data aata hai: instrument master
(lot size, tick size, expiry), stock quotes, candles, option chain (Greeks/IV ke saath) aur live price feed worker.
Har screen par dikhta hai ki data kahan se aaya, kab aaya, aur quality check pass hua ya nahi.
Setup ke liye `docs/indstocks.md` dekhein. Access token sirf server ki `.env` mein rakhein, app mein kabhi nahi.
Yeh integration abhi asli INDstocks token ke saath test nahi hua hai.

**Phase 4 mein kya aaya:** naye indicators (VWAP, Supertrend, ADX, Stochastic, pivots, Fibonacci), candlestick
aur chart patterns, Smart Money Concepts, market regime, strategy router aur AI model lab. AI probability tabhi
dikhegi jab model asli data par baseline se behtar aur calibrated sabit ho, aur admin use promote kare.
AI Analysis screen ka decision abhi bhi NO TRADE rehta hai (koi promoted model nahi hai); paper trades bots ya manual buy se hote hain.

**Phase 5 mein kya aaya:** bots ab sach mein Start/Pause/Resume/Stop hote hain. Ek background worker har band
candle par bot ki entry conditions check karta hai aur signal record karta hai, lekin **koi order nahi lagata**
(orders Phase 6 mein aayenge). Cooldown, session time aur saare bots ke liye ek global daily limit lagte hain.
Worker chalane ke liye: `python -m app.workers.bot_runner`.

**Phase 6 mein kya aaya:** ab paper trading poori chalti hai. Har entry (bot ka signal ho ya option chain
se manual "Paper buy") pehle ek **global risk manager** se guzarti hai: kill switch, daily loss stop,
profit lock, din ki entries, open positions, lots, charges ke saath planned risk, funds, market time aur
data quality. Pass hone par order banta hai aur **PaperBroker** use ask/bid par slippage ke saath fill karta
hai (yeh simulation hai, asli exchange nahi). Charges NSE/SEBI/GST ke published rates se lagte hain
(1 April 2026 se option sell par STT 0.15%). Worker stop-loss, target, trailing stop aur square-off
khud manage karta hai, aur restart ke baad bhi wahi state se chalta hai. Kill switch pending orders cancel
karta hai aur (setting ke hisaab se) positions square off karta hai. DEMO data par lot size asli nahi hai:
1 lot = 1 unit ka placeholder hai, aur app mein "PLACEHOLDER LOT" likha aata hai.
Paper P&L asli profit nahi hai. Live trading abhi bhi band hai. Details: `docs/risk.md`.

**Abhi kya baaki hai:** asli INDstocks token ke saath verification, backtesting aur model learning
(Phase 7), verified broker adapter (Phase 8), aur security/load testing/deployment (Phase 9). Jo screen
ready nahi hai, us par "Not built yet · Phase N" likha aata hai.

**Kaise chalayein:**
1. `cp .env.example .env` karein aur `SECRET_KEY` set karein.
2. `docker compose up -d postgres redis`
3. `cd backend && pip install -r requirements-dev.txt && alembic upgrade head && uvicorn app.main:app --reload`
4. Doosre terminal mein: `cd apps/mobile && npm install && npx expo start`
5. Signup karte waqt OTP code API server ke log mein dikhega (sirf development mode mein).

**Tests:** repo root se `pytest` chalayein, aur `apps/mobile` folder mein `npx jest` chalayein.

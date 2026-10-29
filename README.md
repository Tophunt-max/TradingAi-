# QuantAI F&O

**Smarter Analysis. Better Decisions. Higher Consistency.**

An AI-assisted analysis and paper-trading platform for Indian index derivatives
(NIFTY / BANKNIFTY options buying first), with Android, iOS and web clients.

> ⚠️ QuantAI F&O is an analysis and simulation tool. Derivatives trading carries a
> substantial risk of loss. Predictions can be wrong, and paper or backtest results are
> not live profits and do not guarantee future returns. **Live trading is disabled in
> this build.**

## Current status: Phase 2 (mobile UI) complete

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
- **Empty until Phase 6:** orders, positions and P&L. A "Preview sample layout" toggle shows labelled sample rows.
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
| Market data, analysis engine, bots, risk manager, paper execution, backtesting, broker integration | ❌ Not built (Phases 3–9) |

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

## Implementation plan

| Phase | Scope |
|---|---|
| 1 ✅ | Foundation: repo, design system, auth, DB, API, navigation, demo mode |
| 2 ✅ | Mobile UI for dashboard, markets, charts, option chain, AI, bots, orders, positions, portfolio, risk center, alerts, wired to typed contracts |
| 3 | Authorised market-data provider, instrument master, candles, live updates, option chain, data-quality validation |
| 4 | Indicators, candlestick patterns, SMC, regime detection, strategy router, baseline AI predictions, explanations |
| 5 | Bot system: AI Auto Create, manual builder, templates, validation, lifecycle, persistent workers |
| 6 | Global risk manager, order state machine, simulated fills, charges, restart recovery, kill switch |
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

**Abhi kya baaki hai:** Real market data, AI analysis, bots, risk manager, paper
execution, backtesting aur broker integration. Ye sab agle phases mein banenge. Jo screen
ready nahi hai, us par "Not built yet · Phase N" likha aata hai.

**Kaise chalayein:**
1. `cp .env.example .env` karein aur `SECRET_KEY` set karein.
2. `docker compose up -d postgres redis`
3. `cd backend && pip install -r requirements-dev.txt && alembic upgrade head && uvicorn app.main:app --reload`
4. Doosre terminal mein: `cd apps/mobile && npm install && npx expo start`
5. Signup karte waqt OTP code API server ke log mein dikhega (sirf development mode mein).

**Tests:** repo root se `pytest` chalayein, aur `apps/mobile` folder mein `npx jest` chalayein.

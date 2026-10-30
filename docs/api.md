# API (v1), Phase 1

The full machine-readable spec is in [`openapi.json`](./openapi.json). Interactive docs are
served at `http://localhost:8000/docs` in development and test only.

## Conventions

- Base path `/api/v1`. JSON request and response bodies.
- Auth: `Authorization: Bearer <access_token>`. Access tokens last 15 minutes. Use
  `/auth/refresh` to rotate them.
- Every error uses one envelope:
  ```json
  { "error": { "code": "INVALID_CREDENTIALS", "message": "Invalid credentials.", "details": null, "request_id": "9f1c..." } }
  ```
- `X-Request-ID` is echoed (or generated) on every response.
- List endpoints take `limit` (1–100, default 20) and `offset`, and return
  `{ items, total, limit, offset }`.
- Timestamps are ISO-8601 UTC.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health/live` | – | Liveness |
| GET | `/health/ready` | – | DB / Redis checks. Workers and broker report `not_configured` |
| GET | `/system/status` | – | Environment, data mode, live-trading blockers |
| POST | `/auth/register` | – | Email or phone + password + risk acknowledgement. Sends OTP |
| POST | `/auth/verify` | – | OTP → token pair |
| POST | `/auth/verify/resend` | – | Always 202 |
| POST | `/auth/login` | – | 403 `VERIFICATION_REQUIRED`, 429 `ACCOUNT_LOCKED` |
| POST | `/auth/refresh` | – | Rotates the refresh token. Re-use revokes the session |
| POST | `/auth/logout` | ✓ | Revokes the current session |
| POST | `/auth/password/forgot` | – | Always 202 (enumeration-safe) |
| POST | `/auth/password/reset` | – | Revokes all sessions |
| GET/PATCH | `/users/me` | ✓ | Profile |
| GET/PATCH | `/users/me/preferences` | ✓ | Theme, language, motion, haptics, notifications |
| POST | `/users/me/password` | ✓ | Signs out other sessions |
| GET | `/users/me/sessions` | ✓ | Active devices |
| DELETE | `/users/me/sessions/{id}` | ✓ | Remote sign-out |
| GET | `/users/me/audit-logs` | ✓ | Own audit history |
| GET | `/admin/audit-logs` | admin | Filter by `user_id`, `action` |
| GET | `/accounts` | ✓ | Active mode + DEMO and REAL accounts: status, balances (DEMO from the ledger, REAL `null`), availability and reasons. See [accounts.md](accounts.md) |
| POST | `/accounts/active` | ✓ | `{mode: DEMO\|REAL}`; never moves orders/positions. REAL → 409 `ACCOUNT_MODE_UNAVAILABLE`. Audited |
| GET | `/accounts/demo/ledger?limit=&offset=` | ✓ | DEMO cash ledger, newest first |
| POST | `/accounts/demo/reset` | ✓ | `{confirm: true}` (else 400 `CONFIRMATION_REQUIRED`): cancels demo orders, closes demo positions at the last price, restores ₹10,000, resets today's demo counters. Audited `account.demo_reset`. Never touches REAL |
| POST | `/accounts/real/authorize` | ✓ | Always 409 `LIVE_TRADING_LOCKED` with the blockers (live trading is locked). Audited |
| GET | `/market/indices` | ✓ | **DEMO** synthetic data only |
| GET | `/market/quotes?segment=&symbols=` | ✓ | DEMO quotes (indices + stocks) |
| GET | `/market/movers` | ✓ | DEMO gainers, losers, relative-volume leaders |
| GET | `/market/search?q=` | ✓ | Supported instruments |
| GET | `/charts/{symbol}/candles?timeframe=&limit=&indicators=` | ✓ | Closed candles only. Indicators: `ema9,ema21,ema50,ema100,ema200,sma20,rsi14,macd,bb20,atr14,vwap,supertrend,adx14,cci20,stoch,rvol20,roc12,mom10,obv,volma20,hv20,rv20,pkv20,volstate,ichimoku,avwap`, plus configurable periods `sma<N>`, `ema<N>`, `roc<N>`, `hv<N>` (N = 2–250). `ichimoku` returns `ichi_tenkan, ichi_kijun, ichi_span_a, ichi_span_b` (cloud as drawn at each candle; no chikou). `avwap` is anchored at the latest confirmed swing and is `null` before that swing's confirmation. Unknown names → 422. `annotations=true` adds patterns (with confirmation/invalidation/context), SMC zones (FVG, order blocks, supply/demand) and structure events (BOS/CHOCH, sweeps, retests, failed breakouts). See [technical-analysis.md](technical-analysis.md) |
| GET | `/options/{NIFTY\|BANKNIFTY}/expiries` | ✓ | DEMO dates, not the exchange calendar |
| GET | `/options/{underlying}/chain?expiry=&strikes=` | ✓ | DEMO chain, Black-Scholes Greeks. `lot_size`/`tick_size` are `null` |
| GET | `/analysis/{symbol}?timeframe=` | ✓ | 12-step pipeline: regime, indicators, patterns, SMC, strategies, prediction (promoted model only), setup candidate, contract shortlist, explanation. Always `NO_TRADE`. Also `indicator_version` and `technical` (evidence families, correlated pairs, divergences, trendlines/channel, range), `regime_v2` (UNCALIBRATED state confidences), `strategy_router` (v2 verdicts), `event_risk` (gate result) and `decision_record_id` (learning record, one per closed bar) |
| GET | `/analysis/{symbol}/mtf` | ✓ | Trend/regime/RSI on 5m, 15m, 1h, 1d |
| GET | `/analysis/{symbol}/patterns/stats?timeframe=&horizon=&cost_bps=&min_samples=&history=&by_regime=` | ✓ | Chronological outcome statistics of confirmed candlestick patterns (entry at the next bar's open after confirmation, exit `horizon` bars after confirmation, net of `cost_bps`). Per pattern and per regime: `n`, mean/median net return, hit rate with Wilson 95% interval, `insufficient_sample` (n < `min_samples`, default 30), plus an unconditional baseline. Carries provenance/quality; DEMO results have `evidence_status: NOT_EVIDENCE` and a SIMULATED note |
| GET | `/models`, `/models/{id}` | ✓ | Model registry with walk-forward metrics and gate; also `shadow_started_at`, `rolled_back_at` |
| GET | `/models/predictions?symbol=&limit=` | ✓ | Served and SHADOW predictions (`limit` 1–200, default 50); `outcome` filled by the learning worker |
| GET | `/models/compare?symbol=&timeframe=5m\|15m\|1h\|1d` | ✓ | Up to 50 models for the pair sorted by OOS log loss: log loss, Brier, ECE, accuracy, climatology scores, `vs_climatology` / `vs_logistic` block-bootstrap CIs with `better_*`, `calibration_status`, `gate_passed`, `promotable` (never for DEMO), `promotable_reason`, plus `criteria`. See [ai-models.md](ai-models.md) |
| POST | `/admin/models/train` | admin | Train a candidate `{symbol, timeframe, horizon_bars?, model_type: logistic\|random_forest\|hist_gb, target: three_class\|binary, seed}`. Audited |
| GET | `/models/{id}/promotion` | ✓ | Offline and shadow acceptance checks, one by one, with the policy thresholds. See [learning.md](learning.md) |
| POST | `/admin/models/{id}/shadow` | admin | CANDIDATE → SHADOW (offline criteria must pass; 409 `MODEL_GATE_FAILED`). Audited |
| POST | `/admin/models/{id}/promote` | admin | SHADOW → PROMOTED (409 `MODEL_SHADOW_REQUIRED`, `MODEL_SHADOW_FAILED`, `MODEL_GATE_FAILED`). Audited |
| POST | `/admin/models/{id}/rollback` | admin | PROMOTED → ROLLED_BACK and re-promotes the previous version (409 `NO_PREVIOUS_MODEL`, `PREVIOUS_MODEL_INVALID`). Returns the restored model. Audited |
| POST | `/admin/models/{id}/retire` | admin | PROMOTED → RETIRED. Audited |
| GET | `/options/{NIFTY\|BANKNIFTY}/intelligence?expiry=&side=CE\|PE&strikes=&strike=&stop_pct=&target_pct=&horizon_minutes=&timeframe=` | ✓ | Options-buying research per contract, four separate outputs: underlying direction (PROMOTED model or `null`), premium target-before-stop Monte Carlo (`MODEL_ESTIMATE`), expected net after spread/slippage/charges, risks and flags. Plus IV context, lot/tick/Greeks sources, funds. See [options-intelligence.md](options-intelligence.md) |
| GET | `/fundamentals/{symbol}?as_of=` | ✓ | Point-in-time statements, ratios, valuation, peers, own history, flags. `status: OK\|NO_DATA\|INDEX`; future `as_of` → 422 `INVALID_AS_OF`. See [fundamentals-events.md](fundamentals-events.md) |
| POST | `/admin/fundamentals/import` | admin | `{reports, profiles, corporate_actions}` → added/unchanged/restatement counts. Audited |
| GET | `/events?symbol=&from=&to=&category=` | ✓ | Imported events known now plus the derived calendar (provider expiries, configured holidays) |
| GET | `/events/risk?symbol=&underlying=` | ✓ | Event-risk gate now: `level NONE\|ELEVATED\|HIGH`, `action NONE\|REDUCE_EXPOSURE\|NO_TRADE`, reasons, events, window |
| POST | `/admin/events/import` | admin | `{events}` → `NEW`/`UPDATE`/`DUPLICATE` counts and ids. Audited |
| GET | `/learning/decisions?symbol=&timeframe=&decision=&status=&limit=&offset=` | ✓ | Paginated decision records (NO TRADE included) with outcomes and error classes. See [learning.md](learning.md) |
| GET | `/learning/summary?symbol=&timeframe=` | ✓ | Calibration table, Brier, log loss, ECE, accuracy by timeframe/regime/strategy, error-class counts, HYPOTHETICAL NO TRADE analysis, drift status |
| GET | `/learning/drift` | ✓ | PSI and performance thresholds plus the latest drift report per SHADOW/PROMOTED model |
| POST | `/admin/learning/run` | admin | One learning-worker pass (outcomes + due drift reports). Audited |
| GET | `/bots/strategies` | ✓ | Strategy catalogue / templates |
| POST | `/bots/ai-draft` | ✓ | Rule-based draft bounded by global risk limits |
| GET/POST | `/bots` | ✓ | List (filter `status`, `account_mode` defaulting to the active mode) / create a draft. `config.account_mode` is DEMO (default) or REAL |
| GET/PUT | `/bots/{id}` | ✓ | PUT creates a new immutable config version |
| POST | `/bots/{id}/clone` | ✓ | |
| DELETE | `/bots/{id}?confirm=true` | ✓ | Requires explicit confirmation |
| POST | `/bots/{id}/validate` | ✓ | Pre-activation checks against global limits |
| POST | `/bots/{id}/start\|pause\|resume\|stop` | ✓ | State machine (409 `INVALID_BOT_STATE`); start/resume re-validate (409 `VALIDATION_FAILED`). Runs are PAPER when paper trading is enabled (signals go to the risk manager), else signal-only. Runs record the account they trade in. REAL bots → 409 `LIVE_TRADING_LOCKED` |
| GET | `/bots/{id}/signals`, `/bots/{id}/events`, `/bots/{id}/runs` | ✓ | Recorded signals, bot log, run history |
| GET | `/risk?account_mode=` | ✓ | Shared limits and policies with usage, block reasons and exposure of one account |
| PATCH | `/risk/limits` | ✓ | Cross-field validated, audited |
| POST | `/risk/kill-switch` | ✓ | `{engaged, confirm: true}`; persisted, applies to every account. Engaging cancels pending entries and, if `square_off_on_kill_switch`, sends exits, account by account |
| GET | `/portfolio?account_mode=` | ✓ | One account: `account_mode`, `capital` (opening balance), `cash`, `equity`, `available_funds` (DEMO from the ledger; REAL `null`), used margin, realised/unrealised/net P&L, charges, loss/profit lock flags |
| GET | `/orders?status=PENDING\|EXECUTED\|CANCELLED\|REJECTED&bot_id=&account_mode=` | ✓ | Paginated, one account. Order/position by-id routes 404 for another mode |
| GET | `/orders/{id}` | ✓ | Order + timeline events, fills with charges, risk checks, charges estimate |
| POST | `/orders/paper` | ✓ | Manual BUY in the active (DEMO) account; REAL → 409 `LIVE_TRADING_LOCKED` `{underlying, option_type, expiry?, strike?, lots, stop_loss_pct, target_pct?, trailing_pct?, idempotency_key}`; same key returns the same order. 201 even when the risk manager rejects (see `status`, `reject_reason`); 422 `NO_LOT_SIZE` |
| POST | `/orders/{id}/cancel` | ✓ | 409 `ORDER_NOT_CANCELLABLE` |
| GET | `/positions?state=OPEN\|CLOSED&bot_id=` | ✓ | Mark-to-market, SL/target, realised gross/charges/net |
| POST | `/positions/{id}/exit` | ✓ | Market exit; waits for a bid if there is none |
| GET | `/trades.csv` | ✓ | Closed trades of one account (IST times, `account_mode` column) |
| GET | `/portfolio/charges?day=` | ✓ | Charges breakdown from fills |
| POST | `/charges/estimate` | ✓ | `{buy_price, sell_price, quantity, trade_date?}` → breakdown, net P&L, break-even |
| GET | `/alerts?category=` | ✓ | From your audit trail, bot signals/errors, paper fills/rejections and loss/profit lock hits |
| GET | `/brokers` | ✓ | Paper adapter + planned brokers (none integrated) |
| GET | `/market/provider-status` | ✓ | Provider health, instrument-sync times, price-feed heartbeat |
| POST | `/admin/market-data/sync-instruments` | admin | Download the INDstocks instrument master |
| GET | `/backtests/strategies` | ✓ | Catalogue strategies with `backtestable`, `reason`, default conditions (CE and PE) and tunable params (stop %, target %, threshold) |
| POST | `/backtests/run` | ✓ | `{strategy_id \| bot_id, underlying?, timeframe?, side?, bars (150–2000), fidelity, entry_conditions?, threshold?, stop, target, lot_size, costs, risk, regime_filter, compare_timeframes, seed}` → labelled result (`fidelity`, `data_source`, `evidence`, `warnings`), metrics, by-regime, by-timeframe, equity curve, trades. 422 `OPTION_HISTORY_UNAVAILABLE` for `HISTORICAL_OPTIONS`, `RISK_OVERRIDE_EXCEEDS_LIMIT`, `STRATEGY_NOT_BACKTESTABLE`, `UNSUPPORTED_CONDITION`, `INSUFFICIENT_DATA`. See [backtesting.md](./backtesting.md) |
| POST | `/backtests/walk-forward` | ✓ | `{run, grid (≤16 combos), train_bars, test_bars, holdout_pct, objective, min_trades, max_windows (≤8)}` → per-window chosen params and test metrics, out-of-sample aggregate, stability, holdout evaluated once |
| POST | `/backtests/sensitivity` | ✓ | `{run, grid (≤36 cells)}` → in-sample metric surface over stop %, target %, threshold |
| POST | `/backtests/monte-carlo` | ✓ | `{run, simulations (100–5000), seed}` → recomputes (or reuses the cached) run, bootstraps its trades: percentiles of net P&L and max drawdown, probability of loss |

Every market-data response (quotes, candles, option chain, analysis) also carries
`provider`, `quality: {status: OK|WARN|FAIL, issues: [...]}` and `notes`. In INDSTOCKS mode
only users in `INDSTOCKS_DATA_USER_EMAILS` get data (others: `403 MARKET_DATA_NOT_LICENSED`).

## Example requests

```bash
API=http://localhost:8000/api/v1

curl -s -X POST $API/auth/register -H 'Content-Type: application/json' -d '{
  "full_name": "Asha Trader", "email": "asha@example.com",
  "password": "Str0ngPassw0rd!", "accept_risk_disclosure": true }'

# In development the 6-digit code is printed in the API log ("DEV ONLY verification code ...").
curl -s -X POST $API/auth/verify -H 'Content-Type: application/json' \
  -d '{"identifier": "asha@example.com", "code": "123456"}'

curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"identifier": "asha@example.com", "password": "Str0ngPassw0rd!"}'

TOKEN=...   # access_token from the response
curl -s $API/market/indices -H "Authorization: Bearer $TOKEN"
# => {"source":"DEMO","is_demo":true,"is_live":false,"as_of":"...","disclaimer":"DEMO data: ...","items":[...]}

curl -s -X POST $API/auth/refresh -H 'Content-Type: application/json' \
  -d '{"refresh_token": "..."}'
```

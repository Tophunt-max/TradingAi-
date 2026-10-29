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
| GET | `/market/indices` | ✓ | **DEMO** synthetic data only |
| GET | `/market/quotes?segment=&symbols=` | ✓ | DEMO quotes (indices + stocks) |
| GET | `/market/movers` | ✓ | DEMO gainers, losers, relative-volume leaders |
| GET | `/market/search?q=` | ✓ | Supported instruments |
| GET | `/charts/{symbol}/candles?timeframe=&limit=&indicators=` | ✓ | Closed candles only. Indicators: `ema9,ema21,ema50,ema200,rsi14,macd,bb20,atr14,vwap,supertrend,adx14,cci20,stoch,rvol20`; `annotations=true` adds confirmed patterns and SMC zones |
| GET | `/options/{NIFTY\|BANKNIFTY}/expiries` | ✓ | DEMO dates, not the exchange calendar |
| GET | `/options/{underlying}/chain?expiry=&strikes=` | ✓ | DEMO chain, Black-Scholes Greeks. `lot_size`/`tick_size` are `null` |
| GET | `/analysis/{symbol}?timeframe=` | ✓ | 12-step pipeline: regime, indicators, patterns, SMC, strategies, prediction (promoted model only), setup candidate, contract shortlist, explanation. Always `NO_TRADE` |
| GET | `/analysis/{symbol}/mtf` | ✓ | Trend/regime/RSI on 5m, 15m, 1h, 1d |
| GET | `/models`, `/models/{id}` | ✓ | Model registry with walk-forward metrics and gate |
| GET | `/models/predictions?symbol=` | ✓ | Served predictions (outcomes resolved in Phase 7) |
| POST | `/admin/models/train` | admin | Train a candidate `{symbol, timeframe, horizon_bars?}` |
| POST | `/admin/models/{id}/promote` \| `/retire` | admin | Promote (gate must pass, non-DEMO) or retire |
| GET | `/bots/strategies` | ✓ | Strategy catalogue / templates |
| POST | `/bots/ai-draft` | ✓ | Rule-based draft bounded by global risk limits |
| GET/POST | `/bots` | ✓ | List (filter `status`) / create a PAPER draft |
| GET/PUT | `/bots/{id}` | ✓ | PUT creates a new immutable config version |
| POST | `/bots/{id}/clone` | ✓ | |
| DELETE | `/bots/{id}?confirm=true` | ✓ | Requires explicit confirmation |
| POST | `/bots/{id}/validate` | ✓ | Pre-activation checks against global limits |
| POST | `/bots/{id}/start\|pause\|resume\|stop` | ✓ | State machine (409 `INVALID_BOT_STATE`); start/resume re-validate (409 `VALIDATION_FAILED`). Runs are signal-only |
| GET | `/bots/{id}/signals`, `/bots/{id}/events`, `/bots/{id}/runs` | ✓ | Recorded signals, bot log, run history |
| GET | `/risk` | ✓ | Global limits, usage, block reasons |
| PATCH | `/risk/limits` | ✓ | Cross-field validated, audited |
| POST | `/risk/kill-switch` | ✓ | `{engaged, confirm: true}`; persisted |
| GET | `/portfolio`, `/orders`, `/positions` | ✓ | Real paper ledger (empty until Phase 6) |
| GET | `/alerts?category=` | ✓ | Derived from your audit trail |
| GET | `/brokers` | ✓ | Paper adapter + planned brokers (none integrated) |
| GET | `/market/provider-status` | ✓ | Provider health, instrument-sync times, price-feed heartbeat |
| POST | `/admin/market-data/sync-instruments` | admin | Download the INDstocks instrument master |

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

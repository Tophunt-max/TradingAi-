# INDstocks (INDmoney) market data

QuantAI F&O reads market data from the official INDstocks API ([api-docs.indstocks.com](https://api-docs.indstocks.com/)).
This integration is **read-only**. It never places, modifies or cancels orders. The separate,
read-only broker connection (funds, positions, orders, fills of a user's own INDstocks
account; orders locked) is described in [broker-integration.md](broker-integration.md).

## Status

| Item | State |
|---|---|
| Client, instrument master, quotes, candles, expiries, option chain, price-feed worker | Built and tested against a fake server that follows the documented response shapes |
| Tested against the real INDstocks API | **No.** No access token was available while building this. Run the checks below with your token before relying on it. |

## Setup

1. Log in at indstocks.com and generate an access token at
   [API trading → Access tokens](https://www.indstocks.com/app/api-trading/access-tokens).
   Tokens last 24 hours, so you need a new one each trading day.
2. In the server `.env` (never in the mobile app):
   ```env
   MARKET_DATA_MODE=INDSTOCKS
   INDSTOCKS_ACCESS_TOKEN=<token>
   INDSTOCKS_DATA_USER_EMAILS=<your QuantAI login email>
   MARKET_HOLIDAYS=<NSE holiday dates for the year>
   ```
3. Sync the instrument master (lot sizes, tick sizes, expiries). Do this daily before 09:15 IST:
   ```bash
   cd backend && python -m app.market_data.sync_instruments
   # or, as an admin user: POST /api/v1/admin/market-data/sync-instruments
   ```
4. Optional: run the price-feed worker for streaming last-traded prices (needs `REDIS_URL`):
   ```bash
   python -m app.workers.price_feed NSE:2885 NSE:11536
   ```
5. Check **System Health** in the app, or `GET /api/v1/market/provider-status`.

## What maps to what

| QuantAI | INDstocks endpoint | Notes |
|---|---|---|
| Stock quotes | `GET /market/quotes/full` (`NSE_<SECURITY_ID>`) | The response has no timestamp. Freshness is the receive time, unless the price-feed worker has a newer tick. |
| NIFTY / BANKNIFTY level | `underlying_ltp` from `GET /market/option-chain` | No documented REST index quote, so day change is not shown. Set `INDSTOCKS_INDEX_QUOTE_CODES` only after you confirm a code works. |
| Candles | `GET /market/historical/{interval}` | Windows are paged within the per-call maximum (7 days intraday, 15 days hourly, 1 year daily). The forming candle is dropped. |
| Index candles | Near-month index futures (`FUTIDX`) | **Proxy.** The chart says so explicitly, because futures trade at a premium/discount to the index. |
| Expiries | `GET /market/instruments/expiries` | Past dates are filtered out. |
| Option chain + Greeks + IV | `GET /market/option-chain` | Strikes are sorted numerically. OI change = `oi − previous_oi`. |
| Lot size, tick size, freeze qty | Instrument master CSV (`/market/instruments?source=fno`) | Rows without a lot size, tick size or expiry are rejected. The index file is parsed positionally, as the docs require. |
| Live ticks | `wss://ws-prices.indstocks.com/api/v1/ws/prices` (`ltp` mode) | Reconnects with backoff. Out-of-order and duplicate ticks are dropped. |

## Safeguards

- **Rate limits:** the client keeps to 5 requests/s for data and quote calls, and short TTL caches keep the app well under the daily limit of 100,000. It retries 429, 5xx and network errors with backoff, and never retries 4xx errors.
- **Errors:** the API uses several error-body shapes, and all of them map to clear app errors (`PROVIDER_AUTH_FAILED`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `INSTRUMENTS_NOT_SYNCED`). The token never appears in responses or logs.
- **Data quality** comes with every payload. It flags stale ticks during market hours, clock drift, duplicate and out-of-order candles, candles outside the 09:15–15:30 IST session, missing candles, invalid OHLC, missing lot size, crossed books, expired expiries, market closed, and a missing holiday calendar. A `FAIL` result makes AI analysis abstain.
- **Licensing:** the data is fetched with one person's brokerage login. Only users listed in `INDSTOCKS_DATA_USER_EMAILS` can see it; everyone else gets `403 MARKET_DATA_NOT_LICENSED`. Check INDstocks' terms before sharing data with anyone else.

## Verify against the real API (once you have a token)

```bash
cd backend
export MARKET_DATA_MODE=INDSTOCKS INDSTOCKS_ACCESS_TOKEN=... ENVIRONMENT=development
python -m app.market_data.sync_instruments      # expect SUCCESS with thousands of rows
python - <<'PY'
from app.core.config import get_settings
from app.db.session import get_db
from app.market_data.factory import build_provider
p = build_provider(next(get_db()), get_settings())
print(p.status())
r = p.quotes(["RELIANCE", "NIFTY"], "ALL"); print(r.data, r.quality.as_dict())
r = p.option_chain("NIFTY", None, 3); print(r.data["lot_size"], len(r.data["rows"]))
r = p.candles("RELIANCE", "5m", 20); print(r.data[-1], r.quality.as_dict())
PY
```

Things to confirm on the real API, which the docs leave open:

- The exact index names in the `source=index` file (mapped: `NIFTY 50`, `NIFTY BANK`).
- The `EXPIRY_DATE` format in the F&O CSV. Several formats are accepted; rows that fail to parse are rejected and counted.
- Whether candle `ts` values are true UTC epochs. The documented examples are (09:15 IST = 03:45 UTC). If they are not, the `OUTSIDE_SESSION` warning appears.

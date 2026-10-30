# Broker integration (package P)

QuantAI F&O can connect one real broker account **read-only**: funds, positions, orders and
fills are synced into snapshots and REAL balances in the app come from them. **Live trading
is not enabled.** `LIVE_TRADING_ENABLED` cannot be set to true in this build (Settings
refuses it), so no order can reach a broker whatever the connection state.

## Status

| Item | State |
|---|---|
| Broker-neutral `BrokerClient` interface, typed results | Built and tested |
| INDstocks (INDmoney) adapter | **`UNVERIFIED_AGAINST_LIVE_API`**: every endpoint and field comes from the official docs (sources below); tested only against an in-memory fake that follows the documented shapes (`backend/tests/fake_indstocks_broker.py`) |
| Connect / disconnect / sync / health / permission checklist | Built and tested (fake transport) |
| Token encryption at rest | Fernet (`cryptography`), tested |
| Reconciliation of UNKNOWN orders, charges reconciliation | Built and tested with the fake client |
| Order placement through a broker | **Locked** (see "What it takes to enable live trading") |
| Zerodha, Angel One, Upstox, Dhan | Listed as `NOT_INTEGRATED`; nothing implemented |

The INDstocks integration is off by default (`BROKER_INDSTOCKS_ENABLED=false`) because it has
not been run against the live API. An operator who has verified it (see "Verify against the
real API") can enable read-only connections.

## Interface (`backend/app/brokers/client.py`)

`BrokerClient` (a `typing.Protocol`):

| Method | Purpose |
|---|---|
| `profile()` → `BrokerProfile` | Read-only token check; returns the scopes we could confirm (no identity fields are stored) |
| `funds()` → `BrokerFunds` | Option-buying balance, start-of-day balance, withdrawable, realised/unrealised P&L, brokerage and segment charges accrued today (all `Decimal`) |
| `positions()` → `[BrokerPosition]` | Open/derivative positions |
| `orders()` → `[BrokerOrder]` | Today's order book, statuses normalised to `OPEN/PARTIAL/FILLED/CANCELLED/REJECTED` (raw status kept) |
| `tradebook()` → `[BrokerTrade]` | Today's fills |
| `get(order_id)` → `BrokerOrderState` | Order status with its fills |
| `find_by_client_id(client_order_id)` | Finds an order by the tag we sent with it; used to resolve unknown outcomes, never to resend |
| `place(req)`, `modify(...)`, `cancel(order_id)` | Order path; reachable only through `RealBrokerExecutionAdapter`, which is locked |
| `health()` | `{ok, status, detail}` |

Rules: money and prices are `Decimal`, timestamps UTC. `place` is never retried: a timeout or
5xx raises `BrokerUnavailable` (outcome unknown → the order goes `UNKNOWN` and is reconciled);
a definitive refusal raises `BrokerError`; a refused token raises `BrokerAuthError`. Read calls
retry 429/5xx/network errors with backoff.

`BrokerCapabilities` states what the **integration implements**, not what the broker offers:
`order_types`, `product_types`, `segments`, `streaming`, `modify`, `cancel`, `tradebook`,
`find_by_client_tag`, `revoke_token`, `notes`. Auth flows: `OAUTH_REDIRECT`, `TOKEN_PASTE`.

## INDstocks adapter (`backend/app/brokers/indstocks.py`)

INDstocks is already the market-data provider ([indstocks.md](indstocks.md)), so it is the one
broker adapter. Auth flow: **TOKEN_PASTE** (the user generates an access token on the
INDstocks dashboard; tokens last at most 24 hours).

| Use | Endpoint (documented) | Notes |
|---|---|---|
| Token validation | `GET /user/profile` | Docs call it "a useful endpoint to test if your access token is valid". `is_nse_fno_onboarded` adds the `fno` scope |
| Funds | `GET /funds` | `detailed_avl_balance.option_buy`, `sod_balance`, `withdrawal_balance`, `realized_pnl`, `unrealized_pnl`, `brokerage`, `fno_charges` |
| Positions | `GET /portfolio/positions?segment=derivative` | `net_qty`, `avg_price`, `buy_*`, `sell_*`, `realized_profit`, `drv_*` |
| Order book | `GET /order-book` | `id`, `status`, `traded_qty`, `requested_qty`, `traded_price`, `extra_info`, `remarks` |
| Order details / fills | `GET /order`, `GET /order/trades` | Both take a JSON body `{order_id, segment}` on a GET, as documented |
| Trade book | `GET /trade-book?segment=DERIVATIVE` | `fill_id`, `price`, `quantity`, `trade_date` (IST), `remarks` |
| Place / modify / cancel | `POST /order`, `/order/modify`, `/order/cancel` | Locked. `remarks` carries our `client_order_id` |

Documented behaviour the adapter relies on:
- Header `Authorization: <token>` with no "Bearer" prefix. Rate limits: order APIs 10/s,
  non-trading APIs 15/s; the client enforces both.
- `remarks` (≤100 characters) is stored with the order and echoed by `/order`, `/order-book`
  and `/trade-book`. The docs say it is **not** an idempotency key (two orders with the same
  tag are two orders), which is why `place` is never retried and reconciliation looks the
  tag up instead.
- MARKET orders are converted to LIMIT orders with a price-protection band.
- Order placement needs a whitelisted static IP; read-only endpoints do not.
- There is **no documented token-revoke endpoint**: tokens are revoked on the dashboard.
  `revoke_token` is false and disconnect says so.
- An order-updates WebSocket exists but is not implemented (`streaming: false`).

Status mapping: `QUEUED, O-PENDING, SL-PENDING, PROCESSING, INITIATED, MODIFIED, PENDING` →
OPEN; `SUCCESS` → FILLED; `PARTIALLY FILLED` → PARTIAL; `CANCELLED, EXPIRED, PARTIALLY FILLED -
CANCELLED, PARTIALLY FILLED - EXPIRED` → CANCELLED; `ABORTED, FAILED` → REJECTED. Unknown
statuses map to OPEN so the order is re-read, never assumed finished.

Sources (fetched 2026-10-29; content paraphrased):
[Users / funds / token](https://api-docs.indstocks.com/Users/),
[Orders](https://api-docs.indstocks.com/normal_orders/),
[Portfolio](https://api-docs.indstocks.com/portfolio_funds/),
[Conventions and rate limits](https://api-docs.indstocks.com/conventions/),
[Full export for LLMs](https://api-docs.indstocks.com/llms-full.txt).

## Connection flow

1. `GET /api/v1/brokers` lists Paper, INDstocks (with capabilities, verification status,
   `enabled`, and the user's connection if any) and the not-integrated brokers.
2. `POST /api/v1/brokers/indstocks/connect {token}`. The server refuses when the integration
   is disabled (409 `BROKER_NOT_ENABLED`), when encryption is unavailable (503
   `BROKER_ENCRYPTION_UNAVAILABLE`, nothing stored), or when another broker is connected (409).
   It validates the token with `GET /user/profile` (400 `BROKER_TOKEN_REJECTED` if refused),
   then creates or re-activates the user's **REAL account** (`status ACTIVE`, `broker`,
   `opening_balance NULL`: the DB CHECK forbids virtual money on REAL, and no ledger entry is
   written), encrypts and stores the token, and records `token_expires_at` (now + 24 h, an
   upper bound because the broker does not say when the token was generated) and the scopes.
   Audited `broker.connect` (success or rejection, never the token).
3. `POST /api/v1/brokers/indstocks/sync` (see below).
4. `GET /api/v1/brokers/indstocks/health`: local state first (status, token expiry); when
   CONNECTED it calls `GET /user/profile`. A refused token sets `EXPIRED`, an unreachable
   broker sets `ERROR` with `last_error`.
5. `POST /api/v1/brokers/indstocks/disconnect {confirm: true}`: status `REVOKED`, token
   ciphertext deleted, `revoked_at` set, REAL account `DISCONNECTED`. The response says
   `broker_revoke: NOT_SUPPORTED` and asks the user to revoke the token on the dashboard.
   Audited `broker.disconnect`.

Connection statuses: `CONNECTED`, `EXPIRED`, `REVOKED`, `ERROR`. List statuses add
`NOT_CONNECTED`, `NOT_ENABLED` (integration off on this server) and `NOT_INTEGRATED`.
Broker connect/disconnect and failed syncs also appear in `/alerts` (category SYSTEM).

## Token handling and encryption

- The token arrives once, in the connect body (`SecretStr`, so it never appears in reprs or
  validation errors). It is never logged, audited, returned or sent back to the app; the
  client's `repr` hides it. Tests check responses, logs, audit rows and snapshots for it.
- At rest: `broker_connections.token_ciphertext` = `fernet:v1:` + Fernet token (AES-128-CBC
  with HMAC-SHA256, from the `cryptography` package, added to `requirements.txt`).
- Key: `BROKER_TOKEN_ENCRYPTION_KEY` (generate with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
  In development/test only, a key is derived from `SECRET_KEY` with HKDF-SHA256. Settings
  refuse an enabled broker integration without the dedicated key in staging/paper/production.
- Changing the key makes stored tokens unreadable: the connection goes `ERROR`
  (`BROKER_TOKEN_UNREADABLE`) and the user reconnects. Tokens last 24 h anyway.
- If `cryptography` is missing, connect refuses and nothing is stored.

## Sync and REAL balances

Sync fetches funds, positions, orders and the trade book, then writes four
`broker_snapshots` rows (`FUNDS`, `POSITIONS`, `ORDERS`, `FILLS`; payload JSON with Decimals as
strings; same `fetched_at`). It is all or nothing: if any call fails, no snapshot is written,
the connection is marked `ERROR`/`EXPIRED` and the failure is audited.

`GET /api/v1/accounts` now shows REAL balances from the latest FUNDS snapshot:
`cash` = `start_of_day_balance`, `available_funds` = `available_option_buy`, `unrealized_pnl` =
broker unrealised P&L. `equity` stays `null` (the broker does not report it and we do not
derive it). `balances_source` = `{source: BROKER_SNAPSHOT, broker, fetched_at, fields,
broker_realized_pnl}`; DEMO shows `{source: DEMO_LEDGER}`. Sync never touches DEMO rows.

The pre-trade funds check for REAL still fails closed (it does not trust a possibly stale
snapshot); REAL entries are blocked anyway.

## Execution path (still locked)

`execution/router.py` builds `RealBrokerExecutionAdapter` with the client bound to the
account's connection (`brokers/connections.py: real_broker_client`). Its `health()` is local
(connection CONNECTED and token unexpired), so no network call happens during risk checks.
Without a usable connection the adapter gets `UnavailableBrokerClient`, which fails every call.

All guards remain: `submit` and `modify` raise `LiveTradingLocked` while
`LIVE_TRADING_ENABLED` is false (always in this build), the account is not ACTIVE, there is no
live authorisation, or the broker is unhealthy. The pre-trade risk manager also has a REAL-only
check **`broker` ("Broker connection healthy")**: a disconnected, expired or erroring
connection blocks new REAL entries with the reason.

## Reconciliation (`backend/app/brokers/reconcile.py`)

- `reconcile_unknown_orders(db, settings, account, client=None)`: each REAL order in
  `UNKNOWN`/`RECONCILIATION_REQUIRED` is looked up with `find_by_client_id` (INDstocks: the
  `remarks` tag in today's order book). Found → `ACKNOWLEDGED`, then broker fills are applied
  through the normal fill path (`Fill` rows keyed by broker fill id, so re-running adds
  nothing). Not found → stays UNKNOWN, then `RECONCILIATION_REQUIRED` after the grace period.
  Broker unreachable → counted as `unavailable`. Nothing is ever resent.
- Real P&L: REAL positions and fills are created only from broker-confirmed fills read back
  this way. Charges on those fills are estimates (versioned schedule) until reconciled.
- `reconcile_charges(db, account, day, funds, broker)`: broker-reported `brokerage +
  fno_charges` (accrued for the day, from `GET /funds`) vs the sum of our per-fill estimates
  for that IST day; stored in `broker_charge_reconciliations` (one row per account and day,
  `diff = broker_reported − estimated`, estimate breakdown in `detail`). Sync runs it for today.
  The daily report shows it for REAL accounts.

## Trading permissions checklist

`GET /api/v1/brokers/permissions` returns `{ready, live_trading_enabled, broker, checks,
blockers}`. Checks, in order:

| Key | Passes when | In this build |
|---|---|---|
| `broker_connected` | A connection is CONNECTED | After connect |
| `token_valid` | Connected, token stored and unexpired | After connect |
| `order_permission` | Scopes include `orders.write` | Never: cannot be verified without placing an order; INDstocks also needs a whitelisted static IP |
| `integration_verified` | Adapter status VERIFIED | Never (UNVERIFIED_AGAINST_LIVE_API) |
| `live_flag` | `LIVE_TRADING_ENABLED` | Never |
| `live_authorization` | Unrevoked `live_authorizations` row | Never (no endpoint creates one) |
| `risk_limits` | The user saved risk limits (audited `risk.limits_updated`) | After saving limits |
| `regulatory` | Algo-order approval, static IP registration, compliance review | Never: outside the app |

The mobile **Broker Connections** screen shows this list under "Trading permissions".

## What it takes to enable live trading (not done)

1. **Verify the adapter** against the live INDstocks API with a real token (read-only
   endpoints first, then order placement on the smallest possible size in a supervised test),
   and fix any shape differences. Only then mark it `VERIFIED`.
2. **Credentials and network**: a static IP whitelisted on the INDstocks dashboard for the
   server that would place orders; a dedicated `BROKER_TOKEN_ENCRYPTION_KEY`.
3. **Regulatory checks**: exchange/broker rules for algorithmic orders (algo ids, approvals),
   broker review, and a compliance review of the product.
4. **User authorisation**: a versioned consent flow writing `live_authorizations`, with revoke.
5. **Reconciliation in production**: a worker running `reconcile_unknown_orders` and charges
   reconciliation for REAL accounts, contract-note checks, and alerts on differences.
6. **Remove the lock**: change the code that refuses `LIVE_TRADING_ENABLED=true`. This is a
   deliberate code change and review, not a configuration switch.

None of these are done. Live trading stays off.

## Verify against the real API (read-only)

```bash
cd backend
export ENVIRONMENT=development BROKER_INDSTOCKS_ENABLED=true
python - <<'PY'
from app.brokers.indstocks import IndstocksBrokerClient
c = IndstocksBrokerClient(token="<paste token>")
print(c.profile()); print(c.funds()); print(c.positions()[:2])
print(c.orders()[:2]); print(c.tradebook()[:2])
PY
```

Compare the printed fields with the docs. Do not call `place`, `modify` or `cancel`.

## Tables (migration 0009)

| Table | Key constraints |
|---|---|
| `broker_connections` | `UNIQUE(user_id, broker)`; composite FK `(account_id, account_mode)` → `accounts(id, mode)`; `CHECK account_mode = 'REAL'`; `CHECK status IN (...)` |
| `broker_snapshots` | Composite FK; `CHECK account_mode = 'REAL'`; `CHECK kind IN ('FUNDS','POSITIONS','ORDERS','FILLS')`; index `(account_id, kind, fetched_at)` |
| `broker_charge_reconciliations` | Composite FK; `CHECK account_mode = 'REAL'`; `UNIQUE(account_id, trading_day)` |

Tests: `backend/tests/test_brokers.py`.

# Execution adapters and router

Orders leave the server only through an **execution adapter** bound to one account. The
adapter is chosen by the **execution router** from the account row loaded on the server.

## Adapter interface (`backend/app/brokers/base.py`)

`ExecutionAdapter` (ABC; `BrokerAdapter` is a compatibility alias):

| Member | Meaning |
|---|---|
| `account_mode` | `DEMO` or `REAL`. The constructor raises `AdapterAccountMismatch` for an account of another mode |
| `submit(OrderRequest)` | Place an order. `OrderRequest` carries `account_id`/`account_mode`; a request for another mode or account raises `AdapterAccountMismatch` |
| `cancel(broker_order_id)` | Cancel; returns the broker's order state |
| `get_order(broker_order_id)` | Current state including fills |
| `find_by_client_id(client_order_id)` | Used by reconciliation: an order whose outcome is unknown is looked up, never resent |
| `poll(now)` | Advance/collect fills for the account's open orders |
| `health()` | `{ok, detail}` |

Exceptions: `BrokerError` (definitive rejection, the order does not exist), `BrokerUnavailable`
(outcome unknown → order goes `UNKNOWN` and is reconciled), `LiveTradingLocked` (a `BrokerError`
carrying every blocking reason), `AdapterAccountMismatch` (a routing bug; never swallowed).

## Adapters

- **`PaperExecutionAdapter`** (`brokers/paper.py`, alias `PaperBroker`): the simulator for DEMO
  accounts. Bid/ask fills with slippage, spread and liquidity rules, optional partial fills. Its
  books (`paper_broker_orders/fills`) are scoped by account and CHECKed to DEMO.
- **`RealBrokerExecutionAdapter`** (`brokers/real.py`): broker-neutral wrapper over the
  `BrokerClient` protocol (`brokers/client.py`). The router gives it the client bound to the
  account's broker connection (INDstocks, unverified against the live API); without a usable
  connection it gets `UnavailableBrokerClient`, which raises `BrokerUnavailable` on every call
  and reports unhealthy. See [broker-integration.md](broker-integration.md).

## Router (`backend/app/execution/router.py`)

`route_adapter(db, settings, account, quote_fn)`: DEMO → paper adapter, REAL → real adapter.
The account always comes from the server: the user's own account for manual entries, the
order's or position's `account_id` for exits/cancels/fills, and the bot run's `account_id` for
signal entries. Client input can only *select which of the user's own accounts to view*; it
never chooses an adapter, and writes in REAL mode are refused before any order is created.

`execution.service` uses the account's adapter in `place_entry`, `place_exit`, `fill_now`,
`cancel`, `reconcile`, `manage_positions` and `process_account`, and stamps
`account_id`/`account_mode` on every order, fill, position and daily risk row. `run_pass`
iterates accounts with live orders or open positions and processes **DEMO only**; REAL
accounts are skipped and counted (`real_skipped`) because there is no broker to manage them.

## Guards before a REAL order

`RealBrokerExecutionAdapter.submit` checks, and raises `LiveTradingLocked` with all failures:

1. `LIVE_TRADING_ENABLED` is true. **Settings refuse true in this build**, so this always blocks.
2. The account is REAL (a DEMO account is refused when the adapter is built).
3. The account status is `ACTIVE`.
4. An unrevoked `live_authorizations` row exists for this user and account.
5. The broker client reports healthy (connection CONNECTED, token unexpired; no network call).

`modify` runs the same guards. The pre-trade risk manager also has a REAL-only `broker`
check, so a disconnected or erroring broker blocks new REAL entries. The same reasons feed the pre-trade risk manager's first check (`account`), so a REAL entry
is rejected with an explanation even before the adapter is called. The API refuses REAL
manual entries, exits and cancels with 409 `LIVE_TRADING_LOCKED`, and REAL bots cannot start
(409 `LIVE_TRADING_LOCKED`; the bot validation also fails its `account_mode` check).

## Live authorisation flow (locked)

The intended flow, once a verified broker exists: connect broker → REAL account `ACTIVE` →
the user reads a versioned consent text → a `live_authorizations` row records the version and
time → every order re-checks the five guards above. Revoking sets `revoked_at`.

In this build `POST /api/v1/accounts/real/authorize` always answers 409 with the blockers and
is audited (`account.live_authorization`, outcome `REJECTED`). Live trading stays locked
because there is no verified broker order integration, no regulatory/broker review, and
no real-account reconciliation (funds, margin, contract notes). Demo results never unlock it.

## Tests

`backend/tests/test_accounts_isolation.py` covers routing, adapter refusals in both
directions, the guard order for unauthorised and authorised REAL orders, and that no
`paper_broker_orders` row is created for a REAL attempt.

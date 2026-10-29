# Bot system (Phase 5)

## Lifecycle
```
DRAFT / STOPPED --start--> ACTIVE --pause--> PAUSED --resume--> ACTIVE
ACTIVE / PAUSED --stop--> STOPPED
```
- **Safe transitions:** each transition is a compare-and-set update on the `bots` row. A double tap or a race with the worker returns `409 INVALID_BOT_STATE` and never leaves the bot in a wrong state.
- **Activation checks:** `start` and `resume` re-run validation. Checks are tagged `ACTIVATION` (needed to start) or `ORDERS` (needed to place orders: contract, live data, broker, execution engine). `can_place_orders` stays `false` until Phase 6.
- **Runs:** each start creates a **run** pinned to the current config version. Editing or deleting needs the bot to be STOPPED or DRAFT.
- **Kill switch:** while it is engaged, starting a bot is blocked and running bots suppress every signal.

## Signal-only mode
There is no execution engine yet, so a running bot **records entry signals and places no
orders**. The worker:

1. Claims the run with a 60 s lease, so only one worker evaluates a bot at a time.
2. Fetches closed candles and evaluates **exactly the entry conditions in the bot's configuration** on the newest closed candle, once per candle.
3. Suppresses a signal (and logs why) if any of these apply:
   - data quality failed;
   - the kill switch is engaged;
   - the candle is outside the bot's session window;
   - the relative-volume or ATR filters are not met;
   - the cooldown has not passed;
   - the bot's daily limit is reached;
   - the **global daily entry limit shared by all bots** is reached.
4. Stores the signal. The database's unique (run, candle) constraint makes this idempotent across restarts and workers.
5. Auto-pauses a bot after 10 consecutive errors and logs why.

Supported conditions:
- `RSI`, `EMA_CROSS`, `VWAP`, `MACD`, `RELATIVE_VOLUME`, `SUPERTREND`;
- `RESISTANCE_BREAK` (confirmed swings only), `PATTERN`;
- `SMC_CHOCH`, `SMC_ORDER_BLOCK_RETEST`;
- `MODEL_PROBABILITY` (promoted model only).

Crossovers fire on the crossing candle only. Unsupported conditions fail validation.

## Running the worker
```bash
cd backend && python -m app.workers.bot_runner --interval 15
# or: docker compose --profile workers up bot-runner
```
Several instances are safe to run. A crashed worker's bots are picked up after its lease expires. In
development only, `BOT_WORKER_EMBEDDED=true` runs it inside the API process. Staging and production
refuse that setting.

**Health** (`/health/ready` → `bot_worker`, and each bot's `health`) is one of:
- `OK`
- `WAITING_FOR_WORKER`
- `STALE` (no pass in 120 s)
- `ERROR`
- `BLOCKED` (kill switch)
- `PAUSED`
- `NOT_RUNNING`

## AI Auto Create (v2)
With style **Auto**:
- The market regime picks the strategy:
  - trending → Momentum
  - range expansion → Breakout
  - range → Reversal
  - volatile → SMC, with a warning
- Side **Auto** picks CE or PE from the regime or the trend reading.
- PE bots get mirrored conditions (for example `CROSS_BELOW`, `RSI < 45`).

Everything is chosen by rules from the analysis engine, not by an ML model, and is bounded by the
global risk limits. Drafts still go through validation.

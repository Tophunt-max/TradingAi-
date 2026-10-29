# Backtesting Lab

Code: `backend/app/backtesting/` (engine, metrics, validation, service), API
`backend/app/api/v1/backtests.py`, mobile screen `apps/mobile/app/(app)/backtesting.jsx`.
Tests: `backend/tests/test_backtesting.py`, `apps/mobile/__tests__/backtesting.test.jsx`.

A backtest result is a research measurement under stated assumptions. On the
current data sources it is never evidence that a strategy makes money:

- DEMO candles are synthetic. Every result built from them carries
  `data_source: "DEMO"`, `evidence: false` and the warning
  "Simulated data: results are not evidence of real performance".
- No configured provider supplies historical option premiums, so the only
  available fidelity is an underlying proxy (below).

## Fidelity levels

| `fidelity` | Status | What it models |
|---|---|---|
| `UNDERLYING_PROXY` | Available (default) | Trades the underlying (index/stock) price. P&L = points × quantity, with the sign of the bot's side: CE = long the underlying, PE = short. Labelled `fidelity_note: "incomplete proxy for options: ignores premium, IV, theta, spread"`. |
| `HISTORICAL_OPTIONS` | Not available | Needs historical premiums of the actual (including expired) contracts. Returns `422 OPTION_HISTORY_UNAVAILABLE` "Historical option premiums are not available from the configured data source". |
| `MODELLED_OPTIONS` | Not implemented | Black-Scholes premiums from an assumed IV would be a model of a model, not a backtest. Not offered. |

What the proxy misses: premium level and convexity (delta < 1 and changing),
theta decay over the holding period, IV changes (a move can be right while the
premium falls), the option's own bid/ask spread and liquidity, and lot-size
rounding of the contract actually traded. The bot's premium-based stop/target
(for example 20 % of premium) cannot be applied to the underlying, so the
request's underlying-level `stop` / `target` are used and the result says so.

## Engine model

Event-driven loop over **closed** candles (`engine._Run.run`). For bar *i*:

1. **Open of bar i.** A pending entry fills here. Forced exits fill at this
   open: the square-off time has been reached (bar start ≥ square-off, default
   15:15 IST), or the daily loss stop was hit at the previous close.
2. **Inside bar i.** Stop and target are checked against the bar's high/low:
   - **Gap rule:** if the bar opens beyond the stop, the exit fills at the open
     (`STOP_LOSS_GAP`) and `gap_slippage = |stop − open| × qty` is recorded.
     An open beyond the target fills at the open less half the spread
     (`TARGET_GAP`).
   - **Conservative same-bar rule:** if both stop and target lie inside the
     bar's range, the **stop is assumed to fill first** (`STOP_LOSS`,
     `stop_target_same_bar: true`). Candles do not record the intrabar path.
   - A target is a resting limit order: it fills at the target only once the
     bid (high − half spread for a long) reaches it. A stop is a market order
     after the trigger: it fills at the stop (`stop_fill: STOP_PRICE`) or at the
     bar's worst price (`BAR_WORST`), minus half the spread and slippage.
   - If the entry was not filled at the open (`NEXT_BAR_WORST` entry), the
     target is not credited on the entry bar; only the stop is checked.
3. **Close of bar i.** The last bar of a session (or of the data / window)
   exits at its close (`EOD_SQUARE_OFF` / `END_OF_DATA`); intraday positions are
   never carried overnight. The daily loss stop and profit lock are evaluated on
   realised net + unrealised P&L at the close, and equity is marked.
4. **Decision at the close of bar i.** The strategy sees bars `0..i` only. If it
   fires and the risk rules allow it, an entry is scheduled for bar
   `i + 1 + latency_bars` (default: the next bar's open). Entries are only
   scheduled from bars inside the session window (`session_start_ist` to
   `session_end_ist`, defaults 09:20–15:00, bot values when `bot_id` is given)
   and before the square-off time, and the fill bar must be on the same day.

Bars outside the NSE session (weekdays 09:15–15:30 IST) are dropped first; the
DEMO generator produces 24×7 data. Candles with missing OHLC are dropped and
counted in `data.dropped_missing_ohlc`. At most one position is open at a time.

### Shared logic with bots

Entry conditions are evaluated with the same `Evaluator`
(`app/strategies/conditions.py`) the bot worker uses, with the same semantics
(crossovers true only on the crossing candle; patterns and SMC events only on
their confirmation candle). Catalogue strategies use their `default_entry`
conditions (mirrored for PE, as the bot builder does). With `bot_id`, the
bot's current config version supplies underlying, timeframe, side, entry
conditions and logic, session window, cooldown, relative-volume / ATR filters,
risk per trade and entries per day (each capped by the global limits). Bot
signal suppression rules are reproduced: cooldown since the last accepted
signal, bot and global entries per day, filters, re-entry after a stop-loss.

Differences from the live worker, by design: indicator series are computed
once over the full history (they are causal, so values at bar *i* only use
bars ≤ *i*, but EMA warm-up is longer than the worker's 300-candle window);
structure conditions (`PATTERN`, `SMC_*`, `RESISTANCE_BREAK`) re-detect on a
trailing **150-bar** window at every bar (the worker uses 300) to stay inside
the request time budget; `MODEL_PROBABILITY` conditions and the AI Adaptive
strategy are rejected because they need point-in-time model predictions.

### Risk rules

The engine applies the user's global limits from `RiskSettings` (read via
`services/risk.get_or_create`): risk per trade (default ₹250), daily loss stop
(₹800, exits the open position at the next open when `exit_on_loss_limit`),
daily profit lock (₹1,200, blocks new entries), 2 entries per day, one open
position, 1 lot, no re-entry after a stop-loss unless enabled, square-off
15:15. Request overrides may only be **stricter**; anything looser returns
`422 RISK_OVERRIDE_EXCEEDS_LIMIT`. As in the paper pre-trade check, an entry is
suppressed (`RISK_BUDGET`) when *stop distance × qty + estimated round-trip
charges* exceeds the risk per trade, using the decision bar's close.
Suppressed signals and rejected orders are counted by reason in the result.

## Execution realism and costs

| Setting | Default | Effect |
|---|---|---|
| `spread_bps` | 2 | Full spread assumption (candles carry no bid/ask). Each market fill pays half. |
| `slippage_ticks`, `tick_size` | 1, 0.05 | Adverse slippage per market fill. |
| `slippage_bps` | 0 | Additional proportional slippage. |
| `latency_bars` | 0 | Fill k bars after the next bar. |
| `entry_fill` | `NEXT_OPEN` | `NEXT_BAR_WORST` fills a long at the next bar's high (short: low). |
| `stop_fill` | `STOP_PRICE` | `BAR_WORST` fills a triggered stop at the bar's low (short: high). |
| `reject_probability` | 0 | Seeded random rejection of entry orders (`SIMULATED_REJECTION`). |
| `min_volume` | 0 | Deterministic `NO_LIQUIDITY` rejection when the fill bar's volume is below it. A missing or non-positive price is always rejected (`NO_PRICE`). |
| `partial_fill_probability` | 0 | With this probability an entry of more than one lot fills `ceil(lots/2)` lots; the rest is cancelled. |
| `brokerage_per_order` | `PAPER_BROKERAGE_PER_ORDER` (₹20) | Passed to the charge model. |
| `charge_premium` / `charge_premium_pct` | none / 0.6 % | Assumed option premium per unit for charges. |

Charges use `app.execution.charges.compute` (BUY and SELL legs) with the
schedule in force on the entry date, including STT 0.15 % of sell premium from
2026-04-01 (0.1 % before). Because the proxy has no premium, charges are
computed on an **assumed** premium per unit: `charge_premium` if given, else
`charge_premium_pct` × the underlying entry price (0.6 % ≈ an at-the-money
weekly premium), for both legs. Trades dated before the earliest schedule use
the earliest schedule and add a warning. `lot_size` defaults to 1, a labelled
placeholder like the paper engine's DEMO lot size; set the contract's lot size
for rupee-accurate results.

## Metrics

Over the trade list (net = gross − charges; spread and slippage are already in
the fill prices):

| Metric | Definition |
|---|---|
| `gross_pnl`, `charges_total`, `net_pnl` | Sums over trades |
| `cost_contribution_pct` | charges_total / \|gross_pnl\| × 100 (null when gross is 0) |
| `slippage_total`, `gap_slippage_total` | Rupee cost of spread + slippage versus reference prices; stop-gap cost |
| `win_rate_pct` | trades with net > 0 / trades × 100 |
| `profit_factor` | Σ net of winners / \|Σ net of losers\| (null with no losing trade) |
| `expectancy` | net_pnl / trades |
| `avg_winner`, `avg_loser` | Mean net of winners / losers |
| `max_consecutive_losses` | Longest run of trades with net < 0 |
| `max_drawdown`, `max_drawdown_pct` | Largest peak-to-trough fall of the equity curve (capital + realised + unrealised at each bar close), in rupees and as % of the running peak |
| `sharpe`, `sortino` | On **daily** returns r = day net P&L / capital over every session day in the window (days without trades count as 0), risk-free rate 0, annualised × √252. Sharpe = mean(r) / sample std(r); Sortino = mean(r) / √mean(min(r, 0)²). Null with < 2 days or zero dispersion |
| `per_trade_sharpe` | mean(net) / sample std(net), not annualised |
| `exposure_pct` | Bars with a position open at any time during the bar / bars × 100 |
| `turnover` | Σ (entry + exit price) × qty, underlying notional |
| `by_regime` | Trades grouped by `analysis/regime.detect` at the **signal** bar (causal) |
| `by_timeframe` | The run's timeframe plus up to two `compare_timeframes` run with the same settings |

The equity curve is downsampled to at most 200 points. Trades list entry,
signal and exit times in IST, prices, stop/target, exit reason, gross, charges,
net, slippage, gap slippage, bars held and regime.

## Validation tooling

- **Chronological split:** `validation.chronological_split(n, train_pct,
  validation_pct)` returns index ranges in time order; nothing is shuffled.
- **Walk-forward** (`/walk-forward`): the last `holdout_pct` of bars (default
  20 %) is removed first. Rolling windows of `train_bars` followed by
  `test_bars` step forward by `test_bars`. In each train window every grid
  point (stop %, target %, optional threshold of the first numeric condition)
  is run and the best by `objective` (`net_pnl`, `profit_factor`,
  `expectancy`, `sharpe`; candidates with fewer than `min_trades` trades are
  ineligible; ties keep the first grid point) is applied unchanged to the next
  test window. `oos_metrics` aggregates **test windows only**; windows never
  overlap. Indicators may use bars before a window starts (that is past data).
  Finally parameters are chosen on the `train_bars` immediately before the
  holdout and the holdout is evaluated once, flagged `evaluated_once: true`.
  The API is stateless and cannot stop you from re-running; re-running after
  looking at the holdout turns it into in-sample data.
- **Stability:** mean and sample std of per-window net P&L, win rate,
  expectancy and profit factor, plus the share of profitable windows.
- **Sensitivity** (`/sensitivity`): the full grid run on the same bars. It is
  in-sample by construction (`in_sample: true`): use it to see whether results
  hinge on a narrow parameter choice, not to pick parameters.
- **Monte Carlo** (`/monte-carlo`): bootstrap of the trade sequence. Each of N
  simulations draws as many trades as the backtest produced, with replacement,
  from the net trade P&L (NumPy PCG64 seeded by `seed`). Reported: 5/25/50/75/95th
  percentiles of total net P&L and of max drawdown (on the cumulative trade
  P&L path starting at 0), mean net P&L and the probability of a net loss. It
  assumes trades are independent and ignores regime clustering.

## Lookahead protections

- Python strategy hooks receive a `BarView` whose series are `PastView`
  objects: any index after the decision bar raises `LookaheadError`; negative
  indices and slices are relative to the decision bar.
- Condition strategies receive a `Ctx` whose lists are copies ending at the
  decision bar, so future bars do not exist for them.
- Decisions at bar *i* fill no earlier than bar *i + 1*.
- Tests: a peeking hook raises; and running on the first *k* bars versus the
  full history gives identical trades before *k* (indicators, patterns and
  structure conditions included).

## Determinism

The same request, seed and candles give identical results (all randomness
comes from one seeded generator per run; walk-forward windows use derived
seeds). Results include `data.fingerprint` (hash of the candles used) and
`request_hash`. DEMO candles depend on the current time, so a request made
after a new candle closes uses different data.

## Persistence and caching

Backtests are **not persisted** yet: no tables or migrations were added.
Results are computed on request and memoised in an in-process LRU
(`backtesting/cache.py`, 64 results and 8 prepared histories) keyed by a hash
of the canonical request, the effective risk limits and the data fingerprint.
The cache is per API process and is lost on restart; `cached: true` marks a
cache hit. A results table can be added later without changing the engine.

## Compute limits

`bars` ≤ 2000 session bars, walk-forward grid ≤ 16 combinations and ≤ 8
windows, sensitivity grid ≤ 36 cells, Monte Carlo ≤ 5000 simulations, at most 2
comparison timeframes, and a 10 s wall-clock guard on grids
(`422 COMPUTE_BUDGET_EXCEEDED`). Typical runs on DEMO data take well under a
second for indicator strategies and 2–3 s for 2000 bars of structure
(pattern/SMC) strategies. The router is rate-limited to 60 requests per minute
per client IP.

## Known limitations

- No historical option premiums: proxy fidelity only, so results do not show
  the effect of premium, IV, theta or the option spread.
- DEMO data is synthetic and partly predictable; results on it say nothing
  about real markets.
- Candles have no bid/ask or intrabar path: spread is an assumption and the
  stop-first rule is a conservative guess.
- Charges are computed on an assumed premium; reconcile against contract notes
  for real trading.
- Exits are never randomly rejected; latency applies to entries only.
- Small samples: a few dozen trades give wide uncertainty. Check the Monte
  Carlo percentiles and the walk-forward stability before drawing conclusions.

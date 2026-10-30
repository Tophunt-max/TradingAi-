# Options intelligence (work package I)

Status: implemented and tested for **options buying only** (CE or PE). The output is research.
It never places, sizes or approves an order; the independent risk manager does that.

Code:

| Area | File |
|---|---|
| Contract evaluation, ranking, assumptions | `backend/app/options/intelligence.py` |
| Monte Carlo premium model | `backend/app/options/simulation.py` |
| IV rank, skew, term structure | `backend/app/options/volatility.py` |
| Black-Scholes Greeks | `backend/app/options/greeks.py` |
| Endpoint | `GET /api/v1/options/{underlying}/intelligence` in `backend/app/api/v1/options.py` |
| Mobile | `apps/mobile/features/ai/OptionsIntel.jsx`, `apps/mobile/features/ai/intel.js` |
| Tests | `backend/tests/test_options_intelligence.py` |

## Inputs

Query parameters of `GET /options/{underlying}/intelligence` (`underlying` is `NIFTY` or
`BANKNIFTY`; auth required):

| Parameter | Default | Allowed |
|---|---|---|
| `expiry` | nearest | a listed expiry (else 422 `INVALID_EXPIRY`) |
| `side` | `CE` | `CE`, `PE` |
| `strikes` | 2 | 0–5 strikes each side of ATM |
| `strike` | – | evaluate one strike only (> 0) |
| `stop_pct` | 20 | 0 < x < 100 (premium % below entry) |
| `target_pct` | 30 | 0 < x ≤ 500 (premium % above entry) |
| `horizon_minutes` | 60 | 5–375 |
| `timeframe` | `15m` | `5m`, `15m`, `1h` (underlying candles and direction model) |

Data the endpoint gathers:

- the option chain for the expiry (`max(strikes + 2, 5)` strikes each side, or 10 when `strike`
  is set);
- the chains of up to two other expiries (1 strike each side) for term structure; failures are
  ignored;
- 300 underlying candles on `timeframe` (for HV20 and the direction model);
- the direction from a PROMOTED model, if one exists (see output 1);
- funds: the user's DEMO account cash from the ledger, and `max_risk_per_trade` from the user's
  risk settings (column default ₹250 if the user has none);
- brokerage `PAPER_BROKERAGE_PER_ORDER` (default ₹20) and `DEMO_LOT_SIZE` (1).

Fixed constants: risk-free rate `r = 0.065`, dividend yield `q = 0`, expiry time 10:00 UTC
(15:30 IST), Monte Carlo `paths = 4000`, `seed = 7`, slippage 1 tick each side.

## Candidate selection

- With `strike`: only that strike (a note is added if it is not in the chain).
- Otherwise: the ATM row and `strikes` rows on each side (2 → up to 5 contracts).
- Only the requested side's leg is evaluated.
- No evaluation if the chain has no spot or rows, or if lot size or tick size is unknown.

Ranking: eligible contracts first, then by modelled `expected_net_pnl`, highest first. `rank`
starts at 1. The payload states that a ranking is not a recommendation.

`moneyness`: `ATM` for the chain's ATM row; otherwise a CE is `ITM` if strike < spot, a PE is
`ITM` if strike > spot, else `OTM`.

Time to expiry: ACT/365 calendar from now to expiry 15:30 IST. The evaluation horizon is
`min(horizon_minutes, minutes left)`.

## The four outputs

They are kept separate on purpose. A correct view on the underlying can still lose money through
theta, IV and spread, so they are not combined into one score.

### 1. Underlying direction

From `registry.serving(db, underlying, timeframe)`:

| Case | Output |
|---|---|
| No PROMOTED model | `probabilities: null`, `source: null`, reason "No promoted model for … the direction probability is not estimated." |
| Model exists, candles missing or quality `FAIL` | `probabilities: null`, `model_version`, reason |
| Model answers | the model's prediction (probabilities, uncertainty, calibration status, etc.) with `source: PROMOTED_MODEL` |

Per candidate, `relevant_outcome` is `UP` for CE and `DOWN` for PE, and `relevant_probability`
is that class's probability or `null`. The intelligence module never estimates direction itself.

### 2. Premium: target before stop (`premium_path`, label `MODEL_ESTIMATE`)

Method `mc-gbm-bs-v1` (`simulation.simulate`):

1. **Underlying paths.** Driftless geometric Brownian motion,
   `S(t+dt) = S(t) · exp(−σ²dt/2 + σ√dt · Z)`, `Z ~ N(0, 1)` from
   `numpy.random.default_rng(seed)`. Driftless means no directional edge is assumed.
2. **Volatility source** (`path_volatility_source`): `REALIZED_HV20`, the underlying's 20-bar
   historical volatility annualised on trading time (252 sessions × 375 minutes), when the
   candles are available; otherwise `IMPLIED`, the contract IV read on the same trading-time
   basis. `dt` for the paths is horizon / (252 × 375 minutes) / steps.
3. **Repricing.** At each step the premium is the Black-Scholes-Merton price at the remaining
   calendar time to expiry (ACT/365, so time decay applies), with the contract IV held constant
   and constant r, q.
4. **Entry premium.** The Black-Scholes price at t = 0 with the chain IV
   (`model_entry_premium`), not the market mid, so the model starts consistent with itself. The
   market mid is reported alongside. If the modelled premium is below 0.05 the contract is not
   simulated.
5. **Exit rule.** Target = entry × (1 + target_pct/100), stop = entry × (1 − stop_pct/100). The first
   step where the premium is ≥ target or ≤ stop decides. A target fills at the target; a stop
   fills at the modelled premium at that step (gap risk). Paths that touch neither exit at the
   horizon premium.
6. **Steps**: `max(10, min(horizon_minutes, 240))`, so at most one step per minute.
7. **IV scenarios** (`iv_scenarios`): the same simulation with IV shocked by **−2** and **+2**
   vol points. The shock is applied at entry as an instant mark-to-model change against the
   unshocked entry premium, so a shock can hit the target or stop at step 0.

Fields: `p_target_first`, `p_stop_first`, `p_neither` (each `{p, std_error}` with
`std_error = √(p(1−p)/paths)`), `median_minutes_to_exit`, `paths`, `steps`, `seed`,
`model_entry_premium`, `market_mid`, `target_pct`, `stop_pct`, `horizon_minutes`,
`path_volatility`, `path_volatility_source`, `iv_scenarios [{iv_shock_pts, p_target_first,
p_stop_first, expected_premium_pnl_per_unit}]`.

Not available (with `reason`) when IV is missing, the contract is expired, there is no mid price,
or the contract is blocked.

### 3. Expected net after costs (`expected_net`, label `MODEL_ESTIMATE`)

For one lot (`qty = lot size`):

| Item | Formula |
|---|---|
| `expected_premium_pnl` | mean(simulated exit premium − model entry premium) × lot |
| `costs.spread_crossing` | (ask − bid) × lot (half at entry, half at exit) |
| `costs.slippage` | 2 × slippage_ticks × tick × lot (entry and exit) |
| `costs.charges` | statutory charges and brokerage from `app.execution.charges.round_trip` (the versioned schedule effective on the trade date): buy at ask + slippage, sell at (market mid + modelled premium change) − half spread − slippage |
| `costs.total` | sum of the three |
| `expected_net_pnl` | `expected_premium_pnl − costs.total` |
| `net_pnl_quantiles` | p05, p25, p50, p75, p95 of simulated per-unit premium P&L × lot − total costs |

### 4. Risks

| Field | Definition |
|---|---|
| `theta_per_day_lot` | theta (per calendar day) × lot |
| `spread_pct`, `spread_cost_lot` | (ask − bid) / mid × 100; (ask − bid) × lot |
| `vega_per_vol_pt_lot` | vega (per 1 vol point) × lot |
| `iv_move_pnl_lot` | `{"-2": vega × −2 × lot, "+2": vega × 2 × lot}` |
| `gamma`, `gamma_pnl_1pct_move_lot` | gamma; 0.5 × gamma × (0.01 × spot)² × lot |
| `flags` | below |

Flags (a blocking flag sets `eligible: false` and adds to `blocked_reasons`):

| Code | Rule | Blocks |
|---|---|---|
| `NO_BID_ASK` | bid or ask missing | yes |
| `WIDE_SPREAD` | spread > **2.0 %** of mid | no |
| `LOW_OI` | open interest < **1000** | no |
| `LOW_VOLUME` | volume < **100** | no |
| `NEAR_EXPIRY` | < **2.0** days to expiry | no |
| `HIGH_THETA` | \|theta\| / mid > **5 %** per day | no |
| `NO_IV` | IV missing or ≤ 0 | yes |
| `EXPIRED` | horizon < 1 minute | yes |
| `UNAFFORDABLE` | premium for one lot (ask + slippage) + estimated charges > available DEMO funds | yes |
| `RISK_ABOVE_LIMIT` | estimated loss at stop > risk-per-trade limit | yes |

`affordability`: `premium_per_lot`, `available_funds`, `risk_per_trade_limit`,
`estimated_loss_at_stop` = (entry fill × stop_pct/100 × lot) + spread × lot + slippage × lot +
round-trip charges (buy at entry fill, sell at the stop price), `affordable`,
`within_risk_per_trade`. Checks are skipped (`null`) when funds or the limit are unknown, for
example when the user has no DEMO account.

## Greeks source

| Case | `greeks.source` | Note |
|---|---|---|
| Chain has all four Greeks and `greeks_model` starts with "Provided" (INDstocks: "Provided by INDstocks") | `PROVIDER` | |
| Chain has Greeks from its own model (DEMO: "Black-Scholes (r = 6.5%) on demo IV") | `MODEL` | "Chain Greeks are model values" |
| Chain lacks Greeks but has IV | `MODEL` | computed here with Black-Scholes (r = 0.065, no dividends) |
| No Greeks and no IV | `null` | all Greeks `null` |

The payload also has top-level `greeks_source` and `greeks_model`. Theta is per calendar day,
vega per 1 vol point.

## IV context

- `atm_iv`: mean of the ATM call and put IV.
- **IV rank** = (IV − min) / (max − min) over a history of past IV observations; **IV
  percentile** = share of the history strictly below the current IV. Both need at least **60**
  observations. With fewer, both are `null` with `reason: "insufficient IV history"`. If max = min,
  rank is 0.5. The endpoint does not pass any history today, so IV rank is always `null`
  (see limitations).
- **Skew**: `put_minus_call_iv_k` = put IV k = 2 strikes below ATM minus call IV k strikes above
  ATM. `put_minus_call_iv_25d` = IV of the put with delta nearest −0.25 minus IV of the call with
  delta nearest +0.25, only if both are within 0.1 of ±0.25.
- **Term structure**: ATM IV per available expiry (requested expiry plus up to two others).
  Shape `CONTANGO` if last − first > 0.25 vol points, `BACKWARDATION` if < −0.25, else `FLAT`;
  `null` with a reason if fewer than two expiries have ATM IV.

## Futures basis

Always `{value: null, reason: "No futures quote is available from the market-data provider
interface."}`.

## DEMO data and lot size

- `data_source` is `SIMULATED` for DEMO chains.
- DEMO chains carry no exchange lot or tick size. The engine uses lot size `DEMO_LOT_SIZE` = **1**
  (`lot_size_source: DEMO_PLACEHOLDER`) and tick 0.05 (`tick_size_source: DEMO_PLACEHOLDER`), and
  adds a note that these are placeholders, not NSE contract metadata. All per-lot figures for
  DEMO are therefore per single unit.
- For real chains the lot and tick come from the instrument master (`INSTRUMENT_MASTER`). If
  either is missing, no contract is evaluated.

## Response

`IntelligencePayload`: provenance fields (`source, provider, is_demo, quality, notes, …`),
`underlying, expiry, side, timeframe, spot, atm_strike, data_source, time_to_expiry {days, years,
day_count}, lot_size, lot_size_source, tick_size, tick_size_source, greeks_source, greeks_model,
underlying_direction, underlying_context {close, price_vs_ema50, supertrend, hv20_annualised,
realized_vol20}, iv_context, futures_basis, funds, candidates, ranking, assumptions,
disclaimer_options`.

Each candidate: `strike, side, moneyness, trading_symbol, security_id, ltp, bid, ask, mid, iv, oi,
oi_change, volume, greeks, underlying_direction, premium_path, expected_net, risks,
affordability, eligible, blocked_reasons, lot_size_source, rank`.

`assumptions` lists the pricing model (Black-Scholes-Merton, European), r, q, day count, driftless
GBM, path-volatility source, constant IV with ±2-point scenarios, discrete monitoring, the fill
rule, and what is not modelled.

## Mobile

`OptionsIntelSection` is shown on the option-chain screen for the strike the user selects. It
requests that single strike (`strike=`) with the default stop, target and horizon, has a CE/PE
switch, and renders the first candidate with `OptionsIntelView`: four numbered blocks (underlying direction with a `PROMOTED MODEL` or
`NOT AVAILABLE` badge; premium target/stop/neither with IV scenarios, badge `MODEL ESTIMATE`;
expected net, gross and costs for one lot, badge `MODEL ESTIMATE`; risks with flags and a
`BLOCKED` badge). The header says when the lot is a DEMO placeholder. A footer shows IV rank
("n/a" with the reason), futures basis and the model assumptions. Formatting helpers live in
`intel.js` (`intelSections`, `pct`, `rupees`).

## Limitations / not implemented

- Options **buying** only. No selling, spreads or multi-leg strategies.
- IV rank and percentile are always `null`: no IV history is stored, and the endpoint does not
  pass `iv_history`.
- Futures basis is not available.
- The Monte Carlo is a model estimate: driftless GBM, constant IV, Black-Scholes repricing,
  European exercise. Not modelled: IV smile dynamics, spread widening, overnight gaps, early
  exercise, order-size liquidity impact.
- The simulation starts from the Black-Scholes price, which can differ from the market mid.
- Funds and risk limit come from the DEMO account only; REAL accounts are not supported.
- Direction is available only with a PROMOTED model, and none is promoted today (only simulated
  data is available), so `underlying_direction.probabilities` is `null` in practice.
- The mobile view evaluates one selected strike with the default stop (20 %), target (30 %) and
  horizon (60 min); it has no controls to change them.
- No historical option premiums are available to check the model's probabilities against real
  outcomes.

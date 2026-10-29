# Technical analysis, candlesticks and price structure

Indicator version: **`ta-3`** (`app.indicators.core.INDICATOR_VERSION`). It is returned as
`indicator_version` in `GET /api/v1/analysis/{symbol}`, in `technical.indicator_version`, in
`indicators.indicator_version` and in the pattern-statistics response. Bump it whenever a
definition, default period or warm-up rule changes. The AI feature set (`ai/features.py`,
`f1`) reads only pre-existing series, so its values did not change and its version was not
bumped.

Code:

| Area | Module |
|---|---|
| Series indicators | `backend/app/indicators/core.py` |
| Swings, divergences, trendlines, channels, ranges | `backend/app/indicators/structure.py` |
| Evidence families and correlation | `backend/app/indicators/evidence.py`, `analysis/technical_summary.py` (`evidence`) |
| Per-window series cache | `backend/app/analysis/context.py` |
| Candlesticks, confirmation, outcome stats | `backend/app/analysis/patterns.py` |
| Price structure (SMC) | `backend/app/analysis/smc.py` |
| Feature ablation | `backend/app/analysis/ablation.py` |
| Tests | `backend/tests/test_technical_v3.py` |

## Rules that apply to everything

- **Causal.** The value at bar *i* uses bars 0..*i* only. A test builds the full context
  on prefixes of 30, 37, …, 317 bars and checks that every series equals the full-data series
  on that prefix (`test_every_context_series_is_causal_for_every_prefix`). The structure
  functions (trendlines, ranges, swings, divergences) have the same test.
- **Warm-up.** Values are `None` until the full window exists. A partial window is never
  reported.
- **Missing data.** Series functions (SMA, EMA, ROC, momentum, OBV, HV, RV, percentiles)
  accept `None`/NaN. Any window that touches a missing value is `None`. The EMA restarts
  after a gap and needs `period` fresh values. OBV restarts at 0 after a gap because the
  level before the gap is not comparable. OHLC functions (ATR, ADX, Supertrend, Ichimoku,
  CCI, Stochastic) expect complete candles. The market-data quality layer drops or flags bad
  candles before they reach these functions.
- **No repainting.** Swing pivots need 3 later candles (`left = right = 3`), so a swing at
  *i* is known at *i* + 3. Events are emitted only at the bar where they are known.
  Adding later candles never adds, removes or moves an earlier event. Pattern
  *confirmation status* can move from `PENDING` to a decided state, but a decided state
  never changes (tested).

## Indicators

*n* = period. "Warm-up" is the first index with a value (0-based).

| Indicator | Key(s) in context / chart | Formula | Defaults | Warm-up |
|---|---|---|---|---|
| SMA | `sma20`, `sma<N>` | mean of the last *n* values | 20 | *n*−1 |
| EMA | `ema5…ema200`, `ema<N>` | α = 2/(*n*+1); seeded with the SMA of the first *n* values | 9, 21, 50, 100, 200 | *n*−1 |
| RSI (Wilder) | `rsi14` | 100 − 100/(1+RS), RS = Wilder-smoothed gain / loss | 14 | *n* |
| MACD | `macd`, `macd_signal`, `macd_hist` | EMA12 − EMA26; signal = EMA9 of MACD; hist = MACD − signal | 12/26/9 | 25 / 33 |
| Bollinger | `bb_upper/mid/lower` | SMA ± k·σ (population σ) | 20, 2 | *n*−1 |
| ATR (Wilder) | `atr14` | Wilder average of true range | 14 | *n*−1 |
| ADX, +DI, −DI | `adx14`, `pdi14`, `mdi14` | Wilder DM/TR smoothing; `directional_movement()` returns (+DI, −DI, ADX) | 14 | DI: *n*; ADX: 2*n*−1 |
| Supertrend | `supertrend`, `supertrend_dir` | HL2 ± mult·ATR bands with ratcheting | 10, 3 | *n*−1 |
| CCI | `cci20` | (TP − SMA(TP)) / (0.015 · mean abs dev) | 20 | *n*−1 |
| Stochastic | `stoch_k`, `stoch_d` | raw %K over 14, smoothed 3; %D = SMA3 of %K | 14/3/3 | 15 / 17 |
| VWAP (session) | `vwap` | Σ TP·V / Σ V, resets each IST date | — | 0 |
| Anchored VWAP | `avwap` (chart) | Σ TP·V / Σ V from the anchor bar | anchor = latest confirmed swing | swing confirmation bar |
| Relative volume | `rvol20` | V / mean(V of the previous *n* bars) | 20 | *n* |
| Volume MA | `volma20` | SMA of volume (includes the current bar) | 20 | *n*−1 |
| Rate of change | `roc12`, `roc<N>` | 100 · (C*ᵢ* / C*ᵢ₋ₙ* − 1) | 12 | *n* |
| Momentum | `mom10` | C*ᵢ* − C*ᵢ₋ₙ* | 10 | *n* |
| OBV | `obv` | running Σ ±V by close direction (0 on unchanged), starts at 0 | — | 0 |
| Historical volatility | `hv20`, `hv<N>` | sample σ (ddof 1) of the last *n* log returns × √(bars per year) | 20 | *n* |
| Realized volatility | `rv20` | √Σ r² over the last *n* log returns (not annualised in the context; `periods_per_year` scales by √(ppy/*n*)) | 20 | *n* |
| Parkinson volatility | `pkv20` | √(Σ ln(H/L)² / (4 ln 2 · *n*) · ppy) | 20 | *n*−1 |
| Volatility state | `vol_score`, `vol_state` (chart `volstate`) | mean of the mid-rank percentiles of ATR% and Bollinger width over the last 100 bars; CONTRACTION ≤ 0.2, EXPANSION ≥ 0.8, else NORMAL | 100, 0.2, 0.8 | ATR/BB warm-up + 20 valid bars |
| Ichimoku | `ichi_tenkan`, `ichi_kijun`, `ichi_senkou_a`, `ichi_senkou_b`, `ichi_span_a`, `ichi_span_b` | tenkan = 9-bar midpoint; kijun = 26-bar midpoint; senkou A = (tenkan+kijun)/2; senkou B = 52-bar midpoint | 9/26/52, shift 26 | 8 / 25 / 25 / 51; drawn spans +26 |
| Classic pivots | `indicators.pivots` | floor pivots of the previous **completed** session | — | 2 sessions |
| Fibonacci retracement | `indicators.fibonacci` | high − r·range (up move), r ∈ {0.236, 0.382, 0.5, 0.618, 0.786} | last confirmed swing high/low | — |
| Fibonacci extension | `indicators.fibonacci_extension` | origin ± r·range, r ∈ {0.618, 1, 1.272, 1.618, 2, 2.618}; with a retracement point C: C ± r·range | — | — |

Bars per year for annualisation: `252 × 375 / bar minutes` for intraday timeframes (375 is the
NSE cash session), 252 for daily. The context only knows this when it is built with a
`timeframe` (the analysis pipeline and charts pass it). Without one, 252 is used.

### Ichimoku and leakage
Charts draw senkou A/B 26 bars **ahead** of the bar that computed them. In this codebase:
- `ichi_senkou_a/b[i]` are the values computed at *i* (they belong 26 bars ahead on a chart);
- `ichi_span_a/b[i]` are the spans **drawn at** *i*, i.e. `senkou[i − 26]`.

Both are known at *i*. Decision logic (evidence votes) uses only tenkan, kijun and the
spans drawn at *i*. The chart's `ichimoku` returns the drawn spans, so nothing is placed on
a candle that did not exist when it was computed. **Chikou is not implemented.** It plots
close[*i*] at *i* − 26, so on a past candle it would show a future close.

### Structure functions (`indicators/structure.py`)
All use confirmed pivots (3/3) and take `as_of` (default: last bar). They only use pivots
confirmed at or before `as_of`.

- **Swing points.** Each confirmed swing is labelled against the previous swing of the same kind:
  HH / LH / EH for highs and HL / LL / EL for lows (the first one is H or L). Each has
  `index` and `confirmed_index = index + 3`.
- **Divergence.** Compares two consecutive confirmed swing lows (or highs) that are 5–60
  bars apart. The oscillator is read at the price pivot bars.
  - Regular bullish: lower low in price, higher low in the oscillator.
  - Regular bearish: higher high in price, lower high in the oscillator.
  - Hidden bullish: higher low in price, lower low in the oscillator.
  - Hidden bearish: lower high in price, higher high in the oscillator.

  `detection_index` is the second pivot bar. `confirmed_index` is the detection index + 3,
  the first bar where the divergence is known. The pipeline runs it on RSI 14, the MACD
  histogram and OBV. OBV divergence is the **volume-price divergence**.
- **Trendlines.** A least-squares line through the last 3 confirmed swing highs gives
  resistance; the same on swing lows gives support. Each line reports:
  - `slope` (price per bar), `intercept`, `r2` and `value` at `as_of`;
  - `touches`: swings within 0.25 × ATR of the line;
  - `break_index`: the first close more than 0.25 × ATR beyond the line, after the last fitted
    pivot was confirmed.
- **Channel.** Both lines exist and their drift apart over the fitted span is ≤ 1 × ATR.
  Reports upper, lower, width in ATR, direction (UP / DOWN / FLAT; ±0.02 × ATR per bar) and
  the close's position in the channel.
- **Range boundaries.** Over the last 60 bars, upper = the highest confirmed swing high and
  lower = the lowest confirmed swing low. `is_range` requires:
  - ≥ 2 swings within 0.5 × ATR of each boundary;
  - width ≤ 6 × ATR;
  - the close inside the range.

## Evidence families and correlation

Most indicators are transforms of the same closes. Counting each one as a confirmation
overstates the evidence, so the technical summary groups them:

| Family | Members (vote rule at the last closed bar) |
|---|---|
| trend | `ema_stack` (+1 if EMA9 > EMA21 > EMA50, −1 if reversed), `supertrend` (direction), `adx_di` (sign of +DI − −DI when ADX ≥ 20, else 0), `ichimoku` (+1 above both drawn spans and tenkan > kijun; −1 mirrored) |
| momentum | `rsi` (> 55 / < 45), `macd` (histogram sign), `stochastic` (%K vs %D on the same side of 50), `cci` (> 100 / < −100), `roc` (sign) |
| volume | `obv` (sign of the 10-bar change), `rvol` (candle direction when RVOL ≥ 1.5, else 0), `vwap` (close vs VWAP) |
| volatility | `atr`, `bollinger`, `hv`: **context only**, never a vote; reported as the volatility state |

- **Family vote.** Each family's vote is the sign of the sum of its available member votes.
  The net vote is the sum over the three directional families (−3…+3). `family_bias` is
  BULLISH at ≥ +2, BEARISH at ≤ −2, otherwise MIXED.
- **Correlation.** A Pearson correlation matrix is computed over the last 100 bars of one
  series per indicator, using only the bars where both series in a pair have values (at
  least 20). The series are: EMA9/21 spread, Supertrend direction, DI spread, close vs kijun,
  RSI, MACD histogram, %K, CCI, ROC, OBV, RVOL, close vs VWAP, ATR %, Bollinger width and
  HV. `technical.correlated_pairs` lists pairs with |ρ| > 0.8 (configurable). Each pair says
  whether it is within one family. `cross_family_correlated` counts pairs across families,
  which shows that even family votes are not fully independent.

Votes are descriptive. They are not probabilities and they do not trigger trades.

## Candlestick patterns

Ratios are fractions of the candle range (high − low). The "prior trend" is measured over the
5 closes before the pattern's first bar: UP or DOWN if the move is ≥ 0.5 × ATR(14), else
FLAT. Thresholds are in `patterns.THRESHOLDS`.

| Pattern | Definition |
|---|---|
| Doji | body ≤ 10% of range |
| Dragonfly doji | doji, upper wick ≤ 10%, lower wick ≥ 60%; BULLISH after a downtrend, else NEUTRAL |
| Gravestone doji | doji, lower wick ≤ 10%, upper wick ≥ 60%; BEARISH after an uptrend, else NEUTRAL |
| Long-legged doji | doji, both wicks ≥ 30%, range ≥ 1 × ATR |
| Hammer / Hanging man | lower wick ≥ 2 × body, upper wick ≤ 0.3 × body + 5% of range; hammer after a downtrend, hanging man after an uptrend |
| Inverted hammer / Shooting star | upper wick ≥ 2 × body, lower wick small (as above); after a downtrend / uptrend |
| Marubozu | body ≥ 90% of range |
| Spinning top | body 10–30% of range, both wicks ≥ 25% |
| Bullish / Bearish engulfing | opposite-colour body fully covers the previous body and is larger |
| Piercing line | strong bearish candle (body ≥ 50% of range) after a downtrend; next opens ≤ its close and closes above its body midpoint but below its open |
| Dark cloud cover | mirror of the piercing line after an uptrend |
| Bullish / Bearish harami | strong first candle against the trend; second body strictly inside it and ≤ 60% of its size |
| Tweezer bottom / top | bearish then bullish (or the reverse) with lows (highs) within 0.1 × ATR, after a downtrend (uptrend) |
| Inside / Outside bar | range strictly inside / outside the previous candle (NEUTRAL) |
| Morning / Evening star | strong first candle (body ≥ 60%), small second body (≤ 30% of the first), third closes beyond the first's midpoint |
| Three white soldiers / black crows | three candles with bodies ≥ 60% of range, closing-side wick ≤ 25%, each opening inside the previous body and closing further in the trend direction |
| Three inside up / down | a harami, then a third candle closing beyond the first candle's open, against the prior trend |
| Bullish / Bearish three-bar reversal | the middle candle makes the lowest low (highest high) of the three; the third closes above (below) its high (low), after a downtrend (uptrend) |

Every candlestick event carries:

- **Indexes and timestamps.** `index` and `ts` mark the first pattern bar.
  `detection_index` and `detection_ts` mark the bar that completes the pattern; the older
  `confirmed_index` / `confirmed_ts` fields hold the same value, kept for compatibility.
- **`pattern_high`, `pattern_low`:** the extremes over the pattern's bars.
- **`confirmation`:** `{rule, window_bars, status, index, ts, direction}`.
  - Bullish patterns need a close above the pattern high within 3 bars.
  - Bearish patterns need a close below the pattern low within 3 bars.
  - Neutral patterns need a close beyond either extreme, and the break sets the direction.
  - Status is `PENDING`, `CONFIRMED`, `INVALIDATED` (a close beyond the opposite extreme
    came first) or `EXPIRED` (no break in 3 bars). `confirmed` is true only for CONFIRMED.
- **`invalidation`:** `{level, rule}`. For bullish patterns this is a close below the
  pattern low; for bearish patterns, a close above the pattern high. Neutral patterns get a
  level once a break gives them a direction.
- **`context`:** measured at the detection bar. It holds:
  - `body_range_ratio`, `upper_wick_ratio`, `lower_wick_ratio` and `close_location`
    ((C − L)/range);
  - `relative_volume`, `prior_trend`, `rsi14` and `roc12`;
  - `nearest_resistance` / `nearest_support` from swings already confirmed at the detection
    bar, with their distances in ATR;
  - `volatility_state`, `regime` (the rule-based regime at that bar) and `atr14`.

Chart patterns (double top/bottom, head and shoulders, flags, breakout/breakdown and retest)
are already confirmed by their neckline or level break. They get `confirmation.status =
CONFIRMED` at that bar, an invalidation rule (a close back through the broken level) and the
same context.

No pattern is called profitable or unprofitable anywhere in the code or the UI.

### Outcome statistics

`pattern_outcome_stats(candles, horizon, cost_bps, by_regime=True, min_samples=30)` and
`GET /api/v1/analysis/{symbol}/patterns/stats?timeframe=&horizon=&cost_bps=&min_samples=&history=`.

The outcome definition is returned with the stats:

- **Samples.** Only CONFIRMED candlestick patterns are used, processed in chronological
  order. Neutral patterns are grouped by their break direction, e.g.
  "Inside bar (BULLISH break)".
- **Trade rule.** Entry is the **open of the bar after the confirmation bar**. Exit is the
  **close `horizon` bars after the confirmation bar**. The position takes the pattern
  direction (short for bearish).
- **Cost and hits.** Net return = gross return − `cost_bps` (one round-trip charge). A hit
  is a net return > 0.
- **Statistics.** For each pattern (and each regime at the confirmation bar): `n`, mean,
  median and st. dev. of the net return in %, hit rate with a Wilson 95% interval, and
  `insufficient_sample = n < min_samples`.
- **Baseline.** The same entry/exit rule applied to every bar, long and short, so a pattern
  can be compared with no pattern at all.
- **Labelling.** The response carries the usual provenance and quality fields. DEMO data
  gives `evidence_status: NOT_EVIDENCE` and a SIMULATED note, and so does a failed
  data-quality check. Real data gives `HISTORICAL_SAMPLE`.

Limits:
- **Underlying only.** Option premium, theta, IV and spreads are not modelled.
- **Overlapping windows.** Forward windows can overlap, so samples are not independent and
  the intervals are optimistic.
- **Fixed cost.** One cost per trade; real slippage varies.
- **Single window.** The window is the requested history only, with no out-of-sample split.
- **Not a forecast.** Frequencies describe the past window, not the future.

## Price action / SMC v2

These are **price-structure heuristics**, not evidence of institutional activity. Each
response includes the disclaimer. Thresholds live in `SMCConfig` and are returned as
`smc.thresholds`.

| Setting | Default |
|---|---|
| Pivots (left / right) | 3 / 3 |
| FVG minimum size | 0.1 × ATR |
| Displacement | body > 1.5 × ATR and > 70% of range |
| Equal highs/lows | within 0.1 × ATR |
| Order-block search | 10 bars before the swing |
| Retest window / tolerance | 10 bars / 0.25 × ATR |
| Failed-breakout window | 5 bars |
| Supply/demand base | ≤ 3 candles, body ≤ 50% of range |
| Equilibrium band | ±5% of the swing range |

| Event | Definition | Confirmation | Invalidation |
|---|---|---|---|
| BOS | close beyond the most recent confirmed, unbroken swing in the current trend direction (or with no trend) | break bar | close beyond the opposite confirmed swing known at the break |
| CHOCH | same, but against the prior structure trend | break bar | as BOS |
| Order block | last opposite-colour candle before the break (searched back to swing − 10 bars) | break bar | close beyond the block's far side |
| Fair value gap | low[i] > high[i−2] (bullish) or high[i] < low[i−2], size ≥ 0.1 × ATR | third candle | close beyond the far edge (gap fully filled); `mitigated` when price trades back into it |
| Liquidity sweep | wick beyond a known unbroken swing, close back inside | that bar | close beyond the sweep wick |
| Equal highs / lows | two consecutive confirmed swings within 0.1 × ATR | second swing + 3 | close beyond the pair |
| Displacement | body > 1.5 × ATR and > 70% of range | that bar | close beyond the candle's origin side |
| Supply / demand zone | 1–3 base candles with body ≤ 50% of range directly before a displacement; zone = their high–low | displacement bar | close beyond the zone's far side; `mitigated` on re-entry |
| Breakout and retest | after a BOS/CHOCH, within 10 bars the wick returns to within 0.25 × ATR of the level and the close holds beyond it | retest bar | close back through the level |
| Failed breakout | after a BOS/CHOCH, a close back through the level within 5 bars | that bar | close beyond the breakout bar's extreme |
| Swing structure | confirmed swings labelled HH/HL/LH/LL/EH/EL | pivot + 3 | close beyond the swing (becomes a BOS/CHOCH) |
| Premium / discount | close's position in the range between the latest confirmed swing high and swing low: PREMIUM > 55%, DISCOUNT < 45%, else EQUILIBRIUM; ABOVE/BELOW_RANGE outside it | max(swing confirmations) | recomputed when a new swing is confirmed |

Every event has:

- `index` / `ts`: the anchor candle;
- `detected_index` / `detected_ts`: the bar where the condition occurred;
- `confirmed_index` / `confirmed_ts`: the bar where the event is known;
- `reference`: the swing or level the event refers to;
- `invalidation {level, rule}`;
- `data_quality`: the window's quality status (`OK`, `WARN`, `FAIL`, or `NOT_CHECKED` when
  called without one).

### Does structure add value? (`feature_ablation`)

`analysis/ablation.py::feature_ablation(candles, horizon)` builds two feature sets on the
same rows:

- **Baseline:** log returns over 1/3/6 bars, ATR % and the EMA 21 slope over 3 bars.
- **Baseline + structure:** the baseline plus:
  - net candlestick-pattern direction detected in the last 3 bars;
  - net confirmed-pattern direction in the last 5 bars;
  - structure trend and bars since the last BOS/CHOCH;
  - premium/discount position;
  - net open bullish minus bearish zones;
  - net liquidity sweeps in the last 5 bars.

Each structure feature at row *t* uses only events confirmed at or before *t*. A test checks
this against a prefix. Both arms use the existing `ai/model.py` purged walk-forward split and
logistic model with the same label (`ai/features.label`). The function reports
out-of-sample log loss, Brier score and ECE for each arm, against climatology, with sample
sizes and the delta. Results on DEMO data are marked `NOT_EVIDENCE`.

On the synthetic DEMO NIFTY 15m series (1,200 candles, horizon 4), the structure features
did **not** lower out-of-sample error: log loss went from 0.526 to 0.529 and Brier from
0.289 to 0.294. This describes the generator, not the market. Run it on real history before
drawing any conclusion.

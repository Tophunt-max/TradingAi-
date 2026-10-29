# Analysis engine (Phase 4)

`GET /api/v1/analysis/{symbol}?timeframe=5m|15m|1h|1d` runs a 12-step pipeline on the last 300
**closed** candles and always returns `decision: NO_TRADE` until costs and the risk manager
exist (Phase 6).

| # | Step | Module | Status |
|---|---|---|---|
| 1 | Market-data validation | `market_data/quality.py` | Done (Phase 3) |
| 2 | Feature engineering | `analysis/context.py`, `ai/features.py` | Done |
| 3 | Regime detection | `analysis/regime.py` | Done |
| 4 | Technical analysis | `indicators/core.py` | Done |
| 5 | Patterns & structure | `analysis/patterns.py`, `analysis/smc.py` | Done |
| 6 | Strategy evaluation | `strategies/rules.py` | Done |
| 7 | AI prediction | `ai/model.py`, `ai/registry.py` | Done (needs a promoted model) |
| 8 | Contract selection | `analysis/options_select.py` | Done |
| 9 | Cost estimation | — | **Blocked: Phase 6** |
| 10 | Independent risk validation | — | **Blocked: Phase 6** |
| 11 | Proposal or NO TRADE | `analysis/pipeline.py` | Always NO TRADE |
| 12 | Explanation | `analysis/pipeline.py` | Done |

## No lookahead, no repainting
- Every indicator value at candle *i* uses candles 0..*i* only. The tests check that computing on a prefix gives identical values.
- Swing points need 3 later candles, so a swing is only "known" 3 candles after it happened.
- Patterns and SMC events carry `confirmed_ts`, the first candle at which all the candles they need had closed. The tests check that adding future candles never adds, removes or moves an event confirmed earlier.
- The forming candle is never used (Phase 3).

## What is detected
- **Indicators:** EMA 9/21/50/200, RSI, MACD, VWAP (resets each IST session), Supertrend (10, 3), Bollinger (20, 2), ATR, ADX/±DI, CCI, Stochastic, classic pivots (previous completed session), Fibonacci retracements of the last confirmed swing, relative volume (vs the previous 20 bars), candle anatomy.
- **Candlesticks:** doji, hammer, inverted hammer, shooting star, bullish/bearish engulfing, morning/evening star, inside/outside bars.
- **Chart patterns:** double top/bottom and (inverse) head and shoulders, reported only after a close through the neckline. Also breakout/breakdown and retest, bull/bear flags, and HH/HL or LH/LL structure.
- **SMC:** BOS, CHOCH, order blocks, fair value gaps (with mitigation), liquidity sweeps, equal highs/lows and displacement.
- **Regime:** VOLATILE, TRENDING_UP, TRENDING_DOWN, RANGE_EXPANSION, SQUEEZE or RANGE, from ADX, EMA 50 slope, and the percentiles of ATR% and Bollinger width.

## Strategies
Each strategy reports the conditions it checked and whether each was met, plus whether the
current regime matches its design. A strategy signal is a research observation, not an order.
The DCA/Grid strategy is research-only and is never evaluated.

## AI model
- **Target:** UP / SIDEWAYS / DOWN over a fixed number of bars (for example 4 × 15m), measured as a move larger than 0.5 × ATR(14).
- **Model:** standardised multinomial logistic regression (`logreg-l2-v1`) on 18 versioned features (`f1`). Parameters are stored as JSON, never pickle.
- **Validation:** expanding-window walk-forward over 5 folds, with a purge gap of `horizon` rows so no training label overlaps a test row.
- **Metrics:** log loss and Brier score, each compared with a base-rate baseline; calibration error (ECE) with a calibration curve; accuracy; and log loss per regime.
- **Promotion gate:**
  - at least 300 out-of-sample predictions;
  - log loss at least 2% better than the baseline;
  - Brier score better than the baseline;
  - ECE ≤ 0.05;
  - every class at least 5% of outcomes;
  - trained on **non-DEMO** data.
- **Promotion** is manual and admin-only (`POST /api/v1/admin/models/{id}/promote`). Promoting a model retires the previous one. Nothing retrains or promotes automatically.
- **Serving:** a model serves only its own symbol and timeframe, only on its own data source, and never when data quality fails. Every served forecast is stored once per bar in `predictions`. Outcome scoring and drift monitoring come in Phase 7.

On DEMO data the model scores very well (about 82% accuracy) because the synthetic generator
is a predictable function. This is why DEMO models can never be promoted.

Train from the CLI:
```bash
cd backend
python -m app.ai.train NIFTY 15m --history 3000        # creates a CANDIDATE or REJECTED version
python -m app.ai.train NIFTY 15m --promote             # promotes only if the gate passed
```
With INDstocks, 3,000 × 15m candles is roughly 4–5 months of history, fetched in 7-day
windows at no more than 5 requests/second.

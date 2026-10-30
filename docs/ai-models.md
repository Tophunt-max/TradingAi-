# AI models, regime detection v2 and strategy router v2 (work packages J, K)

Status: implemented and tested. **No model is promoted.** Only simulated (DEMO) market data is
available in this environment, and models trained on DEMO data can never be promoted, so every
analysis today runs without model probabilities.

A probability or confidence threshold never triggers an order on its own. Every prediction
carries the `order_policy` text: "A probability or confidence threshold never triggers an order on
its own; the risk manager must approve any trade."

Code:

| Area | File |
|---|---|
| Features, labels, versions | `backend/app/ai/features.py` |
| Logistic baseline, walk-forward, metrics, gate, bootstrap | `backend/app/ai/model.py` |
| Multi-model engine (specs, calibration, OOD, forecasts, persistence) | `backend/app/ai/engine.py` |
| Registry (train, promote, serve, record) | `backend/app/ai/registry.py` |
| CLI | `backend/app/ai/train.py` |
| Regime (legacy and v2) | `backend/app/analysis/regime.py` |
| Strategy router v2 | `backend/app/strategies/router.py` |
| API | `backend/app/api/v1/models.py`, `backend/app/api/v1/analysis.py` |
| Mobile comparison | `apps/mobile/features/ai/ModelCompare.jsx`, `apps/mobile/app/(app)/model-compare.jsx` |
| Tests | `backend/tests/test_ai_engine.py`, `backend/tests/test_regime_v2.py` |

## Model types

| `model_type` | `algorithm` | Estimator | Hyperparameters |
|---|---|---|---|
| `logistic` (baseline) | `logreg-l2-v1` | scikit-learn `LogisticRegression` on standardised features | `C = 0.5, max_iter = 2000` |
| `random_forest` | `rf-v1` | `RandomForestClassifier` | `n_estimators 100, max_depth 6, min_samples_leaf 25, max_features "sqrt"` |
| `hist_gb` | `histgb-v1` | `HistGradientBoostingClassifier` | `max_iter 120, learning_rate 0.05, max_leaf_nodes 15, min_samples_leaf 40, l2_regularization 1.0, early_stopping False` |

`hist_gb` is the LightGBM-style histogram gradient boosting model. XGBoost and LightGBM are not
installed; they can be added later behind the same `ModelSpec` interface. All models take a
`seed` (0 to 2³¹ − 1) and are deterministic for a given seed (tested).

## Targets

Labels use the close H bars ahead (`H = horizon_bars`).

| `target` | Classes | Rule |
|---|---|---|
| `three_class` (default) | `DOWN, SIDEWAYS, UP` | move = close[t+H] − close[t]. `UP` if move > 0.5 × ATR(14)[t], `DOWN` if move < −0.5 × ATR(14)[t], else **`SIDEWAYS`** (the band of ±0.5 ATR) |
| `binary` | `DOWN, UP` | `UP` if close[t+H] > close[t], `DOWN` if lower; exact ties are dropped. No SIDEWAYS band |

Default horizon by timeframe: `1m 15, 3m 10, 5m 6, 15m 4, 30m 4, 1h 4, 1d 3`. Training timeframes
offered by the API and CLI: `5m, 15m, 1h, 1d`; horizon 1–50.

## Features

`FEATURE_VERSION = "f1"`, 18 features. Row t uses candles 0…t only; rows with any missing value
are dropped.

| Feature | Definition |
|---|---|
| `ret_1`, `ret_3`, `ret_6` | ln(close[t] / close[t−k]) |
| `close_vs_ema9`, `close_vs_ema21`, `close_vs_ema50` | (close − EMA) / EMA |
| `ema9_vs_ema21` | (EMA9 − EMA21) / EMA21 |
| `rsi` | RSI(14) / 100 |
| `macd_hist` | MACD histogram / close |
| `atr_pct` | ATR(14) / close |
| `adx` | ADX(14) / 100 |
| `bb_pct_b` | (close − lower band) / (upper − lower) |
| `log_rvol` | ln(max(RVOL(20), 1e-6)); 0 if RVOL is missing |
| `stoch_k` | Stochastic %K / 100 |
| `body_pct`, `upper_wick_pct`, `lower_wick_pct` | body and wicks / (high − low) |
| `time_of_day` | intraday: (IST minutes since midnight − 555) / 375, i.e. 0 at 09:15 and 1 at 15:30; daily: 0 |

Indicator values come from `app.indicators.core`, `INDICATOR_VERSION = "ta-3"`. Each model
version stores its `feature_version`; a model built with an older feature version cannot be
promoted (`MODEL_FEATURES_OUTDATED`) or rolled back to. Analysis decision records store both
versions.

## Training

`registry.train_candidate`:

1. Fetch `history` candles (default 3000). Data quality `FAIL` → 422 `TRAINING_DATA_REJECTED`.
2. Build the labelled dataset. Fewer than **400** rows → 422 `INSUFFICIENT_DATA`.
3. Purged walk-forward evaluation (below), metrics, comparisons, gate.
4. Fit the final model on all rows and store it.
5. Status `CANDIDATE` if the gate passed, else `REJECTED`.

The recipe (symbol, timeframe, history, horizon, seed, model type, target) is stored in
`metrics.recipe` so a model can be rebuilt.

### Purged walk-forward protocol

`model.walk_forward`, expanding window:

- The first 50 % of rows are the initial training window (`initial_frac = 0.5`).
- The remaining rows are split into **5** consecutive test folds; the last fold takes the rest.
- For a fold starting at row `t0`, training rows are `0 … t0 − H − 1`: a purge gap of H rows, so
  no training label window overlaps a test row.
- A fold is skipped if it has fewer than 50 training rows or its training rows miss a class.
- Never shuffled. Any calibration split is taken inside the fold's training rows.
- The **climatology baseline** for each fold is the class frequency of that fold's training rows,
  used as a constant forecast.

All reported metrics are on the concatenated out-of-sample test rows.

### Calibration

| Model | Method |
|---|---|
| `logistic` | None. Raw multinomial probabilities, status `UNCALIBRATED`; out-of-sample ECE is reported and gated |
| `random_forest`, `hist_gb` | Chronological hold-out inside the training rows: `cal = max(20 % of rows, 100)` most recent rows calibrate; the estimator is fitted on rows `0 … n − cal − H − 1` (purge gap H). Calibrator: `CalibratedClassifierCV` on the frozen estimator, **sigmoid** (Platt), or **isotonic** when `cal ≥ 1000`. Status `CALIBRATED_SIGMOID` or `CALIBRATED_ISOTONIC` |

Tree models fall back to `UNCALIBRATED` when fewer than 200 rows remain for fitting or a class is
missing from either part. The same procedure runs inside each walk-forward fold.

### Metrics

On out-of-sample predictions P (n × K) and labels y:

| Metric | Formula |
|---|---|
| `log_loss` | −mean ln(max(P[i, y_i], 1e-12)) |
| `brier` | mean_i Σ_c (P[i, c] − 1[y_i = c])² |
| `ece` | top-class confidence in 10 equal bins from 1/K to 1; Σ_bins (share of rows) × \|accuracy − mean confidence\| |
| `calibration_curve` | per bin: confidence, accuracy, count |
| `accuracy` | share where argmax P = y |
| `baseline_log_loss`, `baseline_brier` | same scores for the climatology baseline |
| `class_share` | share of each class in the test labels |
| `by_regime` | log loss and baseline log loss per legacy regime label, for regimes with ≥ 20 rows |
| `sample_sizes` | labelled rows, OOS predictions, folds |

Also stored at training: `vs_climatology`, `vs_logistic` (challengers only), `logistic_baseline`
(its log loss, Brier, ECE on the same folds), permutation importance, `label_definition`, and the
learning extras (bootstrap CIs for promotion, decile feature reference for drift, OOS predicted
class mix), see [learning.md](learning.md).

### Comparison and "better than baseline"

`model.block_bootstrap_diff(a, b)`: paired moving-block bootstrap of mean(a − b) over per-sample
log losses. Block length max(H, 10), 1000 resamples, 95 % percentile interval.

- `vs_climatology`: a = model, b = climatology.
- `vs_logistic`: a = challenger, b = logistic baseline trained on **the same folds** (training
  raises an error if the folds differ).
- A model is **better** only if the whole 95 % CI lies below zero (`ci_high < 0`).

Output: `mean_diff, ci_low, ci_high, n, block, n_boot, confidence, better`.

### Promotion gate

`model.gate`, all checks must pass:

| Check | Rule |
|---|---|
| `samples` | OOS predictions ≥ **300** |
| `log_loss` | model log loss / baseline log loss ≤ **0.98** (at least 2 % better) |
| `brier` | model Brier < baseline Brier |
| `calibration` | ECE ≤ **0.05** |
| `classes` | smallest class share ≥ **5 %** |
| `data` | `data_source ≠ DEMO` ("Synthetic DEMO data can never promote a model.") |
| `beats_logistic` | challengers only: `vs_logistic.better` |

Passing the gate makes a model a `CANDIDATE`. Reaching `PROMOTED` through the admin API also
needs the offline and shadow criteria in [learning.md](learning.md#promotion-lifecycle). **DEMO is
never promotable**: the gate, the offline criteria and `registry.promote` each reject it.

## Persistence and serialization

No extra tables: everything is in the JSON `params` column of `model_versions`.

- **Logistic**: plain JSON (`mean, scale, coef, intercept`). Binary models store two symmetric
  rows (`softmax([−z/2, z/2]) = sigmoid(z)`), tested to match scikit-learn exactly.
- **Tree models**: `pickle` (protocol 5) → zlib level 9 → base64, plus `hmac_sha256` over
  `sklearn_version + "\0" + raw bytes`, keyed with the server `SECRET_KEY`.

Loading a tree model checks, **before** unpickling, that the stored scikit-learn version equals
the running one and that the HMAC matches (constant-time compare). Otherwise it raises
`ArtifactError` and the model abstains with the reason. Artifacts larger than 4,000,000 base64
characters are not stored; such a model abstains and must be retrained from its recipe.

Security note: only artifacts written by this server are ever unpickled, and user-supplied data is
never unpickled. The protection depends on `SECRET_KEY` staying secret: anyone with the key and
database write access could store a malicious pickle. Rotating `SECRET_KEY` or upgrading
scikit-learn makes stored tree models unusable until retrained (they abstain; nothing crashes).

`params` also stores `features, classes, seed, calibration, ood, importance (top 10),
return_dist, vol_forecast, model_type, target`.

## Serving and predictions

`registry.serving` returns the latest `PROMOTED` model for `(symbol, timeframe)`.
`registry.predict` uses the last closed candle and abstains (`probabilities: null` with `reason`)
when:

- the current data source differs from the training source;
- there is not enough history for the features;
- the artifact cannot be loaded safely.

The analysis endpoint also abstains when input quality is `FAIL` or the latest drift report is
`DEGRADED`.

### Out-of-distribution (OOD) check

Training stores the feature mean, std, min, max and the inverse of a shrunk covariance
(0.9 × covariance + 0.1 × its diagonal + 1e-9 × I, pseudo-inverse).

- `mahalanobis_d2 = (x − mean)ᵀ Σ⁻¹ (x − mean)`; threshold = χ² quantile at 1 − 0.001 with 18
  degrees of freedom.
- `OUT_OF_DISTRIBUTION` if d² > threshold **or** any |z| > 5 (z = (x − mean) / std).
- `features_outside_training_range` is listed for information only.

An OOD result adds `ood_note` ("Inputs are outside the training distribution; treat as
unreliable.") but the probabilities are still returned.

### Uncertainty

`uncertainty` = normalised entropy −Σ p ln p / ln K (0 = certain, 1 = uniform), reported with the
OOD result in `uncertainty_detail`.

### Other outputs

- `expected_return_pct`: p05, p25, p50, p75, p95 and mean of the mixture Σ_c p_c · F_c, where F_c
  is the empirical distribution of 100 × ln(close[t+H] / close[t]) of training rows with label c
  (39 stored quantiles per class; `null` if a class had fewer than 20 rows). These are training
  return distributions, not a forecast of profit.
- `volatility_forecast`: per-bar vol = rv20 / √20 scaled by the 10th, 50th and 90th percentile of
  the training ratio (forward realised vol over t+1…t+H) / (trailing per-bar vol); each gives a
  ±1σ horizon range `close · exp(±σ√H)`. Needs ≥ 50 training ratios.
- `explanation`: logistic → local contributions (coefficient of the top class minus the class
  average, × standardised feature), top 5; tree models → global permutation importance (mean
  increase in log loss over 3 shuffles on the last fold's test rows), top 5.

### Prediction record

Served predictions (and SHADOW predictions) are stored in `predictions`, unique per
`(model_version_id, bar_ts)`:

`model_version_id, symbol, timeframe, horizon_bars, bar_ts, close, p_down, p_sideways` (0 for
binary models), `p_up, regime` (legacy label), `data_source, outcome, resolved_at, created_at`.
Abstentions are not stored. `outcome` is filled by the learning worker.

The prediction response contains `model_version, model_name, horizon_bars, algorithm,
feature_version, model_type, target, decision_ts, input_quality, regime, calibration_status,
order_policy, metrics {log_loss, baseline_log_loss, brier, baseline_brier, ece, oos_samples},
label_definition, probabilities, most_likely, confidence, uncertainty, uncertainty_detail,
expected_return_pct, volatility_forecast, explanation, reason` and, when relevant, `ood_note`.

## Regime detection

### Legacy regime (`regime.detect`)

Still used for the `regime` field, strategy catalogue families, prediction records and outcome
resolution. Over the last 100 bars: ATR % percentile rank, Bollinger-width percentile rank, EMA50
slope over 5 bars. First match wins: `VOLATILE` (ATR rank ≥ 0.9), `TRENDING_UP` (ADX ≥ 25, slope
> 0, close > EMA50), `TRENDING_DOWN` (mirror), `RANGE_EXPANSION` (width rank 5 bars ago ≤ 0.2 and
now ≥ 0.5), `SQUEEZE` (width rank ≤ 0.2), else `RANGE`.

### Regime detection v2

`regime.detect_v2`, version `regime-v2.0`. Causal: uses candles 0…i and only structure events
confirmed at or before i.

States: `STRONG_UPTREND, STRONG_DOWNTREND, RANGE, HIGH_VOLATILITY, LOW_VOLATILITY, BREAKOUT,
REVERSAL, ABNORMAL, EVENT_UNCERTAINTY`, plus `UNKNOWN`.

**How confidences are computed.** Each state gets a deterministic raw score from features, and the
scores go through a softmax with temperature **0.6**:
`confidence(s) = exp((score_s − max) / 0.6) / Σ exp(...)`.

| State | Raw score |
|---|---|
| `STRONG_UPTREND` | adx_z × (up_agree / 3) × (1 + slope_mag) |
| `STRONG_DOWNTREND` | adx_z × (dn_agree / 3) × (1 + slope_mag) |
| `HIGH_VOLATILITY` | 3 × clip((atr_rank − 0.7) / 0.3, 0, 1) |
| `LOW_VOLATILITY` | 1.5 × clip((0.3 − atr_rank) / 0.3, 0, 1) + 1.5 × clip((0.25 − width_rank) / 0.25, 0, 1) |
| `RANGE` | 1.5 × clip((25 − ADX) / 10, 0, 1) + 0.5 if no recent BOS/CHOCH + 0.5 × clip(1 − 2\|slope_atr\|, 0, 1) |
| `BREAKOUT` | 2.0 if (previous width rank ≤ 0.2 and width rank ≥ 0.5) + 1.0 if recent BOS + 0.5 if RVOL ≥ 1.5 |
| `REVERSAL` | 1.5 if recent CHOCH + 0.5 if recent CHOCH and RSI > 70 or < 30 |
| `ABNORMAL` | 4.0 if abnormal move; **6.0** if data quality `FAIL` |
| `EVENT_UNCERTAINTY` | event level `HIGH` 4.0, `MEDIUM` 1.5, `LOW`/`NONE` 0 |

Where adx_z = clip((ADX − 20) / 10, 0, 2); slope_atr = (EMA50[i] − EMA50[i−5]) / ATR;
slope_mag = clip(|slope_atr|, 0, 1.5); up_agree = count of (slope_atr > 0, close > EMA50, +DI > −DI),
dn_agree the mirror; atr_rank and width_rank are the legacy percentiles; "recent" = confirmed in
the last 10 bars. The event level comes from the event-risk gate (`HIGH → HIGH`,
`ELEVATED → MEDIUM`).

**The confidences are labelled `calibration_status: "UNCALIBRATED"`.** They are scores, not
probabilities, and have not been fitted to outcomes.

**ABNORMAL rule.** For each of the last 100 bars, move = max(|close_k − close_{k−1}|,
|open_k − close_{k−1}|) / ATR_{k−1}. The last bar is abnormal if its move ≥ **3 × ATR** and, when
more than 20 moves exist, ≥ the 99th percentile of the earlier moves. A data-quality failure gives
ABNORMAL a score of 6.0 and the reason "treated as ABNORMAL".

**UNKNOWN rules.**

- Not enough history for the legacy regime features → `UNKNOWN` with no confidences.
- Top confidence < **0.30** → `UNKNOWN`.
- Top two confidences within **0.05** → `UNKNOWN` ("Conflicting evidence").

`no_trade` is true for `UNKNOWN`, `ABNORMAL` and `EVENT_UNCERTAINTY`; the pipeline then adds an
abstain reason. Each state maps to a legacy family (`TRENDING`, `RANGE`, `VOLATILE`,
`RANGE_EXPANSION`, or `null` for the no-trade states). Output: `state, family, confidence, raw_scores,
top (3), margin, calibration_status, no_trade, reasons, features, unavailable` (always includes
`breadth`), `thresholds, legacy_label, version, method`.

## Strategy router v2

`strategies/router.py`, version `router-v2.0`.

expected_compatibility(strategy) = Σ_state confidence(state) × COMPAT[strategy][state]

COMPAT values are design priors (0 = against the design, 1 = what it is built for), not fitted.
ABNORMAL and EVENT_UNCERTAINTY are 0 for every strategy.

| Strategy | UP trend | DOWN trend | RANGE | HIGH vol | LOW vol | BREAKOUT | REVERSAL |
|---|---|---|---|---|---|---|---|
| momentum | 1.0 | 1.0 | 0.1 | 0.4 | 0.2 | 0.7 | 0.1 |
| scalping | 0.7 | 0.7 | 0.4 | 0.3 | 0.3 | 0.6 | 0.2 |
| breakout | 0.5 | 0.5 | 0.3 | 0.3 | 0.6 | 1.0 | 0.2 |
| reversal | 0.1 | 0.1 | 0.8 | 0.3 | 0.4 | 0.1 | 1.0 |
| smc | 0.6 | 0.6 | 0.6 | 0.3 | 0.3 | 0.6 | 0.8 |
| ai_adaptive | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 |
| custom | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 |
| dca_grid | 0.0 | 0.0 | 0.6 | 0.0 | 0.4 | 0.0 | 0.2 |

Verdicts, first match wins:

| Verdict | Rule |
|---|---|
| `NO_TRADE` | strategy is research-only (`dca_grid`) |
| `NO_TRADE` | regime state is `UNKNOWN`, `ABNORMAL` or `EVENT_UNCERTAINTY` |
| `NO_TRADE` | `ai_adaptive` and no promoted model returned probabilities |
| `INCOMPATIBLE` | expected compatibility < **0.4** |
| `REQUIRES_VALIDATION` | no out-of-sample validation recorded for this strategy in the current state |
| `COMPATIBLE` | compatible and validated |

The analysis pipeline does not pass any validation evidence, so no strategy can be `COMPATIBLE`
today; the best possible verdict is `REQUIRES_VALIDATION`. The router never produces orders.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/models?symbol=` | ✓ | Latest 100 model versions |
| GET | `/api/v1/models/{id}` | ✓ | One model with metrics and gate |
| GET | `/api/v1/models/predictions?symbol=&limit=` | ✓ | Stored predictions (limit 1–200, default 50) |
| GET | `/api/v1/models/compare?symbol=&timeframe=` | ✓ | Up to 50 models for the pair, sorted by OOS log loss. Rows: `id, name, algorithm, model_type, target, horizon_bars, status, data_source, created_at, oos_samples, log_loss, brier, ece, accuracy, climatology_log_loss, climatology_brier, vs_climatology, vs_logistic, better_than_climatology, better_than_logistic, calibration_status, gate_passed, promotable, promotable_reason`, plus `criteria` text. `promotable = gate_passed and data_source ≠ DEMO` |
| POST | `/api/v1/admin/models/train` | admin | `{symbol, timeframe, horizon_bars?, model_type, target, seed}`; audited |
| GET | `/api/v1/models/{id}/promotion` | ✓ | Promotion checks, see [learning.md](learning.md) |
| POST | `/api/v1/admin/models/{id}/shadow` \| `/promote` \| `/rollback` \| `/retire` | admin | Lifecycle, see [learning.md](learning.md) |
| GET | `/api/v1/analysis/{symbol}?timeframe=` | ✓ | Includes `prediction`, `probabilities`, `regime_v2`, `strategy_router`, `event_risk`, `decision_record_id` |

CLI: `python -m app.ai.train NIFTY 15m [--horizon 4] [--history 3000] [--model-type
logistic|random_forest|hist_gb] [--target three_class|binary] [--seed 0] [--shadow]` (from
`backend/`).

The mobile **Model comparison** screen (`model-compare.jsx` + `ModelCompareTable`) lists the
compare rows: model type and target, name, status and data source, log loss with climatology,
Brier, ECE, n, the vs-logistic and vs-climatology CIs with YES/NO, a calibration badge, and a
`PROMOTABLE` / `NOT PROMOTABLE` badge with the reason.

## Current status

- No model is promoted, so `prediction` is `null` (or abstains) in every analysis, the options
  direction output is `null`, and `ai_adaptive` routes to NO TRADE.
- Models can be trained on DEMO data for testing. They are labelled with `data_source: DEMO`,
  fail the `data` gate check and show `NOT PROMOTABLE: Trained on synthetic DEMO data.`
- No claim is made that any model beats the baseline on real data; that has not been tested.

## Limitations / not implemented

- XGBoost and LightGBM are not installed; `hist_gb` is the histogram boosting substitute.
- The logistic baseline is never calibrated after fitting; it relies on the ECE gate.
- OOD inputs are flagged but the model still returns probabilities.
- Tree-model explanations are global (permutation importance), not per prediction.
- Regime v2 confidences are uncalibrated scores; breadth data is not available.
- The router's compatibility table is expert priors; no strategy has stored out-of-sample
  validation per regime state, so `COMPATIBLE` is never reached.
- Tree artifacts depend on the exact scikit-learn version and on `SECRET_KEY`.

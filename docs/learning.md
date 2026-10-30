# Learning: decision records, outcomes, attribution, drift, promotion (work package N)

Status: implemented and tested. The learning layer measures and reports. It never trains,
promotes or demotes a model by itself, and nothing it computes places an order.

Code:

| Area | File |
|---|---|
| Decision recording | `backend/app/learning/recorder.py` |
| Outcome resolution | `backend/app/learning/outcomes.py` |
| Error attribution (pure) | `backend/app/learning/attribution.py` |
| Metrics over records | `backend/app/learning/metrics.py` |
| Scoring rules, calibration, bootstrap | `backend/app/learning/stats.py` |
| Drift (PSI, performance) | `backend/app/learning/drift.py` |
| Promotion, shadow, rollback | `backend/app/learning/promotion.py` |
| Serving guard | `backend/app/learning/guard.py` |
| Train-time extras | `backend/app/learning/hooks.py` (called from `backend/app/ai/registry.py`) |
| Worker | `backend/app/workers/learning.py` |
| API | `backend/app/api/v1/learning.py`, promotion routes in `backend/app/api/v1/models.py` |
| Tests | `backend/tests/test_learning.py` |

## Decision records

Table `decision_records` (`backend/app/models/intelligence.py`, migration `20261029_0008_intelligence.py`, which also adds `model_drift_reports` and the `shadow_started_at` / `rolled_back_at` columns of `model_versions`). One row per decision,
**including NO TRADE and skipped ones**.

### Who writes them

| Origin | Writer | When |
|---|---|---|
| `ANALYSIS` | `record_analysis`, from `GET /analysis/{symbol}` | Every analysis request |
| `BOT` | `record_bot`, from the bot engine's `evaluate_run` | Every evaluated bar of a running bot, `NO_SIGNAL` included |

### Dedup rule

Unique constraint on `(symbol, timeframe, bar_ts, origin, origin_ref)`. `origin_ref` is `""` for
analysis and the bot id (hex) for bots. A second analysis of the same closed bar is not stored
again: the insert runs in a savepoint and an `IntegrityError` is swallowed. Any other database
error is logged and ignored, so recording never breaks the caller.

### Fields

| Group | Fields | Analysis records | Bot records |
|---|---|---|---|
| Identity | `origin, origin_ref, decision_ts, bar_ts, symbol, timeframe` | | |
| Horizon | `horizon_bars` | Model horizon if a model answered, else the default | Default |
| Market | `close` (last closed candle), `atr` | | `atr14` at the bar |
| Model | `model_version_id, model_name, feature_version, indicator_version` | Filled when a promoted model answered | Empty |
| Inputs | `data_quality` (OK/WARN/FAIL), `data_source` | | |
| Regime | `regime` (legacy label), `regime_probabilities` | | `regime` only |
| Forecast | `probabilities, uncertainty, calibration_status, predicted_direction, predicted_vol_range` | See below | `calibration_status = NOT_APPLICABLE` |
| Decision | `decision, no_trade_reasons` (≤ 20), `strategies, evidence, plan, event_risk` | | |
| Links | `order_id, position_id, account_mode` | Empty | Filled when the bot placed an order |
| Outcome | see [outcome fields](#outcome-resolution) | | |

Default horizon (bars) by timeframe: `1m 15, 3m 10, 5m 6, 15m 4, 30m 4, 1h 4, 1d 3`.

Analysis-record rules:

- `decision`: `SETUP_CANDIDATE` if the pipeline produced a setup candidate, else the pipeline
  decision (`NO_TRADE`).
- `predicted_direction`: the class with the highest probability; with no probabilities, `UP` for a
  CE setup, `DOWN` for a PE setup, else `null`.
- `calibration_status`: `UNCALIBRATED` when there are no probabilities and no model answered,
  `ABSTAINED` when a model answered without probabilities (drift guard, data-source mismatch,
  quality failure), `CALIBRATED_OOS` when probabilities were served.
- `strategies`: ids of strategies that gave a CE/PE direction and were not regime-incompatible.
- `evidence`: `{patterns, trend}`; `patterns` are those detected in the last 3 bars, with name,
  direction and timestamp, so they can be checked for invalidation later.
- `plan`: the setup candidate; `event_risk`: the event-risk gate level.

Bot-record rules: `decision` is `SIGNAL` for a signal, `SKIPPED` for a suppressed signal, and
`NO_TRADE` for everything else. `plan = {side}` and `predicted_direction` (CE → UP, PE → DOWN)
are set only for non-NO_TRADE decisions.

## Outcome resolution

`resolve_outcomes` (run by the worker). It processes up to 500 `PENDING` decision records and up
to 500 unresolved `predictions`, oldest bar first.

**When.** A record is touched only when both hold:

1. `now ≥ bar_ts + (horizon_bars + 1) × timeframe` (the decision candle plus H more candles have
   closed), and
2. the provider's latest 1000 candles contain the decision candle at index `t` and the H
   candles after it (`t + H < len(candles)`).

Otherwise the record stays `PENDING` (counted as `waiting`). If the decision candle is older than
the first available candle, the record becomes `UNRESOLVABLE` with a note. Candle-fetch failures
leave records for the next pass.

**Which bars.** Only candles `t+1 … t+H`; the decision candle `t` gives the reference close `c0`.
Candles `t+H+1 … t+2H` are used only for the late-direction check in WRONG_TIMING.

**Label.** Same as training: with ATR = the record's `atr` (else ATR(14) recomputed at `t`),
`UP` if `close[t+H] − c0 > 0.5 × ATR`, `DOWN` if `< −0.5 × ATR`, else `SIDEWAYS`.

Fields written:

| Field | Definition |
|---|---|
| `realized_return` | `close[t+H] / c0 − 1` |
| `realized_direction` | label above |
| `realized_volatility` | sample standard deviation of the H one-bar log returns from `c0` to `close[t+H]` (per bar, not annualised; `null` if H = 1) |
| `max_favorable_excursion`, `max_adverse_excursion` | In price units over `t+1…t+H`. For predicted `DOWN`: MFE = c0 − min(low), MAE = max(high) − c0. Otherwise (UP or none): MFE = max(high) − c0, MAE = c0 − min(low) |
| `regime_at_resolution` | Legacy regime label at `t+H` (up to 299 earlier candles of context) |
| `option_premium_change` | Linked position: (exit or last price) − average entry |
| `slippage` | Linked order: average fill − `risk_snapshot.entry_price` for BUY (reverse for SELL) |
| `error_classes` | See below |
| `outcome_notes` | Invalidated patterns; for records without a direction, a note that the outcome is kept for NO TRADE analysis only |
| `outcome_status`, `resolved_at` | `RESOLVED` and the pass time |

**Model predictions** (`predictions` table) are resolved with the model's own target: binary
models get `UP`/`DOWN` by the sign of `close[t+H] − close[t]` (`TIE` if exactly equal, which is
not counted as resolved); three-class models get the ATR label above.

## Error classes

`attribution.attribute`, deterministic, several classes per outcome, fixed order:

| Class | Exact rule |
|---|---|
| `DATA_QUALITY` | `data_quality` is `FAIL` or `WARN` |
| `WRONG_TIMING` | Predicted `UP`/`DOWN`, realized direction differs, and either MFE ≥ 0.5 × ATR (the move in the predicted direction reached the label threshold inside the horizon) or the label at `t+2H` equals the prediction |
| `WRONG_DIRECTION` | Predicted `UP`/`DOWN`, realized direction differs, WRONG_TIMING does not apply |
| `REGIME_CHANGE` | Both regimes known and `regime_at_resolution ≠ regime` |
| `VOLATILITY_ERROR` | A `predicted_vol_range {low, high}` was recorded and `realized_volatility` is outside it |
| `PATTERN_MISCLASSIFICATION` | A pattern cited in `evidence.patterns` (same name and timestamp) has confirmation status `INVALIDATED` when re-detected at `t+H` |
| `OPTION_PRICING` | Linked position, predicted `UP`/`DOWN`, the underlying moved in the predicted direction (`realized_return` > 0 for UP, < 0 for DOWN) and the premium change is negative |
| `EXECUTION_SLIPPAGE` | Linked fill slippage, as a percent of the reference price, above `LEARNING_SLIPPAGE_THRESHOLD_PCT` (default **1.0**, allowed (0, 50]) |
| `RISK_ORDER_MANAGEMENT` | Linked order status is `REJECTED`, `UNKNOWN` or `RECONCILIATION_REQUIRED` |

A predicted `SIDEWAYS` gets neither WRONG_TIMING nor WRONG_DIRECTION.

## Metrics

`GET /learning/summary` runs `metrics.summary` over the newest 5000 records (optionally filtered
by `symbol`, `timeframe`).

| Output | Definition |
|---|---|
| `records, resolved, pending, unresolvable, by_decision` | Counts |
| `probabilistic` | Over resolved records with all three class probabilities. `null` if none |
| `brier` | mean over records of Σ_c (p_c − 1[y = c])² |
| `log_loss` | mean of −ln(max(p_y, 1e-12)) |
| `climatology_brier`, `climatology_log_loss` | Same scores for a constant forecast equal to the class frequency of these same outcomes (in-sample, so an optimistic baseline; labelled as such) |
| `ece`, `calibration` | Top-class confidence in 10 equal buckets from 1/3 to 1. Per bucket: mean confidence (`predicted`), hit rate (`realized`), `count`. ECE = Σ (bucket share × \|realized − predicted\|) |
| `class_reliability` | Per class, 5 buckets of width 0.2 over that class's probability: mean probability vs realized frequency |
| `accuracy.overall, by_timeframe, by_regime, by_strategy` | predicted_direction = realized_direction over resolved records with both in DOWN/SIDEWAYS/UP. `accuracy` is `null` with a note when the group has fewer than **20** samples |
| `error_classes` | Count per class (all nine listed, zeros included) |
| `no_trade` | NO TRADE analysis, below |
| `drift` | Latest drift status per SHADOW/PROMOTED model |

Binary models are not in `probabilistic`; they are scored per model in the promotion report.

### NO TRADE analysis (hypothetical)

Over resolved records with `decision` `NO_TRADE` or `SKIPPED`:
`samples`, `realized_moves` (count per realized direction), `mean_abs_return`, and a
counterfactual: for records whose `plan.side` is CE or PE, the underlying return had the side been
followed (`+realized_return` for CE, `−realized_return` for PE), giving
`counterfactual_setup_samples` and `counterfactual_mean_return`. The block is labelled
`"label": "HYPOTHETICAL"` with the note that these are underlying returns before costs, premium
decay and slippage, and not a performance claim.

## Drift

`drift.compute_report`, for PROMOTED and SHADOW models, stored in `model_drift_reports`.

### Feature drift (PSI)

PSI = Σ_i (a_i − e_i) · ln(a_i / e_i)

- **Bins**: the interior deciles (10 %, 20 %, … 90 %) of each training feature; duplicate edges
  are merged, so a feature can have fewer than 10 bins. Values are assigned with right-closed
  search (`searchsorted(..., side="right")`).
- `e_i`: reference share per bin; `a_i`: share of the recent window. Both floored at **1e-4**.
- **Recent window**: feature rows for the last 300 bars (from the latest 360 candles).
- **Reference distribution source** (`reference_basis`):
  - `TRAIN_TIME`: stored at training in `metrics.feature_reference` by `hooks.train_time_extras`;
  - `RECOMPUTED_TRAINING_WINDOW`: for older models, rebuilt from up to 3000 provider candles inside
    the model's `data_from…data_to`, only if at least max(100, 0.5 × `train_rows`) rows exist;
  - `UNAVAILABLE`: neither, so feature drift is not measured and the report says so.

| PSI | Level |
|---|---|
| < 0.10 | `STABLE` |
| 0.10 – 0.25 | `MODERATE` |
| > 0.25 | `SIGNIFICANT` |

### Prediction drift

Categorical PSI between the out-of-sample predicted top-class mix stored at training
(`metrics.oos_predicted_share`) and the top-class mix of the model's latest 300 recorded
predictions. Computed only with at least 100 predictions.

### Performance drift

Rolling Brier over the resolved predictions among the latest 300, against the model's
walk-forward validation Brier (`metrics.brier`).

- Fewer than **100** resolved predictions → `INSUFFICIENT_DATA`, with the note that no conclusion
  is drawn and a few losses never demote a model.
- Otherwise `DEGRADED` if rolling Brier / validation Brier > **1.15**, else `STABLE`.

### Report status

| Status | Rule |
|---|---|
| `INSUFFICIENT_DATA` | Fewer than 100 recent feature rows **and** performance is `INSUFFICIENT_DATA` |
| `DEGRADED` | Any feature PSI > 0.25, or prediction PSI > 0.25, or performance `DEGRADED` |
| `MODERATE` | Not degraded, and any feature at `MODERATE` or prediction PSI ≥ 0.10 |
| `STABLE` | None of the above |

Report fields: `status, computed_at, reference_basis, window_rows, max_feature_psi, feature_psi
{name: {psi, level}}, prediction_psi, performance, reasons`.

### What happens on significant drift

`guard.serving_block_reason` reads the model's latest report. If it is `DEGRADED`:

- `GET /analysis/{symbol}` returns `probabilities: null` and `prediction.reason` beginning
  "Model is DEGRADED by drift monitoring (…) so it abstains until reviewed, retrained or rolled
  back." The decision record gets `calibration_status = ABSTAINED`.
- The bot engine's probability conditions get `null` (the model abstains).

The model's `status` is not changed. An admin decides whether to retire, roll back or retrain.

## Promotion lifecycle

```
CANDIDATE ──to_shadow──► SHADOW ──promote──► PROMOTED ──retire──► RETIRED
   ▲                                            │
 train (gate passed; else REJECTED)             └─rollback─► ROLLED_BACK (previous version re-PROMOTED)
```

Only one PROMOTED model per `(symbol, timeframe, horizon_bars)`; promoting retires the current
one. Every transition is a manual admin call and is audited.

### Offline criteria (to enter SHADOW, re-checked before PROMOTED)

| Key | Rule |
|---|---|
| `gate` | The training gate passed (thresholds in [ai-models.md](ai-models.md#promotion-gate)) |
| `oos_samples` | ≥ **500** out-of-sample predictions |
| `offline_log_loss_improvement` | Per-sample (climatology − model) log loss, moving-block bootstrap 95 % CI lower bound **> 0** |
| `offline_brier_improvement` | Same for Brier |
| `offline_ece` | Out-of-sample ECE ≤ **0.05** |
| `data` | `data_source ≠ DEMO` |
| `features` | `feature_version` equals the current `FEATURE_VERSION` |

CI method (`stats.block_bootstrap_mean_ci`): moving-block bootstrap of the mean, block length =
max(1, horizon), 1000 resamples, percentiles 2.5 / 97.5, seed 0 (log loss) and 1 (Brier). Blocks
keep the serial dependence of overlapping horizons. These intervals are computed at training and
stored in `metrics.bootstrap`; models trained before that report "Not available: retrain".

### Shadow criteria (to become PROMOTED)

SHADOW predictions are recorded on every analysis request for that symbol and timeframe (unless
input quality is FAIL) but are never shown or used. A shadow failure never affects the response.

| Key | Rule |
|---|---|
| `shadow_status` | Status is `SHADOW` |
| `shadow_samples` | ≥ **200** resolved predictions created since `shadow_started_at` |
| `shadow_data` | At least one, and none on DEMO data |
| `shadow_log_loss_improvement`, `shadow_brier_improvement` | Against the training class shares (`metrics.class_share`) as a constant forecast; block-bootstrap 95 % CI lower bound > 0 (block = max(1, horizon)) |
| `shadow_ece` | ECE on shadow outcomes ≤ **0.05** |

Binary models are scored on their own two classes.

### DEMO is never promotable

Three independent checks: the training gate's `data` check fails for `DEMO`; the offline `data`
criterion fails, so a DEMO model cannot enter SHADOW; `registry.promote` refuses `DEMO`. Shadow
outcomes on DEMO data also fail `shadow_data`.

### Rollback

`POST /admin/models/{id}/rollback` on the PROMOTED model. The previous version is the most recently
promoted `RETIRED` model with the same symbol, timeframe and horizon. It must have the current
feature version and non-DEMO data. The current model becomes `ROLLED_BACK`; the previous one
becomes `PROMOTED` again. With no previous version the call returns 409 `NO_PREVIOUS_MODEL`
(retire instead).

### Few losses don't demote

Every rule has a minimum sample: 100 resolved predictions for performance drift, 200 for shadow
validation, 500 for offline, 20 for any accuracy group. Test `test_few_losses_do_not_demote`
adds two confidently wrong outcomes to a promoted model and checks that the report is
`INSUFFICIENT_DATA` for performance and the model stays PROMOTED.

## Worker

```bash
cd backend
python -m app.workers.learning               # every 300 s
python -m app.workers.learning --interval 60
python -m app.workers.learning --once        # one pass, then exit
```

Each pass (`run_once`): resolve outcomes and commit, then compute a drift report for each
PROMOTED and SHADOW model whose last report is at least **1 hour** old. A failing model is logged
and skipped. The worker never trains, promotes or demotes. Running two workers is safe: outcome
rows are written once. `POST /admin/learning/run` runs one pass in the API process.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/learning/decisions?symbol=&timeframe=&decision=&status=&limit=&offset=` | ✓ | Paginated decision records, newest first |
| GET | `/api/v1/learning/summary?symbol=&timeframe=` | ✓ | Metrics above |
| GET | `/api/v1/learning/drift` | ✓ | Thresholds and latest report per SHADOW/PROMOTED model (`NOT_COMPUTED` if none) |
| POST | `/api/v1/admin/learning/run` | admin | One worker pass; audited `learning.pass` |
| GET | `/api/v1/models/{id}/promotion` | ✓ | `{model_id, status, policy, offline {passed, checks}, shadow}` |
| POST | `/api/v1/admin/models/{id}/shadow` | admin | CANDIDATE → SHADOW; 409 `MODEL_GATE_FAILED` with the failing checks |
| POST | `/api/v1/admin/models/{id}/promote` | admin | SHADOW → PROMOTED; 409 `MODEL_SHADOW_REQUIRED` / `MODEL_SHADOW_FAILED` |
| POST | `/api/v1/admin/models/{id}/rollback` | admin | Returns the re-promoted model |
| POST | `/api/v1/admin/models/{id}/retire` | admin | PROMOTED → RETIRED |

## Limitations / not implemented

- No model is PROMOTED or in SHADOW today: only simulated (DEMO) data is available, and DEMO
  models cannot enter either stage. Drift and shadow scoring have only run in tests.
- `predicted_vol_range` is never filled by the recorders, so `VOLATILITY_ERROR` cannot fire yet.
- Analysis records have no order or position link, so `OPTION_PRICING`, `EXECUTION_SLIPPAGE` and
  `RISK_ORDER_MANAGEMENT` apply only to bot records with orders.
- `calibration_status = CALIBRATED_OOS` on an analysis record means probabilities were served by a
  promoted model (which passed the out-of-sample ECE gate). It does not copy the model's own
  calibration status, so a logistic model labelled `UNCALIBRATED` in the registry still produces
  `CALIBRATED_OOS` records.
- The CLI `python -m app.ai.train … --shadow` only moves a model to SHADOW through the same
  offline criteria as the admin API. It cannot promote.
- Rollback does not check the previous version's drift status.
- Drift is computed at most hourly and only by the worker or the admin endpoint; the API does not
  start the worker.
- Outcomes need the decision candle within the provider's latest 1000 candles; older records
  become `UNRESOLVABLE`.
- The summary is limited to the newest 5000 records.

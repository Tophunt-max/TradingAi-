# QuantAI F&O master plan (three prompts merged)

Sources:
- **P1**: original master prompt (phases 1–9; auth, UI, data, analysis, bots, risk, paper execution).
- **P2**: v2 prompt (DEMO / REAL account modes, ₹10,000 virtual ledger, isolation, 81 screens).
- **P3**: advanced AI intelligence prompt (18 modules: data, TA, candles, SMC, fundamentals,
  news, options, regime, models, decision engine, learning, backtesting, monitoring).

Rule when prompts differ: the later prompt wins on scope; every safety rule from any prompt
stays (no fabricated data, live trading off by default, AI never overrides the risk manager,
demo orders never reach a broker, no profit guarantees).

## Work packages and status

Status: ✅ done and tested · 🟡 partial · ⛔ blocked on something outside the repo · ⏳ in this run.

| # | Package | Prompt refs | Status | Doc |
|---|---|---|---|---|
| A | Repo audit, merged plan | P2 Ph1, P3 Ph1 | ✅ | this file, [implementation-plan.md](implementation-plan.md) |
| B | Account modes: accounts, demo ledger ₹10,000, reset, per-account isolation, mode switch | P2 §4, §23, P3 M15 | ✅ | [accounts.md](accounts.md) |
| C | Execution adapters + router (Paper / RealBroker), unauthorized live-order guards | P2 §4D, §16, P3 M11 | ✅ | [execution.md](execution.md) |
| D | Technical indicators v3 (SMA, EMA100, ROC, OBV, HV/RV, Ichimoku, volume MA, anchored VWAP, divergences, channels) + evidence groups | P3 M2 | ✅ | [technical-analysis.md](technical-analysis.md) |
| E | Candlestick intelligence (full pattern set + context metrics + outcome stats) | P3 M3 | ✅ | same |
| F | Price action / SMC v2 (equal highs/lows, displacement, supply/demand, failed breakout, premium/discount, detection vs confirmation timestamps) | P3 M4 | ✅ | same |
| G | Fundamentals (point-in-time statements, ratios, peers) | P3 M5 | 🟡 engine done, no licensed source | [fundamentals-events.md](fundamentals-events.md) |
| H | News and events (calendar, availability time, event-risk gate) | P3 M6 | 🟡 engine done, no licensed source | [fundamentals-events.md](fundamentals-events.md) |
| I | Options intelligence (IV rank, target-before-stop, expected net, decay/spread risk) | P3 M7 | 🟡 IV rank always null (no IV history stored); no futures basis | [options-intelligence.md](options-intelligence.md) |
| J | Regime probabilities + strategy router v2 | P3 M8 | 🟡 regime confidences are uncalibrated scores; no per-state strategy validation, so no strategy reaches COMPATIBLE | [ai-models.md](ai-models.md) |
| K | Models: logistic, random forest, histogram gradient boosting, calibration, comparison | P3 M9 | ✅ | [ai-models.md](ai-models.md) |
| L | Multi-factor decision engine (evidence, conflicts, NO TRADE reasons, proposal → risk manager) | P3 M10 | ✅ unweighted family vote by default; learned weights fitted and validated but never used on DEMO data | [decision-engine.md](decision-engine.md) |
| M | Backtesting lab (event-driven, costs, walk-forward, Monte Carlo, regime reports) | P2 §19, P3 M14 | ✅ | [backtesting.md](backtesting.md) |
| N | Learning: outcome scoring, error attribution, drift, promotion/rollback | P2 §20, P3 M13 | 🟡 VOLATILITY_ERROR cannot fire (no predicted vol range recorded) | [learning.md](learning.md) |
| O | AI Intelligence Dashboard, Backtesting Lab, Reports screens; mode shown everywhere | P2 §5, P3 M16 | 🟡 dashboard, decision report and active-mode badges done; Reports screens belong to Q; backtests are not persisted, so the dashboard links to the lab | [mobile.md](mobile.md) |
| P | Real broker adapter (broker-neutral, one adapter, OAuth, sync, reconciliation), live locked | P2 §4B, §17, P3 Ph10 | 🟡 interface, INDstocks adapter (token paste, from official docs), encrypted connections, sync snapshots, reconciliation, permissions checklist done and tested on a fake; ⛔ unverified against the live API (no token), live trading locked by design | [broker-integration.md](broker-integration.md) |
| Q | Reports, daily report, CSV, alerts | P2 §21 | ✅ daily report, P&L analytics, CSV exports, stored reports + worker, report/broker alerts, optional Telegram; native CSV export needs a file-sharing module (web only) | [reports.md](reports.md) |
| R | Security, regression, performance tests; deployment docs | P2 Ph9, P3 M17 | ⏳ | [security-testing.md](security-testing.md), [deployment.md](deployment.md) |

## Things this repository cannot do by itself

These are built as code behind interfaces and tested with fakes, never filled with invented data:

- Real INDstocks market data: needs the user's access token.
- Real broker orders/funds: needs a broker app key/secret and the user's login; live stays locked.
- Fundamentals, news, economic calendar: need licensed data sources; the engines accept imported
  point-in-time records and show "no data" otherwise.
- Historical option premiums for credible options backtests: needs a licensed historical source;
  underlying-only backtests are labelled "incomplete proxy".
- A trained, promotable model: needs real (non-simulated) history; models trained on simulated
  data are never promotable.

## Dependency choice

`pandas`, `xgboost` and `lightgbm` are not installed. Models use scikit-learn
(`LogisticRegression`, `RandomForestClassifier`, `HistGradientBoostingClassifier`, which uses
the same histogram-based gradient boosting idea as LightGBM) to avoid new native dependencies.
XGBoost/LightGBM can be added behind the same `ModelSpec` interface later.

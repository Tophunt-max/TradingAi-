# Multi-factor decision engine (work package L)

Code: `backend/app/decision/` (`evidence.py`, `engine.py`, `sizing.py`, `weights.py`), API
`backend/app/api/v1/decision.py`, read-only risk check `RISK.pretrade_dry_run` in
`backend/app/execution/risk.py`. Tests: `backend/tests/test_decision_engine.py`.

The engine turns every analysis layer into one `DecisionReport` with a final decision:
`NO_TRADE`, `WATCH` or `TRADE_PROPOSAL`. It **generates proposals only**. It never places,
reserves or approves an order.

## Endpoint

`GET /api/v1/decision/{NIFTY|BANKNIFTY}?timeframe=5m|15m|1h&side=auto|CE|PE` (auth).

- The report is for the user's **active** account (`account_mode`). `label` is `SIMULATED`
  when the candles are DEMO data.
- `side=auto` tests the side the evidence points to. `side=CE|PE` tests that side even when
  the evidence disagrees (the disagreement then blocks).
- Each call is recorded once per closed bar (see [Recording](#recording)).

## Inputs

| Layer | Source | Role |
|---|---|---|
| Technical analysis | `analysis.pipeline.technical_view` → `indicators.evidence` families | Votes: `trend`, `momentum`, `volume` (one vote each) |
| Candlesticks / chart patterns | `analysis.patterns.all_patterns` | Vote `candles`: CONFIRMED patterns of the last 5 bars only |
| Price action / SMC v2 | `analysis.smc.analyse` | Vote `structure`: latest CONFIRMED BOS / CHOCH / liquidity sweep / failed breakout of the last 10 bars |
| AI prediction | `registry.serving` (PROMOTED only), drift guard | Vote `model` when P(UP) − P(DOWN) is beyond ±0.10; otherwise no vote. Null without a promoted model |
| Regime v2 | `analysis.regime.detect_v2` | Gate: `UNKNOWN`, `ABNORMAL`, `EVENT_UNCERTAINTY` block |
| News / events | `events.store.gate_for` | Gate: `NO_TRADE` (HIGH event) blocks; `REDUCE_EXPOSURE` caps sizing at 1 lot |
| Fundamentals | `fundamentals.service.build` | Context only for intraday options. `NO_DATA` and `INDEX` are allowed |
| Derivatives analytics | `options.intelligence.analyse` for the proposed side | The four separate outputs, liquidity / risk flags, eligibility |
| Liquidity | Same candidate: `NO_BID_ASK`, `WIDE_SPREAD`, `LOW_OI`, `LOW_VOLUME`, … | Blocking flags make the contract ineligible; the others are warnings |
| Execution costs | `expected_net` of the candidate (spread, slippage, statutory charges) | Must be > 0 |
| Risk constraints | `RISK.pretrade_dry_run` against the user's limits and active account | Must pass |
| Data quality | Candle quality report | `FAIL` blocks; `WARN` is listed |

Volatility, regime, fundamentals, events, liquidity and costs never vote. They are gates or
context, listed under `evidence.context`.

## Evidence families and the combination rule

1. Indicators inside a technical family are already collapsed to one vote (sign of the sum of
   member votes, `indicators.evidence.family_votes`). Two correlated momentum indicators are
   one momentum vote.
2. Families linked by a **cross-family** correlated indicator pair (|ρ| > 0.8 over the last 100
   bars, from `technical.correlated_pairs`) are merged into one group. The group casts one vote,
   the sign of its members' sum. In DEMO data trend, momentum and volume usually merge into one
   group, so they count once.
3. A direction passes when it holds a **strict majority of the available groups** (neutral
   groups count against it) **and** at least 3 groups are available.

The result is labelled `weights: "UNWEIGHTED_UNVALIDATED"`. The full vote table, groups and
reasons are in `evidence.combination`.

Supporting and conflicting evidence lists contain one item per family vote (and per pattern /
structure event): `source`, `family`, `statement`, `value`, `timestamp`, `vote`. Unconfirmed
patterns are listed under `evidence.ignored` and never vote.

## Weighting policy

The specification says not to use arbitrary weighted scores without validation, and to learn
or calibrate weights only on chronological training and validation data. So:

- **Default:** the unweighted family vote above.
- **Learned weights:** `weights.fit_evidence_weights` builds a causal vote matrix
  (`weights.vote_matrix`: family votes at each bar using only data known at that bar; label
  UP = close[i + h] > close[i]) and fits a logistic regression (`app.ai.model.fit`, binary)
  on purged, expanding-window walk-forward folds (`app.ai.model.walk_forward`, purge = horizon).
  The out-of-sample error rate is compared with the unweighted rule on the same rows (a tie of
  the unweighted rule scores 0.5) with a paired moving-block bootstrap (block = horizon).
- Learned weights are **used only if** the bootstrap 95% CI shows they beat the unweighted rule,
  their log loss beats the base rate, the training data is non-DEMO **and** the live data is
  non-DEMO. In this environment all data is DEMO, so learned weights are never used, and the
  report says why (`evidence.weights.learned_status`).
- Storage: process memory only (no table, no migration). `POST /decision/admin/weights/fit`
  (admin, audited) fits and stores; `GET /decision/weights/status` shows the latest fit. A
  restart clears it and the engine falls back to the unweighted rule.

## Gates and decision states

Blocking items (any one → `NO_TRADE`):

- data quality `FAIL`;
- regime v2 in a no-trade state (`UNKNOWN`, `ABNORMAL`, `EVENT_UNCERTAINTY`);
- event gate `NO_TRADE`;
- conflicting evidence (bullish and bearish groups both present without a majority, or the
  tested side is outvoted);
- no eligible option contract, or an ineligible one (with its blocked reasons), or an option
  chain that fails quality checks;
- modelled expected net after costs ≤ 0;
- sizing gives 0 lots (one lot risks more than the risk-per-trade budget);
- the risk dry run fails (kill switch, daily loss stop, profit lock, entries, positions, lots,
  planned risk, funds, session, expiry, quote);
- the account cannot trade (REAL while live trading is locked).

Non-blocking missing items (→ `WATCH` when nothing blocks): the evidence majority is not reached
without a conflict (mostly neutral, or fewer than 3 groups), no option chain, or expected net not
modelled (no IV / mid).

`TRADE_PROPOSAL` only when nothing blocks and nothing is missing. A model probability alone
never yields a proposal: the model is one vote among the groups, and every gate still applies.

## Directional view and setup

- `directional_view.probabilities` come from the promoted model only; otherwise `null` with
  `probability_reason`. The evidence direction is not a probability.
- Setup classification (first match wins): `BREAKOUT` (breakout regime, or a fresh BOS in the
  direction outside a strong trend), `TREND_CONTINUATION` (strong trend regime in the
  direction), `REVERSAL` (reversal regime or a CHOCH in the direction), `RANGE_FADE` (range /
  low-volatility regime with price in the discount half for CE or premium half for PE), else
  `NONE`.

## Levels, sizing and expected outcome

- Entry conditions: underlying within ±0.1 ATR of the last close, buy at or below the ask with a
  fresh two-sided quote, sizing within budget, all risk checks at order time.
- Invalidation: the SMC event's invalidation level, the swing stop of `pipeline._setup`, the
  confirmed patterns' opposite extremes, a regime change to a no-trade state, a data failure.
- Stop-loss and target: 20% / 30% of premium (the paper-order defaults) plus the underlying
  invalidation level and targets.
- Sizing (`decision/sizing.py`) uses the execution formula: per-lot risk = (entry − stop) × lot
  + round-trip charges (`execution.charges.round_trip`), stop floored to the tick like an
  order's stop. Lots = floor(budget / per-lot risk), capped by max lots per trade and the manual
  endpoint's 10-lot limit, and at 1 lot when the event gate says reduce exposure.
- Expected outcome: the candidate's `expected_net` (MODEL_ESTIMATE): expected net per lot,
  costs, net quantiles, P(target first), P(stop first) and the simulation assumptions.
- Risk / reward: premium (target − entry) / (entry − stop) and the underlying 1R / 2R levels.

## Read-only risk dry run

`RISK.pretrade_dry_run` runs the same checks as `RISK.pretrade` through a shared `_evaluate`
function, but reads the daily state with `peek_daily_state` (no row is created), takes no lock,
and creates no order or reservation. `pretrade` itself is unchanged in behaviour. When the
proposal has 0 lots, 1 lot is checked so the user still sees every limit.

## Proposal → user confirmation → risk manager

1. The report's `proposal.paper_order_request` is a ready body for `POST /api/v1/orders/paper`
   (`underlying, option_type, expiry, strike, lots, stop_loss_pct, target_pct`).
2. The app shows a "Review paper order" button only for a `TRADE_PROPOSAL` in the DEMO account.
   It opens a confirmation dialog that states the account mode.
3. After confirmation the app sends the body with a fresh idempotency key (created when the
   dialog opens). The existing endpoint is the only execution path; the risk manager
   re-checks every limit then and may reject.
4. REAL account: `execution.available` is false with the live-trading blockers, so the report is
   `NO_TRADE` and there is no proposal.

## Recording

`learning.recorder.record_decision` (additive) writes one `decision_records` row per closed bar
with `origin = DECISION`, `origin_ref = "<account_mode>:<side param>"`, `decision` =
`TRADE_PROPOSAL | WATCH | NO_TRADE`, reasons (no-trade + watch), votes, setup and the plan
(side, sizing, proposal). A repeat call on the same bar returns `recorded:
ALREADY_RECORDED_FOR_BAR` and the existing id. The learning worker resolves outcomes as for
other records.

## Limitations

- All data in this environment is DEMO, so reports are `SIMULATED`, learned weights are never
  used, and DEMO option costs (₹40+ charges on a 1-unit placeholder lot) make expected net
  negative: DEMO reports end as `NO_TRADE` or `WATCH`.
- Regime confidences are uncalibrated scores; regime v2 is a gate, not a probability.
- The 3-group minimum and the ±0.10 model edge are fixed design choices, not fitted values.
- Learned weights are not persisted; a restart forgets them.
- Fundamentals and events depend on imported data; absence of data is not absence of risk.
- Option premiums are a Monte Carlo estimate (`MODEL_ESTIMATE`), not history.

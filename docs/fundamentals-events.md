# Fundamentals and market events (work packages G, H)

Status: engine, storage, import and API are implemented and tested. **No licensed fundamentals,
news or economic-calendar source is connected.** Until an admin imports data, every stock
returns `status: NO_DATA` and the event list contains only the derived calendar (expiries and
configured holidays).

Code:

| Area | Files |
|---|---|
| Tables | `backend/app/models/intelligence.py`, migration `backend/migrations/versions/20261029_0008_intelligence.py` |
| Ratios (pure) | `backend/app/fundamentals/ratios.py` |
| Point-in-time storage, import validation | `backend/app/fundamentals/store.py` |
| Payload assembly (peers, history, index view) | `backend/app/fundamentals/service.py` |
| Event import, novelty, calendar | `backend/app/events/store.py` |
| Event-risk gate (pure) | `backend/app/events/risk.py` |
| API | `backend/app/api/v1/fundamentals.py`, `backend/app/api/v1/events.py` |
| CLI importers | `scripts/import_fundamentals.py`, `scripts/import_events.py` |
| Tests | `backend/tests/test_fundamentals.py`, `backend/tests/test_events.py` |

All test data uses fictional symbols (`TESTCO`, `PEERA`, `PEERB`, `TESTIDX`). The repository ships
no real company numbers or news.

## Data model

Rows are append-only. A restatement or an event update is a new row, never an `UPDATE` of an
old one. The one exception is `company_profiles`, see below.

### `fundamental_reports`

One version of one period's statement.

| Column | Type | Notes |
|---|---|---|
| `symbol` | str(32) | Upper-case; letters, digits, `- & _ .` |
| `period_end` | date | |
| `period_type` | `Q` \| `FY` | |
| `value_basis` | `REPORTED` \| `ESTIMATED` \| `ADJUSTED` \| `RESTATED` | See [value bases](#value-bases) |
| `currency` | str(3) | Default `INR` |
| 18 line items | Numeric(24,4), nullable | `revenue, gross_profit, operating_income, net_income, eps, operating_cash_flow, capex, depreciation_amortization, total_debt, interest_expense, equity, capital_employed, total_assets, current_assets, current_liabilities, shares_outstanding, dividends_per_share, cash` |
| `source`, `source_ref` | str | `source` required |
| `published_at` | datetime | When the statement became public |
| `available_at` | datetime | When this system received it (defaults to import time) |
| `revision` | int | 0 for the first row of a period, then +1 per new version |
| `quality_flags` | JSON list | Computed at import |
| `created_at` | datetime | |

Unique on `(symbol, period_end, period_type, value_basis, revision)`. Index on
`(symbol, available_at)`.

A missing line item is stored as `NULL`, never as zero.

### `company_profiles`

`symbol` (unique), `name`, `sector`, `industry`, `peer_group`, `is_index`, `constituents`
(JSON list of `{symbol, weight}` with weight in (0, 1]), `source`, `available_at`, `updated_at`.

A profile is **upserted**: re-importing a symbol overwrites the row, including `available_at`.
Profiles are therefore not versioned (see [limitations](#limitations--not-implemented)).

### `corporate_actions`

`symbol`, `action_type` (`SPLIT | BONUS | DIVIDEND | RIGHTS`), `ex_date`, `record_date`,
`ratio_from`, `ratio_to`, `amount`, `announced_at`, `available_at`, `source`, `source_ref`.
Unique on `(symbol, action_type, ex_date, source)`; a second import of the same key is counted
as `actions_unchanged` and not stored.

Corporate actions are stored and listed in the fundamentals payload. They are **not** applied to
prices or per-share figures.

### `market_events`

| Column | Notes |
|---|---|
| `category` | See [event categories](#event-categories) |
| `scope` | `INSTRUMENT` \| `SECTOR` \| `INDEX` \| `MARKET` |
| `symbols` | JSON list of instruments, sectors or indices (upper-cased, sorted, de-duplicated). Required unless `scope = MARKET` |
| `title`, `summary`, `url` | `url` must be http(s) |
| `source`, `source_reliability` | `OFFICIAL` \| `LICENSED_VENDOR` \| `OTHER` |
| `event_time` | When the event happens |
| `published_at`, `available_at` | As for reports |
| `expected_value`, `actual_value`, `unit` | Optional (macro releases) |
| `importance` | `LOW` \| `MEDIUM` \| `HIGH`, supplied by the importer |
| `direction_hint` | `UP` \| `DOWN` \| `MIXED`, optional; requires `uncertainty_note` |
| `novelty` | `NEW` \| `UPDATE` \| `DUPLICATE` (computed) |
| `supersedes_id` | Earlier version this row updates or duplicates |
| `fingerprint`, `content_hash` | SHA-256, see [novelty](#novelty-and-de-duplication) |

Events are never generated from sentiment or text analysis. `importance` and `direction_hint`
come from the import, not from the system.

## Point-in-time rule

At a decision time `ts`, a report is visible only when **both** `published_at <= ts` and
`available_at <= ts` (`store.as_of`). The second condition matters for back-dated data: a
statement published in May but loaded in July is invisible to a May decision.

For each `(period_end, period_type, series)` the visible row with the highest `revision` wins.
The series are:

| `value_basis` | Series |
|---|---|
| `REPORTED`, `RESTATED` | `ACTUAL` (a restatement is a later revision of the same series) |
| `ADJUSTED` | `ADJUSTED` |
| `ESTIMATED` | `ESTIMATE` |

`revision` is `max(revision of every earlier row for that symbol/period/period_type) + 1`,
across all series. An import whose line items equal an existing row of the same series
(compared at 4 decimal places) is skipped as `reports_unchanged`. A changed row in an existing
series is counted as a restatement and carries the flag `REVISION_OF_REVISION_<n>`.

Example from the tests: TESTCO's quarter ending 2025-03-31 is reported on 2025-05-10 (net income
120, revision 0) and restated on 2025-07-29 (net income 90, revision 1). Both rows are kept.
`as_of(2025-06-01)` returns the original, `as_of(2025-08-01)` returns the restatement, and the
payload then adds the flag `RESTATED: at least one period has a later revision; latest is used`.
Re-importing the original five quarters returns `reports_unchanged: 5`.

The same rule applies to everything the payload uses:

- profiles: `available_at <= ts`;
- peers: profile `available_at <= ts`, and each peer's reports through `as_of(ts)`;
- corporate actions: `announced_at <= ts` and `available_at <= ts`;
- price: the live quote only if `ts` is within 5 minutes of now, otherwise the last daily close
  at or before `ts` (from the latest 400 daily candles);
- events: `available_at <= ts`.

Import validation rejects timestamps without a timezone, any `published_at`, `announced_at` or
`available_at` in the future, and `available_at` earlier than the publication time. The API
rejects `as_of` in the future with 422 `INVALID_AS_OF`.

## Value bases

| Basis | Used for |
|---|---|
| `REPORTED` | Ratios, TTM, valuation, coverage flags |
| `RESTATED` | Same as `REPORTED` (later revision wins) |
| `ESTIMATED` | Only forward P/E, and only for estimate periods ending after the latest actual period. Never mixed into actuals |
| `ADJUSTED` | Stored and returned in `periods`, **not used** in any ratio |

## Ratios

All ratios come from `ratios.py`. A ratio is `null` when any input is missing or a denominator is
zero. Nothing is imputed. Values are rounded to 4 decimals (valuation multiples to 2).

Definitions: `growth(cur, prev) = (cur − prev) / |prev|`; `div(a, b) = a / b`.

### Per-period ratios (latest actual period)

| Field | Formula |
|---|---|
| `revenue_growth_yoy` | growth(revenue, revenue of the same period type ending 365 ± 20 days earlier) |
| `revenue_growth_qoq` | growth(revenue, revenue of the quarter ending 91 ± 20 days earlier); quarters only |
| `eps_growth_yoy` | growth(eps, eps a year earlier) |
| `gross_margin` | gross_profit / revenue |
| `operating_margin` | operating_income / revenue |
| `net_margin` | net_income / revenue |
| `operating_cash_flow` | as reported (string) |
| `free_cash_flow` | operating_cash_flow − capex (capex is a positive outflow) |
| `debt_to_equity` | total_debt / equity |
| `interest_coverage` | operating_income / interest_expense |
| `working_capital` | current_assets − current_liabilities |
| `current_ratio` | current_assets / current_liabilities |
| `cash_conversion` | operating_cash_flow / net_income, only when net_income > 0 |
| `accruals_ratio` | (net_income − operating_cash_flow) / total_assets |
| `earnings_quality` | `LOW` if accruals_ratio > 0.10, else `OK` |

### Twelve-month base

`ttm()` needs the last four quarters to be consecutive (each gap 70–110 days). Flow items
(`revenue, gross_profit, operating_income, net_income, eps, operating_cash_flow, capex,
depreciation_amortization, interest_expense, dividends_per_share`) are summed, and only if all
four quarters have the item. Balance-sheet items come from the latest quarter. Without four
consecutive quarters the latest `FY` statement is used; without that, the base is `null`.
`ttm_basis` describes which was used.

| Field | Formula (on the twelve-month base) |
|---|---|
| `roe` | net_income / equity |
| `roce` | operating_income / capital_employed |

### Valuation

| Field | Formula | Null when |
|---|---|---|
| `pe` | price / eps | eps ≤ 0 (note added) |
| `pb` | price / (equity / shares_outstanding) | equity ≤ 0 or no shares |
| `ps` | (price × shares_outstanding) / revenue | |
| `ev_ebitda` | (price × shares + total_debt − cash) / (operating_income + depreciation_amortization) | no D&A line (note added), no debt or cash, EBITDA ≤ 0 |
| `dividend_yield` | dividends_per_share / price | |
| `forward_pe` | price / eps of the nearest future `ESTIMATED` period, with `forward_pe_basis` naming the estimate and source | no estimate or estimate eps ≤ 0 |

Valuation needs a price. If the price comes from DEMO market data it is labelled `SIMULATED`
and the payload says the multiples are illustrative only.

## Peer and historical comparison

**Sector peers** (`peers` in the payload): all non-index profiles with the same `sector`, visible
at `ts`, excluding the symbol itself. For `operating_margin`, `net_margin`, `revenue_growth_yoy`
and `debt_to_equity` the payload gives the symbol's percentile rank among peers that have the
metric. `pe` is ranked the same way, with each peer's own point-in-time price (`pe_peers` is the
number of peers with a P/E). `count` is the number of peers with any ratios.

Percentile rank = (number of values strictly below x + 0.5 × number of ties) / n.

**Own history** (`history`): for each earlier actual period, the trailing P/E recomputed with only
the data available at that period's `available_at` and the daily close at that time. The current
P/E's percentile rank in that list is `pe_percentile`; `points` is the list length.

The explanation lists supporting and contradicting factors, each with its number:

| Factor | Supporting | Contradicting |
|---|---|---|
| Revenue growth YoY, operating margin, EPS growth YoY | > 0 | ≤ 0 |
| Debt/equity | ≤ 1.5 | > 1.5 |
| Interest coverage | ≥ 3 | < 3 |
| Cash conversion | ≥ 0.8 | < 0.8 |
| Earnings quality | | `LOW` |
| ROE | ≥ 0.12 | < 0.12 |
| P/E peer percentile | < 0.25 ("cheap relative to peers") | > 0.75 ("expensive relative to peers") |

A fixed context line always says that for intraday options, fundamentals are context and
event-risk inputs only.

## Quality flags

### Inconsistent statements (computed per statement at import)

| Flag | Rule |
|---|---|
| `NEGATIVE_REVENUE` | revenue < 0 |
| `GROSS_PROFIT_EXCEEDS_REVENUE` | gross_profit > revenue |
| `OPERATING_INCOME_EXCEEDS_GROSS_PROFIT` | operating_income > gross_profit |
| `NET_INCOME_EXCEEDS_REVENUE` | revenue > 0 and net_income > revenue |
| `EPS_SIGN_MISMATCH` | net_income and eps both non-zero with different signs |
| `EPS_INCONSISTENT_WITH_NET_INCOME` | \|net_income / shares − eps\| / \|eps\| > 0.25 |
| `CURRENT_ASSETS_EXCEED_TOTAL_ASSETS` | current_assets > total_assets |
| `NEGATIVE_CURRENT_LIABILITIES` | current_liabilities < 0 |

The payload shows these for the last 8 actual periods, suffixed with the period.

### Missing and stale data (computed at `ts`)

| Flag | Rule |
|---|---|
| `NO_ACTUAL_STATEMENTS` | only estimates are visible |
| `STALE: latest period … is N days old (limit L)` | `ts` date − latest actual period_end > `FUNDAMENTALS_STALE_DAYS` (default **150**, allowed 30–730) |
| `MISSING_QUARTER between A and B` | consecutive actual quarters more than 110 days apart |
| `ONLY_<n>_QUARTERS` | fewer than 4 actual quarters (TTM ratios need 4) |
| `RESTATED: …` | any visible actual period has revision > 0 |

## Statuses

| `status` | When | Content |
|---|---|---|
| `OK` | at least one visible report | periods, ratios, valuation, peers, history, flags, explanation |
| `NO_DATA` | stock with no visible report | `flags: ["NO_DATA"]`, all ratio fields `null`, message: no licensed source is configured and the system never fabricates statements |
| `INDEX` | symbol in `NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY, BANKEX`, or profile `is_index = true` | Message that index context comes from index, constituent, breadth and macro data. If the profile has constituents with weights, `constituents` gives `listed`, `weight_with_data` and weight-averaged `revenue_growth_yoy, operating_margin, net_margin, debt_to_equity` over constituents that have data at `ts`. Without a constituent list, `constituents: null` and the message says the weighted view is unavailable |

## Import format

Imports go through `POST /api/v1/admin/fundamentals/import` (admin, audited as
`fundamentals.imported`) or the CLI, which applies the same validation:

```bash
python scripts/import_fundamentals.py data.json            # JSON body as below
python scripts/import_fundamentals.py reports.csv --dry-run  # one report per row; empty cell = missing
```

Body: `{"reports": [...], "profiles": [...], "corporate_actions": [...]}`, up to 5000 reports,
2000 profiles and 5000 actions. Unknown fields are rejected. Shape copied from the tests
(fictional numbers):

```json
{
  "reports": [{
    "symbol": "TESTCO", "period_end": "2025-03-31", "period_type": "Q",
    "value_basis": "REPORTED", "source": "TEST_FIXTURE",
    "published_at": "2025-05-10T00:00:00+00:00", "available_at": "2025-05-10T02:00:00+00:00",
    "revenue": 1200, "gross_profit": 480, "operating_income": 240, "net_income": 120,
    "eps": 1.2, "operating_cash_flow": 132, "capex": 30, "total_debt": 400,
    "interest_expense": 10, "equity": 1000, "capital_employed": 1400, "total_assets": 2000,
    "current_assets": 700, "current_liabilities": 400, "shares_outstanding": 100,
    "dividends_per_share": 0.5, "cash": 150
  }],
  "profiles": [{"symbol": "TESTCO", "sector": "FICTIONAL_SECTOR", "source": "TEST_FIXTURE",
                "available_at": "2024-01-01T00:00:00Z"}],
  "corporate_actions": [{"symbol": "TESTCO", "action_type": "DIVIDEND", "ex_date": "2025-02-10",
                         "amount": 0.5, "announced_at": "2025-01-20T10:00:00+05:30",
                         "available_at": "2025-01-20T12:00:00+05:30", "source": "TEST_FIXTURE"}]
}
```

Field rules: `capex, depreciation_amortization, total_debt, interest_expense, total_assets,
current_assets, dividends_per_share, cash ≥ 0`; `shares_outstanding > 0`; constituent weights in
(0, 1]. Response: `{reports_added, reports_unchanged, restatements, profiles_upserted,
actions_added, actions_unchanged}`.

Events: `POST /api/v1/admin/events/import` (admin, audited as `events.imported`) or
`python scripts/import_events.py events.json [--dry-run]`. Body `{"events": [...]}`, up to 5000:

```json
{"events": [{
  "category": "EARNINGS", "scope": "INSTRUMENT", "symbols": ["TESTCO"],
  "title": "TESTCO quarterly results (fictional)", "source": "TEST_FIXTURE",
  "source_reliability": "OFFICIAL", "event_time": "2025-06-10T09:30:00+00:00",
  "published_at": "2025-06-07T09:00:00+00:00", "importance": "HIGH"
}]}
```

Response: `{NEW, UPDATE, DUPLICATE, ids}` (the CLI and audit log omit `ids`). Re-importing the
same file is safe.

## Event categories

`EARNINGS, CORPORATE_ANNOUNCEMENT, CORPORATE_ACTION, REGULATORY, MONETARY_POLICY, INFLATION,
EMPLOYMENT, GDP, EXPIRY, HOLIDAY, OTHER`.

## Novelty and de-duplication

- `fingerprint = sha256(category | sorted symbols | event_time (UTC ISO) | normalised title)`.
  The title is lower-cased, whitespace collapsed and everything except `a–z 0–9 space` removed,
  so "TESTCO Quarterly Results" and "testco quarterly results" match.
- `content_hash = sha256(summary | expected_value | actual_value | unit | importance |
  direction_hint | uncertainty_note)`.

| Case | Result |
|---|---|
| No earlier row with this fingerprint | `NEW` |
| Same fingerprint and same content hash as an earlier non-duplicate row | `DUPLICATE`, `supersedes_id` = that row |
| Same fingerprint, different content | `UPDATE`, `supersedes_id` = latest earlier non-duplicate row |

When reading (`risk.visible`), `DUPLICATE` rows are dropped and any row superseded by a visible
`UPDATE` is replaced by it. An update only replaces the original once the update's own
`available_at <= ts`.

## Event calendar

`GET /events` returns two lists:

| List | Source | Derived or imported |
|---|---|---|
| `events` | `market_events` rows known now (`available_at <= now`), `event_time` in `[from, to]` (default now − 7 days to now + 30 days) | Imported only |
| `calendar` | Option expiries for every `OPTION_UNDERLYINGS` entry from the market-data provider, plus dates in `MARKET_HOLIDAYS` | Derived |

Expiries are labelled with the provider source; DEMO expiries are labelled `SIMULATED`.
Holidays are labelled `CONFIGURED`; invalid dates in the setting are skipped. No holiday list is
bundled. Monetary policy, results, inflation, GDP and employment dates are **never generated**:
they appear only if imported.

## Event-risk gate

`events/risk.py`, called from `GET /analysis/{symbol}` and `GET /events/risk`.

1. Only events with `available_at <= ts`, after duplicate/supersession handling.
2. Relevant if `scope = MARKET`, or `symbols` contains the instrument, its underlying index, or
   the instrument's sector (from the imported profile).
3. In window if `ts − after ≤ event_time ≤ ts + before`.
   `EVENT_RISK_BEFORE_MINUTES` default **120**, `EVENT_RISK_AFTER_MINUTES` default **60**
   (each 0 to 10080).
4. Level and action:

| Highest importance in window | `level` | `action` |
|---|---|---|
| `HIGH` | `HIGH` | `NO_TRADE` |
| `MEDIUM` | `ELEVATED` | `REDUCE_EXPOSURE` |
| `LOW` or none | `NONE` | `NONE` (LOW events are listed only) |

Response: `level, action, reasons, events (id, category, scope, title, importance, event_time,
source, source_reliability, novelty), window {before_minutes, after_minutes}, as_of`. Each reason
states importance, category, title, minutes until/since and source. With no event the reason
says only imported events are known and absence of data is not absence of risk.

In the analysis pipeline the gate is additive: `NO_TRADE` adds the abstain reason
"Event-risk gate: HIGH-importance event in the window (NO TRADE).", `REDUCE_EXPOSURE` adds a
reduce-exposure reason. It never relaxes a NO TRADE. The level is also passed to regime v2
(`HIGH → HIGH`, `ELEVATED → MEDIUM`), see [ai-models.md](ai-models.md#regime-detection-v2). The
analysis decision record stores the level in `event_risk`.

### Lookahead protection

- Every read filters on `available_at <= ts` (and `published_at <= ts` for reports, `announced_at
  <= ts` for actions).
- An update or restatement loaded later is invisible to earlier decisions.
- Future timestamps are rejected at import and in `as_of`.
- Tests: `test_restatement_published_later_is_invisible_earlier`,
  `test_available_at_gates_visibility_even_after_publication`,
  `test_gate_has_no_lookahead_and_handles_supersession`.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/fundamentals/{symbol}?as_of=` | ✓ | `FundamentalsPayload`: `symbol, as_of, status, message, profile, periods, ratios, returns, ttm_basis, valuation, price {value, basis, label}, peers, history, constituents, corporate_actions, flags, explanation {supporting, contradicting, context}` |
| POST | `/api/v1/admin/fundamentals/import` | admin | Body and response above |
| GET | `/api/v1/events?symbol=&from=&to=&category=` | ✓ | `{as_of, events, calendar, note}` |
| GET | `/api/v1/events/risk?symbol=&underlying=` | ✓ | Gate result at now |
| POST | `/api/v1/admin/events/import` | admin | Body and response above |

## Limitations / not implemented

- No licensed fundamentals, news or economic-calendar feed is connected. Production shows
  `NO_DATA` for stocks and an imported-events list that is empty until someone imports data.
- `company_profiles` rows are overwritten on re-import. A sector change replaces the old
  classification for past `ts` too, so peer groups are not fully point-in-time.
- `ADJUSTED` statements are stored but not used in any ratio.
- `revision` is counted across all series of a period. If an estimate is imported before the
  reported figures, the reported row gets revision 1 and the payload shows the `RESTATED` flag
  even though no restatement happened.
- Corporate actions are listed only; splits and bonuses are not applied to EPS, shares or
  price history.
- The event fingerprint includes `event_time`, so a rescheduled event is stored as a new `NEW`
  event and the original stays active unless it is updated separately.
- The gate fetches only events whose `event_time` is inside the window, so an `UPDATE` that moves
  an event out of the window does not supersede the original inside it.
- `importance` and `direction_hint` are taken from the import as given; the system does not
  score news or measure surprise (`expected_value` vs `actual_value` is stored, not used).
- The gate is applied in the analysis endpoint. The bot engine does not call it.
- Index view needs an imported constituent list with weights; breadth and index-level macro
  data are not available.
- `as_of` historical prices come from the latest 400 daily candles of the provider, so P/E for
  older dates is `null` when that history is not available.

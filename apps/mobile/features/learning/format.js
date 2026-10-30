// Pure helpers for the self-learning, fundamentals and events views.

export const DRIFT_TONE = { STABLE: 'positive', MODERATE: 'warning', DEGRADED: 'negative', INSUFFICIENT_DATA: 'neutral', NOT_COMPUTED: 'neutral' };
export const RISK_TONE = { NONE: 'positive', ELEVATED: 'warning', HIGH: 'negative' };
export const DECISION_TONE = { NO_TRADE: 'neutral', SKIPPED: 'warning', SETUP_CANDIDATE: 'info', SIGNAL: 'primary', TRADE_PROPOSAL: 'positive' };

export const ERROR_LABELS = {
  DATA_QUALITY: 'Data quality',
  WRONG_DIRECTION: 'Wrong direction',
  WRONG_TIMING: 'Wrong timing',
  REGIME_CHANGE: 'Regime change',
  VOLATILITY_ERROR: 'Volatility forecast',
  PATTERN_MISCLASSIFICATION: 'Pattern invalidated',
  OPTION_PRICING: 'Option pricing / IV / theta',
  EXECUTION_SLIPPAGE: 'Execution slippage',
  RISK_ORDER_MANAGEMENT: 'Risk / order management',
};

/** 0.1234 → '12.3%'; null-safe. */
export function pct(v, digits = 1) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—';
  return `${(Number(v) * 100).toFixed(digits)}%`;
}

/** Reliability table rows: predicted vs realized frequency with the gap. */
export function reliabilityRows(calibration) {
  return (calibration ?? []).map((b) => ({
    key: b.bucket,
    bucket: b.bucket,
    predicted: pct(b.predicted, 0),
    realized: pct(b.realized, 0),
    count: b.count,
    gap: Math.round((b.realized - b.predicted) * 100),
  }));
}

/** Error classes sorted by count (zeros last), with readable labels and shares. */
export function errorClassRows(counts) {
  const entries = Object.entries(counts ?? {});
  const total = entries.reduce((s, [, n]) => s + n, 0);
  return entries
    .map(([key, n]) => ({ key, label: ERROR_LABELS[key] ?? key, count: n, share: total ? n / total : 0 }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/** One-line summary of a decision record. */
export function decisionLine(d) {
  const probs = d.probabilities
    ? Object.entries(d.probabilities).map(([k, v]) => `${k[0]}${Math.round(v * 100)}`).join(' ')
    : 'no probabilities';
  const outcome =
    d.outcome_status === 'RESOLVED'
      ? `${d.realized_direction ?? '—'} ${d.realized_return === null ? '' : pct(d.realized_return, 2)}`.trim()
      : d.outcome_status.toLowerCase();
  return `${d.decision.replace('_', ' ')} · ${probs} · ${outcome}`;
}

/** Card state for a fundamentals payload. */
export function fundamentalsView(f) {
  if (!f) return { kind: 'loading' };
  if (f.status === 'NO_DATA') return { kind: 'empty', title: 'No fundamentals imported', message: f.message };
  if (f.status === 'INDEX') {
    const c = f.constituents;
    return {
      kind: 'index',
      title: 'Index: no company statements',
      message: f.message,
      rows: c
        ? Object.entries(c.weighted_ratios).map(([k, v]) => ({ label: RATIO_LABELS[k] ?? k, value: k === 'debt_to_equity' ? Number(v).toFixed(2) : pct(v) }))
        : [],
      coverage: c ? `Constituent weight with data: ${pct(c.weight_with_data, 0)}` : null,
    };
  }
  const r = f.ratios ?? {};
  const v = f.valuation ?? {};
  const rows = [
    ['revenue_growth_yoy', pct(r.revenue_growth_yoy)],
    ['operating_margin', pct(r.operating_margin)],
    ['net_margin', pct(r.net_margin)],
    ['debt_to_equity', r.debt_to_equity === null || r.debt_to_equity === undefined ? '—' : r.debt_to_equity.toFixed(2)],
    ['roe', pct(f.returns?.roe)],
    ['pe', v.pe === null || v.pe === undefined ? '—' : v.pe.toFixed(1)],
    ['ev_ebitda', v.ev_ebitda === null || v.ev_ebitda === undefined ? 'n/a' : v.ev_ebitda.toFixed(1)],
  ].map(([k, value]) => ({ label: RATIO_LABELS[k], value }));
  return {
    kind: 'ok',
    title: `Latest ${r.period_type ?? ''} ${r.period_end ?? ''}`.trim(),
    rows,
    priceLabel: f.price?.label,
    flags: f.flags ?? [],
    supporting: f.explanation?.supporting ?? [],
    contradicting: f.explanation?.contradicting ?? [],
    context: f.explanation?.context ?? [],
  };
}

export const RATIO_LABELS = {
  revenue_growth_yoy: 'Revenue growth (YoY)',
  operating_margin: 'Operating margin',
  net_margin: 'Net margin',
  debt_to_equity: 'Debt / equity',
  roe: 'Return on equity',
  pe: 'P/E (trailing 4Q)',
  ev_ebitda: 'EV / EBITDA',
};

/** Merge imported events and calendar entries into one date-sorted list. */
export function calendarItems(events, calendar) {
  const ev = (events ?? []).map((e) => ({
    key: `e-${e.id}`,
    date: String(e.event_time).slice(0, 10),
    ts: e.event_time,
    title: e.title,
    category: e.category,
    label: e.source_reliability,
    importance: e.importance,
    source: e.source,
    imported: true,
  }));
  const cal = (calendar ?? []).map((c) => ({
    key: `c-${c.category}-${c.date}-${(c.symbols ?? []).join(',')}`,
    date: c.date,
    ts: null,
    title: c.title,
    category: c.category,
    label: c.label,
    importance: null,
    source: c.source,
    imported: false,
  }));
  return [...ev, ...cal].sort((a, b) => (a.date === b.date ? a.title.localeCompare(b.title) : a.date < b.date ? -1 : 1));
}

// Pure helpers for the Backtesting Lab (no React): request building, formatting, sparkline.

export const DEFAULT_FORM = {
  strategyId: 'momentum',
  underlying: 'NIFTY',
  side: 'CE',
  timeframe: '5m',
  bars: '1000',
  stopPct: '0.3',
  targetPct: '0.6',
  spreadBps: '2',
  slippageTicks: '1',
  brokerage: '20',
};

const LIMITS = {
  bars: [150, 2000, true, 'Bars'],
  stopPct: [0.05, 20, false, 'Stop %'],
  targetPct: [0.05, 20, false, 'Target %'],
  spreadBps: [0, 200, false, 'Spread (bps)'],
  slippageTicks: [0, 20, true, 'Slippage (ticks)'],
  brokerage: [0, 100, false, 'Brokerage per order'],
};

/** Form state (strings) -> { body, errors }. errors maps field -> message. */
export function buildRunRequest(form) {
  const errors = {};
  const num = {};
  for (const [key, [min, max, integer, label]] of Object.entries(LIMITS)) {
    const raw = String(form[key] ?? '').trim();
    const n = Number(raw);
    if (raw === '' || !Number.isFinite(n)) errors[key] = `${label} must be a number.`;
    else if (integer && !Number.isInteger(n)) errors[key] = `${label} must be a whole number.`;
    else if (n < min || n > max) errors[key] = `${label} must be between ${min} and ${max}.`;
    else num[key] = n;
  }
  if (Object.keys(errors).length) return { body: null, errors };
  return {
    errors,
    body: {
      strategy_id: form.strategyId,
      underlying: form.underlying,
      side: form.side,
      timeframe: form.timeframe,
      bars: num.bars,
      fidelity: 'UNDERLYING_PROXY',
      stop: { type: 'PERCENT', value: num.stopPct },
      target: { type: 'PERCENT', value: num.targetPct },
      costs: { spread_bps: num.spreadBps, slippage_ticks: num.slippageTicks, brokerage_per_order: num.brokerage },
    },
  };
}

export function rupees(v) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const sign = n < 0 ? '-' : '';
  return `${sign}₹${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function pct(v, digits = 1) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—';
  return `${Number(v).toFixed(digits)}%`;
}

export function ratio(v, digits = 2) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—';
  return Number(v).toFixed(digits);
}

export const toneOf = (v) => (v === null || v === undefined ? 'default' : Number(v) > 0 ? 'positive' : Number(v) < 0 ? 'negative' : 'default');

/** Key metrics as tiles: [{ key, label, value, sub?, tone }]. */
export function metricTiles(m) {
  if (!m) return [];
  return [
    { key: 'net', label: 'Net P&L', value: rupees(m.net_pnl), sub: `Gross ${rupees(m.gross_pnl)}`, tone: toneOf(m.net_pnl) },
    { key: 'charges', label: 'Charges', value: rupees(m.charges_total), sub: m.cost_contribution_pct === null ? 'No gross P&L' : `${pct(m.cost_contribution_pct)} of gross` },
    { key: 'trades', label: 'Trades', value: String(m.trades), sub: `Win rate ${pct(m.win_rate_pct)}` },
    { key: 'pf', label: 'Profit factor', value: m.profit_factor === null ? (m.trades && !m.losses ? 'No losses' : '—') : ratio(m.profit_factor), sub: `Expectancy ${rupees(m.expectancy)}` },
    { key: 'dd', label: 'Max drawdown', value: rupees(m.max_drawdown), sub: pct(m.max_drawdown_pct, 2), tone: m.max_drawdown > 0 ? 'negative' : 'default' },
    { key: 'sharpe', label: 'Sharpe / Sortino', value: `${ratio(m.sharpe)} / ${ratio(m.sortino)}`, sub: 'Daily, annualised √252' },
    { key: 'exposure', label: 'Exposure', value: pct(m.exposure_pct), sub: `Max losing streak ${m.max_consecutive_losses}` },
    { key: 'avg', label: 'Avg win / loss', value: `${rupees(m.avg_winner)} / ${rupees(m.avg_loser)}` },
  ];
}

const REASONS = {
  STOP_LOSS: 'Stop-loss',
  STOP_LOSS_GAP: 'Stop-loss (gap)',
  TARGET: 'Target',
  TARGET_GAP: 'Target (gap)',
  SQUARE_OFF: 'Square-off',
  EOD_SQUARE_OFF: 'End of day',
  DAILY_LOSS_STOP: 'Daily loss stop',
  END_OF_DATA: 'End of data',
};

export const exitReasonLabel = (r) => REASONS[r] ?? String(r ?? '—').replace(/_/g, ' ').toLowerCase();

/** "2026-10-22T10:35:00+05:30" -> "22 Oct 10:35" (backend already sends IST). */
export function istShort(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso ?? '');
  if (!m) return '—';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[4]}:${m[5]}`;
}

/** SVG path for a sparkline of numbers inside width x height (with 2px padding). */
export function sparklinePath(values, width, height) {
  const v = (values ?? []).filter((x) => Number.isFinite(x));
  if (v.length < 2 || width <= 4 || height <= 4) return '';
  const min = Math.min(...v);
  const max = Math.max(...v);
  const pad = 2;
  const w = width - pad * 2;
  const h = height - pad * 2;
  const y = (x) => (max === min ? pad + h / 2 : pad + h - ((x - min) / (max - min)) * h);
  return v.map((x, i) => `${i ? 'L' : 'M'}${(pad + (i / (v.length - 1)) * w).toFixed(1)},${y(x).toFixed(1)}`).join(' ');
}

export function regimeRows(byRegime) {
  return Object.entries(byRegime ?? {}).map(([regime, s]) => ({ regime, ...s }));
}

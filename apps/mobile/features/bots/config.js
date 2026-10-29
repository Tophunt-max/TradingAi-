// Helpers for the bot configuration form. The backend schema (BotConfig) is the
// source of truth; these only build sensible starting values and summaries.

export const TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h'];

export const INDICATORS = [
  { value: 'RSI', label: 'RSI' },
  { value: 'EMA_CROSS', label: 'EMA crossover' },
  { value: 'VWAP', label: 'VWAP' },
  { value: 'MACD', label: 'MACD' },
  { value: 'RELATIVE_VOLUME', label: 'Relative volume' },
  { value: 'SUPERTREND', label: 'Supertrend' },
];

export const OPERATORS = [
  { value: 'GT', label: 'greater than' },
  { value: 'LT', label: 'less than' },
  { value: 'CROSS_ABOVE', label: 'crosses above' },
  { value: 'CROSS_BELOW', label: 'crosses below' },
  { value: 'PRICE_ABOVE', label: 'price above' },
  { value: 'PRICE_BELOW', label: 'price below' },
];

export function blankConfig(strategy, limits) {
  return {
    name: '',
    description: '',
    strategy_id: strategy?.id ?? 'custom',
    timeframe: strategy?.timeframes?.[0] ?? '5m',
    instrument: { underlying: 'NIFTY', option_type: 'CE', strike_selection: 'ATM', expiry_selection: 'NEAREST' },
    entry: { logic: 'ALL', conditions: strategy?.default_entry ?? [] },
    exit: {
      stop_loss: { type: 'PERCENT', value: '20' },
      target: { type: 'PERCENT', value: '35' },
      trailing_stop: null,
      square_off_time_ist: limits?.square_off_time_ist ?? '15:15',
    },
    order: { order_type: 'LIMIT', limit_offset_pct: '0.5' },
    risk: {
      capital_allocation: String(Number(limits?.capital ?? 10000)),
      risk_per_trade: String(Number(limits?.max_risk_per_trade ?? 250)),
      max_entries_per_day: Math.min(2, limits?.max_entries_per_day ?? 2),
      max_open_positions: 1,
      re_entry_after_stop_loss: false,
    },
    session: { start_ist: '09:20', end_ist: '15:00', cooldown_minutes: 15 },
    filters: { min_relative_volume: null, max_atr_pct: null, news_filter: false },
    notifications: { signals: true, orders: true, risk: true },
    mode: 'PAPER',
  };
}

export function describeCondition(c) {
  const ind = INDICATORS.find((i) => i.value === c.indicator)?.label ?? c.indicator.replace(/_/g, ' ');
  const op = OPERATORS.find((o) => o.value === c.operator)?.label ?? c.operator.replace(/_/g, ' ').toLowerCase();
  const params = c.params && Object.keys(c.params).length ? ` (${Object.values(c.params).join(', ')})` : '';
  return `${ind}${params} ${op}${c.value != null ? ` ${c.value}` : ''}`;
}

export function describeLevel(l) {
  if (!l) return 'Off';
  return l.type === 'PERCENT' ? `${Number(l.value)}% of premium` : `${Number(l.value)} points`;
}

export function setIn(obj, path, value) {
  const [head, ...rest] = path;
  return { ...obj, [head]: rest.length ? setIn(obj[head] ?? {}, rest, value) : value };
}

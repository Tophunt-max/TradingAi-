// Honest status of every module. Screens marked `planned` render a clear
// "not built yet" state instead of fake data.
export const FEATURES = {
  'option-chain': { title: 'Option Chain', icon: 'grid-outline', phase: 3, summary: 'Strikes, CE/PE, OI, IV and Greeks from an authorised data source.' },
  orders: { title: 'Orders', icon: 'receipt-outline', phase: 6, summary: 'Pending, executed, cancelled and rejected orders with a persistent order state machine.' },
  positions: { title: 'Positions', icon: 'layers-outline', phase: 6, summary: 'Open and closed positions reconciled against fills.' },
  screener: { title: 'Market Screener', icon: 'funnel-outline', phase: 3, summary: 'Gainers, losers, volume breakouts, volatility and options-liquidity filters.' },
  backtesting: { title: 'Backtesting Lab', icon: 'time-outline', phase: 7, summary: 'Event-driven backtests, walk-forward tests and Monte Carlo analysis without lookahead.' },
  'risk-center': { title: 'Risk Center', icon: 'shield-half-outline', phase: 6, summary: 'Global risk limits, daily loss stop, profit lock and the emergency kill switch.' },
  alerts: { title: 'Alerts', icon: 'notifications-outline', phase: 5, summary: 'Signal, bot, order, risk, data and broker notifications.' },
  brokers: { title: 'Broker Connections', icon: 'link-outline', phase: 8, summary: 'Paper adapter and verified broker integrations. Live trading stays locked.' },
  'model-lab': { title: 'Model Lab', icon: 'git-network-outline', phase: 7, summary: 'Model registry, calibration, drift monitoring, shadow testing and rollback.' },
  reports: { title: 'Reports', icon: 'document-text-outline', phase: 7, summary: 'Daily and weekly performance, charges, slippage and CSV export.' },
  education: { title: 'Education & Strategy Library', icon: 'school-outline', phase: 4, summary: 'Strategy explanations, indicator docs, risk education and glossary.' },
  'ai-analysis': { title: 'AI Market Analysis', icon: 'sparkles-outline', phase: 4, summary: 'UP / DOWN / SIDEWAYS probabilities with calibration, uncertainty and NO TRADE.' },
  'bot-builder': { title: 'Bot Builder', icon: 'construct-outline', phase: 5, summary: 'AI Auto Create, manual builder, templates and validated, versioned configs.' },
  portfolio: { title: 'Portfolio & P&L', icon: 'pie-chart-outline', phase: 6, summary: 'Paper P&L, charges breakdown and analytics. Paper and live P&L are never mixed.' },
  charts: { title: 'Charts & Technicals', icon: 'bar-chart-outline', phase: 4, summary: 'Candlesticks, multi-timeframe indicators, patterns and Smart Money Concepts.' },
};

export const MORE_SECTIONS = [
  { title: 'Trading', items: ['option-chain', 'orders', 'positions', 'screener', 'risk-center'] },
  { title: 'Research', items: ['backtesting', 'model-lab', 'reports', 'education'] },
  { title: 'Connections', items: ['alerts', 'brokers'] },
];

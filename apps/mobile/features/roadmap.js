// Status of every module. `route` = screen exists. `engine` = what is still
// missing behind it. Modules without a route render a "not built yet" card.
export const FEATURES = {
  'option-chain': { title: 'Option Chain', icon: 'grid-outline', phase: 3, route: '/option-chain', summary: 'Strikes, CE/PE, OI, IV and Greeks.' },
  orders: { title: 'Orders', icon: 'receipt-outline', phase: 6, route: '/orders', summary: 'Pending, executed, cancelled and rejected orders.' },
  positions: { title: 'Positions', icon: 'layers-outline', phase: 6, route: '/positions', summary: 'Open and closed positions reconciled against fills.' },
  screener: { title: 'Market Screener', icon: 'funnel-outline', phase: 3, route: '/screener', summary: 'Gainers, losers, volume and options filters.' },
  'risk-center': { title: 'Risk Center', icon: 'shield-half-outline', phase: 6, route: '/risk-center', summary: 'Global risk limits and the emergency kill switch.' },
  alerts: { title: 'Notifications', icon: 'notifications-outline', phase: 5, route: '/alerts', summary: 'Bot, risk and security events.' },
  brokers: { title: 'Broker Connections', icon: 'link-outline', phase: 7, route: '/brokers', summary: 'Paper adapter and planned broker integrations.' },
  education: { title: 'Learn & Grow', icon: 'school-outline', phase: 4, route: '/education', summary: 'Strategies, indicators, risk and glossary.' },
  backtesting: { title: 'Backtesting Lab', icon: 'time-outline', phase: 8, route: '/backtesting', summary: 'Event-driven backtests, walk-forward tests and Monte Carlo analysis without lookahead.' },
  'model-lab': { title: 'AI Model Lab', icon: 'git-network-outline', phase: 4, route: '/models', summary: 'Model registry, walk-forward metrics, promotion gate and prediction history.' },
  events: { title: 'Events & Calendar', icon: 'calendar-outline', phase: 8, route: '/events', summary: 'Imported events, option expiries, holidays and event-risk status.' },
  reports: { title: 'Reports', icon: 'document-text-outline', phase: 8, summary: 'Daily and weekly performance, charges, slippage and CSV export.' },
  'ai-analysis': { title: 'AI Market Analysis', icon: 'sparkles-outline', phase: 4, route: '/ai-lab', summary: 'Probabilities with calibration, uncertainty and NO TRADE.' },
  'bot-builder': { title: 'Bot Builder', icon: 'construct-outline', phase: 5, route: '/bots', summary: 'AI Auto Create, manual builder and templates.' },
  portfolio: { title: 'Portfolio & P&L', icon: 'pie-chart-outline', phase: 6, route: '/portfolio', summary: 'Demo (virtual) and real P&L, kept separate, with charges.' },
  charts: { title: 'Charts & Technicals', icon: 'bar-chart-outline', phase: 4, route: '/instrument/NIFTY', summary: 'Candlesticks, indicators, patterns and SMC zones.' },
};

export const MORE_SECTIONS = [
  { title: 'Trading', items: ['option-chain', 'orders', 'positions', 'screener', 'risk-center'] },
  { title: 'Research', items: ['backtesting', 'model-lab', 'events', 'reports', 'education'] },
  { title: 'Connections', items: ['alerts', 'brokers'] },
];

export function featureHref(id) {
  return FEATURES[id]?.route ?? `/feature/${id}`;
}

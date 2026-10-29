// Static educational content. Plain explanations, no performance claims.
export const LESSONS = [
  {
    id: 'options-basics', section: 'Courses', title: 'Options trading basics', minutes: 6,
    body: [
      'A call (CE) gains value when the underlying rises; a put (PE) gains when it falls. As a buyer, your maximum loss is the premium paid plus charges.',
      'Option prices also depend on time to expiry (theta decay) and implied volatility (IV). The underlying can move in your direction and the option can still lose value if IV falls or time passes.',
      'Liquidity matters: a wide bid/ask spread is a cost you pay on entry and again on exit.',
    ],
  },
  {
    id: 'greeks', section: 'Courses', title: 'The Greeks', minutes: 5,
    body: [
      'Delta: approximate change in option price for a 1-point move in the underlying.',
      'Gamma: how fast delta changes. It is highest near the money and close to expiry.',
      'Theta: value lost per day from time decay, all else equal. It is negative for buyers.',
      'Vega: change in option price for a 1-point change in implied volatility.',
    ],
  },
  {
    id: 'risk', section: 'Courses', title: 'Risk management', minutes: 6,
    body: [
      'Decide the maximum you are willing to lose per trade and per day before you trade. QuantAI applies these limits globally across all bots.',
      'A stop-loss is a plan, not a guarantee: gaps, illiquidity and outages can cause exits at worse prices.',
      'Avoid averaging down or martingale sizing. They turn small losses into large ones.',
      'A daily profit lock stops new entries. It does not protect open positions.',
    ],
  },
  {
    id: 'ema', section: 'Indicators', title: 'EMA (exponential moving average)', minutes: 3,
    body: ['A moving average that weights recent prices more. EMA 9 above EMA 21 is a common short-term uptrend reading. It lags price and whipsaws in sideways markets.'],
  },
  {
    id: 'rsi', section: 'Indicators', title: 'RSI (relative strength index)', minutes: 3,
    body: ['A 0–100 momentum oscillator (Wilder, 14 periods by default). Readings above 70 or below 30 are often called overbought or oversold, but strong trends can stay there for a long time.'],
  },
  {
    id: 'macd', section: 'Indicators', title: 'MACD', minutes: 3,
    body: ['The difference between the 12 and 26 period EMAs, with a 9-period signal line. The histogram shows the gap between them. It is derived from the same prices as EMAs, so it is not an independent confirmation.'],
  },
  {
    id: 'backtests', section: 'Methodology', title: 'How to read a backtest', minutes: 4,
    body: [
      'A credible backtest uses only information available at each moment (no lookahead), includes realistic costs, spreads and slippage, and is tested out-of-sample.',
      'An options backtest needs historical option prices, including expired contracts. A test on the underlying alone is only a proxy.',
      'Past performance, paper or backtested, does not guarantee future returns.',
    ],
  },
  {
    id: 'glossary', section: 'Glossary', title: 'Trading glossary', minutes: 4,
    body: [
      'ATM / ITM / OTM: at, in or out of the money relative to the current price.',
      'OI: open interest, the number of outstanding contracts.',
      'PCR: put-call ratio of open interest.',
      'IV: implied volatility, the volatility implied by the option price.',
      'Slippage: the difference between expected and actual fill price.',
      'Square-off: closing a position, often before the session ends.',
    ],
  },
];

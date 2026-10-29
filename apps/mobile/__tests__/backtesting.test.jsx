import { fireEvent, render, screen } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { buildRunRequest, DEFAULT_FORM, exitReasonLabel, istShort, metricTiles, pct, rupees, sparklinePath } from '../features/backtesting/format';
import { BacktestResults, WalkForwardSummary } from '../features/backtesting/Results';
import { featureHref } from '../features/roadmap';

const wrap = (ui) => render(<ThemeProvider>{ui}</ThemeProvider>);

const METRICS = {
  trades: 2, gross_pnl: 60.5, charges_total: 95.06, net_pnl: -34.56, cost_contribution_pct: 157.12,
  wins: 1, losses: 1, win_rate_pct: 50, profit_factor: 0.42, expectancy: -17.28, avg_winner: 25.0,
  avg_loser: -59.56, max_consecutive_losses: 1, max_drawdown: 80.12, max_drawdown_pct: 0.8,
  sharpe: -1.2, sortino: -0.9, exposure_pct: 12.5, signals: 3, suppressed_signals: 1, rejected_orders: 0,
};

const TRADE = (over) => ({
  entry_index: 70, exit_index: 80, direction: 'LONG', qty: 1, entry_time: '2026-10-22T10:35:00+05:30',
  exit_time: '2026-10-22T11:25:00+05:30', entry_price: 23669.06, exit_price: 23598.05, exit_reason: 'STOP_LOSS',
  stop_target_same_bar: true, gross: -71.01, charges: 47.53, net: -118.54, slippage: 4.8, gap_slippage: 0, ...over,
});

const RESULT = {
  fidelity: 'UNDERLYING_PROXY',
  fidelity_note: 'incomplete proxy for options: ignores premium, IV, theta, spread',
  data_source: 'DEMO',
  evidence: false,
  warnings: [
    'Simulated data: results are not evidence of real performance',
    'UNDERLYING_PROXY: incomplete proxy for options: ignores premium, IV, theta, spread.',
    'Lot size 1 is a placeholder (1 unit per lot), not the exchange lot size.',
  ],
  strategy: { name: 'Momentum', direction: 'LONG underlying' },
  underlying: 'NIFTY',
  timeframe: '5m',
  data: { bars: 1000, from: '2026-10-01T09:15:00+05:30', to: '2026-10-28T15:25:00+05:30' },
  risk_limits: { capital: 10000 },
  metrics: METRICS,
  by_regime: { RANGE: { trades: 1, net_pnl: 83.98, win_rate_pct: 100 }, TRENDING_UP: { trades: 1, net_pnl: -118.54, win_rate_pct: 0 } },
  by_timeframe: { '5m': { trades: 2, net_pnl: -34.56 } },
  equity_curve: [{ t: 'a', equity: 10000 }, { t: 'b', equity: 9900 }, { t: 'c', equity: 9965 }],
  trades: [TRADE({ entry_index: 10, exit_index: 20, net: 83.98, exit_reason: 'TARGET', stop_target_same_bar: false }), TRADE()],
};

describe('backtesting format helpers', () => {
  it('builds a run request from the form', () => {
    const { body, errors } = buildRunRequest(DEFAULT_FORM);
    expect(errors).toEqual({});
    expect(body).toMatchObject({ strategy_id: 'momentum', underlying: 'NIFTY', bars: 1000, fidelity: 'UNDERLYING_PROXY' });
    expect(body.stop).toEqual({ type: 'PERCENT', value: 0.3 });
    expect(body.costs).toEqual({ spread_bps: 2, slippage_ticks: 1, brokerage_per_order: 20 });
  });

  it('validates numbers and ranges', () => {
    const { body, errors } = buildRunRequest({ ...DEFAULT_FORM, bars: '5000', stopPct: 'abc', slippageTicks: '1.5' });
    expect(body).toBeNull();
    expect(errors.bars).toMatch(/between 150 and 2000/);
    expect(errors.stopPct).toMatch(/must be a number/);
    expect(errors.slippageTicks).toMatch(/whole number/);
  });

  it('formats values', () => {
    expect(rupees(-1234.5)).toBe('-₹1,234.50');
    expect(rupees(null)).toBe('—');
    expect(pct(12.345)).toBe('12.3%');
    expect(exitReasonLabel('STOP_LOSS_GAP')).toBe('Stop-loss (gap)');
    expect(exitReasonLabel('SOMETHING_NEW')).toBe('something new');
    expect(istShort('2026-10-22T10:35:00+05:30')).toBe('22 Oct 10:35');
    expect(metricTiles(METRICS).find((t) => t.key === 'net')).toMatchObject({ value: '-₹34.56', tone: 'negative' });
    expect(metricTiles({ ...METRICS, profit_factor: null, losses: 0 }).find((t) => t.key === 'pf').value).toBe('No losses');
  });

  it('draws a sparkline path inside the box', () => {
    expect(sparklinePath([1], 100, 50)).toBe('');
    expect(sparklinePath([0, 10], 104, 54)).toBe('M2.0,52.0 L102.0,2.0');
    expect(sparklinePath([5, 5, 5], 104, 54)).toBe('M2.0,27.0 L52.0,27.0 L102.0,27.0');
  });

  it('routes the roadmap feature to the screen', () => {
    expect(featureHref('backtesting')).toBe('/backtesting');
  });
});

describe('BacktestResults', () => {
  it('shows the fidelity and data-source warnings first, then metrics, regimes and trades', async () => {
    await wrap(<BacktestResults result={RESULT} monteCarlo={null} />);
    expect(screen.getByText(/Simulated data: results are not evidence of real performance/)).toBeTruthy();
    expect(screen.getByText(/Fidelity: UNDERLYING_PROXY\. This is an incomplete proxy for options/)).toBeTruthy();
    expect(screen.getByText(/placeholder/)).toBeTruthy();
    expect(screen.getByText('-₹34.56')).toBeTruthy();
    expect(screen.getByText('157.1% of gross')).toBeTruthy();
    expect(screen.getByText('TRENDING UP')).toBeTruthy();
    expect(screen.getByText(/Stop-loss \(stop assumed first\)/)).toBeTruthy();
    expect(screen.getByTestId('bt-equity')).toBeTruthy();
    expect(screen.queryByTestId('bt-monte-carlo')).toBeNull();
  });

  it('renders the Monte Carlo summary and expands the trade list', async () => {
    const mc = { monte_carlo: { simulations: 1000, trades_per_simulation: 25, seed: 7, method: 'Bootstrap.', probability_of_loss: 0.62, net_pnl: { p5: -500, p25: -200, p50: -50, p75: 80, p95: 300 }, max_drawdown: { p5: 50, p25: 90, p50: 150, p75: 220, p95: 400 } } };
    const many = { ...RESULT, trades: Array.from({ length: 25 }, (_, i) => TRADE({ entry_index: i, exit_index: i + 1 })) };
    await wrap(<BacktestResults result={many} monteCarlo={mc} />);
    expect(screen.getByText('1000 resamples of 25 trades · seed 7')).toBeTruthy();
    expect(screen.getByText('62.0%')).toBeTruthy();
    await fireEvent.press(screen.getByText('Show all 25'));
    expect(screen.getByText('Show fewer')).toBeTruthy();
  });

  it('renders the walk-forward summary with the holdout flag', async () => {
    const wf = {
      ...RESULT,
      objective: 'net_pnl',
      grid_size: 4,
      oos_metrics: METRICS,
      stability: { profitable_windows_pct: 33.3, net_pnl_mean: -12, net_pnl_std: 40 },
      oos_equity_curve: RESULT.equity_curve,
      windows: [{ window: 1, test: { from: '2026-10-05T09:15:00+05:30' }, chosen_params: { stop_pct: 0.2, target_pct: 0.4 }, test_metrics: { trades: 3, net_pnl: 12 } }],
      holdout: { span: { bars: 200, from: '2026-10-25T09:15:00+05:30' }, metrics: { trades: 2, net_pnl: -5 }, note: 'Evaluated once.' },
      protocol: 'Rolling windows.',
    };
    await wrap(<WalkForwardSummary wf={wf} />);
    expect(screen.getByText('Out-of-sample (test windows only)')).toBeTruthy();
    expect(screen.getByText('Final holdout (evaluated once)')).toBeTruthy();
    expect(screen.getByText('#1 5 Oct 09:15 · stop 0.2% / target 0.4%')).toBeTruthy();
  });
});

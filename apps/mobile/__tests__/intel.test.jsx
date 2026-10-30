import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { ModelCompareTable } from '../features/ai/ModelCompare';
import { OptionsIntelSection, OptionsIntelView } from '../features/ai/OptionsIntel';
import { RegimeV2Card, RouterCard } from '../features/ai/Sections';
import { ciText, compareRows, intelSections, pct, regimeBars, routerRows, rupees } from '../features/ai/intel';
import { api } from '../store/auth';

jest.mock('../store/auth', () => ({ api: { get: jest.fn(), post: jest.fn() } }));

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ThemeProvider>{ui}</ThemeProvider>
    </QueryClientProvider>,
  );

const candidate = {
  strike: 24000, side: 'CE', moneyness: 'ATM', trading_symbol: 'NIFTY 24000 CE', eligible: true, blocked_reasons: [],
  greeks: { source: 'MODEL' },
  underlying_direction: { probabilities: null, reason: 'No promoted model for NIFTY 15m.', relevant_outcome: 'UP', relevant_probability: null },
  premium_path: {
    available: true, label: 'MODEL_ESTIMATE', p_target_first: { p: 0.4135 }, p_stop_first: { p: 0.5825 }, p_neither: { p: 0.004 },
    target_pct: 30, stop_pct: 20, horizon_minutes: 60, path_volatility: 0.072, path_volatility_source: 'REALIZED_HV20', paths: 4000,
    iv_scenarios: [{ iv_shock_pts: -2, p_target_first: 0, p_stop_first: 1 }, { iv_shock_pts: 2, p_target_first: 1, p_stop_first: 0 }],
  },
  expected_net: { available: true, qty: 1, expected_premium_pnl: -0.19, expected_net_pnl: -47.57, costs: { spread_crossing: 0.06, slippage: 0.1, charges: 47.22, total: 47.38 } },
  risks: { theta_per_day_lot: -26.79, spread_pct: 0.45, spread_cost_lot: 0.06, iv_move_pnl_lot: { '-2': -5.48, '+2': 5.48 }, gamma: 0.002119, flags: [{ code: 'NEAR_EXPIRY', message: '0.7 days to expiry: time decay accelerates.' }] },
};
const intel = {
  underlying: 'NIFTY', lot_size: 1, lot_size_source: 'DEMO_PLACEHOLDER', time_to_expiry: { days: 0.668 },
  iv_context: { iv_rank: null, reason: 'insufficient IV history' }, futures_basis: { value: null }, assumptions: { risk_free_rate: 0.065 },
  candidates: [candidate],
};
const regime = {
  state: 'BREAKOUT', no_trade: false, version: 'regime-v2.0', calibration_status: 'UNCALIBRATED',
  confidence: { BREAKOUT: 0.33, RANGE: 0.21, LOW_VOLATILITY: 0.08, STRONG_UPTREND: 0.38 * 0 + 0.05 },
  reasons: ['ADX 18.0'], unavailable: ['breadth'],
};
const router = {
  regime_state: 'BREAKOUT', no_trade: false, note: 'Compatibility ranks fit, not profitability.',
  strategies: [
    { id: 'breakout', name: 'Breakout', expected_compatibility: 0.71, verdict: 'REQUIRES_VALIDATION', reasons: ['No out-of-sample validation recorded.'] },
    { id: 'dca_grid', name: 'DCA / Grid', expected_compatibility: 0.1, verdict: 'NO_TRADE', reasons: ['Research-only strategy; it cannot be activated.'] },
  ],
};
const compare = {
  symbol: 'NIFTY', timeframe: '15m', criteria: 'CI must be below zero.',
  rows: [
    { id: 'a', name: 'NIFTY 15m +4 bars · hist_gb', model_type: 'hist_gb', target: 'three_class', status: 'REJECTED', data_source: 'DEMO', oos_samples: 1474, log_loss: 0.51, climatology_log_loss: 1.01, brier: 0.3, ece: 0.05, vs_logistic: { mean_diff: 0.0776, ci_low: 0.06, ci_high: 0.09 }, better_than_logistic: false, vs_climatology: { mean_diff: -0.5, ci_low: -0.52, ci_high: -0.48 }, better_than_climatology: true, calibration_status: 'CALIBRATED_SIGMOID', promotable: false, promotable_reason: 'Trained on synthetic DEMO data.' },
    { id: 'b', name: 'NIFTY 15m +4 bars', model_type: 'logistic', target: 'three_class', status: 'REJECTED', data_source: 'DEMO', oos_samples: 1474, log_loss: 0.43, climatology_log_loss: 1.01, brier: 0.25, ece: 0.04, vs_logistic: null, better_than_logistic: null, vs_climatology: { mean_diff: -0.58, ci_low: -0.6, ci_high: -0.56 }, better_than_climatology: true, calibration_status: 'UNCALIBRATED', promotable: false, promotable_reason: 'Trained on synthetic DEMO data.' },
  ],
};

describe('intel helpers', () => {
  it('formats percentages, rupees and bootstrap intervals', () => {
    expect(pct(0.4135)).toBe('41.4%');
    expect(pct(null)).toBe('—');
    expect(rupees(-47.5)).toBe('−₹47.50');
    expect(rupees(3)).toBe('+₹3.00');
    expect(rupees(undefined)).toBe('—');
    expect(ciText({ mean_diff: -0.01, ci_low: -0.02, ci_high: 0.004 })).toBe('−0.0100 [−0.0200, +0.0040]');
    expect(ciText(null)).toBe('—');
  });

  it('keeps the four intelligence outputs separate', () => {
    const s = intelSections(candidate);
    expect(s.direction).toEqual({ available: false, text: 'Not available', detail: 'No promoted model for NIFTY 15m.' });
    expect(s.premium.target).toBe('41.4%');
    expect(s.premium.scenarios).toHaveLength(2);
    expect(s.net.net).toBe('−₹47.57');
    expect(s.net.negative).toBe(true);
    expect(s.risks.vega).toBe('−₹5.48 / +₹5.48');
    expect(s.risks.flags).toEqual(['0.7 days to expiry: time decay accelerates.']);
    expect(intelSections(null)).toBeNull();
    const withModel = intelSections({ ...candidate, underlying_direction: { probabilities: { UP: 0.6 }, relevant_outcome: 'UP', relevant_probability: 0.6, model_name: 'm' } });
    expect(withModel.direction.text).toBe('P(UP) 60.0%');
  });

  it('sorts regime bars and maps router verdicts', () => {
    const bars = regimeBars(regime);
    expect(bars[0]).toMatchObject({ state: 'BREAKOUT', isTop: true, label: 'BREAKOUT' });
    expect(bars.map((b) => b.value)).toEqual([...bars.map((b) => b.value)].sort((a, b) => b - a));
    expect(regimeBars(null)).toEqual([]);
    const rows = routerRows(router);
    expect(rows[0]).toMatchObject({ verdictLabel: 'NEEDS VALIDATION', tone: 'warning', score: '0.71' });
    expect(rows[1]).toMatchObject({ verdictLabel: 'NO TRADE', tone: 'negative' });
  });

  it('builds comparison rows', () => {
    const rows = compareRows(compare);
    expect(rows[0].vsLogistic).toBe('NO +0.0776 [+0.0600, +0.0900]');
    expect(rows[1].vsLogistic).toBe('baseline');
    expect(rows[1].vsClimatology.startsWith('YES')).toBe(true);
    expect(compareRows(null)).toEqual([]);
  });
});

describe('intel components', () => {
  it('renders the four outputs with MODEL ESTIMATE labels', async () => {
    await wrap(<OptionsIntelView data={intel} />);
    expect(screen.getByText('1. Underlying direction')).toBeTruthy();
    expect(screen.getByText('NOT AVAILABLE')).toBeTruthy();
    expect(screen.getAllByText('MODEL ESTIMATE')).toHaveLength(2);
    expect(screen.getByText('41.4%')).toBeTruthy();
    expect(screen.getByText('−₹47.57')).toBeTruthy();
    expect(screen.getByText('⚠ 0.7 days to expiry: time decay accelerates.')).toBeTruthy();
    expect(screen.getByText(/DEMO placeholder, not the NSE lot/)).toBeTruthy();
    expect(screen.getByText(/insufficient IV history/)).toBeTruthy();
  });

  it('fetches intelligence for the selected strike and side', async () => {
    api.get.mockResolvedValue(intel);
    await wrap(<OptionsIntelSection underlying="NIFTY" expiry="2026-10-30" strike={24000} />);
    await waitFor(() => expect(screen.getByTestId('intel-view')).toBeTruthy());
    expect(api.get).toHaveBeenCalledWith('/api/v1/options/NIFTY/intelligence?expiry=2026-10-30&side=CE&strike=24000');
    await fireEvent.press(screen.getByText('PE'));
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/api/v1/options/NIFTY/intelligence?expiry=2026-10-30&side=PE&strike=24000'));
  });

  it('labels regime confidences as uncalibrated and lists NO TRADE reasons', async () => {
    await wrap(
      <>
        <RegimeV2Card regime={regime} />
        <RouterCard router={router} />
      </>,
    );
    expect(screen.getByTestId('regime-v2-uncalibrated')).toBeTruthy();
    expect(screen.getByText('33.0%')).toBeTruthy();
    await fireEvent.press(screen.getByText('Strategy router v2'));
    expect(screen.getByText('NO TRADE')).toBeTruthy();
    expect(screen.getByText('Research-only strategy; it cannot be activated.')).toBeTruthy();
  });

  it('renders the model comparison table', async () => {
    await wrap(<ModelCompareTable data={compare} />);
    expect(screen.getAllByText('NOT PROMOTABLE')).toHaveLength(2);
    expect(screen.getByText(/NO \+0\.0776/)).toBeTruthy();
    expect(screen.getByText('CI must be below zero.')).toBeTruthy();
    await wrap(<ModelCompareTable data={{ symbol: 'X', timeframe: '5m', rows: [] }} />);
    expect(screen.getByTestId('compare-empty')).toBeTruthy();
  });
});

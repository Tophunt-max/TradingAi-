import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { ActiveModeBadge } from '../features/account/ActiveModeBadge';
import { canReviewPaperOrder, paperOrderBody } from '../features/intelligence/api';
import { DashboardView } from '../features/intelligence/Dashboard';
import { DecisionReportView } from '../features/intelligence/DecisionView';
import DecisionScreen from '../app/(app)/decision';
import { api } from '../store/auth';

jest.mock('../store/auth', () => ({ api: { get: jest.fn(), post: jest.fn() } }));

const metrics = { frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } };
const wrap = (ui) =>
  render(
    <SafeAreaProvider initialMetrics={metrics}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ThemeProvider>{ui}</ThemeProvider>
      </QueryClientProvider>
    </SafeAreaProvider>,
  );

const item = (family, statement, vote) => ({ source: 'technical', family, statement, value: null, timestamp: '2026-10-29T05:00:00Z', vote });
const noTrade = {
  symbol: 'NIFTY', timeframe: '15m', decision: 'NO_TRADE', account_mode: 'DEMO', source: 'DEMO', is_demo: true, side: 'CE',
  bar_ts: '2026-10-29T05:00:00Z', setup: { classification: 'NONE' },
  directional_view: { direction: 'BULLISH', probabilities: null, probability_reason: 'No promoted model for NIFTY 15m.' },
  evidence: { combination: { direction: 'BULLISH', support: 2, available_groups: 2, weights: 'UNWEIGHTED_UNVALIDATED' }, supporting: [item('trend', 'Trend family bullish.', 1)], conflicting: [item('momentum', 'Momentum family bearish.', -1)] },
  data_quality: { warnings: [] },
  no_trade_reasons: ['Expected net after costs is ₹-47.78 per lot (≤ 0): costs and decay outweigh the modelled edge.'],
  watch_reasons: ['Evidence rule not met: Only 2 independent evidence group(s) available; at least 3 are needed.'],
  sizing: null, proposal: null, execution: { available: true }, invalidation: [], decision_disclaimer: 'Research proposal only.',
};
const sizing = { lots: 1, lot_size: 75, entry_premium: '10', stop_premium: '8.00', target_premium: '13.00', stop_pct: '20', target_pct: '30', planned_risk: '199.00', risk_budget: '250', premium_risk_reward: 1.5, notes: [] };
const proposal = {
  ...noTrade,
  source: 'LIVE', is_demo: false, decision: 'TRADE_PROPOSAL', no_trade_reasons: [], watch_reasons: [], sizing,
  options: { contract: { trading_symbol: 'NIFTY-24000-CE' } },
  expected_outcome: { expected_net_pnl_per_lot: 25 }, risk_check: { allowed: true },
  proposal: { paper_order_request: { underlying: 'NIFTY', option_type: 'CE', expiry: '2026-11-05', strike: '24000.0', lots: 1, stop_loss_pct: '20', target_pct: '30' } },
};

describe('decision report', () => {
  it('shows NO TRADE reasons, both evidence lists and no review button', async () => {
    await wrap(<DecisionReportView report={noTrade} onReview={jest.fn()} />);
    expect(screen.getByTestId('decision-badge-no_trade')).toBeTruthy();
    expect(screen.getByText('NO TRADE reasons')).toBeTruthy();
    expect(screen.getByText(/costs and decay outweigh/)).toBeTruthy();
    expect(screen.getByText('Trend family bullish.')).toBeTruthy();
    expect(screen.getByText('Momentum family bearish.')).toBeTruthy();
    expect(screen.getByText('Not estimated')).toBeTruthy();
    expect(screen.getByText('DEMO · VIRTUAL')).toBeTruthy();
    expect(screen.queryByTestId('review-paper-order')).toBeNull();
  });

  it('offers "Review paper order" only for a TRADE_PROPOSAL in DEMO', async () => {
    const onReview = jest.fn();
    await wrap(<DecisionReportView report={proposal} onReview={onReview} />);
    expect(screen.getByTestId('decision-badge-trade_proposal')).toBeTruthy();
    expect(screen.getByTestId('proposal-details')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('review-paper-order'));
    expect(onReview).toHaveBeenCalledTimes(1);
    expect(canReviewPaperOrder({ ...proposal, account_mode: 'REAL', execution: { available: false } })).toBe(false);
    expect(canReviewPaperOrder({ ...proposal, decision: 'WATCH' })).toBe(false);
  });

  it('never offers the button in the REAL account', async () => {
    await wrap(<DecisionReportView report={{ ...proposal, account_mode: 'REAL', execution: { available: false, reason: 'REAL account: execution unavailable while live trading is locked.' } }} />);
    expect(screen.queryByTestId('review-paper-order')).toBeNull();
    expect(screen.getByTestId('execution-unavailable')).toBeTruthy();
    expect(screen.getByText('REAL')).toBeTruthy();
  });

  it('confirms with the account mode, then posts the proposal with a fresh idempotency key', async () => {
    api.get.mockImplementation((url) => Promise.resolve(url.startsWith('/api/v1/decision/') ? proposal : { active_mode: 'DEMO', accounts: [] }));
    api.post.mockResolvedValue({ id: 'o1' });
    await wrap(<DecisionScreen />);
    await fireEvent.press(await screen.findByTestId('review-paper-order'));
    expect(screen.getByText(/DEMO · VIRTUAL account\. Buy 1 lot/)).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();
    await fireEvent.press(screen.getByText('Place paper order'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe('/api/v1/orders/paper');
    expect(body).toMatchObject(proposal.proposal.paper_order_request);
    expect(body.idempotency_key).toMatch(/^[A-Za-z0-9-]{8,48}$/);
    expect(paperOrderBody(proposal, 'k1')).toEqual({ ...proposal.proposal.paper_order_request, idempotency_key: 'k1' });
  });
});

describe('intelligence dashboard', () => {
  it('renders NO_DATA and UNAVAILABLE sections with their reasons', async () => {
    const data = {
      underlying: 'NIFTY', timeframe: '15m', account_mode: 'DEMO', data_source: 'DEMO', as_of: '2026-10-29T05:00:00Z', note: 'Read-only.',
      sections: {
        regime: { status: 'OK', source: 'regime-v2', data: { state: 'RANGE', no_trade: false, top: [{ state: 'RANGE', confidence: 0.5 }], calibration_status: 'UNCALIBRATED' } },
        forecast: { status: 'NO_DATA', source: 'promoted_model', data: { probabilities: null }, reason: 'No promoted model for NIFTY 15m; no probability is estimated.' },
        backtests: { status: 'UNAVAILABLE', source: 'backtesting', data: null, reason: 'Backtests are run on demand and not stored.' },
      },
    };
    await wrap(<DashboardView data={data} />);
    expect(screen.getByTestId('intel-section-regime')).toBeTruthy();
    expect(screen.getByText('RANGE')).toBeTruthy();
    expect(screen.getByText('NO DATA')).toBeTruthy();
    expect(screen.getByText(/no probability is estimated/)).toBeTruthy();
    expect(screen.getByText('UNAVAILABLE')).toBeTruthy();
    expect(screen.getByText('DEMO · VIRTUAL')).toBeTruthy();
    expect(screen.getByText('SIMULATED')).toBeTruthy();
  });
});

describe('active account mode badge', () => {
  it.each([
    ['DEMO', 'DEMO · VIRTUAL'],
    ['REAL', 'REAL'],
  ])('reflects the server active mode %s', async (mode, label) => {
    api.get.mockResolvedValue({ active_mode: mode, accounts: [] });
    await wrap(<ActiveModeBadge />);
    expect(await screen.findByText(label)).toBeTruthy();
    expect(screen.getByTestId(`account-mode-${mode.toLowerCase()}`)).toBeTruthy();
  });
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import Brokers from '../app/(app)/brokers';
import { DailyReportView, ExportCard, PnlView } from '../app/(app)/reports';
import { dayLabel, exportPath, istToday, netBars, pct, shiftDay, signedINR } from '../features/reports/format';
import { featureHref } from '../features/roadmap';
import { api } from '../store/auth';

jest.mock('../store/auth', () => ({ api: { get: jest.fn(), post: jest.fn(), getText: jest.fn() } }));

const metrics = { frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } };
const wrap = (ui) =>
  render(
    <SafeAreaProvider initialMetrics={metrics}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } })}>
        <ThemeProvider>{ui}</ThemeProvider>
      </QueryClientProvider>
    </SafeAreaProvider>,
  );

describe('reports formatting helpers', () => {
  it('formats signed rupees, percentages and days', () => {
    expect(signedINR('110')).toBe('+₹110.00');
    expect(signedINR('-192')).toBe('−₹192.00');
    expect(signedINR('0')).toBe('₹0.00');
    expect(signedINR(null)).toBe('—');
    expect(signedINR('1234567.5')).toBe('+₹12,34,567.50');
    expect(pct('66.67')).toBe('66.67%');
    expect(pct(null)).toBe('—');
    expect(shiftDay('2026-10-28', -1)).toBe('2026-10-27');
    expect(shiftDay('2026-03-01', -1)).toBe('2026-02-28');
    expect(shiftDay('2026-12-31', 1)).toBe('2027-01-01');
    expect(dayLabel('2026-10-28')).toContain('Wed');
  });

  it('uses the IST calendar day', () => {
    expect(istToday(new Date('2026-10-28T18:29:00Z'))).toBe('2026-10-28');
    expect(istToday(new Date('2026-10-28T18:31:00Z'))).toBe('2026-10-29');
  });

  it('scales daily-net bars to the largest absolute value', () => {
    const bars = netBars([
      { day: '2026-10-27', net: '40.00' },
      { day: '2026-10-28', net: '-80.00' },
      { day: '2026-10-29', net: '0.00' },
    ]);
    expect(bars.map((b) => [b.pct, b.positive])).toEqual([[50, true], [100, false], [0, true]]);
    expect(netBars([])).toEqual([]);
  });

  it('builds mode-scoped export paths and routes the roadmap entry', () => {
    expect(exportPath('fills', 'DEMO')).toBe('/api/v1/reports/fills.csv?account_mode=DEMO');
    expect(featureHref('reports')).toBe('/reports');
  });
});

const REPORT = {
  account_mode: 'DEMO', mode_label: 'DEMO · VIRTUAL', pnl_basis: 'Simulated fills.', day: '2026-10-28',
  totals: { trades: 3, wins: 2, losses: 1, win_rate_pct: '66.67', gross: '30.00', charges: '102.00', net: '-72.00', max_drawdown: '192.00' },
  charges_breakdown: { brokerage: '84.00', stt: '6.00', exchange_txn: '6.00', sebi: '0.00', stamp_duty: '0.00', gst: '6.00', total: '102.00' },
  slippage: { orders: 3, total: '6.75' }, fills: { count: 6 },
  risk_events: [{ type: 'LOSS_STOP', at: '2026-10-28T11:00:00+05:30', scope: 'DEMO' }],
  by_bot: [{ key: 'Alpha', trades: 2, net: '120.00' }], by_strategy: [], by_exit_reason: [{ key: 'STOP_LOSS', trades: 1, net: '-192.00' }],
  charges_reconciliation: null,
};

describe('reports screen parts', () => {
  it('renders the daily report cards', async () => {
    await wrap(<DailyReportView r={REPORT} />);
    expect(screen.getByText('66.67%')).toBeTruthy();
    expect(screen.getByText('−₹72.00')).toBeTruthy();
    expect(screen.getByText('LOSS STOP · 11:00 IST')).toBeTruthy();
    expect(screen.getByTestId('report-by-bot')).toBeTruthy();
  });

  it('renders P&L bars, cumulative and drawdown', async () => {
    await wrap(<PnlView p={{ from: '2026-09-29', to: '2026-10-28', mode_label: 'DEMO · VIRTUAL', cumulative_net: '-32.00', max_drawdown: '72.00', current_drawdown: '72.00', daily: [{ day: '2026-10-27', net: '40.00' }, { day: '2026-10-28', net: '-72.00' }], by_exit_reason: [], by_weekday: [], by_instrument: [] }} />);
    expect(screen.getByTestId('pnl-bar-2026-10-28')).toBeTruthy();
    expect(screen.getByText('−₹32.00')).toBeTruthy();
  });

  it('says honestly that export needs the web app on native, and hides the REAL ledger', async () => {
    await wrap(<ExportCard mode="REAL" />);
    expect(screen.getByTestId('export-web-only')).toBeTruthy();
    expect(screen.queryByTestId('export-ledger')).toBeNull();
    expect(screen.getByTestId('export-fills')).toBeTruthy();
  });
});

const BROKERS = [
  { id: 'paper', name: 'Paper Trading', kind: 'PAPER', status: 'CONNECTED', note: 'Simulated', verification: 'SIMULATOR', enabled: true, connection: null },
  { id: 'indstocks', name: 'INDstocks (INDmoney)', kind: 'BROKER', status: 'NOT_CONNECTED', note: 'Read-only', verification: 'UNVERIFIED_AGAINST_LIVE_API', enabled: true, capabilities: { order_types: ['MARKET', 'LIMIT'], product_types: ['INTRADAY'], cancel: true, modify: true, streaming: false }, connection: null },
  { id: 'zerodha', name: 'Zerodha Kite Connect', kind: 'BROKER', status: 'NOT_INTEGRATED', note: 'Not integrated', verification: 'NOT_INTEGRATED', enabled: false, connection: null },
];
const PERMS = { ready: false, checks: [{ key: 'live_flag', label: 'Live trading enabled on the server', passed: false, detail: 'LIVE_TRADING_ENABLED is false.' }], blockers: ['x'] };

describe('broker connections screen', () => {
  it('sends the pasted token once and clears it; lists permission blockers', async () => {
    jest.clearAllMocks();
    api.get.mockImplementation((path) => Promise.resolve(path.endsWith('/permissions') ? PERMS : BROKERS));
    api.post.mockResolvedValue({ ...BROKERS[1], status: 'CONNECTED' });
    await wrap(<Brokers />);
    await waitFor(() => expect(screen.getByTestId('broker-token-indstocks')).toBeTruthy());
    expect(screen.getByTestId('broker-unverified-indstocks')).toBeTruthy();
    expect(screen.getByTestId('broker-token-warning').props.children).toContain('stored there encrypted');
    expect(screen.getByTestId('permission-live_flag')).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId('broker-token-indstocks'), 'secret-token-value-123');
    await fireEvent.press(screen.getByTestId('broker-connect-btn-indstocks'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/v1/brokers/indstocks/connect', { token: 'secret-token-value-123' }));
    expect(screen.getByTestId('broker-token-indstocks').props.value).toBe('');
    expect(screen.queryByText('secret-token-value-123')).toBeNull();
  });
});

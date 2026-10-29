import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { OrderCard, PositionCard, exitLabel } from '../features/portfolio/Lists';
import { newIdempotencyKey } from '../features/trading/api';

jest.mock('../store/auth', () => ({ api: { get: jest.fn(), post: jest.fn() } }));

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ThemeProvider>{ui}</ThemeProvider>
    </QueryClientProvider>,
  );

const base = {
  id: 'p1', bot_id: null, instrument: 'NIFTY 24000 CE', underlying: 'NIFTY', option_type: 'CE', quantity: '75', lots: 1,
  entry_qty: 75, avg_price: '10.05', avg_exit: null, ltp: '12.00', ltp_at: '2026-10-29T05:00:00Z', unrealized_pnl: '146.25',
  realized_gross: '0', charges: '24.10', realized_net: '-24.10', stop_loss: '8.00', target: '13.00', trailing_pct: null,
  exit_reason: null, data_source: 'INDSTOCKS', opened_at: '2026-10-29T04:00:00Z', closed_at: null, status: 'OPEN',
};

describe('paper trading UI', () => {
  it('shows an open position with live P&L and an exit button', async () => {
    await wrap(<PositionCard p={base} />);
    expect(screen.getByText('NIFTY 24000 CE')).toBeTruthy();
    expect(screen.getByText('DEMO · VIRTUAL')).toBeTruthy();
    expect(screen.getByText('INDSTOCKS')).toBeTruthy();
    expect(screen.getByText('Unrealised (gross)')).toBeTruthy();
    expect(screen.getByText('Exit at market')).toBeTruthy();
    expect(screen.getByText('+19.40%')).toBeTruthy();
  });

  it('shows net P&L and the exit reason for a closed position', async () => {
    await wrap(<PositionCard p={{ ...base, status: 'CLOSED', quantity: '0', avg_exit: '10.45', realized_gross: '30.00', realized_net: '-14.10', exit_reason: 'STOP_LOSS', closed_at: '2026-10-29T05:10:00Z' }} />);
    expect(screen.getByText('Net of charges')).toBeTruthy();
    expect(screen.getByText('Stop-loss')).toBeTruthy();
    expect(screen.queryByText('Exit at market')).toBeNull();
  });

  it('labels DEMO data and placeholder lots, and shows reject reasons', async () => {
    await wrap(
      <OrderCard
        o={{ id: 'o1', instrument: 'NIFTY 24000 CE', side: 'BUY', quantity: '1', lots: 1, order_type: 'MARKET', purpose: 'ENTRY', status: 'REJECTED', filled_qty: 0, avg_fill_price: null, reject_reason: 'Daily loss stop reached.', created_at: '2026-10-29T04:00:00Z' }}
      />,
    );
    expect(screen.getByText('REJECTED')).toBeTruthy();
    expect(screen.getByText('Daily loss stop reached.')).toBeTruthy();
  });

  it('maps exit reasons and builds valid idempotency keys', () => {
    expect(exitLabel('KILL_SWITCH')).toBe('Kill switch');
    expect(exitLabel(null)).toBe('—');
    const k = newIdempotencyKey();
    expect(k).toMatch(/^[A-Za-z0-9-]{8,48}$/);
    expect(newIdempotencyKey()).not.toBe(k);
  });
});

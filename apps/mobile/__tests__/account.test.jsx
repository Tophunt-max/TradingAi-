import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { Text } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { DemoResetCard } from '../app/(app)/account';
import { DEMO_RESET_MESSAGE, useAccountMode } from '../features/account/mode';
import { api } from '../store/auth';

jest.mock('../store/auth', () => ({ api: { get: jest.fn(), post: jest.fn() } }));

const metrics = { frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } };
const wrap = (ui) =>
  render(
    <SafeAreaProvider initialMetrics={metrics}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } })}>
        <ThemeProvider>{ui}</ThemeProvider>
      </QueryClientProvider>
    </SafeAreaProvider>,
  );

const ACCOUNTS = {
  active_mode: 'DEMO',
  live_trading_enabled: false,
  accounts: [
    { id: 'a1', mode: 'DEMO', status: 'ACTIVE', available: true, unavailable_reasons: [], cash: '10000.0000' },
    { id: null, mode: 'REAL', status: 'NOT_CONNECTED', available: false, unavailable_reasons: ['No real broker account is connected.'], cash: null },
  ],
};

function ModeProbe() {
  const m = useAccountMode();
  return <Text testID="probe">{JSON.stringify({ mode: m.mode, options: m.options })}</Text>;
}

describe('account modes (server-backed)', () => {
  it('builds the selector options from GET /api/v1/accounts', async () => {
    jest.clearAllMocks();
    api.get.mockResolvedValue(ACCOUNTS);
    await wrap(<ModeProbe />);
    await waitFor(() => expect(JSON.parse(screen.getByTestId('probe').props.children).options[1].reason).toContain('No real broker'));
    const out = JSON.parse(screen.getByTestId('probe').props.children);
    expect(api.get).toHaveBeenCalledWith('/api/v1/accounts');
    expect(out.mode).toBe('DEMO');
    expect(out.options.map((o) => [o.mode, o.available])).toEqual([['DEMO', true], ['REAL', false]]);
  });

  it('explains the demo reset and only resets after confirmation', async () => {
    jest.clearAllMocks();
    api.post.mockResolvedValue({ positions_closed: 1, orders_cancelled: 2, reset_count: 1, account: { cash: '10000' } });
    await wrap(<DemoResetCard />);
    expect(screen.queryByText(DEMO_RESET_MESSAGE)).toBeNull();
    await fireEvent.press(screen.getByTestId('demo-reset'));
    expect(screen.getByText('Reset demo account?')).toBeTruthy();
    expect(DEMO_RESET_MESSAGE).toMatch(/closes every open demo position/);
    expect(DEMO_RESET_MESSAGE).toMatch(/cancels every pending demo order/);
    expect(DEMO_RESET_MESSAGE).toMatch(/₹10,000/);
    expect(DEMO_RESET_MESSAGE).toMatch(/real account is never affected/);
    expect(screen.getByText(DEMO_RESET_MESSAGE)).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();
    await fireEvent.press(screen.getByText('Reset demo'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/v1/accounts/demo/reset', { confirm: true }));
    await waitFor(() => expect(screen.getByTestId('demo-reset-done')).toBeTruthy());
  });
});

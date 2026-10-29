import { render, screen } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { ConfigSummary } from '../features/bots/ConfigSummary';
import { blankConfig, describeCondition, describeLevel, setIn } from '../features/bots/config';

const limits = { capital: '20000.0000', max_risk_per_trade: '300.0000', max_entries_per_day: 1, square_off_time_ist: '15:10' };

describe('bot config helpers', () => {
  it('builds a PAPER, buy-only draft bounded by global limits', () => {
    const cfg = blankConfig({ id: 'breakout', timeframes: ['15m'], default_entry: [{ indicator: 'RSI', operator: 'GT', value: 55, params: {} }] }, limits);
    expect(cfg.mode).toBe('PAPER');
    expect(cfg.strategy_id).toBe('breakout');
    expect(cfg.timeframe).toBe('15m');
    expect(cfg.risk.capital_allocation).toBe('20000');
    expect(cfg.risk.risk_per_trade).toBe('300');
    expect(cfg.risk.max_entries_per_day).toBe(1);
    expect(cfg.exit.square_off_time_ist).toBe('15:10');
    expect(cfg.entry.conditions).toHaveLength(1);
  });

  it('describes conditions and levels in plain words', () => {
    expect(describeCondition({ indicator: 'RSI', operator: 'GT', value: 55, params: { period: 14 } })).toBe('RSI (14) greater than 55');
    expect(describeCondition({ indicator: 'SMC_CHOCH', operator: 'PRESENT', params: {} })).toBe('SMC CHOCH present');
    expect(describeLevel({ type: 'PERCENT', value: '20' })).toBe('20% of premium');
    expect(describeLevel(null)).toBe('Off');
  });

  it('updates nested values immutably', () => {
    const a = { risk: { x: 1, y: 2 } };
    const b = setIn(a, ['risk', 'x'], 5);
    expect(b.risk).toEqual({ x: 5, y: 2 });
    expect(a.risk.x).toBe(1);
  });

  it('renders a configuration summary', async () => {
    await render(
      <ThemeProvider>
        <ConfigSummary config={{ ...blankConfig(null, limits), name: 'Test' }} strategyName="Custom Manual" />
      </ThemeProvider>,
    );
    expect(screen.getByText('Custom Manual')).toBeTruthy();
    expect(screen.getByText('NIFTY CE · buy only')).toBeTruthy();
    expect(screen.getByText('No entry conditions defined.')).toBeTruthy();
    expect(screen.getByText('DEMO · VIRTUAL')).toBeTruthy();
  });
});

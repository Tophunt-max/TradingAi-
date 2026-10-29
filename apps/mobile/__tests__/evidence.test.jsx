import { fireEvent, render, screen } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { correlationNote, familyRows, patternContextLine, patternStatus } from '../features/ai/evidence';
import { EvidenceCard, PatternsCard } from '../features/ai/Sections';

const wrap = (ui) => render(<ThemeProvider>{ui}</ThemeProvider>);

const technical = {
  indicator_version: 'ta-3',
  net_family_vote: 1,
  max_family_votes: 3,
  family_bias: 'MIXED',
  volatility_state: 'CONTRACTION',
  correlation_threshold: 0.8,
  note: 'Families vote once each.',
  correlated_pairs: [
    { a: 'rsi', b: 'cci', rho: 0.95, same_family: true },
    { a: 'ema_stack', b: 'adx_di', rho: 0.91, same_family: true },
    { a: 'rsi', b: 'ichimoku', rho: -0.85, same_family: false },
    { a: 'macd', b: 'roc', rho: 0.82, same_family: true },
  ],
  families: [
    { family: 'trend', directional: true, vote: 1, bias: 'BULLISH', members: [{ indicator: 'ema_stack', vote: 1 }, { indicator: 'supertrend', vote: 1 }, { indicator: 'adx_di', vote: 0 }, { indicator: 'ichimoku', vote: null }] },
    { family: 'momentum', directional: true, vote: 0, bias: 'NEUTRAL', members: [{ indicator: 'rsi', vote: 1 }, { indicator: 'macd', vote: -1 }] },
    { family: 'volatility', directional: false, vote: 0, bias: 'NEUTRAL', members: [{ indicator: 'atr', vote: 0 }] },
  ],
};

const hammer = {
  name: 'Hammer', direction: 'BULLISH', detail: 'Long lower wick.', bars_ago: 1, confirmed_ts: 't1',
  confirmation: { status: 'PENDING', direction: 'BULLISH', rule: 'A close above 71.10' },
  invalidation: { level: 66, rule: 'A close below 66.00.' },
  context: { relative_volume: 1.42, prior_trend: 'DOWN' },
};

describe('evidence helpers', () => {
  it('maps confirmation states to badges', () => {
    expect(patternStatus(hammer)).toEqual({ label: 'UNCONFIRMED', tone: 'warning' });
    expect(patternStatus({ confirmation: { status: 'CONFIRMED' } }).tone).toBe('positive');
    expect(patternStatus({ confirmation: { status: 'INVALIDATED' } }).label).toBe('INVALIDATED');
    expect(patternStatus({ name: 'BOS' })).toBeNull();
  });

  it('summarises invalidation, relative volume and prior trend', () => {
    expect(patternContextLine(hammer)).toBe('invalid below 66.00 · rel. vol 1.42× · prior trend down');
    const bear = { ...hammer, direction: 'BEARISH', confirmation: { status: 'CONFIRMED', direction: 'BEARISH' }, invalidation: { level: 120.5 }, context: {} };
    expect(patternContextLine(bear)).toBe('invalid above 120.50');
    expect(patternContextLine({ name: 'BOS' })).toBe('');
  });

  it('counts one vote per family and keeps volatility as context', () => {
    const rows = familyRows(technical);
    expect(rows.map((r) => r.label)).toEqual(['Trend', 'Momentum', 'Volatility']);
    expect(rows[0].detail).toBe('2 of 3 indicators agree · counts as 1 vote');
    expect(rows[2]).toMatchObject({ bias: 'CONTEXT', detail: 'CONTRACTION · context only, no vote' });
    expect(familyRows(null)).toEqual([]);
  });

  it('describes correlated pairs', () => {
    expect(correlationNote(technical)).toBe('4 pairs move together (|ρ| > 0.8): rsi–cci (0.95), ema_stack–adx_di (0.91), rsi–ichimoku (-0.85) and 1 more. They are not independent confirmations.');
    expect(correlationNote({ correlated_pairs: [] })).toMatch(/No indicator pairs/);
  });
});

describe('evidence UI', () => {
  it('renders family votes on expand', async () => {
    await wrap(<EvidenceCard technical={technical} />);
    expect(screen.getByText('Net +1 of 3 family votes · 4 correlated pairs')).toBeTruthy();
    await fireEvent.press(screen.getByRole('button'));
    expect(screen.getByText('Trend')).toBeTruthy();
    expect(screen.getByText('CONTEXT')).toBeTruthy();
  });

  it('renders nothing without a technical payload', async () => {
    const { toJSON } = await wrap(<EvidenceCard technical={undefined} />);
    expect(toJSON()).toBeNull();
  });

  it('shows pattern confirmation state and context', async () => {
    await wrap(<PatternsCard patterns={[hammer]} structure={null} />);
    await fireEvent.press(screen.getByRole('button'));
    expect(screen.getByText('UNCONFIRMED')).toBeTruthy();
    expect(screen.getByText(/invalid below 66.00 · rel. vol 1.42×/)).toBeTruthy();
  });
});

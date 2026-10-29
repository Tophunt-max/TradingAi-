import { fireEvent, render, screen } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { PnLText } from '../components/ui';
import { QualityBanner } from '../features/market/QualityBanner';
import { fixed, formatPrice } from '../services/format';

const wrap = (ui) => render(<ThemeProvider>{ui}</ThemeProvider>);

describe('live data rendering', () => {
  it('shows a dash instead of zero when the provider sends no value', async () => {
    expect(formatPrice(null)).toBe('—');
    expect(formatPrice(24471.7)).toBe('24,471.70');
    expect(fixed(undefined)).toBe('—');
    expect(fixed(0.5612, 3)).toBe('0.561');
    await wrap(<PnLText value={null} />);
    expect(screen.getByText('—')).toBeTruthy();
  });

  it('warns loudly when data quality fails and lists issues on tap', async () => {
    const payload = {
      quality: {
        status: 'FAIL',
        issues: [
          { code: 'STALE_QUOTE', severity: 'FAIL', message: 'Last update 300s ago (limit 60s).' },
          { code: 'MARKET_CLOSED', severity: 'INFO', message: 'Weekend: prices are from the last session.' },
        ],
      },
      notes: ['Index level comes from the option chain.'],
    };
    await wrap(<QualityBanner payload={payload} />);
    expect(screen.getByText('Data quality check failed: do not trade on this data')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('quality-banner'));
    expect(screen.getByText('• [FAIL] Last update 300s ago (limit 60s).')).toBeTruthy();
    expect(screen.getByText('• Index level comes from the option chain.')).toBeTruthy();
  });

  it('renders nothing for a clean payload without notes', async () => {
    await wrap(<QualityBanner payload={{ quality: { status: 'OK', issues: [] }, notes: [] }} />);
    expect(screen.queryByTestId('quality-banner')).toBeNull();
  });
});

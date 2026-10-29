import { fireEvent, render, screen } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { AccountModeBadge, AccountModeSelector, DataSourceBadge, ModeBadge } from '../components/ui';

const metrics = { frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } };
const wrap = (ui) => render(<SafeAreaProvider initialMetrics={metrics}><ThemeProvider>{ui}</ThemeProvider></SafeAreaProvider>);
const options = [
  { mode: 'DEMO', available: true, reason: 'Starting balance ₹10,000 (virtual).' },
  { mode: 'REAL', available: false, reason: 'Not available: no verified broker.' },
];

describe('account mode', () => {
  it('keeps the DEMO account and SIMULATED market data visibly distinct', async () => {
    await wrap(
      <>
        <AccountModeBadge mode="DEMO" />
        <AccountModeBadge mode="REAL" />
        <DataSourceBadge source="DEMO" />
        <ModeBadge live={false} />
      </>,
    );
    expect(screen.getAllByText('DEMO · VIRTUAL')).toHaveLength(2);
    expect(screen.getByText('REAL')).toBeTruthy();
    expect(screen.getByText('SIMULATED')).toBeTruthy();
  });

  it('shows an unavailable REAL option with its reason and never selects it', async () => {
    const onChange = jest.fn();
    await wrap(<AccountModeSelector value="DEMO" options={options} onChange={onChange} />);
    expect(screen.getByText('Demo account')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('account-mode-selector'));
    expect(screen.getByText('Not available: no verified broker.')).toBeTruthy();
    expect(screen.getByText('UNAVAILABLE')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('account-mode-option-real'));
    expect(onChange).not.toHaveBeenCalled();
  });
});

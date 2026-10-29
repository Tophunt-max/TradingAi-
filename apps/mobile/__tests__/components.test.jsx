import { render, screen } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { PlannedFeature } from '../components/PlannedFeature';
import { DataSourceBadge, PnLText, StateView } from '../components/ui';

const wrap = async (ui) => render(<ThemeProvider>{ui}</ThemeProvider>);

describe('design system', () => {
  it('labels demo, live and stale data distinctly', async () => {
    await wrap(
      <>
        <DataSourceBadge source="DEMO" />
        <DataSourceBadge source="LIVE" />
        <DataSourceBadge source="LIVE" stale />
      </>,
    );
    expect(screen.getByText('SIMULATED')).toBeTruthy();
    expect(screen.getByText('LIVE')).toBeTruthy();
    expect(screen.getByText('STALE')).toBeTruthy();
  });

  it('signs and colours P&L values', async () => {
    await wrap(
      <>
        <PnLText value={12.5} />
        <PnLText value={-3} />
      </>,
    );
    expect(screen.getByText('+12.50')).toBeTruthy();
    expect(screen.getByText('-3.00')).toBeTruthy();
  });

  it('renders an error state with retry', async () => {
    const onAction = jest.fn();
    await wrap(<StateView kind="error" message="Boom" actionLabel="Retry" onAction={onAction} />);
    expect(screen.getByText('Something went wrong')).toBeTruthy();
    expect(screen.getByText('Boom')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('marks unbuilt modules as not built instead of showing fake data', async () => {
    await wrap(<PlannedFeature id="option-chain" />);
    expect(screen.getByText('Option Chain')).toBeTruthy();
    expect(screen.getByText('PHASE 3')).toBeTruthy();
    expect(screen.getByText(/Not built yet/)).toBeTruthy();
  });
});

import { fireEvent, render, screen } from '@testing-library/react-native';
import { ThemeProvider } from '../components/theme/ThemeProvider';
import { ProbabilityCard } from '../features/ai/ProbabilityCard';
import { PipelineCard, StrategiesCard } from '../features/ai/Sections';
import { mapAnnotations } from '../features/market/annotations';

const wrap = (ui) => render(<ThemeProvider>{ui}</ThemeProvider>);

describe('AI analysis UI', () => {
  it('explains why there is no probability', async () => {
    await wrap(<ProbabilityCard prediction={null} />);
    expect(screen.getByText('NOT AVAILABLE')).toBeTruthy();
    expect(screen.getByText(/beats a base-rate baseline out of sample/)).toBeTruthy();
  });

  it('shows the model reason when the model abstains', async () => {
    await wrap(<ProbabilityCard prediction={{ probabilities: null, reason: 'Model trained on LIVE data; current data is DEMO.' }} />);
    expect(screen.getByText('Model trained on LIVE data; current data is DEMO.')).toBeTruthy();
  });

  it('renders calibrated probabilities with their baseline comparison', async () => {
    const prediction = {
      model_name: 'NIFTY 15m +4 bars',
      horizon_bars: 4,
      probabilities: { UP: 0.52, SIDEWAYS: 0.18, DOWN: 0.3 },
      uncertainty: 0.91,
      metrics: { log_loss: 0.98, baseline_log_loss: 1.05, ece: 0.03 },
      label_definition: 'Close 4 bars ahead…',
    };
    await wrap(<ProbabilityCard prediction={prediction} />);
    expect(screen.getByText('52.0%')).toBeTruthy();
    expect(screen.getByText('0.98 vs 1.05 baseline')).toBeTruthy();
    expect(screen.getByText(/never authorise a trade/)).toBeTruthy();
  });

  it('lists the pipeline and the reasons for NO TRADE', async () => {
    await wrap(
      <PipelineCard
        steps={[{ key: 'risk', label: 'Independent risk validation', status: 'BLOCKED', detail: 'Phase 6.' }]}
        reasons={['Input data is synthetic DEMO data.']}
        explanation={['NIFTY is ranging.', 'Decision: NO TRADE.']}
      />,
    );
    expect(screen.getByText('NO TRADE')).toBeTruthy();
    expect(screen.queryByText('• Input data is synthetic DEMO data.')).toBeNull();
    await fireEvent.press(screen.getByRole('button'));
    expect(screen.getByText('• Input data is synthetic DEMO data.')).toBeTruthy();
  });

  it('expands strategy conditions and flags regime mismatches', async () => {
    const strategies = [
      { id: 'momentum', name: 'Momentum', direction: 'CE', regime_compatible: false, summary: 'CE met', conditions: [{ label: 'RSI 14 above 55', met: true }] },
      { id: 'smc', name: 'Smart Money Concepts', direction: 'NONE', regime_compatible: true, summary: 'No signal', conditions: [] },
    ];
    await wrap(<StrategiesCard strategies={strategies} />);
    expect(screen.getByText('1 of 2 signalling on the last closed candle')).toBeTruthy();
    await fireEvent.press(screen.getByRole('button'));
    expect(screen.getByText('REGIME MISMATCH')).toBeTruthy();
    expect(screen.getByText('RSI 14 above 55')).toBeTruthy();
  });

  it('maps confirmed annotations onto visible candles only', () => {
    const candles = ['t1', 't2', 't3'].map((ts) => ({ ts }));
    const ann = {
      patterns: [
        { name: 'Hammer', direction: 'BULLISH', confirmed_ts: 't2' },
        { name: 'Doji', direction: 'NEUTRAL', confirmed_ts: 't3' },
        { name: 'Old', direction: 'BEARISH', confirmed_ts: 't0' },
      ],
      zones: [{ name: 'Bullish FVG', direction: 'BULLISH', start_ts: 't1', end_ts: 't3', top: 10, bottom: 9 }],
    };
    const { markers, zones } = mapAnnotations(candles, ann);
    expect(markers).toEqual([{ index: 1, direction: 'BULLISH', label: 'Hammer' }]);
    expect(zones[0]).toMatchObject({ start: 0, end: 2, top: 10, bottom: 9 });
    expect(mapAnnotations(candles, null)).toEqual({ markers: [], zones: [] });
  });
});

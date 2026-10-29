import { useState } from 'react';
import { View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { AppText, Button, Card, CardHeader, DataTable, KeyValue, NoticeBanner, PnLText, StatTile } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { exitReasonLabel, istShort, metricTiles, pct, ratio, regimeRows, rupees, sparklinePath, toneOf } from './format';

/** Fidelity and data-source warnings always come first. */
export function ResultBanners({ result }) {
  const others = (result.warnings ?? []).filter((w) => !w.startsWith('Simulated data') && !w.startsWith('UNDERLYING_PROXY'));
  return (
    <View style={{ gap: 8 }}>
      {result.data_source === 'DEMO' || result.evidence === false ? (
        <NoticeBanner tone="danger" icon="alert-circle-outline" testID="bt-banner-data">
          {`Data source: ${result.data_source}. Simulated data: results are not evidence of real performance.`}
        </NoticeBanner>
      ) : null}
      <NoticeBanner tone="warning" icon="flask-outline" testID="bt-banner-fidelity">
        {`Fidelity: ${result.fidelity}. This is an ${result.fidelity_note}.`}
      </NoticeBanner>
      {others.map((w) => (
        <NoticeBanner key={w} tone="info">{w}</NoticeBanner>
      ))}
    </View>
  );
}

export function EquitySparkline({ points, height = 90 }) {
  const theme = useTheme();
  const [width, setWidth] = useState(300);
  const values = (points ?? []).map((p) => p.equity);
  const d = sparklinePath(values, width, height);
  const up = values.length > 1 && values[values.length - 1] >= values[0];
  return (
    <View testID="bt-equity" accessibilityLabel={`Equity curve, ${values.length} points`} onLayout={(e) => setWidth(e.nativeEvent.layout.width)} style={{ height }}>
      {d ? (
        <Svg width={width} height={height}>
          <Path d={d} stroke={up ? theme.colors.positive : theme.colors.negative} strokeWidth={2} fill="none" />
        </Svg>
      ) : (
        <AppText variant="caption" tone="faint">Not enough points to draw the equity curve.</AppText>
      )}
    </View>
  );
}

const REGIME_COLUMNS = [
  { key: 'regime', title: 'Regime', flex: 2, render: (r) => <AppText variant="caption">{r.regime.replace(/_/g, ' ')}</AppText> },
  { key: 'trades', title: 'Trades', align: 'right' },
  { key: 'win', title: 'Win %', align: 'right', render: (r) => <AppText variant="mono">{pct(r.win_rate_pct, 0)}</AppText> },
  { key: 'net', title: 'Net', flex: 1.5, align: 'right', render: (r) => <PnLText value={r.net_pnl} /> },
];

function TradeRow({ t }) {
  return (
    <View style={{ paddingVertical: 8, gap: 2 }} accessibilityRole="text">
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <AppText variant="bodyStrong">{`${t.direction} ${t.qty} · ${istShort(t.entry_time)} → ${istShort(t.exit_time).split(' ').pop()}`}</AppText>
        <PnLText value={t.net} />
      </View>
      <AppText variant="caption" tone="muted">
        {`${ratio(t.entry_price)} → ${ratio(t.exit_price)} · ${exitReasonLabel(t.exit_reason)}${t.stop_target_same_bar ? ' (stop assumed first)' : ''} · gross ${rupees(t.gross)} · charges ${rupees(t.charges)} · slippage ${rupees(t.slippage)}${t.gap_slippage ? ` · gap ${rupees(t.gap_slippage)}` : ''}`}
      </AppText>
    </View>
  );
}

export function MonteCarloSummary({ mc }) {
  if (!mc) return null;
  return (
    <Card testID="bt-monte-carlo">
      <CardHeader title="Monte Carlo (bootstrap)" subtitle={`${mc.simulations} resamples of ${mc.trades_per_simulation} trades · seed ${mc.seed}`} />
      <KeyValue label="Net P&L 5th / 50th / 95th pct" value={`${rupees(mc.net_pnl.p5)} / ${rupees(mc.net_pnl.p50)} / ${rupees(mc.net_pnl.p95)}`} />
      <KeyValue label="Max drawdown 50th / 95th pct" value={`${rupees(mc.max_drawdown.p50)} / ${rupees(mc.max_drawdown.p95)}`} />
      <KeyValue label="Probability of a net loss" value={pct(mc.probability_of_loss * 100)} tone={mc.probability_of_loss > 0.5 ? 'negative' : 'default'} />
      <AppText variant="caption" tone="faint">{mc.method}</AppText>
    </Card>
  );
}

export function BacktestResults({ result, monteCarlo }) {
  const [showAll, setShowAll] = useState(false);
  const m = result.metrics;
  const trades = [...(result.trades ?? [])].reverse();
  const shown = showAll ? trades : trades.slice(0, 20);
  const regimes = regimeRows(result.by_regime);
  return (
    <View style={{ gap: 12 }} testID="bt-results">
      <ResultBanners result={result} />
      <Card>
        <CardHeader title={`${result.strategy.name} · ${result.underlying} ${result.timeframe}`} subtitle={`${result.strategy.direction} · ${result.data.bars} bars ${istShort(result.data.from)} → ${istShort(result.data.to)}`} />
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {metricTiles(m).map((t) => (
            <StatTile key={t.key} label={t.label} value={t.value} sub={t.sub} tone={t.tone} />
          ))}
        </View>
      </Card>
      <Card>
        <CardHeader title="Equity curve" subtitle={`Marked at each bar close · capital ${rupees(result.risk_limits?.capital)}`} />
        <EquitySparkline points={result.equity_curve} />
      </Card>
      <Card>
        <CardHeader title="By market regime" subtitle="Regime detected at the signal bar" />
        {regimes.length ? <DataTable testID="bt-regimes" columns={REGIME_COLUMNS} rows={regimes} keyExtractor={(r) => r.regime} /> : <AppText tone="muted">No trades.</AppText>}
      </Card>
      {Object.keys(result.by_timeframe ?? {}).length > 1 ? (
        <Card>
          <CardHeader title="By timeframe" />
          {Object.entries(result.by_timeframe).map(([tf, s]) => (
            <KeyValue key={tf} label={`${tf} · ${s.trades} trades`} value={rupees(s.net_pnl)} tone={toneOf(s.net_pnl)} />
          ))}
        </Card>
      ) : null}
      <MonteCarloSummary mc={monteCarlo?.monte_carlo} />
      <Card testID="bt-trades">
        <CardHeader title="Trades" subtitle={`${trades.length} trades · times in IST · ${m.signals} signals, ${m.suppressed_signals} suppressed, ${m.rejected_orders} rejected`} />
        {shown.length ? shown.map((t) => <TradeRow key={`${t.entry_index}-${t.exit_index}`} t={t} />) : <AppText tone="muted">No trades in this window.</AppText>}
        {trades.length > 20 ? <Button variant="ghost" title={showAll ? 'Show fewer' : `Show all ${trades.length}`} onPress={() => setShowAll((v) => !v)} /> : null}
      </Card>
    </View>
  );
}

export function WalkForwardSummary({ wf }) {
  if (!wf) return null;
  const o = wf.oos_metrics;
  return (
    <View style={{ gap: 12 }} testID="bt-walk-forward">
      <ResultBanners result={wf} />
      <Card>
        <CardHeader title="Out-of-sample (test windows only)" subtitle={`${wf.windows.length} windows · objective ${wf.objective} · grid ${wf.grid_size}`} />
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {metricTiles(o).slice(0, 4).map((t) => (
            <StatTile key={t.key} label={t.label} value={t.value} sub={t.sub} tone={t.tone} />
          ))}
        </View>
        <KeyValue label="Profitable windows" value={pct(wf.stability.profitable_windows_pct, 0)} />
        <KeyValue label="Net P&L per window (mean ± std)" value={`${rupees(wf.stability.net_pnl_mean)} ± ${rupees(wf.stability.net_pnl_std)}`} />
        <EquitySparkline points={wf.oos_equity_curve} height={70} />
      </Card>
      <Card>
        <CardHeader title="Windows" />
        {wf.windows.map((w) => (
          <KeyValue key={w.window} label={`#${w.window} ${istShort(w.test.from)} · stop ${w.chosen_params.stop_pct}% / target ${w.chosen_params.target_pct}%${w.chosen_params.threshold !== undefined ? ` / ${w.chosen_params.threshold}` : ''}`} value={`${w.test_metrics.trades} · ${rupees(w.test_metrics.net_pnl)}`} tone={toneOf(w.test_metrics.net_pnl)} />
        ))}
      </Card>
      {wf.holdout ? (
        <Card testID="bt-holdout">
          <CardHeader title="Final holdout (evaluated once)" subtitle={`${wf.holdout.span.bars} bars from ${istShort(wf.holdout.span.from)}`} />
          <KeyValue label="Trades / net P&L" value={`${wf.holdout.metrics.trades} · ${rupees(wf.holdout.metrics.net_pnl)}`} tone={toneOf(wf.holdout.metrics.net_pnl)} />
          <AppText variant="caption" tone="warning">{wf.holdout.note}</AppText>
        </Card>
      ) : null}
      <AppText variant="caption" tone="faint">{wf.protocol}</AppText>
    </View>
  );
}

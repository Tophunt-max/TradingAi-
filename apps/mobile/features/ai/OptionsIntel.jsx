import { useState } from 'react';
import { View } from 'react-native';
import { AppText, Badge, Card, KeyValue, QueryState, SegmentedControl } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useOptionsIntelligence } from './api';
import { intelSections } from './intel';

function Block({ n, title, badge, children, testID }) {
  const theme = useTheme();
  return (
    <View testID={testID} style={{ padding: 10, borderRadius: 10, backgroundColor: theme.colors.surfaceRaised, gap: 2 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <AppText variant="bodyStrong" style={{ flex: 1 }}>{n}. {title}</AppText>
        {badge}
      </View>
      {children}
    </View>
  );
}

const ModelBadge = () => <Badge label="MODEL ESTIMATE" tone="info" />;

/** The four separated outputs for one contract. Presentational (no data fetching). */
export function OptionsIntelView({ data }) {
  const c = data?.candidates?.[0];
  const s = intelSections(c);
  if (!s) return <AppText tone="muted" testID="intel-empty">{data?.notes?.join(' ') || 'No contract to evaluate at this strike.'}</AppText>;
  return (
    <View style={{ gap: 8 }} testID="intel-view">
      <AppText variant="caption" tone="faint">
        {c.trading_symbol ?? `${data.underlying} ${c.strike} ${c.side}`} · {c.moneyness} · {data.time_to_expiry?.days?.toFixed(2)} days to expiry · lot {data.lot_size}
        {data.lot_size_source === 'DEMO_PLACEHOLDER' ? ' (DEMO placeholder, not the NSE lot)' : ''} · Greeks {c.greeks?.source}
      </AppText>
      <Block n={1} title="Underlying direction" badge={<Badge label={s.direction.available ? 'PROMOTED MODEL' : 'NOT AVAILABLE'} tone={s.direction.available ? 'primary' : 'warning'} />} testID="intel-direction">
        <AppText variant="bodyStrong">{s.direction.text}</AppText>
        <AppText variant="caption" tone="muted">{s.direction.detail}</AppText>
      </Block>
      <Block n={2} title="Premium: target before stop" badge={<ModelBadge />} testID="intel-premium">
        {s.premium.available ? (
          <>
            <KeyValue label="Target first" value={s.premium.target} tone="positive" />
            <KeyValue label="Stop first" value={s.premium.stop} tone="negative" />
            <KeyValue label="Neither by horizon" value={s.premium.neither} />
            <AppText variant="caption" tone="faint">{s.premium.detail}</AppText>
            {s.premium.scenarios.map((t) => <AppText key={t} variant="caption" tone="faint">• {t}</AppText>)}
          </>
        ) : <AppText variant="caption" tone="muted">{s.premium.detail}</AppText>}
      </Block>
      <Block n={3} title="Expected net outcome after costs" badge={<ModelBadge />} testID="intel-net">
        {s.net.available ? (
          <>
            <KeyValue label="Expected net P&L (1 lot)" value={s.net.net} tone={s.net.negative ? 'negative' : 'positive'} />
            <KeyValue label="Expected premium P&L" value={s.net.gross} />
            <KeyValue label="Costs" value={s.net.costs} tone="negative" />
            <AppText variant="caption" tone="faint">{s.net.detail}</AppText>
          </>
        ) : <AppText variant="caption" tone="muted">{s.net.detail}</AppText>}
      </Block>
      <Block n={4} title="Risks" badge={s.eligible ? null : <Badge label="BLOCKED" tone="negative" />} testID="intel-risks">
        <KeyValue label="Theta per day (lot)" value={s.risks.theta} tone="negative" />
        <KeyValue label="Spread" value={s.risks.spread} />
        <KeyValue label="IV −2 / +2 vol pts (lot)" value={s.risks.vega} />
        <KeyValue label="Gamma" value={s.risks.gamma} />
        {s.risks.flags.map((f) => <AppText key={f} variant="caption" tone="warning">⚠ {f}</AppText>)}
      </Block>
      <AppText variant="caption" tone="faint">
        IV rank: {data.iv_context?.iv_rank == null ? `n/a (${data.iv_context?.reason ?? 'unavailable'})` : data.iv_context.iv_rank}. Futures basis: {data.futures_basis?.value ?? 'n/a'}. Model estimates assume a driftless underlying, constant IV and Black-Scholes repricing (r {data.assumptions?.risk_free_rate}); they are not forecasts and never place an order.
      </AppText>
    </View>
  );
}

export function OptionsIntelSection({ underlying, expiry, strike }) {
  const [side, setSide] = useState('CE');
  const q = useOptionsIntelligence({ underlying, expiry, side, strike });
  return (
    <Card testID="options-intel">
      <AppText variant="bodyStrong">Options intelligence</AppText>
      <SegmentedControl value={side} onChange={setSide} options={[{ value: 'CE', label: 'CE' }, { value: 'PE', label: 'PE' }]} testID="intel-side" />
      <QueryState query={q}>{(d) => <OptionsIntelView data={d} />}</QueryState>
    </Card>
  );
}

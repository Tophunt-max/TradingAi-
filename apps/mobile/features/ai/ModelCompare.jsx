import { View } from 'react-native';
import { AppText, Badge, Card, CardHeader } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { compareRows } from './intel';

/** Out-of-sample comparison of registered models for one symbol and timeframe. */
export function ModelCompareTable({ data }) {
  const theme = useTheme();
  const rows = compareRows(data);
  return (
    <Card testID="model-compare">
      <CardHeader title="Model comparison" subtitle={data ? `${data.symbol} · ${data.timeframe} · ${rows.length} models` : ''} />
      {rows.length ? (
        rows.map((r) => (
          <View key={r.id} testID={`compare-${r.id}`} style={{ paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle, gap: 2 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <AppText variant="bodyStrong" style={{ flex: 1, textTransform: 'capitalize' }}>{r.title}</AppText>
              <Badge label={r.calibration.replace('_', ' ')} tone={r.calibration === 'UNCALIBRATED' ? 'warning' : 'info'} />
              <Badge label={r.promotable ? 'PROMOTABLE' : 'NOT PROMOTABLE'} tone={r.promotable ? 'positive' : 'neutral'} />
            </View>
            <AppText variant="caption" tone="faint">{r.subtitle}</AppText>
            <AppText variant="caption" tone="muted">{r.metrics}</AppText>
            <AppText variant="caption" tone="muted">Beats logistic (Δ log loss, 95% CI): {r.vsLogistic}</AppText>
            <AppText variant="caption" tone="muted">Beats climatology: {r.vsClimatology}</AppText>
            <AppText variant="caption" tone="faint">{r.promotableReason}</AppText>
          </View>
        ))
      ) : (
        <AppText tone="muted" testID="compare-empty">No models trained for this symbol and timeframe yet.</AppText>
      )}
      {data?.criteria ? <AppText variant="caption" tone="faint" style={{ marginTop: 6 }}>{data.criteria}</AppText> : null}
    </Card>
  );
}

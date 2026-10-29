import { View } from 'react-native';
import { router } from 'expo-router';
import { AppText, Badge, Button, Card, CardHeader, KeyValue } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';

const ROWS = [
  ['UP', 'positive'],
  ['SIDEWAYS', 'warning'],
  ['DOWN', 'negative'],
];

export function ProbabilityBars({ probabilities }) {
  const theme = useTheme();
  return (
    <View style={{ gap: 8 }} testID="probability-bars">
      {ROWS.map(([k, tone]) => {
        const p = probabilities[k];
        return (
          <View key={k} style={{ gap: 4 }} accessibilityLabel={`${k} ${Math.round(p * 100)} percent`}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
              <AppText variant="caption" tone="muted">{k}</AppText>
              <AppText variant="caption" tone={tone} style={{ fontWeight: '700' }}>{(p * 100).toFixed(1)}%</AppText>
            </View>
            <View style={{ height: 8, borderRadius: 4, backgroundColor: theme.colors.surfaceRaised }}>
              <View style={{ width: `${p * 100}%`, height: 8, borderRadius: 4, backgroundColor: theme.colors[tone] }} />
            </View>
          </View>
        );
      })}
    </View>
  );
}

export function ProbabilityCard({ prediction }) {
  if (!prediction || !prediction.probabilities) {
    return (
      <Card testID="probability-card">
        <CardHeader title="AI probability" right={<Badge label="NOT AVAILABLE" tone="warning" />} />
        <AppText tone="muted">
          {prediction?.reason ??
            'No promoted model for this instrument and timeframe. A model is promoted only after it beats a base-rate baseline out of sample and is calibrated, on real market data.'}
        </AppText>
        <Button title="Model Lab" size="sm" variant="ghost" icon="git-network-outline" onPress={() => router.push('/models')} />
      </Card>
    );
  }
  const m = prediction.metrics;
  return (
    <Card testID="probability-card">
      <CardHeader title="AI probability" subtitle={prediction.model_name} right={<Badge label={`+${prediction.horizon_bars} BARS`} tone="info" />} />
      <ProbabilityBars probabilities={prediction.probabilities} />
      <KeyValue label="Uncertainty (0 = sure, 1 = coin flip)" value={prediction.uncertainty.toFixed(2)} />
      <KeyValue label="Out-of-sample log loss" value={`${m.log_loss} vs ${m.baseline_log_loss} baseline`} />
      <KeyValue label="Calibration error (ECE)" value={m.ece} />
      {prediction.label_definition ? <AppText variant="caption" tone="faint">{prediction.label_definition}</AppText> : null}
      <AppText variant="caption" tone="warning" style={{ marginTop: 6 }}>
        Probabilities describe the underlying, not whether an option trade would profit. They never authorise a trade on their own.
      </AppText>
    </Card>
  );
}

import { useState } from 'react';
import { View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Badge, Card, CardHeader, KeyValue, NoticeBanner, QueryState, Screen, SegmentedControl, StateView } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useModels, usePredictionHistory } from '../../features/market/api';
import { formatIST } from '../../services/format';

const STATUS_TONE = { PROMOTED: 'positive', CANDIDATE: 'info', REJECTED: 'negative', RETIRED: 'neutral' };

function ModelCard({ m }) {
  const theme = useTheme();
  const x = m.metrics;
  return (
    <Card testID={`model-${m.id}`}>
      <CardHeader title={m.name} subtitle={`${m.algorithm} · features ${m.feature_version} · ${m.data_source} via ${m.data_provider}`} right={<Badge label={m.status} tone={STATUS_TONE[m.status]} />} />
      <KeyValue label="Out-of-sample predictions" value={x.oos_samples} />
      <KeyValue label="Log loss (lower is better)" value={`${x.log_loss} vs ${x.baseline_log_loss} baseline`} />
      <KeyValue label="Brier score" value={`${x.brier} vs ${x.baseline_brier} baseline`} />
      <KeyValue label="Calibration error (ECE)" value={x.ece} />
      <KeyValue label="Accuracy" value={`${(x.accuracy * 100).toFixed(1)}%`} />
      <KeyValue label="Training window" value={`${formatIST(m.data_from, { withDate: true })} → ${formatIST(m.data_to, { withDate: true })}`} />
      <AppText variant="label" tone="muted" style={{ marginTop: 8 }}>PROMOTION GATE</AppText>
      {m.gate.checks.map((c) => (
        <View key={c.key} style={{ flexDirection: 'row', gap: 6, paddingVertical: 2 }}>
          <Ionicons name={c.passed ? 'checkmark-circle' : 'close-circle'} size={15} color={c.passed ? theme.colors.positive : theme.colors.negative} style={{ marginTop: 2 }} />
          <AppText variant="caption" style={{ flex: 1 }}>
            <AppText variant="caption" style={{ fontWeight: '700' }}>{c.label}: </AppText>
            <AppText variant="caption" tone="muted">{c.detail}</AppText>
          </AppText>
        </View>
      ))}
      {m.metrics.by_regime && Object.keys(m.metrics.by_regime).length ? (
        <>
          <AppText variant="label" tone="muted" style={{ marginTop: 8 }}>BY REGIME (LOG LOSS VS BASELINE)</AppText>
          {Object.entries(m.metrics.by_regime).map(([r, v]) => (
            <KeyValue key={r} label={`${r.replace('_', ' ')} (${v.samples})`} value={`${v.log_loss} / ${v.baseline_log_loss}`} tone={v.log_loss < v.baseline_log_loss ? 'positive' : 'negative'} />
          ))}
        </>
      ) : null}
      {m.data_source === 'DEMO' ? (
        <AppText variant="caption" tone="warning" style={{ marginTop: 6 }}>
          Scores on synthetic DEMO data are meaningless for real markets (the generator is predictable), which is why DEMO models can never be promoted.
        </AppText>
      ) : null}
    </Card>
  );
}

function History() {
  const q = usePredictionHistory();
  return (
    <QueryState query={q} isEmpty={(d) => !d.length} emptyMessage="Predictions are recorded when a promoted model serves a forecast.">
      {(rows) => (
        <Card>
          {rows.map((p) => (
            <KeyValue
              key={p.id}
              label={`${p.symbol} ${p.timeframe} · ${formatIST(p.bar_ts, { withDate: true })}`}
              value={`↑${Math.round(p.p_up * 100)} ↔${Math.round(p.p_sideways * 100)} ↓${Math.round(p.p_down * 100)} · ${p.outcome ?? 'pending'}`}
            />
          ))}
          <AppText variant="caption" tone="faint">Outcome scoring, drift monitoring and shadow testing arrive in Phase 8.</AppText>
        </Card>
      )}
    </QueryState>
  );
}

export default function Models() {
  const [tab, setTab] = useState('MODELS');
  const q = useModels();
  return (
    <Screen edges={['bottom']} refreshing={q.isRefetching} onRefresh={q.refetch}>
      <SegmentedControl value={tab} onChange={setTab} options={[{ value: 'MODELS', label: 'Model registry' }, { value: 'HISTORY', label: 'Prediction history' }]} />
      {tab === 'MODELS' ? (
        <QueryState query={q}>
          {(rows) =>
            rows.length ? (
              rows.map((m) => <ModelCard key={m.id} m={m} />)
            ) : (
              <StateView kind="empty" title="No models trained yet" message="An admin trains candidates with: python -m app.ai.train NIFTY 15m" />
            )
          }
        </QueryState>
      ) : (
        <History />
      )}
      <NoticeBanner tone="info">
        Models are never retrained or promoted automatically. A candidate is promoted by an admin only after it beats the base-rate baseline out of sample, is calibrated and was trained on real data.
      </NoticeBanner>
    </Screen>
  );
}

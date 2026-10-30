import { View } from 'react-native';
import { AppText, Badge, Card, CardHeader, DataTable, KeyValue, NoticeBanner, QueryState } from '../../components/ui';
import { formatIST } from '../../services/format';
import { useDecisions, useDrift, useFundamentals, useLearningSummary } from './api';
import { DECISION_TONE, DRIFT_TONE, decisionLine, errorClassRows, fundamentalsView, pct, reliabilityRows } from './format';

export function DecisionList({ page }) {
  if (!page.items.length) {
    return <AppText tone="muted">No decisions recorded yet. Every analysis and bot evaluation is recorded, including NO TRADE.</AppText>;
  }
  return (
    <Card testID="decision-list">
      <CardHeader title="Decision history" subtitle={`${page.total} recorded (NO TRADE and skipped included)`} />
      {page.items.map((d) => (
        <View key={d.id} style={{ paddingVertical: 6, gap: 2 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
            <AppText variant="caption" style={{ fontWeight: '700' }}>
              {d.symbol} {d.timeframe} · {d.origin} · {formatIST(d.bar_ts, { withDate: true })}
            </AppText>
            <Badge label={d.decision.replace('_', ' ')} tone={DECISION_TONE[d.decision]} />
          </View>
          <AppText variant="caption" tone="muted">{decisionLine(d)}</AppText>
          {d.error_classes?.length ? <AppText variant="caption" tone="warning">Errors: {d.error_classes.join(', ')}</AppText> : null}
          {d.data_source === 'DEMO' ? <AppText variant="caption" tone="faint">DEMO data: outcomes are not evidence.</AppText> : null}
        </View>
      ))}
    </Card>
  );
}

export function CalibrationCard({ summary }) {
  const p = summary.probabilistic;
  if (!p) {
    return (
      <Card testID="calibration-card">
        <CardHeader title="Calibration" subtitle="Predicted vs realized frequency" />
        <AppText tone="muted">No resolved probabilistic forecasts yet. Calibration appears once a promoted model has served forecasts and their horizons have passed.</AppText>
      </Card>
    );
  }
  const rows = reliabilityRows(p.calibration);
  return (
    <Card testID="calibration-card">
      <CardHeader title="Calibration" subtitle={`${p.samples} resolved forecasts`} />
      <KeyValue label="Brier score (lower is better)" value={`${p.brier} vs ${p.climatology_brier} climatology`} />
      <KeyValue label="Log loss" value={`${p.log_loss} vs ${p.climatology_log_loss} climatology`} />
      <KeyValue label="Calibration error (ECE)" value={p.ece} />
      <DataTable
        testID="reliability-table"
        keyExtractor={(r) => r.key}
        columns={[
          { key: 'bucket', title: 'Confidence' },
          { key: 'predicted', title: 'Predicted', align: 'right' },
          { key: 'realized', title: 'Realized', align: 'right' },
          { key: 'count', title: 'N', align: 'right' },
        ]}
        rows={rows}
      />
      <AppText variant="caption" tone="faint">{p.climatology_note}</AppText>
    </Card>
  );
}

export function ErrorAttributionCard({ summary }) {
  const rows = errorClassRows(summary.error_classes);
  const nt = summary.no_trade;
  return (
    <Card testID="error-card">
      <CardHeader title="Error attribution" subtitle={`${summary.resolved} resolved decisions`} />
      {rows.map((r) => (
        <KeyValue key={r.key} label={r.label} value={r.count ? `${r.count} (${pct(r.share, 0)})` : '0'} tone={r.count ? 'warning' : undefined} />
      ))}
      <AppText variant="label" tone="muted" style={{ marginTop: 8 }}>AFTER NO TRADE (HYPOTHETICAL)</AppText>
      <KeyValue label="Abstentions resolved" value={nt.samples} />
      <KeyValue label="Mean absolute move" value={nt.mean_abs_return === null ? '—' : pct(nt.mean_abs_return, 2)} />
      <KeyValue label="Setup followed anyway (mean)" value={nt.counterfactual_mean_return === null ? '—' : pct(nt.counterfactual_mean_return, 2)} />
      <AppText variant="caption" tone="faint">{nt.note}</AppText>
    </Card>
  );
}

export function DriftCard({ drift }) {
  return (
    <Card testID="drift-card">
      <CardHeader title="Drift monitoring" subtitle={`PSI < ${drift.thresholds.psi_moderate} stable · > ${drift.thresholds.psi_significant} significant`} />
      {drift.models.length ? (
        drift.models.map((m) => (
          <View key={m.model_id} style={{ paddingVertical: 4, gap: 2 }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
              <AppText variant="caption" style={{ flex: 1, fontWeight: '700' }}>{m.model_name} ({m.model_status})</AppText>
              <Badge label={m.status.replace('_', ' ')} tone={DRIFT_TONE[m.status]} />
            </View>
            {m.max_feature_psi !== undefined && m.max_feature_psi !== null ? <AppText variant="caption" tone="muted">Max feature PSI {m.max_feature_psi}</AppText> : null}
            {(m.reasons ?? []).slice(0, 2).map((r) => <AppText key={r} variant="caption" tone="muted">• {r}</AppText>)}
          </View>
        ))
      ) : (
        <AppText tone="muted">No promoted or shadow models to monitor.</AppText>
      )}
      <AppText variant="caption" tone="faint">A DEGRADED model abstains (no probabilities) until it is reviewed, retrained or rolled back. A handful of losses never changes a model.</AppText>
    </Card>
  );
}

export function DecisionHistory() {
  const q = useDecisions();
  return <QueryState query={q}>{(page) => <DecisionList page={page} />}</QueryState>;
}

export function LearningOverview() {
  const s = useLearningSummary();
  const d = useDrift();
  return (
    <>
      <QueryState query={d}>{(drift) => <DriftCard drift={drift} />}</QueryState>
      <QueryState query={s}>
        {(summary) => (
          <>
            <CalibrationCard summary={summary} />
            <ErrorAttributionCard summary={summary} />
          </>
        )}
      </QueryState>
    </>
  );
}

export function FundamentalsBody({ data }) {
  const v = fundamentalsView(data);
  if (v.kind === 'empty' || v.kind === 'index') {
    return (
      <>
        <AppText variant="bodyStrong">{v.title}</AppText>
        <AppText variant="caption" tone="muted">{v.message}</AppText>
        {(v.rows ?? []).map((r) => <KeyValue key={r.label} label={r.label} value={r.value} />)}
        {v.coverage ? <AppText variant="caption" tone="faint">{v.coverage}</AppText> : null}
      </>
    );
  }
  return (
    <>
      <AppText variant="caption" tone="muted">{v.title}</AppText>
      {v.rows.map((r) => <KeyValue key={r.label} label={r.label} value={r.value} />)}
      {v.priceLabel === 'SIMULATED' ? <AppText variant="caption" tone="warning">Valuation uses a SIMULATED (DEMO) price.</AppText> : null}
      {v.supporting.map((t) => <AppText key={t} variant="caption" tone="positive">+ {t}</AppText>)}
      {v.contradicting.map((t) => <AppText key={t} variant="caption" tone="negative">− {t}</AppText>)}
      {v.flags.length ? <NoticeBanner tone="warning">{v.flags.slice(0, 3).join(' · ')}</NoticeBanner> : null}
      {v.context.map((t) => <AppText key={t} variant="caption" tone="faint">{t}</AppText>)}
    </>
  );
}

export function FundamentalsCard({ symbol }) {
  const q = useFundamentals(symbol);
  return (
    <Card testID="fundamentals-card">
      <CardHeader title="Fundamentals" subtitle="Point-in-time; context and event risk only for intraday options" />
      <QueryState query={q}>{(data) => <FundamentalsBody data={data} />}</QueryState>
    </Card>
  );
}

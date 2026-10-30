import { View } from 'react-native';
import { AccountModeBadge, AppText, Badge, Button, Card, CardHeader, DataSourceBadge, KeyValue, NoticeBanner } from '../../components/ui';
import { formatINR, formatIST } from '../../services/format';
import { canReviewPaperOrder, DECISION_LABEL, DECISION_TONE } from './api';

export function DecisionBadge({ decision }) {
  return <Badge label={DECISION_LABEL[decision] ?? decision} tone={DECISION_TONE[decision] ?? 'neutral'} testID={`decision-badge-${String(decision).toLowerCase()}`} />;
}

function EvidenceList({ title, items, empty, testID }) {
  return (
    <Card testID={testID}>
      <CardHeader title={title} subtitle={`${items.length} item(s)`} />
      {items.length ? (
        items.map((e, i) => (
          <View key={`${e.family}-${i}`} style={{ paddingVertical: 3 }}>
            <AppText variant="bodyStrong">
              {e.source} · {e.family}
            </AppText>
            <AppText variant="caption" tone="muted">{e.statement}</AppText>
            {e.timestamp ? <AppText variant="caption" tone="faint">{formatIST(e.timestamp, { withDate: true })} IST</AppText> : null}
          </View>
        ))
      ) : (
        <AppText tone="muted">{empty}</AppText>
      )}
    </Card>
  );
}

function Reasons({ title, reasons, tone, testID }) {
  if (!reasons?.length) return null;
  return (
    <Card testID={testID}>
      <CardHeader title={title} />
      {reasons.map((r) => (
        <AppText key={r} variant="caption" tone={tone}>• {r}</AppText>
      ))}
    </Card>
  );
}

/**
 * The DecisionReport. `onReview` is called only from the "Review paper order" button,
 * which exists only for a TRADE_PROPOSAL in the DEMO account.
 */
export function DecisionReportView({ report, onReview }) {
  const r = report;
  const dv = r.directional_view ?? {};
  const comb = r.evidence?.combination ?? {};
  const sz = r.sizing;
  const en = r.expected_outcome;
  const probs = dv.probabilities;
  return (
    <View style={{ gap: 12 }}>
      <Card testID="decision-summary">
        <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <DecisionBadge decision={r.decision} />
          <AccountModeBadge mode={r.account_mode} />
          <DataSourceBadge source={r.source} />
          {r.side ? <Badge label={r.side} tone="primary" /> : null}
        </View>
        <AppText variant="caption" tone="faint" style={{ marginTop: 6 }}>
          {r.symbol} · {r.timeframe} · setup {r.setup?.classification ?? 'NONE'} · bar {formatIST(r.bar_ts, { withDate: true })} IST
        </AppText>
        <KeyValue label="Evidence" value={`${comb.direction ?? 'NONE'} · ${comb.support ?? 0} of ${comb.available_groups ?? 0} groups`} />
        <KeyValue label="Weights" value={comb.weights ?? '—'} />
        <KeyValue label="Direction probability" value={probs ? Object.entries(probs).map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`).join(' · ') : 'Not estimated'} />
        {!probs ? <AppText variant="caption" tone="muted">{dv.probability_reason}</AppText> : null}
      </Card>
      {r.is_demo ? <NoticeBanner tone="info">SIMULATED market data: this report is not evidence about the real market.</NoticeBanner> : null}
      <Reasons title="NO TRADE reasons" reasons={r.no_trade_reasons} tone="negative" testID="no-trade-reasons" />
      <Reasons title="Watching for" reasons={r.watch_reasons} tone="warning" testID="watch-reasons" />
      <EvidenceList title="Supporting evidence" items={r.evidence?.supporting ?? []} empty="No supporting evidence." testID="supporting-evidence" />
      <EvidenceList title="Conflicting evidence" items={r.evidence?.conflicting ?? []} empty="No conflicting evidence." testID="conflicting-evidence" />
      {r.data_quality?.warnings?.length ? <Reasons title="Data-quality and risk warnings" reasons={r.data_quality.warnings} tone="muted" testID="dq-warnings" /> : null}
      {sz ? (
        <Card testID="proposal-details">
          <CardHeader title="Proposal details" subtitle={r.options?.contract?.trading_symbol ?? undefined} />
          <KeyValue label="Lots" value={`${sz.lots} × ${sz.lot_size}`} />
          <KeyValue label="Entry premium" value={formatINR(sz.entry_premium)} />
          <KeyValue label="Stop-loss" value={`${formatINR(sz.stop_premium)} (−${sz.stop_pct}%)${r.stop_loss?.underlying_level ? ` · underlying ${r.stop_loss.underlying_level}` : ''}`} />
          <KeyValue label="Target" value={`${formatINR(sz.target_premium)} (+${sz.target_pct}%)`} />
          <KeyValue label="Planned risk" value={`${formatINR(sz.planned_risk)} of ${formatINR(sz.risk_budget)}`} />
          <KeyValue label="Premium risk / reward" value={sz.premium_risk_reward ?? '—'} />
          {en ? <KeyValue label="Expected net / lot (model)" value={en.expected_net_pnl_per_lot == null ? '—' : formatINR(en.expected_net_pnl_per_lot)} /> : null}
          {r.risk_check ? <KeyValue label="Risk dry run" value={r.risk_check.allowed ? 'Would pass now' : 'Blocked'} tone={r.risk_check.allowed ? 'positive' : 'negative'} /> : null}
          {sz.notes?.map((n) => <AppText key={n} variant="caption" tone="faint">{n}</AppText>)}
        </Card>
      ) : null}
      {r.invalidation?.length ? (
        <Card>
          <CardHeader title="Invalidation" />
          {r.invalidation.map((x, i) => <AppText key={`${x.source}-${i}`} variant="caption" tone="muted">• {x.rule}</AppText>)}
        </Card>
      ) : null}
      {!r.execution?.available ? <NoticeBanner tone="warning" testID="execution-unavailable">{r.execution?.reason}</NoticeBanner> : null}
      {canReviewPaperOrder(r) ? <Button title="Review paper order" icon="checkmark-done-outline" fullWidth onPress={onReview} testID="review-paper-order" /> : null}
      <AppText variant="caption" tone="faint">{r.decision_disclaimer}</AppText>
    </View>
  );
}

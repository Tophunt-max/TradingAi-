import { useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { AppText, ChipRow, ConfirmDialog, QueryState, Screen } from '../../components/ui';
import { ACCOUNT_MODES } from '../../components/ui/AccountMode';
import { describeError } from '../../features/auth/api';
import { canReviewPaperOrder, paperOrderBody, useDecisionReport } from '../../features/intelligence/api';
import { DecisionReportView } from '../../features/intelligence/DecisionView';
import { newIdempotencyKey, usePaperEntry } from '../../features/trading/api';

function confirmMessage(r) {
  const req = r.proposal.paper_order_request;
  const mode = ACCOUNT_MODES[r.account_mode]?.badge ?? r.account_mode;
  return `${mode} account. Buy ${req.lots} lot(s) of ${req.underlying} ${req.strike} ${req.option_type} (expiry ${req.expiry}) with a ${req.stop_loss_pct}% stop-loss and ${req.target_pct}% target. The risk manager re-checks every limit before the paper order is placed.`;
}

export default function DecisionReport() {
  const params = useLocalSearchParams();
  const [underlying, setUnderlying] = useState(params.underlying === 'BANKNIFTY' ? 'BANKNIFTY' : 'NIFTY');
  const [tf, setTf] = useState(['5m', '15m', '1h'].includes(params.timeframe) ? params.timeframe : '15m');
  const [side, setSide] = useState('auto');
  const q = useDecisionReport(underlying, tf, side);
  const entry = usePaperEntry();
  // The report and one idempotency key are captured when the dialog opens, so a retry or
  // double tap can never create a second order.
  const [confirm, setConfirm] = useState(null);

  return (
    <Screen edges={['bottom']} refreshing={q.isRefetching} onRefresh={q.refetch}>
      <ChipRow value={underlying} onChange={setUnderlying} options={['NIFTY', 'BANKNIFTY'].map((v) => ({ value: v, label: v }))} testIDPrefix="decision-underlying" />
      <ChipRow value={tf} onChange={setTf} options={['5m', '15m', '1h'].map((v) => ({ value: v, label: v.toUpperCase() }))} testIDPrefix="decision-tf" />
      <ChipRow value={side} onChange={setSide} options={[{ value: 'auto', label: 'AUTO' }, { value: 'CE', label: 'CE' }, { value: 'PE', label: 'PE' }]} testIDPrefix="decision-side" />
      <QueryState query={q}>{(r) => <DecisionReportView report={r} onReview={() => setConfirm({ report: r, key: newIdempotencyKey() })} />}</QueryState>
      {entry.error ? <AppText tone="negative">{describeError(entry.error)}</AppText> : null}
      {confirm && canReviewPaperOrder(confirm.report) ? (
        <ConfirmDialog
          visible
          title="Place this paper order?"
          message={confirmMessage(confirm.report)}
          confirmLabel="Place paper order"
          loading={entry.isPending}
          onCancel={() => setConfirm(null)}
          onConfirm={() =>
            entry.mutate(paperOrderBody(confirm.report, confirm.key), {
              onSuccess: (o) => router.push(`/orders/${o.id}`),
              onSettled: () => setConfirm(null),
            })
          }
        />
      ) : null}
    </Screen>
  );
}

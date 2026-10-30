import { useState } from 'react';
import { View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Badge, Button, Card, CardHeader, ConfirmDialog, KeyValue, NoticeBanner, QueryState, Screen } from '../../../components/ui';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { ORDER_TONE, SourceBadges, exitLabel } from '../../../features/portfolio/Lists';
import { useCancelOrder, useOrder } from '../../../features/trading/api';
import { fixed, formatINR, formatIST } from '../../../services/format';

const CANCELLABLE = ['SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED'];
const CHARGE_ROWS = [
  ['brokerage', 'Brokerage'],
  ['stt', 'STT'],
  ['exchange_txn', 'Exchange txn'],
  ['sebi', 'SEBI fee'],
  ['stamp_duty', 'Stamp duty'],
  ['gst', 'GST'],
];

function RiskChecks({ checks }) {
  const theme = useTheme();
  if (!checks.length) return <AppText tone="muted">No risk checks recorded (exit orders skip entry checks).</AppText>;
  return checks.map((c) => (
    <View key={c.key} style={{ flexDirection: 'row', gap: 8, paddingVertical: 5, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
      <Ionicons name={c.passed ? 'checkmark-circle' : 'close-circle'} size={16} color={theme.colors[c.passed ? 'positive' : 'negative']} style={{ marginTop: 2 }} />
      <View style={{ flex: 1 }}>
        <AppText variant="caption" style={{ fontWeight: '700' }}>{c.label ?? c.key}</AppText>
        {c.detail ? <AppText variant="caption" tone="negative">{c.detail}</AppText> : null}
      </View>
    </View>
  ));
}

export default function OrderDetail() {
  const { id } = useLocalSearchParams();
  const q = useOrder(id);
  const cancel = useCancelOrder();
  const theme = useTheme();
  const [confirm, setConfirm] = useState(false);
  return (
    <Screen edges={['bottom']} refreshing={q.isRefetching} onRefresh={q.refetch}>
      <QueryState query={q}>
        {(o) => (
          <>
            <Card testID="order-detail">
              <CardHeader title={o.instrument} subtitle={`${o.side} · ${o.purpose === 'EXIT' ? `exit (${exitLabel(o.exit_reason)})` : 'entry'} · ${o.order_type}`} right={<Badge label={o.status.replace('_', ' ')} tone={ORDER_TONE[o.status]} testID="order-status" />} />
              <SourceBadges mode={o.account_mode} dataSource={o.data_source} lotSource={o.lot_size_source} />
              <KeyValue label="Quantity" value={`${o.quantity} (${o.lots} lot${o.lots === 1 ? '' : 's'} × ${o.lot_size})`} />
              <KeyValue label="Filled" value={`${o.filled_qty} / ${o.quantity}`} />
              <KeyValue label="Avg fill" value={o.avg_fill_price ? `₹${fixed(o.avg_fill_price)}` : '—'} />
              {o.limit_price ? <KeyValue label="Limit price" value={`₹${fixed(o.limit_price)}`} /> : null}
              {o.purpose === 'ENTRY' ? <KeyValue label="Stop-loss / Target" value={`${o.stop_price ? `₹${fixed(o.stop_price)}` : '—'} / ${o.target_price ? `₹${fixed(o.target_price)}` : '—'}`} /> : null}
              <KeyValue label="Client order ID" value={o.client_order_id} />
              <KeyValue label="Paper broker ID" value={o.broker_order_id ?? '—'} />
              <KeyValue label="Created" value={`${formatIST(o.created_at, { withDate: true })} IST`} />
              {o.reject_reason ? <NoticeBanner tone="danger">{o.reject_reason}</NoticeBanner> : null}
              {o.lot_size_source === 'DEMO_PLACEHOLDER' ? (
                <AppText variant="caption" tone="warning">DEMO data has no exchange lot size, so 1 lot = {o.lot_size} unit here. This is a placeholder, not the NSE lot size.</AppText>
              ) : null}
              {CANCELLABLE.includes(o.status) ? <Button title="Cancel order" icon="close-circle-outline" variant="danger" size="sm" onPress={() => setConfirm(true)} testID="cancel-order" /> : null}
              {cancel.error ? <AppText tone="negative">{cancel.error.message}</AppText> : null}
            </Card>

            <Card>
              <CardHeader title="Order timeline" subtitle="Every state change is recorded" />
              {o.events.map((e, i) => (
                <View key={`${e.ts}-${i}`} style={{ paddingVertical: 6, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
                  <AppText variant="caption" style={{ fontWeight: '700' }}>
                    {e.to_status.replace('_', ' ')}{e.from_status ? ` (from ${e.from_status.replace('_', ' ')})` : ''} · {formatIST(e.ts, { withDate: true })}
                  </AppText>
                  <AppText variant="caption" tone="muted">{e.message}</AppText>
                </View>
              ))}
            </Card>

            <Card>
              <CardHeader title="Fills" subtitle="Simulated against bid/ask with slippage" />
              {o.fills.length ? (
                o.fills.map((f) => (
                  <View key={f.broker_fill_id} style={{ paddingVertical: 6, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle }}>
                    <AppText variant="bodyStrong">{f.side} {f.quantity} @ ₹{fixed(f.price)}</AppText>
                    <AppText variant="caption" tone="muted">{formatIST(f.ts, { withDate: true })} IST · charges {formatINR(f.charges_total)}</AppText>
                    <AppText variant="caption" tone="faint">
                      {CHARGE_ROWS.filter(([k]) => Number(f.charges[k])).map(([k, l]) => `${l} ${formatINR(f.charges[k])}`).join(' · ')}
                    </AppText>
                  </View>
                ))
              ) : (
                <AppText tone="muted">No fills yet.</AppText>
              )}
            </Card>

            <Card>
              <CardHeader title="Risk manager checks" subtitle="Global limits, shared by all bots" />
              <RiskChecks checks={o.risk_checks} />
              {o.charges_estimate ? (
                <AppText variant="caption" tone="faint" style={{ marginTop: 6 }}>
                  Estimated round-trip charges at entry: {formatINR(o.charges_estimate.total.total)} (break-even exit ₹{fixed(o.charges_estimate.break_even_price)})
                </AppText>
              ) : null}
            </Card>
            <AppText variant="caption" tone="faint">Paper order. No real money or broker is involved.</AppText>

            <ConfirmDialog
              visible={confirm}
              title="Cancel this order?"
              message="Any quantity already filled stays in your position."
              confirmLabel="Cancel order"
              destructive
              loading={cancel.isPending}
              onCancel={() => setConfirm(false)}
              onConfirm={() => cancel.mutate(o.id, { onSettled: () => setConfirm(false) })}
            />
          </>
        )}
      </QueryState>
    </Screen>
  );
}

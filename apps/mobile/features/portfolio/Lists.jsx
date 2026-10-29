import { useState } from 'react';
import { View } from 'react-native';
import { router } from 'expo-router';
import { AppText, Badge, Button, Card, ChipRow, ConfirmDialog, KeyValue, NoticeBanner, PnLText, QueryState, StateView } from '../../components/ui';
import { useExitPosition, useOrders, usePositions } from '../trading/api';
import { fixed, formatINR, formatIST } from '../../services/format';

export const ORDER_TONE = {
  CREATED: 'neutral',
  VALIDATED: 'neutral',
  SUBMITTED: 'warning',
  ACKNOWLEDGED: 'warning',
  PARTIALLY_FILLED: 'warning',
  CANCEL_PENDING: 'warning',
  UNKNOWN: 'negative',
  RECONCILIATION_REQUIRED: 'negative',
  FILLED: 'positive',
  CANCELLED: 'neutral',
  REJECTED: 'negative',
};

const EXIT_LABEL = {
  STOP_LOSS: 'Stop-loss',
  TARGET: 'Target',
  TRAILING_STOP: 'Trailing stop',
  SQUARE_OFF: 'Square-off time',
  LOSS_LIMIT: 'Daily loss stop',
  KILL_SWITCH: 'Kill switch',
  MANUAL: 'Manual exit',
};
export const exitLabel = (r) => EXIT_LABEL[r] ?? r ?? '—';

/** DEMO trades use a placeholder lot of 1 unit; say so wherever quantity is shown. */
export function SourceBadges({ dataSource, lotSource }) {
  return (
    <View style={{ flexDirection: 'row', gap: 4, flexWrap: 'wrap' }}>
      <Badge label="PAPER" tone="primary" />
      <Badge label={dataSource} tone={dataSource === 'DEMO' ? 'info' : 'positive'} />
      {lotSource === 'DEMO_PLACEHOLDER' ? <Badge label="PLACEHOLDER LOT" tone="warning" /> : null}
    </View>
  );
}

export function OrderCard({ o }) {
  return (
    <Card onPress={() => router.push(`/orders/${o.id}`)} accessibilityLabel={`Order ${o.instrument} ${o.status}`} testID={`order-${o.id}`}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <View style={{ flex: 1 }}>
          <AppText variant="bodyStrong">{o.instrument}</AppText>
          <AppText variant="caption" tone="muted">
            {o.side} {o.quantity} qty ({o.lots} lot{o.lots === 1 ? '' : 's'}) · {o.order_type} · {o.purpose === 'EXIT' ? `exit: ${exitLabel(o.exit_reason)}` : 'entry'} · {formatIST(o.created_at, { withDate: true })} IST
          </AppText>
        </View>
        <Badge label={o.status.replace('_', ' ')} tone={ORDER_TONE[o.status]} />
      </View>
      {o.avg_fill_price ? <KeyValue label="Avg fill" value={`₹${fixed(o.avg_fill_price)} × ${o.filled_qty}`} /> : null}
      {o.reject_reason ? <AppText variant="caption" tone="negative">{o.reject_reason}</AppText> : null}
    </Card>
  );
}

export function OrdersList({ botId }) {
  const [status, setStatus] = useState('PENDING');
  const q = useOrders(status, botId);
  return (
    <View style={{ gap: 12 }}>
      <ChipRow
        value={status}
        onChange={setStatus}
        testIDPrefix="orders"
        options={[
          { value: 'PENDING', label: 'Open' },
          { value: 'EXECUTED', label: 'Executed' },
          { value: 'CANCELLED', label: 'Cancelled' },
          { value: 'REJECTED', label: 'Rejected' },
        ]}
      />
      <QueryState query={q}>
        {(page) =>
          page.items.length ? (
            <>
              {page.items.map((o) => <OrderCard key={o.id} o={o} />)}
              {page.total > page.items.length ? <AppText variant="caption" tone="faint">Showing latest {page.items.length} of {page.total}.</AppText> : null}
            </>
          ) : (
            <StateView kind="empty" title={`No ${status === 'PENDING' ? 'open' : status.toLowerCase()} orders`} message="Paper orders appear here when you buy from the option chain or a running bot signals." />
          )
        }
      </QueryState>
    </View>
  );
}

export function PositionCard({ p }) {
  const exit = useExitPosition();
  const [confirm, setConfirm] = useState(false);
  const open = p.status !== 'CLOSED';
  const pnl = open ? p.unrealized_pnl : p.realized_net;
  const pct = open && p.ltp ? ((Number(p.ltp) - Number(p.avg_price)) / Number(p.avg_price)) * 100 : null;
  return (
    <Card testID={`position-${p.id}`}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <View style={{ flex: 1, gap: 4 }}>
          <AppText variant="bodyStrong">{p.instrument}</AppText>
          <SourceBadges dataSource={p.data_source} />
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <PnLText value={pnl} prefix="₹" variant="bodyStrong" />
          <AppText variant="caption" tone="faint">{open ? 'Unrealised (gross)' : 'Net of charges'}</AppText>
        </View>
      </View>
      <KeyValue label="Quantity" value={`${open ? p.quantity : p.entry_qty} (${p.lots} lot${p.lots === 1 ? '' : 's'})`} />
      <KeyValue label={open ? 'Avg / LTP' : 'Entry / Exit'} value={open ? `₹${fixed(p.avg_price)} / ${p.ltp ? `₹${fixed(p.ltp)}` : '—'}` : `₹${fixed(p.avg_price)} / ₹${fixed(p.avg_exit)}`} />
      {pct != null ? <KeyValue label="Premium change" value={`${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`} tone={pct >= 0 ? 'positive' : 'negative'} /> : null}
      <KeyValue label="Stop-loss / Target" value={`${p.stop_loss ? `₹${fixed(p.stop_loss)}` : '—'} / ${p.target ? `₹${fixed(p.target)}` : '—'}${p.trailing_pct ? ` · trail ${Number(p.trailing_pct)}%` : ''}`} />
      {open ? null : (
        <>
          <KeyValue label="Gross / Charges" value={`${formatINR(p.realized_gross)} / ${formatINR(p.charges)}`} />
          <KeyValue label="Exit reason" value={exitLabel(p.exit_reason)} />
        </>
      )}
      <AppText variant="caption" tone="faint">
        Opened {formatIST(p.opened_at, { withDate: true })} IST{p.closed_at ? ` · closed ${formatIST(p.closed_at, { withDate: true })} IST` : ''}
        {open && p.ltp_at ? ` · LTP ${formatIST(p.ltp_at)} IST` : ''}
      </AppText>
      {p.status === 'CLOSING' ? <NoticeBanner tone="info">Exit order working. It fills at the bid when one is available.</NoticeBanner> : null}
      {p.status === 'OPEN' ? <Button title="Exit at market" icon="exit-outline" size="sm" variant="danger" onPress={() => setConfirm(true)} testID={`exit-${p.id}`} /> : null}
      {exit.error ? <AppText tone="negative">{exit.error.message}</AppText> : null}
      <ConfirmDialog
        visible={confirm}
        title="Exit this paper position?"
        message={`A SELL order for ${p.quantity} qty of ${p.instrument} is sent at market. It fills at the current bid minus slippage, and exit charges apply.`}
        confirmLabel="Exit"
        destructive
        loading={exit.isPending}
        onCancel={() => setConfirm(false)}
        onConfirm={() => exit.mutate(p.id, { onSettled: () => setConfirm(false) })}
      />
    </Card>
  );
}

export function PositionsList({ botId }) {
  const [state, setState] = useState('OPEN');
  const q = usePositions(state, botId);
  return (
    <View style={{ gap: 12 }}>
      <ChipRow value={state} onChange={setState} testIDPrefix="positions" options={[{ value: 'OPEN', label: 'Open' }, { value: 'CLOSED', label: 'Closed' }]} />
      <QueryState query={q}>
        {(page) =>
          page.items.length ? page.items.map((p) => <PositionCard key={p.id} p={p} />) : (
            <StateView kind="empty" title={`No ${state.toLowerCase()} positions`} message="Positions are created from confirmed paper fills." />
          )
        }
      </QueryState>
    </View>
  );
}

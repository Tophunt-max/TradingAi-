import { useState } from 'react';
import { View } from 'react-native';
import { AppText, Badge, Card, ChipRow, KeyValue, NoticeBanner, QueryState, StateView, ToggleRow } from '../../components/ui';
import { useOrders, usePositions } from '../trading/api';
import { SAMPLE_ORDERS, SAMPLE_POSITIONS } from './samples';
import { formatIST } from '../../services/format';

const ORDER_TONE = { PENDING: 'warning', EXECUTED: 'positive', CANCELLED: 'neutral', REJECTED: 'negative' };

export function SampleToggle({ value, onChange }) {
  return (
    <>
      <ToggleRow title="Preview sample layout" subtitle="Shows labelled sample rows, not your trades" value={value} onValueChange={onChange} />
      {value ? <NoticeBanner tone="primary" testID="sample-banner">SAMPLE DATA: illustrative rows only. These are not your orders, positions or profits.</NoticeBanner> : null}
    </>
  );
}

function OrderCard({ o, sample }) {
  return (
    <Card>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <View style={{ flex: 1 }}>
          <AppText variant="bodyStrong">{o.instrument}</AppText>
          <AppText variant="caption" tone="muted">
            {o.side} {o.quantity} lot · {o.order_type} · {formatIST(o.created_at)} IST
          </AppText>
        </View>
        <View style={{ alignItems: 'flex-end', gap: 4 }}>
          <Badge label={o.status} tone={ORDER_TONE[o.status]} />
          {sample ? <Badge label="SAMPLE" tone="primary" /> : null}
        </View>
      </View>
      {o.avg_fill_price ? <KeyValue label="Avg fill" value={`₹${o.avg_fill_price}`} /> : null}
    </Card>
  );
}

export function OrdersList() {
  const [status, setStatus] = useState('PENDING');
  const [sample, setSample] = useState(false);
  const q = useOrders(status);
  return (
    <View style={{ gap: 12 }}>
      <ChipRow
        value={status}
        onChange={setStatus}
        options={[
          { value: 'PENDING', label: 'Pending' },
          { value: 'EXECUTED', label: 'Executed' },
          { value: 'CANCELLED', label: 'Cancelled' },
          { value: 'REJECTED', label: 'Rejected' },
        ]}
      />
      <SampleToggle value={sample} onChange={setSample} />
      {sample ? (
        SAMPLE_ORDERS.filter((o) => o.status === status).map((o) => <OrderCard key={o.id} o={o} sample />)
      ) : (
        <QueryState query={q}>
          {(page) =>
            page.items.length ? page.items.map((o) => <OrderCard key={o.id} o={o} />) : (
              <StateView kind="empty" title={`No ${status.toLowerCase()} orders`} message="Paper orders appear here once the execution engine runs (Phase 6)." />
            )
          }
        </QueryState>
      )}
    </View>
  );
}

function PositionCard({ p, sample }) {
  const chg = p.ltp ? ((Number(p.ltp) - Number(p.avg_price)) / Number(p.avg_price)) * 100 : null;
  return (
    <Card>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <AppText variant="bodyStrong">{p.instrument}</AppText>
        {sample ? <Badge label="SAMPLE" tone="primary" /> : null}
      </View>
      <KeyValue label="Quantity" value={`${p.quantity} lot`} />
      <KeyValue label="Avg / LTP" value={`₹${p.avg_price} / ${p.ltp ? `₹${p.ltp}` : '—'}`} />
      {chg != null ? <KeyValue label="Premium change" value={`${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`} tone={chg >= 0 ? 'positive' : 'negative'} /> : null}
      <KeyValue label="Stop-loss / Target" value={`${p.stop_loss ?? '—'} / ${p.target ?? '—'}`} />
    </Card>
  );
}

export function PositionsList() {
  const [state, setState] = useState('OPEN');
  const [sample, setSample] = useState(false);
  const q = usePositions(state);
  return (
    <View style={{ gap: 12 }}>
      <ChipRow value={state} onChange={setState} options={[{ value: 'OPEN', label: 'Open' }, { value: 'CLOSED', label: 'Closed' }]} />
      <SampleToggle value={sample} onChange={setSample} />
      {sample ? (
        (state === 'OPEN' ? SAMPLE_POSITIONS : []).map((p) => <PositionCard key={p.id} p={p} sample />)
      ) : (
        <QueryState query={q}>
          {(page) =>
            page.items.length ? page.items.map((p) => <PositionCard key={p.id} p={p} />) : (
              <StateView kind="empty" title={`No ${state.toLowerCase()} positions`} message="Positions are created from confirmed fills once paper execution runs." />
            )
          }
        </QueryState>
      )}
    </View>
  );
}

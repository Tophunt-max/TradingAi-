import { useState } from 'react';
import { View } from 'react-native';
import { AccountModeBadge, AppText, Badge, Button, Card, CardHeader, ConfirmDialog, KeyValue, NoticeBanner, PnLText, QueryState, Screen } from '../../components/ui';
import { DEMO_RESET_MESSAGE, useAccountMode, useDemoLedger, useDemoReset } from '../../features/account/mode';
import { formatINR, formatIST } from '../../services/format';

const KIND_LABELS = {
  OPENING_CREDIT: 'Opening balance',
  TRADE_BUY: 'Option bought',
  TRADE_SELL: 'Option sold',
  CHARGES: 'Charges (estimated)',
  RESET_CLOSE: 'Closed by reset',
  RESET_ADJUSTMENT: 'Balance reset',
};

function AccountCard({ a, active }) {
  const demo = a.mode === 'DEMO';
  return (
    <Card testID={`account-card-${a.mode.toLowerCase()}`}>
      <CardHeader
        title={demo ? 'Demo account' : 'Real account'}
        right={
          <View style={{ flexDirection: 'row', gap: 6 }}>
            <AccountModeBadge mode={a.mode} />
            {active ? <Badge label="ACTIVE" tone="positive" /> : null}
          </View>
        }
      />
      {demo ? (
        <>
          <KeyValue label="Virtual cash" value={formatINR(a.cash)} />
          <KeyValue label="Available to trade" value={formatINR(a.available_funds)} />
          <KeyValue label="Equity (cash + open positions)" value={formatINR(a.equity)} />
          <KeyValue label="Premium in open positions" value={formatINR(a.used_premium)} />
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <AppText tone="muted">Realised net P&L (all time)</AppText>
            <PnLText value={a.realized_pnl_total} prefix="₹" variant="bodyStrong" />
          </View>
          <KeyValue label="Opening balance" value={formatINR(a.opening_balance)} />
          <KeyValue label="Resets" value={a.reset_count ? `${a.reset_count} · last ${formatIST(a.last_reset_at, { withDate: true })} IST` : 'Never'} />
        </>
      ) : (
        <>
          <KeyValue label="Status" value={a.status === 'NOT_CONNECTED' ? 'Not connected' : a.status} />
          <KeyValue label="Balance" value="—" />
          {(a.unavailable_reasons ?? []).slice(0, 3).map((r) => (
            <AppText key={r} variant="caption" tone="warning">{`• ${r}`}</AppText>
          ))}
        </>
      )}
      <AppText variant="caption" tone="faint">{a.note}</AppText>
    </Card>
  );
}

function LedgerCard() {
  const q = useDemoLedger(50);
  return (
    <Card testID="demo-ledger">
      <CardHeader title="Demo ledger" subtitle="Every change to your virtual cash, newest first" />
      <QueryState query={q} isEmpty={(d) => !d.items.length} emptyMessage="No ledger entries yet.">
        {(d) =>
          d.items.map((e) => (
            <View key={e.id} style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8, paddingVertical: 6 }}>
              <View style={{ flex: 1 }}>
                <AppText variant="bodyStrong">{KIND_LABELS[e.kind] ?? e.kind}</AppText>
                <AppText variant="caption" tone="muted">{`${formatIST(e.created_at, { withDate: true })} IST${e.note ? ` · ${e.note}` : ''}`}</AppText>
              </View>
              <View style={{ alignItems: 'flex-end' }}>
                <PnLText value={e.amount} prefix="₹" />
                <AppText variant="caption" tone="faint">{formatINR(e.balance_after)}</AppText>
              </View>
            </View>
          ))
        }
      </QueryState>
    </Card>
  );
}

export function DemoResetCard() {
  const reset = useDemoReset();
  const [confirm, setConfirm] = useState(false);
  return (
    <Card>
      <CardHeader title="Reset demo account" />
      <AppText variant="caption" tone="muted">Start again with ₹10,000 of virtual money. Your real account is never affected.</AppText>
      {reset.isSuccess ? (
        <NoticeBanner tone="info" testID="demo-reset-done">
          {`Demo account reset: ${reset.data.positions_closed} position(s) closed, ${reset.data.orders_cancelled} order(s) cancelled. Balance ${formatINR(reset.data.account.cash)}.`}
        </NoticeBanner>
      ) : null}
      {reset.error ? <AppText tone="negative">{reset.error.message}</AppText> : null}
      <Button title="Reset demo account" icon="refresh-outline" variant="danger" onPress={() => setConfirm(true)} testID="demo-reset" />
      <ConfirmDialog
        visible={confirm}
        title="Reset demo account?"
        message={DEMO_RESET_MESSAGE}
        confirmLabel="Reset demo"
        destructive
        loading={reset.isPending}
        onCancel={() => setConfirm(false)}
        onConfirm={() => reset.mutate(undefined, { onSettled: () => setConfirm(false) })}
      />
    </Card>
  );
}

export default function AccountScreen() {
  const account = useAccountMode();
  return (
    <Screen edges={['bottom']}>
      <QueryState query={account.query}>
        {(d) => (
          <>
            {d.accounts.map((a) => (
              <AccountCard key={a.mode} a={a} active={a.mode === d.active_mode} />
            ))}
          </>
        )}
      </QueryState>
      <LedgerCard />
      <DemoResetCard />
      <NoticeBanner>Demo and real balances, orders, positions and P&L are kept in separate accounts and are never added together.</NoticeBanner>
    </Screen>
  );
}

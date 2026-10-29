import { useState } from 'react';
import { View } from 'react-native';
import { router } from 'expo-router';
import { AccountModeBadge, AppText, Button, Card, CardHeader, KeyValue, NoticeBanner, QueryState, Screen, SegmentedControl, StatTile } from '../../../components/ui';
import { OrdersList, PositionCard, PositionsList } from '../../../features/portfolio/Lists';
import { usePortfolio, usePositions } from '../../../features/trading/api';
import { formatINR } from '../../../services/format';

function OpenPositions() {
  const q = usePositions('OPEN');
  if (!q.data?.items.length) {
    return (
      <Card>
        <CardHeader title="Open positions" />
        <AppText tone="muted">No open position. Buy a CE/PE from the option chain or start a bot.</AppText>
        <Button title="Open option chain" icon="grid-outline" size="sm" variant="secondary" onPress={() => router.push('/option-chain')} testID="portfolio-open-chain" />
      </Card>
    );
  }
  return q.data.items.map((p) => <PositionCard key={p.id} p={p} />);
}

function Summary() {
  const q = usePortfolio();
  return (
    <QueryState query={q}>
      {(p) => (
        <>
          <Card testID="portfolio-summary">
            <CardHeader title="Total P&L" subtitle="Demo account · virtual funds · net of charges" right={<AccountModeBadge mode="DEMO" />} />
            <AppText variant="display" tone={Number(p.total_net_pnl) > 0 ? 'positive' : Number(p.total_net_pnl) < 0 ? 'negative' : 'default'}>
              {formatINR(p.total_net_pnl)}
            </AppText>
            <View style={{ flexDirection: 'row', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
              <StatTile label="Today's P&L" value={formatINR(p.realized_pnl_today)} />
              <StatTile label="Entries today" value={`${p.trades_today} / ${p.max_entries_per_day}`} />
              <StatTile label="Charges today" value={formatINR(p.charges_today)} />
            </View>
            {p.data_source ? <AppText variant="caption" tone="faint" style={{ marginTop: 8 }}>Latest trade priced from {p.data_source} data.</AppText> : null}
          </Card>
          {p.loss_limit_hit ? <NoticeBanner tone="danger" testID="loss-limit-hit">Daily loss stop reached. New entries are blocked for the rest of the day.</NoticeBanner> : null}
          {p.profit_lock_hit ? <NoticeBanner tone="primary" testID="profit-lock-hit">Daily profit lock reached. New entries are blocked; open positions keep their stop-loss and target.</NoticeBanner> : null}
          <OpenPositions />
          <Card>
            <CardHeader title="Funds & limits" right={<Button title="Risk Center" size="sm" variant="ghost" onPress={() => router.push('/risk-center')} />} />
            <KeyValue label="Available balance" value={formatINR(p.available_funds)} />
            <KeyValue label="Total capital" value={formatINR(p.capital)} />
            <KeyValue label="Used margin" value={formatINR(p.used_margin)} />
            <KeyValue label="Unrealised P&L" value={formatINR(p.unrealized_pnl)} />
            <KeyValue label="Gross P&L today" value={formatINR(p.gross_pnl_today)} />
            <KeyValue label="Daily loss limit" value={formatINR(p.daily_loss_limit)} />
            <KeyValue label="Daily profit lock" value={formatINR(p.daily_profit_lock)} />
          </Card>
          <Button title="Charges calculator" icon="calculator-outline" variant="secondary" onPress={() => router.push('/charges')} testID="open-charges" />
          <AppText variant="caption" tone="faint">{p.note}</AppText>
        </>
      )}
    </QueryState>
  );
}

export default function Portfolio() {
  const [tab, setTab] = useState('SUMMARY');
  return (
    <Screen title="Portfolio" subtitle="Positions, orders and P&L">
      <SegmentedControl
        value={tab}
        onChange={setTab}
        testID="portfolio-tabs"
        options={[
          { value: 'SUMMARY', label: 'Summary' },
          { value: 'POSITIONS', label: 'Positions' },
          { value: 'ORDERS', label: 'Orders' },
        ]}
      />
      {tab === 'SUMMARY' ? <Summary /> : tab === 'POSITIONS' ? <PositionsList /> : <OrdersList />}
    </Screen>
  );
}

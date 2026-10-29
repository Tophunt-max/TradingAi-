import { useState } from 'react';
import { View } from 'react-native';
import { router } from 'expo-router';
import { AppText, Badge, Button, Card, CardHeader, KeyValue, NoticeBanner, QueryState, Screen, SegmentedControl, StatTile } from '../../../components/ui';
import { OrdersList, PositionsList } from '../../../features/portfolio/Lists';
import { usePortfolio } from '../../../features/trading/api';
import { formatINR } from '../../../services/format';

function Summary() {
  const q = usePortfolio();
  return (
    <QueryState query={q}>
      {(p) => (
        <>
          <Card testID="portfolio-summary">
            <CardHeader title="Total P&L" subtitle="Paper account · net of charges" right={<Badge label="PAPER" tone="primary" />} />
            <AppText variant="display" tone={Number(p.total_net_pnl) > 0 ? 'positive' : Number(p.total_net_pnl) < 0 ? 'negative' : 'default'}>
              {formatINR(p.total_net_pnl)}
            </AppText>
            <View style={{ flexDirection: 'row', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
              <StatTile label="Today's P&L" value={formatINR(p.realized_pnl_today)} />
              <StatTile label="Total trades" value={`${p.trades_today} / ${p.max_entries_per_day}`} />
            </View>
          </Card>
          {!p.engine_active ? <NoticeBanner tone="info">{p.note}</NoticeBanner> : null}
          <Card>
            <CardHeader title="Open position" />
            <AppText tone="muted">{p.open_positions ? `${p.open_positions} open` : 'No open position.'}</AppText>
          </Card>
          <Card>
            <CardHeader title="Funds & limits" right={<Button title="Risk Center" size="sm" variant="ghost" onPress={() => router.push('/risk-center')} />} />
            <KeyValue label="Available balance" value={formatINR(p.available_funds)} />
            <KeyValue label="Total capital" value={formatINR(p.capital)} />
            <KeyValue label="Used margin" value={formatINR(p.used_margin)} />
            <KeyValue label="Unrealised P&L" value={formatINR(p.unrealized_pnl)} />
            <KeyValue label="Charges today" value={formatINR(p.charges_today)} />
            <KeyValue label="Daily loss limit" value={formatINR(p.daily_loss_limit)} />
            <KeyValue label="Daily profit lock" value={formatINR(p.daily_profit_lock)} />
          </Card>
          <AppText variant="caption" tone="faint">Paper results are simulated and are never mixed with live P&L.</AppText>
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

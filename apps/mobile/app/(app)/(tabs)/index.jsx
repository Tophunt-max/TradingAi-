import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useQueryClient } from '@tanstack/react-query';
import { AppText, Badge, Button, Card, CardHeader, KeyValue, ListRow, QuickAction, Screen, StatTile } from '../../../components/ui';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { BrandMark } from '../../../features/auth/BrandMark';
import { IndexHeroCards } from '../../../features/market/IndexHeroCards';
import { useAnalysis } from '../../../features/market/api';
import { TradingModeBanner } from '../../../features/system/TradingModeBanner';
import { useAlerts, useBots, usePortfolio, useRisk } from '../../../features/trading/api';
import { useResponsive } from '../../../hooks/useResponsive';
import { formatINR, formatIST } from '../../../services/format';
import { useAuthStore } from '../../../store/auth';

function IconButton({ icon, label, onPress, testID }) {
  const theme = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={label} onPress={onPress} hitSlop={8} style={{ padding: 8, borderRadius: 12, backgroundColor: theme.colors.surface }}>
      <Ionicons name={icon} size={20} color={theme.colors.text} />
    </Pressable>
  );
}

const TREND_TONE = { BULLISH: 'positive', BEARISH: 'negative', SIDEWAYS: 'warning' };

function InsightCard() {
  const theme = useTheme();
  const a = useAnalysis('NIFTY', '15m');
  return (
    <Card testID="insight-card" style={{ borderColor: theme.colors.primary, backgroundColor: theme.colors.primarySoft }}>
      <View style={{ flexDirection: 'row', gap: 12, alignItems: 'center' }}>
        <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: theme.colors.surface, alignItems: 'center', justifyContent: 'center' }}>
          <Ionicons name="sparkles" size={22} color={theme.colors.primary} />
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <AppText variant="heading">Market Insight</AppText>
          {a.data ? (
            <AppText variant="caption" tone="muted">
              NIFTY 15m: <AppText variant="caption" tone={TREND_TONE[a.data.trend]}>{a.data.trend}</AppText>
              {a.data.regime ? ` · ${a.data.regime.label.replace('_', ' ').toLowerCase()} regime` : ''} · Decision: NO TRADE
            </AppText>
          ) : (
            <AppText variant="caption" tone="muted">{a.isError ? a.error.message : 'Loading…'}</AppText>
          )}
        </View>
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 10 }}>
        {a.data?.probabilities ? (
          <Badge label={`UP ${Math.round(a.data.probabilities.UP * 100)}% · DOWN ${Math.round(a.data.probabilities.DOWN * 100)}%`} tone="info" />
        ) : (
          <Badge label="NO PROMOTED MODEL" tone="warning" />
        )}
        <Button title="View analysis" size="sm" variant="ghost" icon="arrow-forward" onPress={() => router.push('/ai-lab')} />
      </View>
    </Card>
  );
}

function PerformanceCard() {
  const p = usePortfolio();
  const d = p.data;
  return (
    <Card testID="performance-card">
      <CardHeader title="Today's Performance" subtitle="Paper account" right={<Badge label="PAPER" tone="primary" />} />
      <View style={{ flexDirection: 'row', gap: 10, flexWrap: 'wrap' }}>
        <StatTile label="Total P&L" value={d ? formatINR(d.total_net_pnl) : '—'} />
        <StatTile label="Trades" value={d ? `${d.trades_today} / ${d.max_entries_per_day}` : '—'} sub={d ? `Open positions ${d.open_positions}` : undefined} />
      </View>
      {d && !d.engine_active ? <AppText variant="caption" tone="faint" style={{ marginTop: 8 }}>{d.note}</AppText> : null}
    </Card>
  );
}

function RiskAndBotsCard() {
  const risk = useRisk();
  const bots = useBots();
  const r = risk.data;
  return (
    <Card>
      <CardHeader title="Risk & Bots" right={<Button title="Risk Center" size="sm" variant="ghost" onPress={() => router.push('/risk-center')} />} />
      <KeyValue label="Daily loss limit used" value={r ? `${formatINR(0)} / ${formatINR(r.limits.daily_loss_limit)}` : '—'} />
      <KeyValue label="Entries used" value={r ? `${r.usage.entries_used} / ${r.limits.max_entries_per_day}` : '—'} />
      <KeyValue label="Kill switch" value={r ? (r.limits.kill_switch_engaged ? 'ENGAGED' : 'Off') : '—'} tone={r?.limits.kill_switch_engaged ? 'negative' : undefined} />
      <KeyValue label="Bots" value={bots.data ? `${bots.data.length} (all drafts)` : '—'} />
    </Card>
  );
}

function RecentAlerts() {
  const a = useAlerts();
  const items = a.data?.slice(0, 3) ?? [];
  return (
    <Card>
      <CardHeader title="Recent alerts" right={<Button title="All" size="sm" variant="ghost" onPress={() => router.push('/alerts')} />} />
      {items.length === 0 ? <AppText variant="caption" tone="muted">No alerts yet.</AppText> : null}
      {items.map((it) => (
        <ListRow key={it.id} title={it.title} subtitle={`${formatIST(it.created_at, { withDate: true })} IST`} icon={it.severity === 'critical' ? 'alert-circle-outline' : 'notifications-outline'} />
      ))}
    </Card>
  );
}

export default function Home() {
  const user = useAuthStore((s) => s.user);
  const qc = useQueryClient();
  const { columns } = useResponsive();
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = async () => {
    setRefreshing(true);
    await qc.invalidateQueries();
    setRefreshing(false);
  };

  const header = (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <BrandMark size={34} />
      <View style={{ flex: 1 }}>
        <AppText variant="heading">{'QuantAI F&O'}</AppText>
        <AppText variant="caption" tone="muted">{`Hi, ${user?.full_name?.split(' ')[0] ?? 'Trader'}`}</AppText>
      </View>
      <IconButton icon="notifications-outline" label="Notifications" onPress={() => router.push('/alerts')} testID="home-alerts" />
      <IconButton icon="apps-outline" label="More" onPress={() => router.push('/more')} />
      <IconButton icon="person-circle-outline" label="Profile and settings" onPress={() => router.push('/settings')} />
    </View>
  );

  const left = (
    <View style={{ gap: 16 }}>
      <IndexHeroCards />
      <Card padded={false} style={{ paddingVertical: 14, paddingHorizontal: 8 }}>
        <View style={{ flexDirection: 'row' }}>
          <QuickAction icon="trending-up" label="Markets" onPress={() => router.push('/markets')} />
          <QuickAction icon="sparkles-outline" label="AI Insights" onPress={() => router.push('/ai-lab')} />
          <QuickAction icon="hardware-chip-outline" label="My Bots" onPress={() => router.push('/bots')} />
          <QuickAction icon="grid-outline" label="Options" onPress={() => router.push('/option-chain')} testID="qa-options" />
        </View>
      </Card>
      <InsightCard />
    </View>
  );
  const right = (
    <View style={{ gap: 16 }}>
      <PerformanceCard />
      <RiskAndBotsCard />
      <RecentAlerts />
    </View>
  );

  return (
    <Screen refreshing={refreshing} onRefresh={onRefresh}>
      {header}
      <TradingModeBanner />
      {columns > 1 ? (
        <View style={{ flexDirection: 'row', gap: 16, alignItems: 'flex-start' }}>
          <View style={{ flex: 1 }}>{left}</View>
          <View style={{ flex: 1 }}>{right}</View>
        </View>
      ) : (
        <>
          {left}
          {right}
        </>
      )}
      <AppText variant="caption" tone="faint" style={{ textAlign: 'center' }}>
        No profits or prediction accuracy are guaranteed.
      </AppText>
    </Screen>
  );
}

import { View } from 'react-native';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../../components/theme/ThemeProvider';
import { AppText, Badge, Card, ModeBadge } from '../../components/ui';
import { formatIST } from '../../services/format';
import { HEALTH, STATUS_TONE } from './health';

const METHOD_LABEL = { AI: 'AI GENERATED', MANUAL: 'MANUAL', TEMPLATE: 'TEMPLATE', CLONE: 'CLONE' };

export function BotCard({ bot }) {
  const theme = useTheme();
  const dash = (v) => (v == null ? '—' : v);
  const health = HEALTH[bot.health] ?? HEALTH.NOT_RUNNING;
  return (
    <Card testID={`bot-${bot.id}`} onPress={() => router.push(`/bots/${bot.id}`)} accessibilityLabel={`${bot.name}, ${bot.status}`}>
      <View style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start' }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.primarySoft, alignItems: 'center', justifyContent: 'center' }}>
          <Ionicons name={bot.creation_method === 'AI' ? 'sparkles' : 'hardware-chip-outline'} size={20} color={theme.colors.primary} />
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <AppText variant="bodyStrong" numberOfLines={1}>{bot.name}</AppText>
          <AppText variant="caption" tone="muted">
            {bot.underlying} · {bot.strategy_name} · {bot.timeframe}
          </AppText>
          <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
            <Badge label={bot.status} tone={STATUS_TONE[bot.status]} />
            <ModeBadge live={bot.mode === 'LIVE'} />
            {bot.status === 'ACTIVE' ? <Badge label="SIGNAL-ONLY" tone="info" /> : null}
            <Badge label={METHOD_LABEL[bot.creation_method]} tone={bot.creation_method === 'AI' ? 'info' : 'neutral'} />
          </View>
        </View>
        <Ionicons name="chevron-forward" size={18} color={theme.colors.textFaint} />
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 12 }}>
        <Metric label="Today P&L" value={dash(bot.today_pnl)} />
        <Metric label="Signals today" value={bot.signals_today} />
        <Metric label="Trades" value={bot.trade_count} />
        <Metric label="Health" value={health.label} tone={health.tone} />
      </View>
      <AppText variant="caption" tone="faint" style={{ marginTop: 8 }} numberOfLines={2}>
        {bot.last_signal ? `Last signal ${bot.last_signal} · ${formatIST(bot.last_signal_at, { withDate: true })} IST` : bot.health_detail ?? 'No signals yet.'}
      </AppText>
    </Card>
  );
}

function Metric({ label, value, tone }) {
  return (
    <View>
      <AppText variant="caption" tone="faint">{label}</AppText>
      <AppText variant="caption" tone={tone === 'neutral' ? 'muted' : tone} style={{ fontWeight: '600' }}>{String(value)}</AppText>
    </View>
  );
}

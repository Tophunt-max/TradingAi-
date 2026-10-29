import { Pressable, View } from 'react-native';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { AppText, Card, CardHeader, ListRow, Screen } from '../../../components/ui';
import { useTheme } from '../../../components/theme/ThemeProvider';
import { IndexQuotesCard } from '../../../features/market/IndexQuotes';
import { TradingModeBanner } from '../../../features/system/TradingModeBanner';
import { FEATURES } from '../../../features/roadmap';
import { useResponsive } from '../../../hooks/useResponsive';
import { useAuthStore } from '../../../store/auth';

const PLANNED_WIDGETS = [
  { label: 'AI market summary & regime', feature: 'ai-analysis' },
  { label: 'Active bots & bot health', feature: 'bot-builder' },
  { label: "Open positions & today's P&L", feature: 'portfolio' },
  { label: 'Funds, margin & risk-limit usage', feature: 'risk-center' },
  { label: 'Recent orders', feature: 'orders' },
  { label: 'Alerts', feature: 'alerts' },
];

function HeaderActions() {
  const theme = useTheme();
  const btn = (icon, label, onPress) => (
    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress} hitSlop={8} style={{ padding: 8, borderRadius: 12, backgroundColor: theme.colors.surface }}>
      <Ionicons name={icon} size={20} color={theme.colors.text} />
    </Pressable>
  );
  return (
    <View style={{ flexDirection: 'row', gap: 8 }}>
      {btn('apps-outline', 'More', () => router.push('/more'))}
      {btn('person-circle-outline', 'Profile and settings', () => router.push('/settings'))}
    </View>
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

  return (
    <Screen title={`Hi, ${user?.full_name?.split(' ')[0] ?? 'Trader'}`} subtitle="Home dashboard" right={<HeaderActions />} refreshing={refreshing} onRefresh={onRefresh}>
      <TradingModeBanner />
      <View style={{ flexDirection: columns > 1 ? 'row' : 'column', gap: 16, alignItems: 'flex-start' }}>
        <View style={columns > 1 ? { flex: 1 } : { width: '100%' }}>
          <IndexQuotesCard title="Market indices" />
        </View>
        <View style={columns > 1 ? { flex: 1 } : { width: '100%' }}>
          <Card testID="planned-widgets">
            <CardHeader title="Dashboard widgets" subtitle="Shown once their backend modules exist" />
            {PLANNED_WIDGETS.map((w) => (
              <ListRow
                key={w.label}
                title={w.label}
                subtitle={`Phase ${FEATURES[w.feature].phase} · not built yet`}
                icon={FEATURES[w.feature].icon}
                onPress={() => router.push(`/feature/${w.feature}`)}
              />
            ))}
          </Card>
        </View>
      </View>
      <AppText variant="caption" tone="faint" style={{ textAlign: 'center' }}>
        No profits or prediction accuracy are guaranteed.
      </AppText>
    </Screen>
  );
}

import { useState } from 'react';
import { Pressable, View } from 'react-native';
import { router } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { AppText, Card, CardHeader, KeyValue, ListRow, NoticeBanner, Screen } from '../../components/ui';
import { useTheme } from '../../components/theme/ThemeProvider';
import { useSystemStatus } from '../../features/system/api';
import { API_URL } from '../../services/config';

// Optional, public contact for this deployment. Nothing is shown when unset,
// so the app never displays an invented address.
const SUPPORT_EMAIL = process.env.EXPO_PUBLIC_SUPPORT_EMAIL || null;

const FAQ = [
  ['What is the Demo account?', 'Every new user gets ₹10,000 of virtual money. Orders, fills, positions and P&L in the Demo account are simulated and never reach a broker.'],
  ['When can I use a Real account?', 'Only after a verified broker integration is connected and every live-trading safety check passes. Until then the Real account is shown as unavailable.'],
  ['Are demo profits real?', 'No. Simulated results include estimated spread, slippage and charges, but real fills can be worse. Past or simulated results do not guarantee future returns.'],
  ['Why does the AI say NO TRADE?', 'The AI abstains when data quality, model calibration, costs or risk checks do not support a trade. A probability alone never authorises an order.'],
  ['What does "SIMULATED" mean?', 'Market prices marked SIMULATED are synthetic, generated for testing. They are not real market quotes.'],
  ['Can a bot change my risk limits?', 'No. Only you can change risk limits. Bots and AI read them and every entry must pass the global risk manager.'],
];

function Faq({ q, a }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  return (
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen((o) => !o)} style={{ paddingVertical: 10, borderBottomWidth: 1, borderColor: theme.colors.borderSubtle, gap: 6 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <AppText variant="bodyStrong" style={{ flex: 1 }}>{q}</AppText>
        <Ionicons name={open ? 'chevron-up' : 'chevron-down'} size={16} color={theme.colors.textMuted} />
      </View>
      {open ? <AppText variant="caption" tone="muted">{a}</AppText> : null}
    </Pressable>
  );
}

export default function Help() {
  const status = useSystemStatus();
  return (
    <Screen edges={['bottom']}>
      <Card>
        <CardHeader title="Frequently asked questions" />
        {FAQ.map(([q, a]) => <Faq key={q} q={q} a={a} />)}
      </Card>
      <Card>
        <CardHeader title="Guides" />
        <ListRow title="Learn & Grow" subtitle="Strategies, indicators, risk and glossary" icon="school-outline" onPress={() => router.push('/education')} />
        <ListRow title="Risk Center" subtitle="Your limits and the kill switch" icon="shield-half-outline" onPress={() => router.push('/risk-center')} />
        <ListRow title="System Health" subtitle="Data, workers and services" icon="pulse-outline" onPress={() => router.push('/system-health')} />
        <ListRow title="Audit history" subtitle="Every sign-in and change on your account" icon="list-outline" onPress={() => router.push('/settings/audit')} />
      </Card>
      <Card testID="support-contact">
        <CardHeader title="Report a problem" />
        {SUPPORT_EMAIL ? (
          <KeyValue label="Support email" value={SUPPORT_EMAIL} />
        ) : (
          <AppText tone="muted">No support contact is configured for this deployment.</AppText>
        )}
        <AppText variant="caption" tone="faint">Error messages include a request ID. Share it when reporting a problem; never share passwords, OTPs or broker credentials.</AppText>
      </Card>
      <Card>
        <CardHeader title="About" />
        <KeyValue label="Environment" value={status.data?.environment ?? '—'} />
        <KeyValue label="API" value={`${API_URL} · ${status.data?.api_version ?? '—'}`} />
        <KeyValue label="Market data" value={status.data ? (status.data.is_demo_data ? 'Simulated' : status.data.market_data_mode) : '—'} />
      </Card>
      <NoticeBanner>{status.data?.disclaimer ?? 'Derivatives trading carries a substantial risk of loss. No profits or prediction accuracy are guaranteed.'}</NoticeBanner>
    </Screen>
  );
}

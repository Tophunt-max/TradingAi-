import { Stack } from 'expo-router';
import { useTheme } from '../../components/theme/ThemeProvider';
import { usePreferences } from '../../features/account/api';

const TITLES = {
  more: 'More',
  'system-health': 'System Health',
  'feature/[id]': '',
  'settings/index': 'Profile & Settings',
  'settings/security': 'Security',
  'settings/sessions': 'Active Sessions',
  'settings/audit': 'Audit History',
  'instrument/[symbol]': 'Chart & Analysis',
  'option-chain': 'Option Chain',
  'bots/new': 'Create Bot',
  'bots/ai-create': 'AI Auto Create Bot',
  'bots/builder': 'Manual Bot Builder',
  'bots/templates': 'Strategy Templates',
  'bots/[id]': 'Bot Details',
  orders: 'Orders',
  'orders/[id]': 'Order Details',
  charges: 'Charges Calculator',
  positions: 'Positions',
  'risk-center': 'Risk Management',
  alerts: 'Notifications',
  brokers: 'Broker Connections',
  screener: 'Market Screener',
  education: 'Learn & Grow',
  models: 'AI Model Lab',
};

export default function AppLayout() {
  const theme = useTheme();
  usePreferences(); // hydrate theme / motion / haptics preferences after sign-in
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.colors.background },
        headerTintColor: theme.colors.text,
        headerTitleStyle: { color: theme.colors.text },
        headerShadowVisible: false,
        headerBackButtonDisplayMode: 'minimal',
        contentStyle: { backgroundColor: theme.colors.background },
      }}
    >
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      {Object.entries(TITLES).map(([name, title]) => (
        <Stack.Screen key={name} name={name} options={{ title }} />
      ))}
    </Stack>
  );
}
